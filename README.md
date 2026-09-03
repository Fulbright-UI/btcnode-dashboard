# btcnode-dashboard

*[Deutsche Fassung](README.de.md)*

A status page for your own Bitcoin full node. Runs on a Raspberry Pi next to
Bitcoin Core and shows in a browser what the node is doing.

**The point of it: the page cannot do anything.** A Python program queries the
node and writes finished files into a folder. The web server only hands out
those files — it does not know the node, accepts no input and executes
nothing. Whoever takes over the web server gets files.

No framework, no third-party packages, no Docker. One Python program from the
standard library and one installation script.

**It installs neither Bitcoin Core nor an Electrum server.** That is
deliberate: a script that occupies 750 GB and builds from source for hours is
a different animal from a status page — and nobody should run it unread. If a
node already runs on your machine, the dashboard is set up in two minutes.

The page speaks **English or German**. The installer asks once; you can change
it later in one line of the configuration file.

---

## What it shows

- **Sync progress** with rate and estimated time remaining
- **Connected nodes** as a fan: your own node in the middle, the peers to the
  left and right, with address, network type, latency and data volume on the
  line. Pointing at a row adds identifier, services and connection time.
  The peer that announced the most recent block first gets an orange spoke,
  with a 24-hour ranking of who announces first most often. Your own
  Electrum server, which connects over P2P like any peer, gets its own
  colour
- **Chain check**: every few minutes Bitcoin Core asks a random node for its
  height — its defence against being fed a false chain. The last hour of
  those probes is shown as a row of dots; a node that reports more blocks
  than you have raises a notice at the top of the page
- **A line from the early days** in the header, typed like someone
  typing into a terminal — the cryptography mailing list, the P2P
  Foundation thread, bitcointalk — one quote per data cycle, wording
  checked against the Nakamoto Institute
- **System state** of the machine: the day's temperature as one bar per
  hour, CPU, memory, disk space — on a Raspberry Pi also the power supply,
  because undervoltage is the most common cause of corrupted blockchain data
- **The fee to use**: Core's economical estimate for the next block, large
  at the top, the conservative one small underneath when it differs
- **Days to the halving**, with the date and the blocks still to go
- **Mempool** (memory, fees waiting, fill level) and **chain** (difficulty,
  next adjustment), with an estimate of the electricity the difficulty
  stands for — from the hashrate at an assumed fleet efficiency, next to
  air conditioning, data centres, banking and gold mining as bars
- **Volume and fee history** of the last 24 hours, one bar per block, fees
  tinted by tier; every bar on the page tells its value when pointed at
- **Electrum server**, if one runs, with the addresses for your wallet, a
  copy button next to them and a bar showing how far its index has got
- **Log** of the node, live — accepted blocks in orange, their
  announcements muted, chain-check probes green, errors and warnings red
  and yellow. The lines stay plain text; only the colour comes from a
  pattern table

Without data the cards are still there — with a muted skeleton and dashes
instead of numbers. **Never invented values:** on a display that reports the
state of a node, nobody could tell on the next screenshot what was measured
and what was drawn.

---

## Requirements

- A **running Bitcoin Core**, version 26 or newer. How it was set up does not
  matter
- **Python 3.9** or newer (present on Raspberry Pi OS and Debian anyway)
- **nginx**, or any other web server for static files
- Write access to `bitcoin.conf`

Tested on Raspberry Pi OS Lite 64-bit (Debian 13) with Bitcoin Core 31.1.

---

## Setting it up

```bash
git clone https://github.com/Fulbright-UI/btcnode-dashboard.git
cd btcnode-dashboard
sudo bash install.sh
```

The script asks **one question** — the language of the page — and then runs
through without further prompts: it finds the data directory itself, creates a
**read-only** RPC account, sets the generator up as a service and limits the
firewall to your local network.

Piped into a shell, where nobody could answer, it assumes English. To skip the
question entirely:

```bash
sudo bash install.sh --language de
```

One step it deliberately does not take by itself — **restarting bitcoind**.
Without a restart the node does not know about the new account, and until then
the dashboard shows "node not reachable". A restart during an initial sync
costs the warm cache, and that is the operator's decision:

```bash
sudo systemctl restart bitcoind
```

Add `--restart` if you want that done for you.

When detection fails:

```bash
sudo bash install.sh --datadir /mnt/bitcoin/bitcoin --subnet 192.168.1.0/24
```

| Option | Meaning |
|---|---|
| `--language de\|en` | language of the page; skips the question |
| `--datadir PATH` | data directory of Bitcoin Core |
| `--port N` | port of the status page, default 80 |
| `--subnet CIDR` | local network for the firewall, e.g. `192.168.1.0/24` |
| `--restart` | restart bitcoind at the end |
| `--uninstall` | remove service, program, page and user again |

Everything is repeatable: running it twice breaks nothing, and an existing RPC
password stays unchanged.

### With prebuilt kits

Umbrel, Start9 and MyNode manage `bitcoin.conf` themselves and overwrite it on
restart. There these lines belong at the place the kit provides for your own
additions:

```
rpcauth=dashboard:<salt>$<digest>
rpcwhitelist=dashboard:getblockchaininfo,getnetworkinfo,getmempoolinfo,getconnectioncount,uptime,estimatesmartfee,getblockstats,getblockhash,getblockheader,getpeerinfo,getnetworkhashps
rpcwhitelistdefault=0
```

Generate the `rpcauth` line like this — then put the password into
`/etc/node-dashboard.conf`:

