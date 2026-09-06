#!/usr/bin/env bash
#
# install.sh — sets up the node dashboard on an existing Bitcoin node.
#
# Assumes a running bitcoind. How it was set up does not matter: by hand, from
# a package or by hand. Prebuilt kits (Umbrel, Start9, MyNode) are not
# covered: they manage bitcoin.conf themselves.
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
#   sudo bash install.sh --rpc-port 18332         RPC port of the node (else detected)
#   sudo bash install.sh --subnet 192.168.1.0/24  give the local network
#   sudo bash install.sh --electrum-port 50002   port of your Electrum server (default 50001)
#   sudo bash install.sh --restart                restart bitcoind at the end
#   sudo bash install.sh --yes                    answer every question with its default
#   sudo bash install.sh --uninstall              remove everything again
#
# Questions (language, install nginx?, restart bitcoind?) are asked only
# when a terminal is attached; piped into a shell, or with --yes, the
# defaults apply. Every red line names the next command.
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
YES=0
ELECTRUM_PORT=50001
LANGUAGE=""
RPC_PORT=""

# Read-only methods, all of them. Before adding anything here, check that the
# method really changes nothing.
METHODS="getblockchaininfo,getnetworkinfo,getmempoolinfo,getconnectioncount,uptime,estimatesmartfee,getblockstats,getpeerinfo,getnetworkhashps"

# Colour only when the output is a terminal; a log file gets plain text.
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
    C_RED=""; C_GREEN=""; C_YELLOW=""; C_BOLD=""; C_OFF=""
