#!/usr/bin/env bash
#
# install.sh — sets up the node dashboard on an existing Bitcoin node.
#
# Assumes a running bitcoind. How it was set up does not matter: by hand, from
# a package, RaspiBolt, or with the scripts of any prebuilt kit.
#
# It installs neither Bitcoin Core nor an Electrum server. That is deliberate:
# a script that occupies 750 GB and builds from source for hours is a
# different animal from a status page — and nobody should run it unread.
#
# What this script does:
#   1. find the data directory and bitcoin.conf
#   2. create a READ-ONLY RPC account (rpcauth + rpcwhitelist)
#   3. create the service user 'nodedash' without login rights
#   4. set up the generator as a systemd service
#   5. set up a web server that hands out static files and nothing else
#   6. limit the firewall to the local network, if ufw is present
#
# What it does NOT do: restart bitcoind without asking, delete data, open
# anything to the internet.
#
# Usage:
#   sudo bash install.sh                          find everything itself
#   sudo bash install.sh --language en            page language (de or en)
#   sudo bash install.sh --datadir /path/to/data  give the data directory
#   sudo bash install.sh --port 8080              another port for the page
#   sudo bash install.sh --subnet 192.168.1.0/24  give the local network
#   sudo bash install.sh --restart                restart bitcoind at the end
#   sudo bash install.sh --uninstall              remove everything again
#
# Repeatable: running it twice breaks nothing.

set -uo pipefail

PORT=80
DATADIR=""
SUBNET=""
WWW="/var/www/node"
DASH_USER="nodedash"
CONF="/etc/node-dashboard.conf"
RPC_USER="dashboard"
ACTION="install"
RESTART=0
LANGUAGE=""

# Read-only methods, all of them. Before adding anything here, check that the
# method really changes nothing.
METHODS="getblockchaininfo,getnetworkinfo,getmempoolinfo,getconnectioncount,uptime,estimatesmartfee,getblockstats,getblockhash,getblockheader,getpeerinfo,getnetworkhashps"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
title() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok()    { printf '  [ ok ] %s\n' "$*"; }
info()  { printf '  ...... %s\n' "$*"; }
warn()  { printf '  [ !! ] %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --language|--lang) LANGUAGE="${2:-}"; shift 2 ;;
        --datadir)         DATADIR="${2:-}"; shift 2 ;;
        --port)            PORT="${2:-}"; shift 2 ;;
        --subnet)          SUBNET="${2:-}"; shift 2 ;;
        --restart)         RESTART=1; shift ;;
        --uninstall)       ACTION="remove"; shift ;;
        -h|--help)         sed -n '2,32p' "$0"; exit 0 ;;
        *) red "Unknown option: $1"; exit 1 ;;
    esac
done

[[ $EUID -eq 0 ]] || { red "Please run with sudo."; exit 1; }

SCRIPTDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ================================================================= Uninstall =
if [[ "$ACTION" == "remove" ]]; then
    title "Removing"
    systemctl disable --now node-dashboard >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/node-dashboard.service
    systemctl daemon-reload
    rm -f /usr/local/bin/node-dashboard
    rm -rf "$WWW"
    rm -f /etc/nginx/sites-enabled/node-dashboard /etc/nginx/sites-available/node-dashboard
    systemctl reload nginx >/dev/null 2>&1 || true
    userdel "$DASH_USER" >/dev/null 2>&1 || true
    ok "service, program, web page and user removed"
    yellow "  Left alone: $CONF and the lines in your bitcoin.conf."
    yellow "  Clean up bitcoin.conf by hand if you no longer want the account:"
    echo "    rpcauth=${RPC_USER}:..."
    echo "    rpcwhitelist=${RPC_USER}:..."
    exit 0
fi

# ================================================================== Language =
# Asked exactly once, and only when there is a terminal to answer. Piped into
# a shell — the usual "curl … | bash" — the question would hang forever, so
# English is assumed there. Everything else in this script runs without
# questions.
if [[ -z "$LANGUAGE" ]]; then
    if [[ -t 0 ]]; then
        printf '\n\033[1m== Language ==\033[0m\n'
        printf '  Language of the dashboard page — [E]nglish or [G]erman? [E] '
        read -r ANSWER
        case "${ANSWER,,}" in
            g|ger|german|de|d) LANGUAGE="de" ;;
            *)                 LANGUAGE="en" ;;
        esac
    else
        LANGUAGE="en"
    fi
