#!/usr/bin/env bash
#
# install.sh — richtet das Node-Dashboard auf einem vorhandenen Bitcoin-Node ein.
#
# Setzt einen laufenden bitcoind voraus. Wie der aufgesetzt wurde, ist egal:
# von Hand, per Paket, RaspiBolt, oder mit den Skripten irgendeines Bausatzes.
#
# Es installiert weder Bitcoin Core noch einen Electrum-Server. Das ist Absicht:
# Ein Skript, das 750 GB belegt und stundenlang aus der Quelle baut, ist etwas
# anderes als eine Statusseite — und es soll niemand ungelesen ausfuehren.
#
# Was das Skript tut:
#   1. Datenverzeichnis und bitcoin.conf finden
#   2. einen NUR LESENDEN RPC-Zugang anlegen (rpcauth + rpcwhitelist)
#   3. Dienstnutzer 'nodedash' ohne Anmelderechte anlegen
#   4. Generator als systemd-Dienst einrichten
#   5. Webserver einrichten, der ausschliesslich statische Dateien ausliefert
#   6. Firewall auf das Heimnetz begrenzen, falls ufw vorhanden ist
#
# Was es NICHT tut: bitcoind ohne Rueckfrage neu starten, Daten loeschen,
# irgendetwas ins Internet oeffnen.
#
# Aufruf:
#   sudo bash install.sh                          alles selbst finden
#   sudo bash install.sh --datadir /pfad/zu/data  Datenverzeichnis vorgeben
#   sudo bash install.sh --port 8080              anderer Port fuer die Seite
#   sudo bash install.sh --subnetz 192.168.1.0/24 Heimnetz vorgeben
#   sudo bash install.sh --neustart               bitcoind am Ende neu starten
#   sudo bash install.sh --deinstallieren         alles wieder entfernen
#
# Es laeuft ohne Rueckfragen durch und startet von sich aus nichts neu.
# Wiederholbar: zweimal ausfuehren aendert nichts kaputt.

set -uo pipefail

PORT=80
DATADIR=""
SUBNETZ=""
WWW="/var/www/node"
DASH_USER="nodedash"
CONF="/etc/node-dashboard.conf"
RPC_USER="dashboard"
AKTION="einrichten"
NEUSTART=0

# Ausschliesslich lesende Methoden. Wer hier etwas ergaenzt, prueft vorher,
# ob die Methode wirklich nichts veraendert.
METHODEN="getblockchaininfo,getnetworkinfo,getmempoolinfo,getconnectioncount,uptime,estimatesmartfee,getblockstats,getblockhash,getblockheader,getpeerinfo"

rot()   { printf '\033[31m%s\033[0m\n' "$*"; }
gelb()  { printf '\033[33m%s\033[0m\n' "$*"; }
titel() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok()    { printf '  [ ok ] %s\n' "$*"; }
info()  { printf '  ...... %s\n' "$*"; }
warn()  { printf '  [ !! ] %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --datadir)        DATADIR="${2:-}"; shift 2 ;;
        --port)           PORT="${2:-}"; shift 2 ;;
        --subnetz)        SUBNETZ="${2:-}"; shift 2 ;;
        --neustart)       NEUSTART=1; shift ;;
        --deinstallieren) AKTION="entfernen"; shift ;;
        -h|--help)        sed -n '2,36p' "$0"; exit 0 ;;
        *) rot "Unbekannte Option: $1"; exit 1 ;;
    esac
done

[[ $EUID -eq 0 ]] || { rot "Bitte mit sudo starten."; exit 1; }

SKRIPTORDNER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================ Deinstallieren =
if [[ "$AKTION" == "entfernen" ]]; then
    titel "Entfernen"
    systemctl disable --now node-dashboard >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/node-dashboard.service
    systemctl daemon-reload
    rm -f /usr/local/bin/node-dashboard
    rm -rf "$WWW"
    rm -f /etc/nginx/sites-enabled/node-dashboard /etc/nginx/sites-available/node-dashboard
    systemctl reload nginx >/dev/null 2>&1 || true
    userdel "$DASH_USER" >/dev/null 2>&1 || true
    ok "Dienst, Programm, Webseite und Nutzer entfernt"
    gelb "  Nicht angetastet: $CONF und die Zeilen in deiner bitcoin.conf."
    gelb "  Die bitcoin.conf von Hand aufraeumen, wenn du den Zugang nicht mehr willst:"
    echo "    rpcauth=${RPC_USER}:..."
    echo "    rpcwhitelist=${RPC_USER}:..."
    exit 0
fi