fi
red()   { printf '  %s[FAIL]%s %s\n' "$C_RED" "$C_OFF" "$*"; }
yellow(){ printf '  %s%s%s\n' "$C_YELLOW" "$*" "$C_OFF"; }
title() { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_OFF"; }
ok()    { printf '  %s[ ok ]%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
info()  { printf '  ...... %s\n' "$*"; }
warn()  { printf '  %s[ !! ]%s %s\n' "$C_YELLOW" "$C_OFF" "$*"; }
# A failure names the next command, then stops. Nothing is left half done
# that a second run would not repair.
fail()  { red "$1"; shift; for L in "$@"; do printf '         %s\n' "$L"; done; exit 1; }

# ask "question" default(y|n) -> returns 0 for yes. Without a terminal, or
# with --yes, the default answers.
ask() {
    local question="$1" default="$2" answer
    if [[ -t 0 && $YES -eq 0 ]]; then
        if [[ "$default" == "y" ]]; then
            printf '  %s [Y/n] ' "$question"
        else
            printf '  %s [y/N] ' "$question"
        fi
        read -r answer
        answer="${answer,,}"
        [[ -z "$answer" ]] && answer="$default"
        [[ "$answer" == "y" || "$answer" == "yes" || "$answer" == "j" || "$answer" == "ja" ]]
    else
        [[ "$default" == "y" ]]
    fi
}

# Install a Debian package after asking. The script itself installs nothing
# behind anyone's back — but it does offer, because "nginx is missing, come
# back later" is not a finished installation.
need_package() {
    local cmd="$1" pkg="$2" why="$3"
    command -v "$cmd" >/dev/null && return 0
    warn "$pkg is not installed — $why"
    if ask "Install $pkg now with apt?" y; then
        if ! command -v apt-get >/dev/null; then
            fail "no apt-get on this system." "Install $pkg with your package manager and run this script again."
        fi
        info "apt-get install -y $pkg …"
        # mktemp, not /tmp/install-$pkg.log: that path is predictable and
        # this runs as root — anyone could have put a symlink there and had
        # the file it points at truncated (2026-09-06).
        local log
        log="$(mktemp)" || fail "no temporary file could be created."
        if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg" >"$log" 2>&1; then
            ok "$pkg installed"
            rm -f "$log"
        else
            fail "$pkg could not be installed. The last lines of apt:" "$(tail -n 5 "$log")" "Then run this script again."
        fi
    else
        return 1
    fi
}

# Insert lines into the network-agnostic part of bitcoin.conf — everything
# above the first [section] header. Appending at the end put them inside
# whatever section came last: on a file ending in [test] the account existed
# on testnet only, mainnet answered 401, and the page's hint pointed at the
# password (2026-09-06). Written back through the original file (cat >), so
# owner and mode stay as they were — bitcoin.conf belongs to the node's user.
conf_insert() {
    local file="$1" block="$2" tmp rc
    tmp="$(mktemp)" || return 1
    awk -v blockfile="$block" '
        function emit(   line) { while ((getline line < blockfile) > 0) print line
                                 close(blockfile) }
        !done && /^[[:space:]]*\[[^]]+\][[:space:]]*$/ { emit(); done = 1 }
        { print }
        END { if (!done) emit() }
    ' "$file" > "$tmp" && cat "$tmp" > "$file"
    rc=$?
    rm -f "$tmp"
    return $rc
}

# A value out of the network-agnostic part of bitcoin.conf.
bitcoin_conf_value() {
    sed -n "/^[[:space:]]*\[/q; s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" \
        "$BITCOIN_CONF" | head -1
}

# A value out of our own configuration file, so a second run does not throw
# away what somebody edited there. Until 2026-09-06 the file was rewritten
# from the defaults on every run: a hand-set INTERVAL, LOG_LINES, PEERS_MAX
# or ELECTRS_METRICS was gone, and a German page came back English.
conf_value() {
    local v=""
    [[ -f "$CONF" ]] && v="$(sed -n "s/^$1=//p" "$CONF" | head -1)"
    printf '%s' "${v:-$2}"
}

# The one comment that says "this rule is ours". Set on the way in, matched
# on the way out.
UFW_MARK='node dashboard LAN'

# Delete every ufw rule that carries our comment — and only those.
# Until 2026-09-06 this matched on the port: grep "80/tcp" with no anchor,
# which also hits 8080/tcp, 1080/tcp, 3080/tcp, and the loop deleted them
# all without a word. The script even recommends --port 8080 two screens
# up. Reported from outside with a reproduction; no test had ever run a
# line of this file. The comment is the only thing that marks a rule as
# ours, so that is what we match — anchored to the end of the line.
# And --force instead of "yes |": under pipefail, yes dies of SIGPIPE the
# moment ufw exits, the pipeline reports failure, and "|| break" left the
# loop after the first of two rules (v4 deleted, v6 kept — seen in the
# test below, the same day).
ufw_delete_marked() {
    local nr
    while :; do
        nr="$(ufw status numbered 2>/dev/null \
              | grep -E "# ${UFW_MARK}[[:space:]]*\$" | head -1 \
              | tr -d '[]' | awk '{print $1}')"
        [[ "$nr" =~ ^[0-9]+$ ]] || break
        ufw --force delete "$nr" >/dev/null 2>&1 || break
    done
}

# An option that takes a value must be given one. Until 2026-09-06 the loop
# ran "shift 2" on a single remaining argument: bash refuses, $# stays 1, and
# the same case runs again — "sudo bash install.sh --datadir" span forever,
# printing shift errors, until Ctrl-C.
# Called as a statement, never inside $( ): fail() ends the script with
# exit, and inside a command substitution that would only end the subshell —
# the loop would run on with an empty value.
need_value() {
    [[ -n "${2:-}" ]] || fail "$1 needs a value." \
        "sudo bash install.sh --help  lists the options"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --language|--lang) need_value "$1" "${2:-}"; LANGUAGE="$2"; shift 2 ;;
        --datadir)         need_value "$1" "${2:-}"; DATADIR="$2"; shift 2 ;;
        --port)            need_value "$1" "${2:-}"; PORT="$2"; shift 2 ;;
        --rpc-port)        need_value "$1" "${2:-}"; RPC_PORT="$2"; shift 2 ;;
        --subnet)          need_value "$1" "${2:-}"; SUBNET="$2"; shift 2 ;;
        --electrum-port)   need_value "$1" "${2:-}"; ELECTRUM_PORT="$2"; shift 2 ;;
        --restart)         RESTART=1; shift ;;
        --yes|-y)          YES=1; shift ;;
        --uninstall)       ACTION="remove"; shift ;;
        -h|--help)         sed -n '2,37p' "$0"; exit 0 ;;
        *) fail "Unknown option: $1" "sudo bash install.sh --help  lists the options" ;;
    esac