fi
case "${LANGUAGE,,}" in
    de|d|german|deutsch) LANGUAGE="de" ;;
    *)                   LANGUAGE="en" ;;
esac

# =============================================================== Preconditions
title "Preconditions"

ok "page language: $LANGUAGE"

command -v python3 >/dev/null || { red "python3 is missing. apt install python3"; exit 1; }
ok "python3 present ($(python3 -V 2>&1))"

if ! systemctl is-active --quiet bitcoind && ! pgrep -x bitcoind >/dev/null; then
    warn "No bitcoind is running."
    warn "The dashboard will be set up but will show 'node not reachable'"
    warn "until one runs. This script does not install Bitcoin Core."
else
    ok "bitcoind is running"
fi

# --- find the data directory ------------------------------------------------
if [[ -z "$DATADIR" ]]; then
    # First: from the command line of the running process
    CANDIDATE="$(tr '\0' '\n' < /proc/"$(pgrep -x bitcoind | head -1)"/cmdline 2>/dev/null \
                | sed -n 's/^-datadir=//p' | head -1)"
    # Second: from the systemd unit
    [[ -z "$CANDIDATE" ]] && CANDIDATE="$(systemctl cat bitcoind 2>/dev/null \
                | sed -n 's/.*-datadir=\([^ ]*\).*/\1/p' | head -1)"
    # Third: the usual places
    if [[ -z "$CANDIDATE" ]]; then
        for P in /mnt/*/bitcoin /var/lib/bitcoind /home/bitcoin/.bitcoin \
                 /home/*/.bitcoin "$HOME/.bitcoin"; do
            [[ -f "$P/bitcoin.conf" ]] && { CANDIDATE="$P"; break; }
        done
    fi
    DATADIR="$CANDIDATE"
fi

if [[ -z "$DATADIR" || ! -d "$DATADIR" ]]; then
    red "  Data directory not found."
    echo "  Please give it:  sudo bash install.sh --datadir /path/to/datadir"
    exit 1
fi
BITCOIN_CONF="${DATADIR}/bitcoin.conf"
[[ -f "$BITCOIN_CONF" ]] || { red "  $BITCOIN_CONF is missing."; exit 1; }
ok "data directory: $DATADIR"

if [[ ! -w "$BITCOIN_CONF" ]]; then
    red "  $BITCOIN_CONF is not writable."
    echo "  Prebuilt kits (Umbrel, Start9, MyNode) manage it themselves and"
    echo "  overwrite it on restart. There, add the lines from the README by"
    echo "  hand at the place the kit provides for your own additions."
    exit 1
fi

# --- local network ----------------------------------------------------------
if [[ -z "$SUBNET" ]]; then
    CIDR="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | head -1)"
    if [[ -n "$CIDR" ]]; then
        SUBNET="$(python3 -c "import ipaddress,sys; print(ipaddress.ip_network(sys.argv[1], strict=False))" "$CIDR" 2>/dev/null)"
    fi
fi
if [[ -n "$SUBNET" ]]; then
    ok "network range detected: $SUBNET"
else
    warn "network range not detected — the firewall is left alone."
fi

SOURCE="${SCRIPTDIR}/node-dashboard.py"
[[ -f "$SOURCE" ]] || { red "node-dashboard.py is missing next to this script."; exit 1; }
ok "generator found"

# ================================================================ RPC account =
title "Read-only access to the node"

if grep -q "^rpcauth=${RPC_USER}:" "$BITCOIN_CONF" && [[ -f "$CONF" ]]; then
    ok "account already exists — password left unchanged"
    DASH_PASS="$(sed -n 's/^RPC_PASSWORD=//p' "$CONF" | head -1)"
    RESTART_NEEDED=0
else
    DASH_PASS="$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"
    RPCAUTH="$(python3 - "$RPC_USER" "$DASH_PASS" <<'PY'
