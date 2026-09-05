#!/usr/bin/env bash
#
# tests/install-test.sh — runs the firewall part of install.sh against a
# stand-in ufw. No root, no Pi, no real firewall.
#
# Why this exists (2026-09-06): the ~220 checks in probelauf.py never
# execute a line of install.sh. The firewall block deleted rules by port
# with an unanchored grep — "80/tcp" also matched 8080/tcp — and nothing
# noticed until someone outside ran it against a fake. This is that fake,
# kept.
#
# Usage:  bash tests/install-test.sh

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

FAILURES=0
check() {
    if [[ "$1" == "0" ]]; then printf '  [ ok ] %s\n' "$2"
    else printf '  [FAIL] %s\n' "$2"; [[ -n "${3:-}" ]] && printf '         %s\n' "$3"; FAILURES=$((FAILURES+1)); fi
}

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
RULES="$TMP/rules"; mkdir -p "$TMP/bin"

# A ufw that speaks just enough: "status numbered" prints the file with
# [ n] prefixes and the "Status: active" header, "--force delete N" drops
# line N,
# "allow … comment X" appends a line the way ufw prints it.
cat > "$TMP/bin/ufw" <<'EOF'
#!/usr/bin/env bash
S="$UFW_FAKE_RULES"
[[ "$1" == --force ]] && shift
case "$1" in
    status) echo "Status: active"; echo; n=0
            while IFS= read -r l; do n=$((n+1)); printf '[%2d] %s\n' "$n" "$l"; done < "$S" ;;
    delete) sed -i "${2}d" "$S" ;;
    allow)  port="" from="" cmt=""
            while [[ $# -gt 0 ]]; do case "$1" in
                from) from="$2"; shift 2 ;; port) port="$2"; shift 2 ;;
                comment) cmt="$2"; shift 2 ;; *) shift ;; esac; done
            printf '%-26s ALLOW IN    %-22s # %s\n' "${port}/tcp" "$from" "$cmt" >> "$S" ;;
esac
EOF
chmod +x "$TMP/bin/ufw"
export PATH="$TMP/bin:$PATH" UFW_FAKE_RULES="$RULES"

# The function under test, taken from install.sh itself so the test cannot
# drift away from the script.
eval "$(sed -n "/^UFW_MARK=/p; /^ufw_delete_marked() {/,/^}/p" install.sh)"
declare -f ufw_delete_marked >/dev/null || { echo "ufw_delete_marked not found in install.sh"; exit 1; }

seed() {
    cat > "$RULES" <<'EOF'
22/tcp                     ALLOW IN    Anywhere
8080/tcp                   ALLOW IN    192.168.1.0/24         # my own thing
8333/tcp                   ALLOW IN    Anywhere               # Bitcoin P2P
50001/tcp                  ALLOW IN    192.168.1.0/24         # Electrum LAN
80/tcp                     ALLOW IN    10.0.0.0/8             # node dashboard LAN
80/tcp (v6)                ALLOW IN    fe80::/10 (v6)         # node dashboard LAN
80/tcp                     ALLOW IN    Anywhere
EOF
}
has() { grep -qE "^$1" "$RULES"; }

echo
echo "== install.sh firewall against a stand-in ufw =="

seed; ufw_delete_marked
check "$(! has '80/tcp .*node dashboard LAN'; echo $?)"  "own rules (v4 and v6) are deleted"
check "$(has '8080/tcp'; echo $?)"                       "8080/tcp survives — the bug of 2026-09-06"
check "$(has '80/tcp .*Anywhere'; echo $?)"              "a user's own 80/tcp without our comment survives"
check "$(has '8333/tcp'; echo $?)"                       "8333/tcp survives"
check "$(has '50001/tcp'; echo $?)"                      "50001/tcp survives"
check "$(has '22/tcp'; echo $?)"                         "22/tcp survives"

seed; PORT=8080 SUBNET=192.168.1.0/24
ufw_delete_marked
ufw allow from "$SUBNET" to any port "$PORT" proto tcp comment "$UFW_MARK" >/dev/null
check "$(has '8080/tcp .*my own thing'; echo $?)"        "--port 8080 does not eat the user's 8080 rule"
check "$(has '8080/tcp .*node dashboard LAN'; echo $?)"  "new rule written with the mark"
n1=$(wc -l < "$RULES")
ufw_delete_marked
ufw allow from "$SUBNET" to any port "$PORT" proto tcp comment "$UFW_MARK" >/dev/null
check "$(( $(wc -l < "$RULES") != n1 ))"                 "second run leaves the same number of rules"

: > "$RULES"; ufw_delete_marked
check "$?"                                               "empty rule set: no error, no loop"

echo
if [[ $FAILURES -eq 0 ]]; then echo "=== all checks passed ==="; else echo "=== $FAILURES failed ==="; exit 1; fi