done

# Ports and the subnet end up in the nginx configuration, in a ufw rule and
# in a configuration file. They are checked before any of that happens.
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) \
    || fail "--port $PORT is not a port number (1–65535)."
[[ "$ELECTRUM_PORT" =~ ^[0-9]+$ ]] && (( ELECTRUM_PORT >= 1 && ELECTRUM_PORT <= 65535 )) \
    || fail "--electrum-port $ELECTRUM_PORT is not a port number (1–65535)."
[[ -z "$RPC_PORT" || ( "$RPC_PORT" =~ ^[0-9]+$ && RPC_PORT -ge 1 && RPC_PORT -le 65535 ) ]] \
    || fail "--rpc-port $RPC_PORT is not a port number (1–65535)."
[[ -z "$SUBNET" || "$SUBNET" =~ ^[0-9a-fA-F:.]+/[0-9]+$ ]] \
    || fail "--subnet $SUBNET is not a CIDR range, e.g. 192.168.1.0/24."

[[ $EUID -eq 0 ]] || fail "This script needs root." "sudo bash install.sh"

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
    # The welcome site was unlinked during the install; without this, removing
    # the dashboard left the machine with no nginx site at all (2026-09-06).
    if [[ -e /etc/nginx/sites-available/default && ! -e /etc/nginx/sites-enabled/default ]]; then
        ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
        ok "nginx's welcome site enabled again"
    fi
    systemctl reload nginx >/dev/null 2>&1 || true
    rm -rf /var/lib/node-dashboard
    userdel "$DASH_USER" >/dev/null 2>&1 || true
    # The firewall rule went with nothing until 2026-09-06: the port stayed
    # open for a page that was no longer there.
    command -v ufw >/dev/null && ufw_delete_marked
    ok "service, program, web page, user and firewall rule removed"
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
# A second run keeps the language of the first unless one is given: piped
# into a shell the question cannot be asked, and the default (English) used
# to flip a German installation over without a word (2026-09-06).
if [[ -z "$LANGUAGE" && -f "$CONF" ]]; then
    LANGUAGE="$(conf_value LANGUAGE "")"
    [[ -n "$LANGUAGE" ]] && ok "language kept from $CONF: $LANGUAGE"
fi
if [[ -z "$LANGUAGE" ]]; then
    if [[ -t 0 && $YES -eq 0 ]]; then
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

need_package python3 python3 "the generator is a Python program" \
    || fail "python3 is required." "sudo apt install python3   then run this script again"
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
    fail "Data directory of Bitcoin Core not found." \
         "Find it (usually where bitcoin.conf and the 'blocks' folder are) and give it:" \
         "sudo bash install.sh --datadir /path/to/datadir"
fi
BITCOIN_CONF="${DATADIR}/bitcoin.conf"
[[ -f "$BITCOIN_CONF" ]] || fail "$BITCOIN_CONF is missing." \
    "Create it (an empty file is enough) and run this script again:" \
    "sudo touch $BITCOIN_CONF"
ok "data directory: $DATADIR"

if [[ ! -w "$BITCOIN_CONF" ]]; then
    fail "$BITCOIN_CONF is not writable." \
         "This script needs to add a read-only RPC account there." \
         "Prebuilt kits (Umbrel, Start9, MyNode) manage that file themselves" \
         "and are not covered by this script."
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
[[ -f "$SOURCE" ]] || fail "node-dashboard.py is missing next to this script." \
    "Run the script from the cloned folder:" \
    "cd btcnode-dashboard && sudo bash install.sh"
ok "generator found"