import hashlib, hmac, os, sys
user, password = sys.argv[1], sys.argv[2]
salt = os.urandom(16).hex()
digest = hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()
print(f"rpcauth={user}:{salt}${digest}")
PY
)"
    BACKUP="${BITCOIN_CONF}.$(date +%Y%m%d-%H%M%S).before-dashboard"
    cp -a "$BITCOIN_CONF" "$BACKUP"
    ok "bitcoin.conf backed up to $BACKUP"

    # Remove old lines of the same user, then write fresh ones.
    sed -i "/^rpcauth=${RPC_USER}:/d;/^rpcwhitelist=${RPC_USER}:/d" "$BITCOIN_CONF"
    {
        echo ""
        echo "# --- node dashboard, read-only access ----------------------------"
        echo "$RPCAUTH"
        echo "rpcwhitelist=${RPC_USER}:${METHODS}"
    } >> "$BITCOIN_CONF"

    # rpcwhitelistdefault=0 means the restriction applies only to users that
    # have a whitelist of their own. Everyone else keeps full access.
    grep -q '^rpcwhitelistdefault=' "$BITCOIN_CONF" \
        || echo "rpcwhitelistdefault=0" >> "$BITCOIN_CONF"

    ok "account '${RPC_USER}' added, limited to 10 read-only methods"
    RESTART_NEEDED=1
fi

if [[ -z "${DASH_PASS:-}" ]]; then
    red "  No password could be determined. Please check $CONF."
    exit 1
fi

# =================================================================== Install =
title "Setting up the dashboard"

id -u "$DASH_USER" >/dev/null 2>&1 \
    || useradd --system --no-create-home --shell /usr/sbin/nologin "$DASH_USER"
ok "service user '$DASH_USER' present"

install -d -m 0755 -o "$DASH_USER" -g "$DASH_USER" "$WWW"
install -d -m 0755 /var/lib/node-dashboard
install -m 0755 -o root -g root "$SOURCE" /usr/local/bin/node-dashboard
ok "generator in /usr/local/bin/node-dashboard"

# Must be allowed to read the journal, otherwise the log panel stays empty
usermod -aG systemd-journal "$DASH_USER" 2>/dev/null || true

cat > "$CONF" <<EOF
# Configuration of the node dashboard.
# The password belongs to the RPC user '${RPC_USER}', which bitcoin.conf
# restricts to read-only commands via rpcwhitelist.
RPC_HOST=127.0.0.1
RPC_PORT=8332
RPC_USER=${RPC_USER}
RPC_PASSWORD=${DASH_PASS}
OUT_DIR=${WWW}
DATA_DIR=${DATADIR}

# Display language of the page: de or en. This affects only what appears in
# the browser — log lines come from the node and stay as they are.
LANGUAGE=${LANGUAGE}

# Port of the Electrum server, if one runs. Otherwise the card is omitted.
ELECTRS_PORT=50001

# Interval in seconds: querying the node and refreshing the log panel.
INTERVAL=30
LOG_INTERVAL=5

# Log sources, comma separated. Leaving it empty switches the panel off.
LOG_SERVICES=bitcoind
LOG_LINES=150

# Timeout per call. During the initial sync Bitcoin Core stalls its query
# interface while it writes the dbcache to disk.
RPC_TIMEOUT=45

# This many unsuccessful calls in a row before the node counts as down.
TOLERANCE=3

# Maximum number of peers in the network map.
PEERS_MAX=64
EOF
chown "root:${DASH_USER}" "$CONF"
chmod 640 "$CONF"
ok "configuration in $CONF (readable by root and $DASH_USER only)"

cat > /etc/systemd/system/node-dashboard.service <<EOF
[Unit]
Description=Generates the static status page of the node
After=network.target

[Service]
Type=simple
User=${DASH_USER}
Group=${DASH_USER}
ExecStart=/usr/local/bin/node-dashboard --config ${CONF}
Restart=always
RestartSec=10