# ============================================================ Vorbedingungen =
titel "Vorbedingungen"

command -v python3 >/dev/null || { rot "python3 fehlt. apt install python3"; exit 1; }
ok "python3 vorhanden ($(python3 -V 2>&1))"

if ! systemctl is-active --quiet bitcoind && ! pgrep -x bitcoind >/dev/null; then
    warn "Es laeuft kein bitcoind."
    warn "Das Dashboard wird eingerichtet, zeigt aber 'Node nicht erreichbar',"
    warn "bis einer laeuft. Dieses Skript installiert Bitcoin Core nicht."
else
    ok "bitcoind laeuft"
fi

# --- Datenverzeichnis finden ------------------------------------------------
if [[ -z "$DATADIR" ]]; then
    # Erstens: aus der Befehlszeile des laufenden Prozesses
    KANDIDAT="$(tr '\0' '\n' < /proc/"$(pgrep -x bitcoind | head -1)"/cmdline 2>/dev/null \
                | sed -n 's/^-datadir=//p' | head -1)"
    # Zweitens: aus der systemd-Einheit
    [[ -z "$KANDIDAT" ]] && KANDIDAT="$(systemctl cat bitcoind 2>/dev/null \
                | sed -n 's/.*-datadir=\([^ ]*\).*/\1/p' | head -1)"
    # Drittens: die ueblichen Orte
    if [[ -z "$KANDIDAT" ]]; then
        for P in /mnt/*/bitcoin /var/lib/bitcoind /home/bitcoin/.bitcoin \
                 /home/*/.bitcoin "$HOME/.bitcoin"; do
            [[ -f "$P/bitcoin.conf" ]] && { KANDIDAT="$P"; break; }
        done
    fi
    DATADIR="$KANDIDAT"
fi

if [[ -z "$DATADIR" || ! -d "$DATADIR" ]]; then
    rot "  Datenverzeichnis nicht gefunden."
    echo "  Bitte angeben:  sudo bash install.sh --datadir /pfad/zum/datadir"
    exit 1
fi
BITCOIN_CONF="${DATADIR}/bitcoin.conf"
[[ -f "$BITCOIN_CONF" ]] || { rot "  $BITCOIN_CONF fehlt."; exit 1; }
ok "Datenverzeichnis: $DATADIR"

if [[ ! -w "$BITCOIN_CONF" ]]; then
    rot "  $BITCOIN_CONF ist nicht beschreibbar."
    echo "  Bei fertigen Bausaetzen (Umbrel, Start9, MyNode) wird sie vom System"
    echo "  verwaltet und beim Neustart ueberschrieben. Dort die Zeilen aus dem"
    echo "  README von Hand an der vorgesehenen Stelle eintragen."
    exit 1
fi

# --- Heimnetz ---------------------------------------------------------------
if [[ -z "$SUBNETZ" ]]; then
    CIDR="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | head -1)"
    if [[ -n "$CIDR" ]]; then
        SUBNETZ="$(python3 -c "import ipaddress,sys; print(ipaddress.ip_network(sys.argv[1], strict=False))" "$CIDR" 2>/dev/null)"
    fi
fi
if [[ -n "$SUBNETZ" ]]; then
    ok "Netzbereich erkannt: $SUBNETZ"
else
    warn "Netzbereich nicht erkannt — die Firewall bleibt unangetastet."
fi

QUELLE="${SKRIPTORDNER}/node-dashboard.py"
[[ -f "$QUELLE" ]] || { rot "node-dashboard.py fehlt neben diesem Skript."; exit 1; }
ok "Generator gefunden"

# ============================================================== RPC-Zugang ===
titel "Lesender Zugang zum Node"

if grep -q "^rpcauth=${RPC_USER}:" "$BITCOIN_CONF" && [[ -f "$CONF" ]]; then
    ok "Zugang besteht bereits — Passwort bleibt unveraendert"
    DASH_PASS="$(sed -n 's/^RPC_PASSWORD=//p' "$CONF" | head -1)"
    NEUSTART_NOETIG=0
else
    DASH_PASS="$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"
    RPCAUTH="$(python3 - "$RPC_USER" "$DASH_PASS" <<'PY'
