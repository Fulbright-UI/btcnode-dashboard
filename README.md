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
  those probes is shown as a row of dots. A single stranger claiming more
  blocks is a red dot and a calm sentence (such claims are unproven);
  two recent ones raise a notice at the top of the page
- **A timeline in the header**, typed like someone typing into a
  terminal, one entry per data cycle and in date order: from the Bank of
  England, Jekyll Island and the Federal Reserve Act through Bretton
  Woods, 1971 and the cypherpunks to Satoshi's own words (checked against
  the Nakamoto Institute), the genesis block, Mt. Gox, Silk Road, the
  halvings and the ETF. Each restart of the service begins at the top;
  before a new entry the old one is deleted backwards
- **System state** of the machine: the day's temperature as one bar per
  hour, CPU, memory, disk space — on a Raspberry Pi also the power supply,
  because undervoltage is the most common cause of corrupted blockchain data
- **The fee to use**: Core's economical estimate for the next block, large
  at the top, the conservative one small underneath when it differs
- **Days to the halving**, with the date and the blocks still to go
- **Chain, mempool and Electrum** in one card: difficulty, next
  adjustment, mempool memory, fees waiting and fill level in the left
  column; in the right one your Electrum server — found by its port, so
  electrs, Fulcrum and ElectrumX all count — with a bar showing how far
  its index has got, and underneath the addresses for your wallet with a
  copy button. Without a server the column stays and says so, and why
  it matters
- **Volume and fee history** of the last 24 hours, one bar per block, in
  three steps — grey, green, block orange — fees by sat/vB, volume
  against the day's mean; the label names the peak, every bar on the
  page tells its value when pointed at
- **Hashrate since 2009** as a curve behind the state bar, linear, with
  the change against a year ago
- **Log** of the node, live — accepted blocks tinted orange, errors and
  warnings red and yellow, everything else plain. The lines stay plain
  text; only the colour comes from a pattern table

Without data the cards are still there — with a muted skeleton and dashes
instead of numbers. **Never invented values:** on a display that reports the
state of a node, nobody could tell on the next screenshot what was measured
and what was drawn.

---

## Requirements

- A **running Bitcoin Core**, version 26 or newer. How it was set up does not
  matter
- **Python 3.9** or newer (present on Raspberry Pi OS and Debian anyway)
- **nginx**, or any other web server for static files — the installer
  offers to install nginx if it is missing