# The generator reads the node and writes files. It needs nothing else.
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
MemoryDenyWriteExecute=true
ProtectClock=true
ProtectHostname=true
ProtectKernelLogs=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native
CapabilityBoundingSet=
# The generator talks to bitcoind and electrs on this machine and to nothing
# else. The kernel enforces that here; the code only promises it.
IPAddressDeny=any
IPAddressAllow=localhost
ReadWritePaths=${WWW}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable node-dashboard >/dev/null
# Explicitly restart: if the service is already running, 'enable --now' does
# nothing and the version just installed would not take effect.
systemctl restart node-dashboard
sleep 3
if systemctl is-active --quiet node-dashboard; then
    ok "generator is running"
else
    red "  Generator does not start:"
    journalctl -u node-dashboard -n 20 --no-pager | sed 's/^/    /'
    exit 1
fi

# ================================================================ Web server =
title "Serving"

# The Content Security Policy is the reason style and script live in their own
# files: only that way does it work without 'unsafe-inline'.
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

    # Allow read requests only
    if (\$request_method !~ ^(GET|HEAD)\$) { return 405; }

    # Deliberately without gzip: on a local network the link is fast and the
    # CPU of a single-board computer is scarce.
    gzip off;

    # Nothing that starts with a dot: the generator's temporary files
    # (.tmp-…) sit in the same folder for a split second.
    location ~ /\. { return 404; }

    location / { try_files \$uri \$uri/ =404; }

    # 'expires' rather than 'add_header Cache-Control': an add_header inside a
    # location block would discard every header set above.
    location ~ \.(json|txt)\$ { expires -1; }
    location ~ \.(css|js)\$   { expires 10m; }
}
EOF
    ln -sf /etc/nginx/sites-available/node-dashboard /etc/nginx/sites-enabled/node-dashboard
    if nginx -t >/dev/null 2>&1; then
        systemctl enable --now nginx >/dev/null 2>&1 || true
        systemctl reload nginx
        ok "nginx serves $WWW on port $PORT"
    else
        red "  nginx configuration is faulty:"
        nginx -t
        exit 1
    fi
else
    warn "nginx is not installed."
    info "Either install it (apt install nginx) and run this script again, or"
    info "serve $WWW with any web server you like. It only has to hand out"
    info "static files — nothing else."
fi

# ================================================================== Firewall =
if command -v ufw >/dev/null && [[ -n "$SUBNET" ]]; then
    title "Firewall"
    while ufw status numbered 2>/dev/null | grep -q "${PORT}/tcp"; do
        NR="$(ufw status numbered | grep "${PORT}/tcp" | head -1 | tr -d '[]' | awk '{print $1}')"
        [[ -n "$NR" ]] || break
        yes | ufw delete "$NR" >/dev/null 2>&1 || break
    done
    ufw allow from "$SUBNET" to any port "$PORT" proto tcp comment 'node dashboard LAN' >/dev/null
    ok "port $PORT reachable from $SUBNET only"
    info "The page is not reachable from the internet, not even with an open router."
fi

# ===================================================================== Done ==
title "Done"

IP="$(ip -4 -o route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
echo "  In the browser:   http://${IP:-<ip-of-this-machine>}${PORT:+$([[ $PORT != 80 ]] && echo ":$PORT")}"
echo

if (( RESTART_NEEDED == 1 )); then
    if (( RESTART == 1 )); then
        systemctl restart bitcoind && ok "bitcoind restarted"
    else
        # Deliberately not on its own: a restart during the initial sync costs
        # the warm cache, and that is the operator's decision, not this
        # script's.
        yellow "  One step is left: bitcoind learns about the new account only"
        yellow "  after a restart. Until then the dashboard shows"
        yellow "  'node not reachable'."
        echo
        echo "      sudo systemctl restart bitcoind"
        echo
        warn "During the initial sync this costs the warm cache — depending"
        warn "on dbcache, a few minutes of progress."
    fi
fi

echo
echo "  Commands:"
echo "    systemctl status node-dashboard              the generator"
echo "    journalctl -u node-dashboard -f              follow along"
echo "    node-dashboard --config ${CONF} --once       rebuild the page by hand"
echo "    sudo bash install.sh --uninstall             remove everything again"
echo
echo "  To change the language later, edit LANGUAGE in ${CONF}"
echo "  and run: systemctl restart node-dashboard"