import hashlib, hmac, os, sys
benutzer, passwort = sys.argv[1], sys.argv[2]
salz = os.urandom(16).hex()
pruef = hmac.new(salz.encode(), passwort.encode(), hashlib.sha256).hexdigest()
print(f"rpcauth={benutzer}:{salz}${pruef}")
PY
)"
    SICHERUNG="${BITCOIN_CONF}.$(date +%Y%m%d-%H%M%S).vor-dashboard"
    cp -a "$BITCOIN_CONF" "$SICHERUNG"
    ok "bitcoin.conf gesichert nach $SICHERUNG"

    # Alte Zeilen desselben Nutzers entfernen, dann neu schreiben.
    sed -i "/^rpcauth=${RPC_USER}:/d;/^rpcwhitelist=${RPC_USER}:/d" "$BITCOIN_CONF"
    {
        echo ""
        echo "# --- Node-Dashboard, nur lesender Zugang -------------------------"
        echo "$RPCAUTH"
        echo "rpcwhitelist=${RPC_USER}:${METHODEN}"
    } >> "$BITCOIN_CONF"

    # rpcwhitelistdefault=0 heisst: Die Beschraenkung gilt nur fuer Nutzer,
    # die eine eigene Whitelist haben. Alle anderen behalten vollen Zugriff.
    grep -q '^rpcwhitelistdefault=' "$BITCOIN_CONF" \
        || echo "rpcwhitelistdefault=0" >> "$BITCOIN_CONF"

    ok "Zugang '${RPC_USER}' eingetragen, auf 10 lesende Methoden beschraenkt"
    NEUSTART_NOETIG=1
fi

if [[ -z "${DASH_PASS:-}" ]]; then
    rot "  Kein Passwort ermittelbar. Bitte $CONF pruefen."
    exit 1
fi

# ============================================================== Einrichten ===
titel "Dashboard einrichten"

id -u "$DASH_USER" >/dev/null 2>&1 \
    || useradd --system --no-create-home --shell /usr/sbin/nologin "$DASH_USER"
ok "Dienstnutzer '$DASH_USER' vorhanden"

install -d -m 0755 -o "$DASH_USER" -g "$DASH_USER" "$WWW"
install -d -m 0755 /var/lib/node-dashboard
install -m 0755 -o root -g root "$QUELLE" /usr/local/bin/node-dashboard
ok "Generator in /usr/local/bin/node-dashboard"

# Journal lesen duerfen, sonst bleibt die Protokollanzeige leer
usermod -aG systemd-journal "$DASH_USER" 2>/dev/null || true

cat > "$CONF" <<EOF
# Konfiguration des Node-Dashboards.
# Das Passwort gehoert zum RPC-Nutzer '${RPC_USER}', der in der bitcoin.conf
# per rpcwhitelist auf ausschliesslich lesende Befehle beschraenkt ist.
RPC_HOST=127.0.0.1
RPC_PORT=8332
RPC_USER=${RPC_USER}
RPC_PASSWORD=${DASH_PASS}
OUT_DIR=${WWW}
DATA_DIR=${DATADIR}

# Port des Electrum-Servers, falls einer laeuft. Sonst entfaellt die Karte.
ELECTRS_PORT=50001

# Takt in Sekunden: Abfrage des Nodes und Erneuerung der Protokollanzeige.
INTERVALL=30
LOG_INTERVALL=5

# Protokollquellen, mit Komma getrennt. Leer lassen schaltet die Anzeige ab.
LOG_DIENSTE=bitcoind
LOG_ZEILEN=150

# Zeitlimit je Abfrage. Waehrend der Erstsynchronisation haelt Bitcoin Core
# seine Abfrageschnittstelle an, solange es den dbcache auf die SSD schreibt.
RPC_TIMEOUT=45

# So viele erfolglose Abfragen in Folge, bevor der Node als ausgefallen gilt.
TOLERANZ=3

# Hoechstzahl der Gegenstellen in der Netzkarte.
PEERS_MAX=64
EOF
chown "root:${DASH_USER}" "$CONF"
chmod 640 "$CONF"
ok "Konfiguration in $CONF (nur root und $DASH_USER duerfen lesen)"

cat > /etc/systemd/system/node-dashboard.service <<EOF
[Unit]
Description=Erzeugt die statische Statusseite des Nodes
After=network.target

[Service]
Type=simple
User=${DASH_USER}
Group=${DASH_USER}
ExecStart=/usr/local/bin/node-dashboard --config ${CONF}
Restart=always
RestartSec=10

# Der Generator liest den Node und schreibt Dateien. Mehr braucht er nicht.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
ReadWritePaths=${WWW}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable node-dashboard >/dev/null
# Ausdruecklich restart: Laeuft der Dienst schon, tut 'enable --now' nichts,
# und die eben eingespielte Fassung wuerde nicht wirksam.
systemctl restart node-dashboard
sleep 3
if systemctl is-active --quiet node-dashboard; then
    ok "Generator laeuft"