# --- Electrum server --------------------------------------------------------
# Not installed here, but looked for: it is the main reason to run a node of
# one's own — without it the wallet asks foreign servers. Recognised by the
# port (electrs, Fulcrum, ElectrumX all answer there), the dashboard shows
# what it finds and says so when nothing is there.
port_listens() { python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', int(sys.argv[1])))==0 else 1)" "$1" 2>/dev/null; }
if port_listens "$ELECTRUM_PORT"; then
    ok "Electrum server answers on 127.0.0.1:${ELECTRUM_PORT}"
elif [[ -f /etc/systemd/system/electrs.service ]]; then
    ok "electrs is set up (not answering yet — still indexing, probably)"
else
    warn "no Electrum server found on port ${ELECTRUM_PORT}."
    info "Without one your wallet asks foreign servers, and those learn which"
    info "addresses belong to you. electrs is the usual choice; the README says how."
    if [[ -t 0 && $YES -eq 0 ]]; then
        printf '  Does one run on another port? Enter it, or Enter for none: '
        read -r ANSWER
        if [[ "$ANSWER" =~ ^[0-9]+$ ]]; then
            ELECTRUM_PORT="$ANSWER"
            if port_listens "$ELECTRUM_PORT"; then
                ok "Electrum server answers on 127.0.0.1:${ELECTRUM_PORT}"
            else
                warn "nothing answers on ${ELECTRUM_PORT} right now — the port is kept, the dashboard will look there."
            fi
        fi
    fi
    info "The dashboard shows the gap on the page until a server answers there."
fi

# --- web server, decided up front so the run does not stop in the middle ----
WEBSERVER="nginx"
if ! need_package nginx nginx "it serves the page (static files, nothing else)"; then
    WEBSERVER=""
    warn "continuing without a web server — the page is written to $WWW,"
    warn "serve that folder with any server that hands out static files."
fi

# ================================================================ RPC account =
title "Read-only access to the node"

RESTART_NEEDED=0
KEEP_PASSWORD=0
if grep -q "^rpcauth=${RPC_USER}:" "$BITCOIN_CONF" && [[ -f "$CONF" ]]; then
    DASH_PASS="$(conf_value RPC_PASSWORD "")"
    [[ -n "$DASH_PASS" ]] && KEEP_PASSWORD=1
fi

if (( KEEP_PASSWORD == 1 )); then
    ok "account already exists — password left unchanged"
else
    DASH_PASS="$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"
fi

# The whitelist is written on every run, new password or not. It used to be
# written once and never again: an installation from before getnetworkhashps
# joined the list kept answering 403, the generator remembered the refusal
# half an hour at a time, and the hashrate curve never appeared — with
# nothing anywhere to say why (2026-09-06).
WANTED_WHITELIST="rpcwhitelist=${RPC_USER}:${METHODS}"
if (( KEEP_PASSWORD == 0 )) || ! grep -qxF "$WANTED_WHITELIST" "$BITCOIN_CONF"; then
    BACKUP="${BITCOIN_CONF}.$(date +%Y%m%d-%H%M%S).before-dashboard"
    cp -a "$BITCOIN_CONF" "$BACKUP"
    ok "bitcoin.conf backed up to $BACKUP"

    BLOCK="$(mktemp)" || fail "no temporary file could be created."
    {
        echo ""
        echo "# --- node dashboard, read-only access ----------------------------"
        if (( KEEP_PASSWORD == 0 )); then
            RPC_USER="$RPC_USER" DASH_PASS="$DASH_PASS" python3 - <<'PY'
import hashlib, hmac, os
# The password travels through the environment, not through argv:
# /proc/<pid>/cmdline is world-readable, /proc/<pid>/environ is not.
user, password = os.environ["RPC_USER"], os.environ["DASH_PASS"]
salt = os.urandom(16).hex()
digest = hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()
print(f"rpcauth={user}:{salt}${digest}")
PY
        else
            grep "^rpcauth=${RPC_USER}:" "$BITCOIN_CONF" | head -1
        fi
        echo "$WANTED_WHITELIST"
        # rpcwhitelistdefault=0 means the restriction applies only to users
        # that have a whitelist of their own. Everyone else keeps full access.
        grep -q '^rpcwhitelistdefault=' "$BITCOIN_CONF" || echo "rpcwhitelistdefault=0"
    } > "$BLOCK"

    # Drop the old lines of this user, then write the fresh ones into the
    # part of the file that belongs to no network section.
    sed -i "/^rpcauth=${RPC_USER}:/d;/^rpcwhitelist=${RPC_USER}:/d" "$BITCOIN_CONF"
    conf_insert "$BITCOIN_CONF" "$BLOCK" || fail "bitcoin.conf could not be written." \
        "The file as it was is in $BACKUP"
    rm -f "$BLOCK"

    ok "account '${RPC_USER}' written, limited to $(tr ',' '\n' <<<"$METHODS" | wc -l) read-only methods"
    RESTART_NEEDED=1
else
    ok "read-only whitelist already up to date"
fi

if [[ -z "${DASH_PASS:-}" ]]; then
    fail "No password could be determined." \
         "Remove $CONF and run this script again — a fresh account is created."
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

# Read what is there BEFORE the file is touched — install(1) truncates it,
# and conf_value would then only ever find the defaults.
CFG_INTERVAL="$(conf_value INTERVAL 30)"
CFG_LOG_INTERVAL="$(conf_value LOG_INTERVAL 5)"
CFG_LOG_SERVICES="$(conf_value LOG_SERVICES bitcoind)"
CFG_LOG_LINES="$(conf_value LOG_LINES 150)"
CFG_RPC_TIMEOUT="$(conf_value RPC_TIMEOUT 45)"
CFG_TOLERANCE="$(conf_value TOLERANCE 3)"
CFG_PEERS_MAX="$(conf_value PEERS_MAX 64)"
CFG_ELECTRS_METRICS="$(conf_value ELECTRS_METRICS 127.0.0.1:4224)"

# Created with its final mode BEFORE anything is written into it: "cat >"
# followed by chmod left the RPC password world-readable for the length of
# two commands (2026-09-06).
install -m 0640 -o root -g "$DASH_USER" /dev/null "$CONF"
cat > "$CONF" <<EOF
# Configuration of the node dashboard.
# The password belongs to the RPC user '${RPC_USER}', which bitcoin.conf
# restricts to read-only commands via rpcwhitelist.
RPC_HOST=127.0.0.1
RPC_PORT=${RPC_PORT}
RPC_USER=${RPC_USER}
RPC_PASSWORD=${DASH_PASS}
OUT_DIR=${WWW}
DATA_DIR=${DATADIR}

# Display language of the page: de or en. This affects only what appears in
# the browser — log lines come from the node and stay as they are.
LANGUAGE=${LANGUAGE}

# Port of the Electrum server (electrs, Fulcrum, ElectrumX). The page shows
# what answers there — and says so when nothing does.
ELECTRS_PORT=${ELECTRUM_PORT}

# Prometheus endpoint of electrs; read for the index bar while it is
# still indexing.
ELECTRS_METRICS=${CFG_ELECTRS_METRICS}

# Interval in seconds: querying the node and refreshing the log panel.
INTERVAL=${CFG_INTERVAL}
LOG_INTERVAL=${CFG_LOG_INTERVAL}

# Log sources, comma separated. Leaving it empty switches the panel off.
LOG_SERVICES=${CFG_LOG_SERVICES}
LOG_LINES=${CFG_LOG_LINES}

# Timeout per call. During the initial sync Bitcoin Core stalls its query
# interface while it writes the dbcache to disk.
RPC_TIMEOUT=${CFG_RPC_TIMEOUT}

# This many unsuccessful calls in a row before the node counts as down.
TOLERANCE=${CFG_TOLERANCE}

# Maximum number of peers in the network map.
PEERS_MAX=${CFG_PEERS_MAX}
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
    red "Generator does not start. The last lines of its log:"
    journalctl -u node-dashboard -n 20 --no-pager | sed 's/^/         /'
    fail "See above." "journalctl -u node-dashboard -n 50   shows more"
fi

# ================================================================ Web server =
title "Serving"

# The Content Security Policy is the reason style and script live in their own
# files: only that way does it work without 'unsafe-inline'.
CSP="default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

if [[ -n "$WEBSERVER" ]] && command -v nginx >/dev/null; then
    # The package ships a "Welcome to nginx" site on port 80. Two sites on
    # one port without a name: nginx serves the first by file name, and
    # "default" sorts before "node-dashboard" — the welcome page would win.
    # Only the link in sites-enabled goes; the file stays in sites-available.
    DEFAULT_SITE_REMOVED=0
    if [[ -e /etc/nginx/sites-enabled/default ]]; then
        rm -f /etc/nginx/sites-enabled/default
        DEFAULT_SITE_REMOVED=1
        info "nginx's welcome site disabled (sites-enabled/default)"
    fi
    cat > /etc/nginx/sites-available/node-dashboard <<EOF
server {
    listen ${PORT} default_server;
    listen [::]:${PORT} default_server;
    root ${WWW};
    index index.html;

    server_tokens off;
    autoindex off;
    # UTF-8 for text/plain as well: log.txt carries the node's own lines, and
    # without this nginx sends them without a charset — a browser opening the
    # file directly then reads them as latin-1.
    charset utf-8;
    # text/html stays in the list: naming any type at all replaces nginx's
    # default set, and dropping text/html would take the charset off the page.
    charset_types text/html text/plain text/css application/json application/javascript;
    # 'always': without it nginx drops these headers on every answer that is
    # not a normal 200 — the 404 for a dotfile and the 405 for a POST went out
    # bare (2026-09-06).
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header X-Frame-Options DENY always;
    add_header Content-Security-Policy "${CSP}" always;

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
        red "nginx rejects the configuration:"
        nginx -t 2>&1 | sed 's/^/         /'
        # Put the machine back the way it was before failing: the welcome
        # site was already unlinked at this point, and leaving without it
        # broke a setup that had been working (2026-09-06).
        rm -f /etc/nginx/sites-enabled/node-dashboard
        if (( DEFAULT_SITE_REMOVED == 1 )) && [[ -e /etc/nginx/sites-available/default ]]; then
            ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
            info "nginx's welcome site put back"
        fi
        fail "See above." "Usually another site already listens on port ${PORT}:" \
             "sudo bash install.sh --port 8080   moves the dashboard to another port"
    fi
else
    warn "no web server set up — $WWW must be served by hand."
fi

# ================================================================== Firewall =
title "Firewall"
if ! command -v ufw >/dev/null; then
    warn "ufw is not installed — port $PORT is open to every device in the network."
    info "That is fine at home. Never forward this port on your router: the"
    info "page has no login. (sudo apt install ufw && sudo bash install.sh limits it)"
elif [[ -z "$SUBNET" ]]; then
    warn "network range not detected — the firewall is left alone."
    info "sudo bash install.sh --subnet 192.168.1.0/24   (your range) sets the rule"
else
    # 'ufw status' prints nothing but "Status: inactive" when the firewall is
    # off — ufw_delete_marked then finds no rule to delete and every run
    # added another copy of the same one. 'ufw show added' lists them either
    # way (2026-09-06).
    UFW_ACTIVE=0
    ufw status 2>/dev/null | grep -qi '^Status: active' && UFW_ACTIVE=1
    if (( UFW_ACTIVE == 1 )); then
        ufw_delete_marked
        ufw allow from "$SUBNET" to any port "$PORT" proto tcp comment "$UFW_MARK" >/dev/null
        ok "port $PORT reachable from $SUBNET only"
        info "The page is not reachable from the internet, not even with an open router."
    else
        if ufw show added 2>/dev/null | grep -qF "comment '${UFW_MARK}'"; then
            info "the rule is already in ufw's rule set"
        else
            ufw allow from "$SUBNET" to any port "$PORT" proto tcp comment "$UFW_MARK" >/dev/null
            info "rule written for $SUBNET on port $PORT"
        fi
        # This used to read "port N reachable from SUBNET only" whether ufw
        # was running or not. It is a statement about the safety of the
        # machine, and it was false on every host with ufw installed but
        # never enabled (2026-09-06).
        warn "ufw is installed but NOT active — nothing is being filtered."
        info "sudo ufw enable   turns it on (check your SSH rule first)."
    fi
fi

# ===================================================================== Done ==
title "Done"

# The address people type into a browser. First choice: the interface the
# default route uses; the fallback covers a Pi without a default route.
IP="$(ip -4 -o route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
[[ -n "$IP" ]] || IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
URL="http://${IP:-<ip-of-this-machine>}"
[[ "$PORT" != "80" ]] && URL="${URL}:${PORT}"

# How far the node is: a restart during the initial sync costs the warm
# cache, so the default answer follows the state. Asked through the cookie,
# which this script may read as root; no answer means "unknown".
PROGRESS=""
if command -v bitcoin-cli >/dev/null; then
    PROGRESS="$(bitcoin-cli -datadir="$DATADIR" getblockchaininfo 2>/dev/null \
                | sed -n 's/.*"verificationprogress": *\([0-9.]*\).*/\1/p')"
fi
SYNCED=""
if [[ -n "$PROGRESS" ]]; then
    SYNCED="$(python3 -c "print('yes' if float('$PROGRESS') >= 0.9999 else 'no')" 2>/dev/null)"
    PERCENT="$(python3 -c "print(f'{float(\"$PROGRESS\") * 100:.1f}')" 2>/dev/null)"
fi

DO_RESTART=0
if (( RESTART_NEEDED == 1 )); then
    yellow "bitcoind learns about the new account only after a restart."
    yellow "Until then the dashboard shows 'node not reachable'."
    if (( RESTART == 1 )); then
        DO_RESTART=1
    elif [[ "$SYNCED" == "yes" ]]; then
        ok "the chain is up to date (${PERCENT} %) — a restart costs nothing"
        ask "Restart bitcoind now?" y && DO_RESTART=1
    elif [[ "$SYNCED" == "no" ]]; then
        warn "the node is still syncing (${PERCENT} %) — a restart costs the warm"
        warn "cache, depending on dbcache a few minutes of progress."
        ask "Restart bitcoind now anyway?" n && DO_RESTART=1
    else
        warn "could not ask the node how far it is."
        ask "Restart bitcoind now?" n && DO_RESTART=1
    fi
    if (( DO_RESTART == 1 )); then
        if systemctl restart bitcoind 2>/dev/null; then
            ok "bitcoind restarted"
        else
            warn "systemctl could not restart bitcoind (no unit of that name?)."
            info "Restart it the way you started it — the account is in bitcoin.conf."
        fi
    else
        info "Later:  sudo systemctl restart bitcoind"
    fi
fi

# --- final check: is the page there, does the node answer? ----------------
# Up to a minute, because bitcoind takes a moment to come back after a
# restart and the generator asks it every 30 s.
if [[ -n "$WEBSERVER" ]]; then
    PAGE_OK=0; NODE_OK=0
    for _ in $(seq 1 20); do
        # python3 rather than curl: curl is not on every minimal image,
        # python3 is (the generator needs it).
        BODY="$(python3 -c "import urllib.request,sys; print(urllib.request.urlopen(sys.argv[1], timeout=3).read().decode('utf-8','replace'))" "http://127.0.0.1:${PORT}/" 2>/dev/null || true)"
        if [[ "$BODY" == *"<title>"* ]]; then
            PAGE_OK=1
            # The state bar carries data-stufe: 'fehler' while the node is
            # not reachable, anything else once it answers.
            if [[ "$BODY" != *'data-stufe="fehler"'* ]]; then NODE_OK=1; break; fi
        fi
        sleep 3
    done
    if (( PAGE_OK == 1 && NODE_OK == 1 )); then
        ok "page answers and the node is reachable"
    elif (( PAGE_OK == 1 && RESTART_NEEDED == 1 && DO_RESTART == 1 )); then
        ok "page answers; the node is still coming back after the restart"
        info "Normal — it verifies its last blocks first, longer while syncing."
        info "The page picks it up by itself, nothing to do."
    elif (( PAGE_OK == 1 )); then
        warn "page answers, but the node does not — the restart is still due:"
        info "sudo systemctl restart bitcoind"
    else
        warn "page not reachable on 127.0.0.1:${PORT} — check: systemctl status nginx"
    fi
fi

echo
printf '  %sIn the browser:   %s%s\n' "$C_BOLD" "$URL" "$C_OFF"
echo
echo "  Commands:"
echo "    systemctl status node-dashboard              the generator"
echo "    journalctl -u node-dashboard -f              follow along"
echo "    node-dashboard --once                        rebuild the page by hand"
echo "    sudo bash install.sh --uninstall             remove everything again"
echo
echo "  To change the language later, edit LANGUAGE in ${CONF}"
echo "  and run: systemctl restart node-dashboard"