- Write access to `bitcoin.conf`
- An **Electrum server** is optional. The page shows the gap if there is
  none — see [Electrum server](#electrum-server) below

Tested on Raspberry Pi OS Lite 64-bit (Debian 13) with Bitcoin Core 31.1.

---

## Setting it up

```bash
git clone https://github.com/Fulbright-UI/btcnode-dashboard.git
cd btcnode-dashboard
sudo bash install.sh
```

The script asks **three questions at most** — the language of the page,
whether to install nginx if it is missing, and whether to restart bitcoind at
the end — and does everything else by itself: it finds the data directory,
creates a **read-only** RPC account, sets the generator up as a service,
serves the page and limits the firewall to your local network. It ends with
the address to type into a browser, and it checks that the page answers.

Every question has a default (shown in capitals); Enter takes it. Piped into
a shell, or with `--yes`, the defaults apply throughout.

The restart is the one step it does not take on its own: bitcoind learns
about the new account only after a restart, and a restart during an initial
sync costs the warm cache. The script asks the node how far it is and
proposes accordingly — "yes" when the chain is up to date, "no" while it is
still syncing.

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
| `--electrum-port N` | port of your Electrum server, default 50001 |
| `--restart` | restart bitcoind at the end, without asking |
| `--yes` | answer every question with its default |
| `--uninstall` | remove service, program, page and user again |

Everything is repeatable: running it twice breaks nothing, and an existing RPC
password stays unchanged.

---

## Electrum server

A wallet does not talk to Bitcoin Core directly; it talks to an Electrum
server, which indexes the chain by address. Without one of your own, the
wallet asks somebody else's server — and that server learns which addresses
belong to you. For most people that is the reason to run a node at all.

The dashboard does not install one; that is a build of its own (electrs is
the usual choice on a Pi — from source, dynamically against the system
RocksDB, then hours of indexing). What the dashboard does:

- it looks for a server on the Electrum port (`50001` by default,
  `--electrum-port` or `ELECTRS_PORT` for another) — electrs, Fulcrum and
  ElectrumX all answer there;
- it shows whether the server runs and answers, how far its index has got,
  and the two addresses to enter in the wallet — local network and, if a
  Tor hidden service exists for it, the onion address — with a copy button;
- without a server, the column says so and why it matters, in one muted
  sentence. No alarm: the node itself is fine.

The index figure comes from electrs' own metrics endpoint while it is still
indexing, and from the Electrum protocol (`blockchain.headers.subscribe`)
once it serves — the same question every wallet asks first. Nothing here
leaves the machine.

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

### Why not Docker

There is no container image, and none is planned. Not out of dislike —
the isolation this project relies on comes from systemd, and a container
would make it weaker, not stronger:

- The unit confines the generator with `ProtectSystem=strict`, one
  writable path, `IPAddressDeny=any` and a capability set of nothing. A
  container gives a whole userland with a shell, a package manager and a
  network namespace that has to be opened towards the node anyway.
- The generator needs three things from the host: the RPC port on
  `127.0.0.1`, the journal of `bitcoind` for the log, and `hwmon` for the
  Pi's supply voltage. Each of those is a hole to punch into a container;
  on the host they are membership of `systemd-journal` and one `ReadWritePaths`.
- What Docker would add — reproducible installs on any distribution — the
  project does not need: one Python file from the standard library and
  one shell script, nothing to build.

If you want a container anyway, build it yourself; it is not hard. The
container needs Python 3.9, the one file, `--network host` (or the RPC
port forwarded), the `bitcoind` journal mounted read-only, and the
`OUT_DIR` shared with whatever web server serves it. Keep the RPC user
read-only in `bitcoin.conf` exactly as `install.sh` sets it up — that
part is the same in every deployment and is what actually protects the
node.

---

## JavaScript

The page updates itself without reloading. That changes nothing about the
principle: **the interface is a static file.** The generator writes
`status.json` just as it writes the HTML; the script in the browser fetches it
and fills the values in. There is no endpoint that accepts anything.

Everything keeps working without JavaScript — the page then reloads itself via
a `<meta http-equiv=refresh>` inside `<noscript>`. Inside `<noscript>` on
purpose: a refresh outside it is scheduled by the browser at parse time, and
a script removing the element afterwards cancels nothing — the page kept
reloading under the script every cycle (3.4).

Generated files:

| File | Interval | Content |
|---|---|---|
| `index.html` | 30 s | the complete page |
| `status.json` | 30 s | the same building blocks plus peers as pure structure |
| `log.txt` | 5 s | journal lines, plain text, without any markup |
| `chronik.json` | once | the timeline for the header, with the generator's start time |
| `stil.css`, `dash.js`, `bitcoin.png` | once | change only when the program is replaced |

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
| `ELECTRS_PORT` | 50001 | port of the Electrum server; the page looks there |
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

```bash
bash tests/install-test.sh                  # the firewall part of install.sh
```

runs `install.sh`'s firewall function against a stand-in `ufw` on `$PATH` —
no root, no real firewall. It exists because nothing in `probelauf.py`
executes a line of the installer, and the first bug found there deleted
rules (2026-09-06).

```bash
cd tests && npm install --no-save jsdom && cd ..
node tests/dash-test.js                     # dash.js against the generated page
```

runs the browser script under jsdom against the page, `status.json`,
`log.txt` and `chronik.json` the generator has just written — the detail
box, the peer map, the copy buttons, the log colouring, a failed fetch,
and the chronicle's typing loop with a frozen clock. `probelauf.py` runs
it by itself when `node` and jsdom are there and says so when they are not.

About 220 checks run in the process, in both languages: well-formedness of the
HTML, no buttons that reach the node, no inline styles, the correct decimal
separator, escaping of foreign values, the tolerance window in both
directions, the geometry of the network map, the path of the last block and
the chain check — and several that came out of real breakage: every visible
string must have a translation, every CSS class used in the markup must exist
in the style sheet, every field the browser script reads must exist in
`status.json` and every `data-` attribute it reads must be on the page,
the meta refresh must sit inside `<noscript>`, and the power supply row must
appear even though the mock, like the sandboxed service, cannot call
`vcgencmd`.

---

## Language of the source

Code, comments and identifiers are English; the user interface speaks both.
The comments record **why** something is built the way it is, usually with the
date and the measurement that made the case. They are the most valuable part
of this repository — please keep them that way when you change something.

---

## Licence

MIT — see [LICENSE](LICENSE).