```bash
python3 - <<'PY'
import hashlib, hmac, os, secrets
password = secrets.token_urlsafe(32)
salt = os.urandom(16).hex()
digest = hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()
print(f"rpcauth=dashboard:{salt}${digest}")
print(f"Password: {password}")
PY
```

---

## How safe is this

This is the actual design question, so it gets room.

**The node is separated from the dashboard.** There is no route from the web
page back to the node. The generator writes files, the web server reads them.
Nothing else happens.

**The RPC account may only read.** `bitcoin.conf` carries
`rpcwhitelistdefault=0` and an explicit list of eleven methods. Even if the
password leaked, nothing could be done with it: no wallet, no sending, no
configuration, no shutdown. Other RPC users are unaffected.

**The service runs as its own system user** without login rights, confined by
systemd (`ProtectSystem=strict`), with exactly one writable path.

**Foreign text never becomes markup.** Log lines, peer addresses and
identifiers of other nodes are chosen by the peer, not by this program. They
are escaped server-side and set in the browser exclusively via `textContent`,
where markup cannot arise by construction.

**The Content Security Policy works without `unsafe-inline`.** That is exactly
why style and script live in their own files and not in the HTML.

**The generator never goes online.** It talks to `127.0.0.1` and nothing
else — and the service unit says so to the kernel (`IPAddressDeny=any`,
`IPAddressAllow=localhost`), so the promise holds even against a bug in the
program.

**No button reaches the node.** A restart button would need an endpoint that
accepts input and a path of privilege up to systemd. That turns "at worst
somebody reads a file" into "at worst somebody stops the node". The only
buttons on the page copy an address to the browser clipboard — nothing more.

**The firewall limits access to the local network**, if `ufw` is present. The
page has no login — do not put it on the internet.

---

## JavaScript

The page updates itself without reloading. That changes nothing about the
principle: **the interface is a static file.** The generator writes
`status.json` just as it writes the HTML; the script in the browser fetches it
and fills the values in. There is no endpoint that accepts anything.

Everything keeps working without JavaScript — the page then reloads itself via
a `<meta http-equiv=refresh>`.

Generated files:

| File | Interval | Content |
|---|---|---|
| `index.html` | 30 s | the complete page |
| `chronik.json` | once | the quotes for the header line |
| `status.json` | 30 s | the same building blocks plus peers as pure structure |
| `log.txt` | 5 s | journal lines, plain text, without any markup |
| `stil.css`, `dash.js` | once | change only when the program is replaced |

`dash.js` carries the labels of the configured language and is therefore
written per installation. Its fingerprint is built over the finished text, so
a language change shows up as a new file rather than an old one from the
browser cache.

---

## Settings

In `/etc/node-dashboard.conf`, then `sudo systemctl restart node-dashboard`:

| Key | Default | Meaning |
|---|---|---|
| `LANGUAGE` | `en` | page language: `de` or `en` |
| `INTERVAL` | 30 | interval of the node query, in seconds |
| `LOG_INTERVAL` | 5 | interval of the log panel |
| `LOG_SERVICES` | `bitcoind` | sources, comma separated. Empty switches it off |
| `LOG_LINES` | 150 | scrollback in the log. It fills the column and scrolls inside |
| `RPC_TIMEOUT` | 45 | timeout per call, in seconds |
| `TOLERANCE` | 3 | unsuccessful calls before the alarm is raised |
| `PEERS_MAX` | 64 | maximum number of dots in the network map |
| `ELECTRS_PORT` | 50001 | port of the Electrum server, if one runs |
| `ELECTRS_METRICS` | 127.0.0.1:4224 | Prometheus endpoint of electrs, read for the index bar while it is not serving yet |

The language affects more than words: German writes `1.234.567,8`, English
`1,234,567.8` — period and comma swap roles, both of them. Every number on the
page goes through one formatter for that reason, and the test run checks both
notations.

### Why there is a tolerance window

During the initial sync Bitcoin Core stalls its query interface while it
writes its cache to disk. On slow hardware that regularly takes longer than
the timeout of a single call.

A dashboard that reports "node not reachable" and zeroes every figure in
response is worse than none — it reports an outage that is not happening. So
this one holds the last measured state and quietly says how old it is. The red
card appears only after `TOLERANCE` calls in a row have failed.

---

## Developing

There is a complete test run that needs neither a node nor a Raspberry Pi:

```bash
python3 tests/probelauf.py                  # chain up to date, all cards
python3 tests/probelauf.py --case sync      # initial sync
python3 tests/probelauf.py --case leer      # node answers, delivers nothing
python3 tests/probelauf.py --language en    # the English page
```

`tests/attrappe.py` is a real HTTP server with basic auth and JSON-RPC — not a
replacement for the query layer, so that the test runs the same code that will
run later, error handling included. It answers unknown methods with HTTP 403,
exactly as a missing whitelist entry would.

The generated page then sits in `tests/ausgabe/index.html` and can be opened
in a browser.

About 170 checks run in the process, in both languages: well-formedness of the
HTML, no buttons that reach the node, no inline styles, the correct decimal
separator, escaping of foreign values, the tolerance window in both
directions, the geometry of the network map, the path of the last block and
the chain check — and several that came out of real breakage: every visible
string must have a translation, every CSS class used in the markup must exist
in the style sheet, every field the browser script reads must exist in
`status.json`, and the power supply row must appear even though the mock,
like the sandboxed service, cannot call `vcgencmd`.

---

## Language of the source

Code, comments and identifiers are English; the user interface speaks both.
The comments record **why** something is built the way it is, usually with the
date and the measurement that made the case. They are the most valuable part
of this repository — please keep them that way when you change something.

---

## Licence

MIT — see [LICENSE](LICENSE).