else
    rot "  Generator startet nicht:"
    journalctl -u node-dashboard -n 20 --no-pager | sed 's/^/    /'
    exit 1
fi

# =============================================================== Webserver ===
titel "Auslieferung"

# Die Content-Security-Policy ist der Grund, warum Stil und Skript in eigenen
# Dateien liegen: Nur so kommt sie ohne 'unsafe-inline' aus.
CSP="default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

if command -v nginx >/dev/null; then
    cat > /etc/nginx/sites-available/node-dashboard <<EOF
server {
    listen ${PORT};
    listen [::]:${PORT};
    root ${WWW};
    index index.html;

    server_tokens off;
    autoindex off;
    add_header X-Content-Type-Options nosniff;
    add_header Referrer-Policy no-referrer;
    add_header X-Frame-Options DENY;
    add_header Content-Security-Policy "${CSP}";

    # Nur lesende Anfragen zulassen
    if (\$request_method !~ ^(GET|HEAD)\$) { return 405; }

    # Bewusst ohne gzip: Im Heimnetz ist die Leitung schnell und die CPU eines
    # Einplatinenrechners knapp.
    gzip off;

    location / { try_files \$uri \$uri/ =404; }

    # 'expires' statt 'add_header Cache-Control': ein add_header in einem
    # location-Block wuerde alle oben gesetzten Kopfzeilen verwerfen.
    location ~ \.(json|txt)\$ { expires -1; }
    location ~ \.(css|js)\$   { expires 10m; }
}
EOF
    ln -sf /etc/nginx/sites-available/node-dashboard /etc/nginx/sites-enabled/node-dashboard
    if nginx -t >/dev/null 2>&1; then
        systemctl enable --now nginx >/dev/null 2>&1 || true
        systemctl reload nginx
        ok "nginx liefert $WWW auf Port $PORT aus"
    else
        rot "  nginx-Konfiguration fehlerhaft:"
        nginx -t
        exit 1
    fi
else
    warn "nginx ist nicht installiert."
    info "Entweder installieren (apt install nginx) und dieses Skript erneut"
    info "ausfuehren, oder $WWW mit einem beliebigen Webserver ausliefern."
    info "Er muss nur statische Dateien ausliefern koennen — sonst nichts."
fi

# ================================================================ Firewall ===
if command -v ufw >/dev/null && [[ -n "$SUBNETZ" ]]; then
    titel "Firewall"
    while ufw status numbered 2>/dev/null | grep -q "${PORT}/tcp"; do
        NR="$(ufw status numbered | grep "${PORT}/tcp" | head -1 | tr -d '[]' | awk '{print $1}')"
        [[ -n "$NR" ]] || break
        yes | ufw delete "$NR" >/dev/null 2>&1 || break
    done
    ufw allow from "$SUBNETZ" to any port "$PORT" proto tcp comment 'Node-Dashboard LAN' >/dev/null
    ok "Port $PORT nur aus $SUBNETZ erreichbar"
    info "Aus dem Internet ist die Seite nicht erreichbar, auch nicht bei offenem Router."
fi

# ================================================================= Fertig ====
titel "Fertig"

IP="$(ip -4 -o route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
echo "  Im Browser:   http://${IP:-<IP-des-Rechners>}${PORT:+$([[ $PORT != 80 ]] && echo ":$PORT")}"
echo

if (( NEUSTART_NOETIG == 1 )); then
    if (( NEUSTART == 1 )); then
        systemctl restart bitcoind && ok "bitcoind neu gestartet"
    else
        # Bewusst nicht von sich aus: Ein Neustart waehrend der
        # Erstsynchronisation kostet den warmen Zwischenspeicher, und das ist
        # eine Entscheidung des Betreibers, nicht dieses Skripts.
        gelb "  Ein Schritt fehlt noch: bitcoind kennt den neuen Zugang erst"
        gelb "  nach einem Neustart. Bis dahin zeigt das Dashboard"
        gelb "  'Node nicht erreichbar'."
        echo
        echo "      sudo systemctl restart bitcoind"
        echo
        warn "Waehrend der Erstsynchronisation kostet das den warmen"
        warn "Zwischenspeicher — je nach dbcache einige Minuten Fortschritt."
    fi
fi

echo
echo "  Befehle:"
echo "    systemctl status node-dashboard              Generator"
echo "    journalctl -u node-dashboard -f              mitlesen"
echo "    node-dashboard --config ${CONF} --once       Seite von Hand erneuern"
echo "    sudo bash install.sh --deinstallieren        alles wieder entfernen"
