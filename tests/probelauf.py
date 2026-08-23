#!/usr/bin/env python3
"""Test run of the dashboard without a Raspberry Pi.

Starts the mock, lets node-dashboard.py produce a real page and checks it. The
result lands in tests/ausgabe/ and can be opened in a browser — that way a
design change is visible before it goes to the Pi.

Checked here:
  * the HTML is well formed, the script only as its own file, no inline
    handlers
  * every expected card is present and sits in the right zone
  * the network map holds one dot per peer
  * foreign text (the identifier of another node) never lands as markup
  * status.json is valid and agrees with the page
  * the tolerance window keeps the last state instead of raising the alarm
  * copy fields do not overflow
  * numbers use the decimal separator of the configured language throughout
  * the 24 hour data is fetched once, not on every cycle

Usage:
    python3 tests/probelauf.py                        # case 'synchron'
    python3 tests/probelauf.py --case sync            # initial sync
    python3 tests/probelauf.py --case leer
    python3 tests/probelauf.py --language en          # the English page
"""

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
OUTPUT = HERE / "ausgabe"
PORT = 18332

# The mock sends this identifier as the 'subver' of a foreign node. If it
# turns up unescaped anywhere on the page, a foreign node can write markup
# into the dashboard.
POISON = "<b>Knoten</b>"

failures = []


def check(passed, text, detail=""):
    mark = "ok  " if passed else "FEHL"
    print(f"  [{mark}] {text}{('  — ' + detail) if detail else ''}")
    if not passed:
        failures.append(text)


def load_dashboard():
    spec = importlib.util.spec_from_file_location(
        "node_dashboard", PROJECT / "node-dashboard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replace_system_parts(nd, case):
    """Replace everything that exists only on the Pi with fixed values.

    Deliberately kept narrow: only files under /sys, /proc and systemd calls.
    The RPC layer stays untouched and really talks over HTTP.
    """
    real_read = nd.read_file
    # An invented address of the right length (56 characters plus .onion).
    # The real address of this node once stood here — in a file destined for
    # a repository. An onion address is no secret in the cryptographic sense,
    # but it is the doorway to a service that should be open to nobody else.
    onion = ("beispielbeispielbeispielbeispiel"
             "beispielbeispielbeispiel.onion")

    def fake_read(path, default=None):
        path = str(path)
        if "thermal" in path:
            return "69634"
        if "onion" in path or "hostname" in path:
            return onion
        return real_read(path, default)

    nd.read_file = fake_read
    nd.service_running = lambda name: True
    nd.port_open = lambda host, port: True
    nd.own_ip = lambda: "192.168.1.50"

    real_exists = os.path.exists
    nd.os.path.exists = lambda p: True if ".service" in str(p) else real_exists(p)

    # A real log, not a single line. The mock used to answer "throttled=0x0"
    # to every call — including journalctl. That made the log 18 pixels tall
    # in the test and 2673 on the Pi, and a layout fault that only shows with
    # a full log stayed invisible.
    pattern = ("2026-08-23T14:04:16+02:00 btcnode bitcoind[62345]: "
              "2026-08-23T12:04:16Z UpdateTip: new best="
              "00000000000000000118e7c0614044d2846a57fc347fb2ae684415e8fdefb293 "
              "height={h} version=0x20000000 log2_work=85.493329 tx=167769911 "
              "date='2016-11-03T12:23:13Z' progress=0.112807 "
              "cache=204.1MiB(1483920txo)")
    log = "\n".join(pattern.format(h=437184 - i) for i in range(150))

    def run(command, *a, **k):
        class Result:
            returncode = 0
            stderr = ""
            stdout = (log if "journalctl" in " ".join(map(str, command))
                      else "throttled=0x0")
        return Result()

    nd.subprocess.run = run

    # One hour of temperature history so the curve has something to draw
    now = time.time()
    for i in range(90):
        nd.TEMP_HISTORY.append(
            (now - (90 - i) * nd.TEMP_STEP, 56 + 13 * (i / 89) + 1.6 * math.sin(i / 3))
        )


def write_config(language="de"):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = HERE / "probe.conf"
    path.write_text(
        f"RPC_HOST=127.0.0.1\nRPC_PORT={PORT}\nRPC_USER=dashboard\n"
        f"RPC_PASSWORD=probe\nOUT_DIR={OUTPUT}\nDATA_DIR={PROJECT}\n"
        "ELECTRS_PORT=50001\nINTERVAL=30\nLOG_SERVICES=bitcoind\n"
        "LOG_LINES=40\nLOG_INTERVAL=5\nTOLERANCE=3\nPEERS_MAX=64\n"
        f"LANGUAGE={language}\n"
        f"UPDATE_FILE={HERE / 'updates-probe.json'}\n",
        encoding="utf-8")
    (HERE / "updates-probe.json").write_text(
        '{"geprueft": %d, "eintraege": ['
        '{"name": "Bitcoin Core", "installiert": "31.1", "neueste": "31.1"},'
        '{"name": "electrs", "installiert": "0.11.1", "neueste": "0.11.1"}]}'
        % int(time.time() - 1260), encoding="utf-8")
    return str(path)


# What has to appear on the finished page, per language. This table is filled
# in by hand on purpose and does NOT read the DE table from the program:
# otherwise the test would check the translation against itself and a swapped
# entry would never show up.
EXPECTED = {
    "de": {
        "raster": ["System", "Netzwerk-Eckdaten", "Mempool &amp; Gebühren"],
        "netz": "Verbundene Knoten",
        "voll": "Electrum-Server",
        "protokoll": "Protokoll",
        "laufzeit": "läuft seit",
        "weit": ("Volumen · 24 Stunden", "Gebührenverlauf · 24 Stunden"),
        "warten": "Erscheint, sobald die Kette steht",
        "verbindungen": "Verbindungen",
        "abgefragt": "werden abgefragt",
        "aufgeloest": ("Blockchain", "Netzwerk", "Aktualisierungen"),
        "decimal_sep": ",",
    },
    "en": {
        "raster": ["System", "Network facts", "Mempool &amp; fees"],
        "netz": "Connected nodes",
        "voll": "Electrum server",
        "protokoll": "Log",
        "laufzeit": "up for",
        "weit": ("Volume · 24 hours", "Fee history · 24 hours"),
        "warten": "Appears once the chain is up to date",
        "verbindungen": "Connections",
        "abgefragt": "querying peers",
        "aufgeloest": ("Blockchain", "Network", "Updates"),
        "decimal_sep": ".",
    },
}


# --------------------------------------------------------------------- Checks
class FormChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.faults = [], []

    def handle_startendtag(self, tag, attrs):
        """<circle .../> and its relatives close themselves.

        Without this override HTMLParser calls start and end in turn, and the
        SVG of the network map is wrongly reported as broken.
        """
        return

    def handle_starttag(self, tag, attrs):
        if tag not in ("meta", "br", "hr", "img", "link", "input"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.faults.append(f"</{tag}> without a matching start")


def zones_of(page):
    """Map the card titles to the zones they sit in."""
    zones = {}
    spots = [(m.start(), m.group(1))
               for m in re.finditer(
                   r'id=z-(kopf|zustand|stoerung|band|netz|raster|weit|voll)',
                   page)]
    spots.append((len(page), "ende"))
    for i in range(len(spots) - 1):
        start, name = spots[i]
        chunk = page[start:spots[i + 1][0]]
        zones[name] = re.findall(r"<h2>(.*?)</h2>", chunk)
    zones["protokoll"] = re.findall(
        r'class="karte protokoll">.*?<h2>(.*?)</h2>', page, re.S)
    return zones


def check_translation(nd):
    """Every visible string must have a German entry.

    The reason is concrete: while moving the source to English, placeholders
    were renamed ({dauer} -> {duration}) but the DE table was not. In such a
    case t() falls back to the English text without a word — the page stays
    intact and is suddenly English in one spot. No other test would find it.
    """
    print("\n  Translation completeness")
    source = (PROJECT / "node-dashboard.py").read_text(encoding="utf-8")
    a = source.index("DE = {")
    b = source.index("\n}\n", a)
    table, leftovers = source[a:b], source[:a] + source[b:]

    # Real t() calls only, not .get(" or dict(" — hence the boundary before
    # the t.
    calls = set(re.findall(r'(?<![\w.])t\(\s*("(?:[^"\\]|\\.)*")', leftovers))
    missing = sorted(a[1:-1] for a in calls if a[1:-1] not in table)
    check(not missing,
          f"all {len(calls)} single-line t() strings are in DE",
          " | ".join(f[:40] for f in missing[:4]))

    # And the other direction: placeholders must be named the same on both
    # sides, otherwise str.format raises a KeyError in mid-operation.
    askew = []
    for tight, de in nd.DE.items():
        if set(re.findall(r"\{(\w+)\}", tight)) != set(re.findall(r"\{(\w+)\}", de)):
            askew.append(tight[:40])
    check(not askew, f"placeholders agree in all {len(nd.DE)} entries",
          " | ".join(askew[:4]))


def check_classes(page, nd, case="synchron"):
    """Every CSS class used in the markup must exist in the style sheet.

    On 2026-08-23 a mechanical rename turned 'punkt' into 'point' — in the
    markup, not in the style sheet. The page stayed well formed but the
    colour dots were invisible. A test that only looks at the markup never
    finds something like that.
    """
    print("\n  Classes and style")
    known = set(re.findall(r"\.([a-zA-Z][\w-]*)", nd.STYLE))
    # 'neutral' deliberately carries no rule of its own: .netzfarbe has a
    # base colour and 'neutral' means exactly "take that one". Anyone adding
    # to this list must be sure the class really is meant to have no effect —
    # otherwise a broken rename stops being noticed.
    known |= {"neutral"}
    # Classes that only dash.js sets do not appear in the generated markup —
    # the other way round there are none the style should not know.
    used = set()
    for m in re.finditer(r'class="([^"]*)"|class=([\w-]+)', page):
        for w in (m.group(1) or m.group(2) or "").split():
            if re.fullmatch(r"[a-zA-Z][\w-]*", w):
                used.add(w)
    unknown = sorted(used - known)
    check(not unknown,
          f"all {len(used)} used classes are defined in the style",
          " | ".join(unknown[:6]))

    # The same for the ids where dash.js updates the page.
    idents = ["z-kopf", "z-zustand", "z-band", "z-raster", "z-netz",
                 "logtext", "stempel"]
    # The detail box exists only when there are peers — without them the
    # fallback list stands there, and rightly so.
    if case != "leer":
        idents.append("peerdetail")
    for ident in idents:
        check(f"id={ident}" in page, f"id '{ident}' present in the markup")


def check_page(page, case, nd=None, language="de"):
    E = EXPECTED[language]
    print("\n  Structure")
    checker = FormChecker()
    checker.feed(page)
    check(not checker.faults and not checker.stack, "HTML is well formed",
          ", ".join(checker.faults + [f"<{t}> offen" for t in checker.stack]))

    scripts = re.findall(r"<script([^>]*)>", page)
    check(len(scripts) == 1 and re.fullmatch(r' src="dash\.js\?v=[0-9a-f]{8}"',
                                             scripts[0]),
          "script included only as its own file", str(scripts))

    # Fingerprint on the URL: without it the browser serves old rules to new
    # markup for up to ten minutes after a program swap. On the Pi that turned
    # into green blocks inside the cards on 2026-08-23.
    check(re.search(r'href="stil\.css\?v=[0-9a-f]{8}"', page) is not None,
          "style carries a fingerprint against the cache")
    check("onclick" not in page and "onmouse" not in page
          and "javascript:" not in page,
          "no event handlers in the markup")
    check("Content-Security-Policy" in page and "'unsafe-inline'" not in page,
          "strict Content Security Policy without unsafe-inline")

    # The CSP drops style attributes in the markup, not only <style> blocks.
    # That is why the progress bar always showed full on the Pi on
    # 2026-08-23: its width was an inline style. Geometry belongs in SVG
    # attributes, colour in a class.
    inline = re.findall(r'style="[^"]*"', page)
    check(not inline,
          "no style attribute in the markup (the CSP would drop it)",
          " | ".join(sorted(set(inline))[:4]))
    check("<style" not in page, "no style block in the page")

    zones = zones_of(page)
    print("\n  Zones")
    for name, cards in zones.items():
        print(f"        {name:<10} {', '.join(cards) or '(leer)'}")

    # Order, not just presence: it is chosen deliberately and used to fall out
    # of the order in which the collect_* functions happen to be called.
    expected_grid = E["raster"]
    check(zones.get("raster") == expected_grid,
          "cards stand in the defined order",
          " | ".join(zones.get("raster", [])))

    # These three cards were dissolved. Their figures now live in the metrics
    # bar, in 'Connected nodes' and in the page header.
    for gone in E["aufgeloest"]:
        check(gone not in zones.get("raster", []),
              f"card '{gone}' is dissolved")

    check(E["netz"] in " ".join(zones.get("netz", [])),
          "network map comes before the card grid")

    print("\n  Page header")
    head = re.search(r'<div id=z-kopf>(.*?)</div></div>', page, re.S)
    content = head.group(1) if head else ""
    check("Core 31.1" in content, "Bitcoin Core version in the header",
          re.sub(r"<[^>]+>", " ", content).strip())
    check("electrs 0.11.1" in content, "electrs version in the header")
    check(E["laufzeit"] in content, "node uptime in the header")
    check("kopfinfo gut" in content,
          "all current is shown green and without an arrow")
    check(zones.get("protokoll") == [E["protokoll"]], "log card present")

    # Split in two: everything interpreted on the left, the raw log on the
    # right. The header sits above and spans both columns.
    print("\n  Two-column layout")
    check(re.search(r"</header>\s*<div class=inhalt><div class=links>", page)
          is not None,
          "header above both columns, then the split")
    # Split exactly at the column boundary, not at the next best </div> —
    # otherwise the supposedly left part reaches into the right column and
    # every check below is worthless.
    boundary = page.find("<div class=rechts>")
    check(boundary > 0, "the right column is its own block")
    start = page.find("<div class=links>")
    left_side = page[start:boundary] if boundary > start > 0 else ""
    right_side = page[boundary:] if boundary > 0 else ""

    for zone in ("z-zustand", "z-band", "z-weit", "z-raster", "z-voll"):
        check(zone in left_side, f"'{zone}' sits in the left column")
    for zone in ("z-netz", "logtext"):
        check(zone in right_side, f"'{zone}' sits in the right column")
    check("z-netz" not in left_side and "logtext" not in left_side,
          "none of it appears twice in the left column")

    # The log must not dictate the page height. 150 lines are about 2670 px,
    # the left column about 920 — without a cap the page would be three times
    # as long as needed. On 2026-08-23 it was.
    print("\n  Height of the log")
    lines = (OUTPUT / "log.txt").read_text(encoding="utf-8").count("\n") + 1
    natural = lines * 11.5 * 1.55
    check(lines >= 100,
          f"the mock delivers a full log ({lines} lines, "
          f"{natural:.0f} px natural height)")

    tight = (OUTPUT / "stil.css").read_text(encoding="utf-8")
    tight = tight.replace("\n", "").replace(" ", "")
    check("<div class=logbox>" in page,
          "the <pre> sits in a box that sets the height")
    check(".protokollpre{position:absolute;inset:0" in tight,
          "and lies absolutely inside it, so adds no height")
    check(".logbox{flex:1;min-height:0" in tight,
          "the box claims the remaining space of the column")

    style = (OUTPUT / "stil.css").read_text(encoding="utf-8")
    terse = style.replace("\n", "").replace(" ", "")

    # Buttons may touch the clipboard and nothing else. Anything that could
    # reach the node has no place on this page.
    buttons = re.findall(r"<button([^>]*)>", page)
    check(all("kopierknopf" in k for k in buttons),
          f"buttons for copying only ({len(buttons)} of them)",
          " | ".join(k for k in buttons if "kopierknopf" not in k))
    check("<form" not in page and "action=" not in page,
          "no form, no action in the markup")

    check("grid-template-columns:repeat(4,minmax(0,1fr))" in terse,
          "the metrics bar has a fixed four columns")

    # 'auto-fill' creates as many tracks as fit and leaves the surplus ones
    # empty — three cards in a four-track column then leave a quarter free on
    # the right. Exactly what was on screen on 2026-08-23.
    check("repeat(auto-fit,minmax(19rem,1fr))" in terse,
          "the card grid uses auto-fit, not auto-fill")
    check("repeat(auto-fill" not in terse,
          "and auto-fill nowhere any more")

    # The column count must follow the width of the column, not that of the
    # window — the left column is only half as wide.
    check("container-type:inline-size" in terse,
          "the columns are a size context for their cards")
    check(terse.count("@container") >= 2,
          f"grid and 24 hour cards query the column width "
          f"({terse.count('@container')} queries)")
    check("@media(min-width:72rem)and(max-width:95.99rem)" not in terse,
          "no column count measured against the window any more")

    # An empty zone is invisible but still counts as an item in the flex
    # column and creates a second gap — that looked like an uneven margin.
    # Without content it has to disappear entirely.
    check("<div id=z-stoerung></div>" in page or "class=veraltet" in page
          or "fehlerkarte" in page,
          "the trouble zone is either empty or filled, never half")
    check(".links>*:empty,.rechts>*:empty{display:none}" in terse,
          "empty zones create no gap")
    # The fault that made the whole page wider than the window on
    # 2026-08-23: grid items have min-width:auto, and the log's <pre> with
    # 'white-space:pre' takes the minimum width of its longest line. Without
    # this rule it pushes the column out of view.
    check(".links>*,.rechts>*{min-width:0}" in terse,
          "the columns cannot grow wider than their share")
    check(E["voll"] in zones.get("voll", []),
          "Electrum card at full width")

    # The 24 hour cards are always present so the layout is complete.
    # Without data they carry a skeleton.
    for card in E["weit"]:
        check(card in zones.get("weit", []), f"card '{card}' at half width")

    if case != "synchron":
        print("\n  Placeholders")
        skeletons = re.findall(r'class="minikurve geruest"', page)
        check(len(skeletons) >= 3,
              f"skeleton instead of graph where data is missing ({len(skeletons)})")

        # The most important point: no invented numbers. The cards without
        # data must hold nothing that could be taken for a measurement.
        for card in E["weit"]:
            block = re.search(
                r"<h2>" + re.escape(card) + r"</h2>(.*?)</section>", page, re.S)
            values = re.findall(r"<dd[^>]*>([^<]*)</dd>", block.group(1) if block else "")
            check(all(w.strip() in ("—", "") for w in values),
                  f"'{card}' shows dashes instead of invented values",
                  " | ".join(w for w in values if w.strip() not in ("—", "")))
        check(E["warten"] in page,
              "the cards say what they are waiting for")

    print("\n  Metrics bar")
    tiles = re.findall(r"<div class=klabel>(.*?)</div>", page)
    check(len(tiles) >= 3, f"{len(tiles)} tiles in the bar", ", ".join(tiles))

    print("\n  Network map")
    if case == "leer":
        # Without peer data the card must still show the connection figures
        # — the 'Network' card that used to carry them is gone.
        check("netzersatz" in page, "without peers the fallback list stands there")
        check(E["verbindungen"] in page, "the connection count stays visible")
        # The node answered, only with an empty list. In that case it must
        # not claim the call is not allowed.
        check("06-tor.sh" not in page,
              "no not-allowed notice when the node answered")
        check(E["abgefragt"] in page,
              "the note about the pending answer instead")
    else:
        dots = re.findall(r'<g class="peer [\w ]+" tabindex="0" data-nr="(\d+)"',
                            page)
        check(len(dots) == 19, f"one row per peer ({len(dots)} of 19)")
        check([int(n) for n in dots] == list(range(len(dots))),
              "the rows are numbered consecutively")
        check("id=peerdetail" in page, "detail box is present")

        # The hub carries the Bitcoin mark as an image. As a character
        # (U+20BF) it would be an empty box in many fonts.
        if nd is not None:
            check_logo(nd, page)
        check(E["netz"] in page, "card carries a heading")

        # The fan hangs left and right off the hub. With an odd count the
        # left side gets the extra row.
        lines = re.findall(r'<text x="([\d.]+)"[^>]*text-anchor="(\w+)"'
                            r'[^>]*class="peerzeile"', page)
        left = [x for x, a in lines if a == "end"]
        right = [x for x, a in lines if a == "start"]
        check(len(left) == 10 and len(right) == 9,
              f"split over both sides ({len(left)} left, {len(right)} right)")
        check(all(float(x) < 600 for x in left)
              and all(float(x) > 600 for x in right),
              "left labels sit left of the hub, right ones right")

        # Label and dot must sit at the same height, otherwise the fan looks
        # crooked. The text used to stand eight pixels above the line because
        # a horizontal ran there.
        pairs = re.findall(
            r'<circle cx="[\d.]+" cy="([\d.]+)" r="4\.5"[^>]*/>'
            r'<text x="[\d.]+" y="([\d.]+)"[^>]*class="peerzeile"', page)
        check(len(pairs) == 19, f"{len(pairs)} dot/text pairs found")
        askew = [(p, t) for p, t in pairs if abs(float(p) - float(t)) > 0.01]
        check(not askew, "dot and label sit at the same height",
              " | ".join(f"{p} gegen {t}" for p, t in askew[:3]))
        check('dominant-baseline="central"' in page,
              "and the text is vertically centred")

        # The key figures belong on the line, not only when pointing.
        texts = re.findall(r'class="peerzeile">([^<]*)</text>', page)
        check(all("·" in t for t in texts),
              "every row carries address, network type and figures",
              texts[0] if texts else "")

        # An SVG clips everything beyond its viewBox. On 2026-08-23 the
        # width was fixed at 1200, and with six-digit latencies the end of
        # every right-hand line disappeared.
        frame = re.search(r'class=netzkarte viewBox="0 0 (\d+) (\d+)"', page)
        check(frame is not None, "the network map has a viewBox")
        if frame:
            width = int(frame.group(1))
            fields = re.findall(
                r'<text x="([\d.]+)" y="[\d.]+" text-anchor="(\w+)"'
                r' class="peerzeile">([^<]*)</text>', page)
            mark = 12.5 * 0.63          # muss zu PEER_FONT passen
            outside = []
            for x, anchor, text in fields:
                x, span = float(x), len(text) * mark
                left, right = ((x - span, x) if anchor == "end"
                                 else (x, x + span))
                if left < 0 or right > width:
                    outside.append(text[:28])
            check(not outside,
                  f"all {len(fields)} labels fit inside the drawing",
                  " | ".join(outside[:3]))

    print("\n  Copy fields")
    fields = re.findall(
        r'<span class=kopierlabel>(.*?)</span>.*?<code class=kopier[^>]*>(.*?)</code>',
        page, re.S)
    check(len(fields) == 2, f"two addresses to copy ({len(fields)} found)")
    for label, value in fields:
        check("\n" not in value and "<" not in value,
              f"'{label}' stands there as plain text", f"{len(value)} Zeichen")

    # The text must wrap instead of running out of the card: an onion address
    # is 70 characters and fits into no half card width.
    css = (OUTPUT / "stil.css").read_text(encoding="utf-8")
    tight_css = css.replace("\n", "").replace(" ", "")
    check("white-space:nowrap" not in tight_css.replace("white-space:nowrap;", "", 0)
          or "word-break:break-all" in tight_css,
          "copy fields wrap instead of overflowing")
    check("overflow-wrap:anywhere" in tight_css,
          "and break mid-word if they must")

    buttons = re.findall(r'class=kopierknopf data-wert="([^"]*)"', page)
    check(len(buttons) == len(fields),
          f"one copy button per address ({len(buttons)} to {len(fields)})")
    check(all(w for w in buttons), "every button knows its value")
    script_raw = (OUTPUT / "dash.js").read_text(encoding="utf-8")
    # navigator.clipboard exists only over HTTPS. This page runs on the local
    # network over http:// — without a fallback the button would do nothing.
    check("execCommand" in script_raw and "isSecureContext" in script_raw,
          "with a fallback, because http:// has no clipboard interface")

    print("\n  Number formatting")
    # Version numbers are not decimals — "31.1.0" stays as it is.
    no_numbers = ("version", "bitcoin core", "electrs", "stand der", "kennung",
                    "identifier", "state of")
    # We look for the WRONG decimal separator in each case: a period between
    # digits in German, a comma in the same spot in English. Thousands
    # separators do not count because three digits follow there.
    wrong = (r"\d\.\d{1,2}(?!\d)" if language == "de" else r"\d,\d{1,2}(?!\d)")
    suspect = []
    for bez, value in re.findall(r"<dt[^>]*>([^<]*)</dt><dd[^>]*>([^<]*)</dd>", page):
        if any(k in bez.lower() for k in no_numbers):
            continue
        if re.search(wrong, value):
            suspect.append(f"{bez}: {value}")
    check(not suspect,
          f"'{E['decimal_sep']}' used as decimal separator throughout the cards",
          " | ".join(suspect))

    # The big numbers do not live in a <dl>. That is exactly where a
    # "11.24 %" slipped through on 2026-08-23, because the check above only
    # looked at cards.
    big = re.findall(r"<div class=(?:zwort|zzahl|kwert)>([^<]*)</div>", page)
    bad = [w for w in big if re.search(wrong, w)]
    check(not bad,
          f"correct decimal separator in the {len(big)} big numbers too",
          " | ".join(bad))

    if language == "de":
        # Shell scripts deliberately print ASCII, the page does not. This
        # went wrong three times ("Bloecke", "Schaetzung", "waehrend"), hence
        # a fixed list of the words that tend to slip through. In English
        # there is nothing to transliterate.
        ascii_forms = ("waehrend", "moeglich", "Bloecke", "bloecke", "Schaetzung",
                       "Eintraege", "Pruefung", "naechste", "Groesse", "koennen",
                       "muessen", "gehoert", "zurueck", "ueber ", "fuer ")
        found = [w for w in ascii_forms if w in page]
        check(not found, "no ASCII transliteration in the visible text",
              " | ".join(found))
    else:
        # The other way round: no German leftovers may remain in the English
        # version. Umlauts are the most reliable marker — they appear in no
        # English word.
        leftovers = sorted(set(re.findall(r"[A-Za-z]*[äöüÄÖÜß][A-Za-z]*", page)))
        # Identifiers of foreign nodes and log lines do not count: what
        # stands there is decided by the peer, not by this program.
        without_log = re.sub(r"<code id=logtext>.*?</code>", "", page, flags=re.S)
        leftovers = [w for w in leftovers if w in without_log]
        check(not leftovers, "no German leftovers in the English version",
              " | ".join(leftovers[:6]))

    print("\n  Safety")
    check("rpcpassword" not in page.lower() and "probe" not in page,
          "no credentials in the page")
    check(POISON not in page,
          "a foreign node identifier does not land as markup in the page")
    graphics = re.findall(r"<dd class=grafik>(.*?)</dd>", page, re.S)
    check(all(g.lstrip().startswith("<svg") or g.lstrip().startswith("<span")
              for g in graphics),
          f"all {len(graphics)} graphic fields hold generated SVG only")


def check_logo(nd, page):
    """The mark is an image, not rebuilt geometry.

    It was assembled by hand from rectangles and arcs three times and was
    wrong three times — and every attempt cost a round trip via a screenshot,
    because nothing can be looked at here. A logo is not a geometry exercise.
    What is checked now is that the image is present, sits correctly and fits
    into the circle.
    """
    hit = re.search(
        r'<image href="bitcoin\.png\?v=([0-9a-f]{8})" x="([\d.]+)" y="([\d.]+)" '
        r'width="(\d+)" height="(\d+)"/>', page)
    check(hit is not None, "the Bitcoin mark stands as an image in the hub")
    if not hit:
        return

    fingerprint, x, y, width, height = hit.groups()
    x, y, width, height = float(x), float(y), int(width), int(height)
    check(fingerprint == nd.BITCOIN_V,
          "it carries the fingerprint against the cache")
    check(width == height == nd.LOGO_R * 2,
          f"it is square and {nd.LOGO_R * 2} units across "
          f"({width} x {height})")

    # Centred on the hub of the network map
    frame = re.search(r'class=netzkarte viewBox="0 0 (\d+) (\d+)"', page)
    if frame:
        kb, kh = int(frame.group(1)), int(frame.group(2))
        mitte_x, mitte_y = x + width / 2, y + height / 2
        check(abs(mitte_x - kb / 2) < 1 and abs(mitte_y - kh / 2) < 1,
              "it sits exactly on the hub",
              f"{mitte_x:.1f}/{mitte_y:.1f} statt {kb / 2:.1f}/{kh / 2:.1f}")

    # Die Datei selbst
    image = OUTPUT / "bitcoin.png"
    check(image.exists(), "bitcoin.png was written")
    if image.exists():
        raw = image.read_bytes()
        check(raw[:8] == b"\x89PNG\r\n\x1a\n", "and is a valid PNG")
        check(raw == nd.BITCOIN_PNG, "and matches the embedded copy")
        check(len(raw) < 8000, f"and stays small ({len(raw)} bytes)")


def check_status(case):
    """status.json is the read-only API. It has to match the page."""
    print("\n  Read-only API")
    path = OUTPUT / "status.json"
    check(path.exists(), "status.json was written")
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        check(False, "status.json is valid JSON", str(e))
        return
    check(True, "status.json is valid JSON", f"{path.stat().st_size} Bytes")

    for key in ("erzeugt", "stempel", "titel", "stufe", "zonen", "peers"):
        check(key in data, f"field '{key}' present")

    zones = data.get("zonen", {})
    check(set(zones) == {"kopf", "zustand", "stoerung", "band", "netz",
                          "raster", "weit", "voll"},
          "all zones present", ", ".join(sorted(zones)))

    peers = data.get("peers", [])
    if case == "leer":
        check(peers == [], "without peers the list stays empty")
    else:
        check(len(peers) == 19, f"{len(peers)} peers in the list")
        check(any(POISON in p.get("version", "") for p in peers),
              "the foreign identifier is a plain value in the list")
        markup = [z for z in zones.values() if POISON in z]
        check(not markup,
              "the foreign identifier is in none of the HTML zones")

    text = (OUTPUT / "log.txt")
    check(text.exists(), "log.txt was written")
    if text.exists():
        content = text.read_text(encoding="utf-8")
        check("<html" not in content and "<pre" not in content,
              "log.txt is plain text without markup")

    for name in ("stil.css", "dash.js"):
        check((OUTPUT / name).exists(), f"{name} was written")

    # A typo in dash.js would silently switch off the entire moving layer —
    # the page would look right and merely never get newer. No other test
    # here would find that. If node is available, we check it.
    try:
        r = subprocess.run(["node", "--check", str(OUTPUT / "dash.js")],
                           capture_output=True, text=True, timeout=20)
        check(r.returncode == 0, "dash.js is syntactically valid",
              (r.stderr or "").strip().split("\n")[0])
    except (OSError, subprocess.SubprocessError):
        print("  [ --  ] dash.js not checked (node not available)")


def check_tor_notice(nd, cfg):
    """The watchdog reports through a file, the dashboard displays it.

    The failure case matters most: if 06-tor.sh fails that must be impossible
    to miss — the node may then be sitting half converted.
    """
    print("\n  Tor watchdog")
    # Deliberately outside the project folder: depending on the environment
    # that sits on a mount where files cannot be deleted again.
    folder = tempfile.mkdtemp(prefix="torprobe-")
    path = Path(folder) / "tor.json"
    cfg["TOR_FILE"] = str(path)

    cases = [
        ("wartet", "", False, "quiet during the sync"),
        ("bereit", "meldung warn", True, "announces the switchover"),
        ("laeuft", "meldung warn", True, "reports the running switchover"),
        ("fehler", "fehlerkarte", True, "shows the failure clearly"),
        ("fertig", "", False, "quiet again once the work is done"),
    ]
    try:
        for state, marker, visible, text in cases:
            path.write_text(json.dumps({
                "zustand": state, "meldung": "Probe",
                "treffer": 3, "noetig": 6, "zeit": int(time.time()),
            }), encoding="utf-8")
            nd.one_pass(cfg)
            page = (OUTPUT / "index.html").read_text(encoding="utf-8")
            block = re.search(r"<div id=z-stoerung>(.*?)</div>\s*<div class=band",
                              page, re.S)
            content = block.group(1) if block else ""
            check((marker in content) if visible else (content.strip() == ""),
                  f"state '{state}': {text}")

        # The cancel command must be shown while there is still something to cancel.
        path.write_text(json.dumps({"zustand": "bereit", "meldung": "",
                                    "treffer": 3, "noetig": 6}), encoding="utf-8")
        nd.one_pass(cfg)
        page = (OUTPUT / "index.html").read_text(encoding="utf-8")
        check("08-tor-automatik.sh --aus" in page,
              "the cancel command stands in the announcement")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        cfg.pop("TOR_FILE", None)
        nd.one_pass(cfg)


def check_peers_on_hiccup(nd, cfg):
    """A hiccup in getpeerinfo must not wipe the peer list.

    On 2026-08-23 it did exactly that: at twelve seconds response time the
    call occasionally hit the timeout, the dashboard threw away every peer and
    showed "will be allowed by 06-tor.sh" instead — although the method had
    long been allowed.
    """
    print("\n  Peers during hiccups")
    nd.one_pass(cfg)
    before = len(json.loads(
        (OUTPUT / "status.json").read_text(encoding="utf-8"))["peers"])
    check(before > 0, f"in the normal case {before} peers are shown")

    real_rpc = nd.rpc

    def stumbling(c, method, params=None):
        if method == "getpeerinfo":
            raise nd.RpcError("Node not reachable: timed out")
        return real_rpc(c, method, params)

    nd.rpc = stumbling
    try:
        nd.one_pass(cfg)
        data = json.loads((OUTPUT / "status.json").read_text(encoding="utf-8"))
        check(len(data["peers"]) == before,
              f"after a hiccup they are still there ({len(data['peers'])})")
        page = (OUTPUT / "index.html").read_text(encoding="utf-8")
        check("06-tor.sh" not in page,
              "and no not-allowed notice appears")
    finally:
        nd.rpc = real_rpc
        nd.one_pass(cfg)


def check_denied_methods(nd, cfg):
    """What the node refuses must not be asked again every 30 seconds.

    Every refused call makes bitcoind write a log line "RPC User dashboard not
    allowed to call method …" — and that lands straight in the log display of
    the dashboard. On the Pi two of them appeared there every minute on
    2026-08-23.
    """
    print("\n  Refused methods")
    nd.DENIED.clear()

    # Counted on the wire, not at the function call: the lock sits inside
    # rpc(), and a counter in front of it would count every call and prove
    # nothing.
    attempts = {"n": 0}
    real_urlopen = nd.urllib.request.urlopen

    def counting(*a, **k):
        attempts["n"] += 1
        return real_urlopen(*a, **k)

    nd.urllib.request.urlopen = counting
    try:
        for _ in range(5):
            try:
                nd.rpc(cfg, "gibtesnicht")
            except nd.RpcError:
                pass
        check(attempts["n"] == 1,
              f"five calls reach the node exactly once ({attempts['n']}x)")
        check("gibtesnicht" in nd.DENIED, "the refusal is remembered")

        # After the deadline it is asked again — otherwise a method allowed
        # later would stay missing forever.
        nd.DENIED["gibtesnicht"] = time.time() - nd.DENIED_RETRY_AFTER - 1
        try:
            nd.rpc(cfg, "gibtesnicht")
        except nd.RpcError:
            pass
        check(attempts["n"] == 2,
              f"after the deadline it tries again ({attempts['n']}x)")
    finally:
        nd.urllib.request.urlopen = real_urlopen
        nd.DENIED.clear()


def check_tolerance(nd, cfg):
    """A single hiccup must not count as an outage.

    The node stalls its RPC thread while writing the dbcache to disk. That is
    exactly what used to trigger 'not reachable' and a block height of zero,
    while the log next to it showed the sync carrying on.
    """
    print("\n  Tolerance window")

    def block_height():
        page = (OUTPUT / "index.html").read_text(encoding="utf-8")
        hit = re.search(r"<div class=zzahl>(.*?)</div>", page)
        return hit.group(1) if hit else None

    before = block_height()
    real_rpc = nd.rpc
    nd.rpc = lambda *a, **k: (_ for _ in ()).throw(
        nd.RpcError("Node not reachable: timed out"))

    try:
        for attempt in (1, 2):
            nd.one_pass(cfg)
            page = (OUTPUT / "index.html").read_text(encoding="utf-8")
            check("fehlerkarte" not in page,
                  f"hiccup {attempt}: no red error card")
            check("class=veraltet" in page,
                  f"hiccup {attempt}: quiet note about stale values")
            check(block_height() == before,
                  f"hiccup {attempt}: block height stays put",
                  f"{before} -> {block_height()}")

        nd.one_pass(cfg)
        page = (OUTPUT / "index.html").read_text(encoding="utf-8")
        check("fehlerkarte" in page,
              "after three hiccups in a row the error card appears")

        # The case right after a service restart: no old state for the window
        # to hold on to. It used to raise the alarm here immediately although
        # the node was running.
        nd.LAST_STATE.clear()
        nd.FAILURES_IN_ROW = 0
        nd.one_pass(cfg)
        page = (OUTPUT / "index.html").read_text(encoding="utf-8")
        check("fehlerkarte" not in page,
              "freshly started: no error card on the first hiccup")
        check('data-stufe="anlauf"' in page,
              "freshly started: the page says it is still waiting")
    finally:
        nd.rpc = real_rpc
        nd.FAILURES_IN_ROW = 0
        nd.one_pass(cfg)          # sauberen Stand wiederherstellen


def check_cache(nd, cfg):
    """The 24 hour data may be fetched only once.

    Re-querying 144 blocks on every cycle would keep the node needlessly busy
    every 30 seconds.
    """
    print("\n  Caching")
    counter = {"n": 0}
    real_rpc = nd.rpc

    def counting(c, method, params=None):
        if method in ("getblockstats", "getblockheader"):
            counter["n"] += 1
        return real_rpc(c, method, params)

    # The previous cycle has already filled the buffers — for the measurement
    # they must be empty, otherwise nothing is measured.
    nd.BLOCK_DATA.clear()
    nd.DIFFICULTY.clear()

    nd.rpc = counting
    nd.fetch_difficulty(cfg, 915312)
    nd.fetch_block_data(cfg, 915312)
    first = counter["n"]
    counter["n"] = 0
    nd.fetch_difficulty(cfg, 915312)
    nd.fetch_block_data(cfg, 915312)
    nd.rpc = real_rpc
    check(first > 100, f"initial fill fetches {first} blocks")
    check(counter["n"] == 0,
          f"second cycle fetches nothing ({counter['n']} calls)")


def check_write_thrift(nd, cfg):
    """Unchanged content must not be written to disk again."""
    print("\n  Writes")
    written = []
    real = nd.write_file_atomic

    def counting_writes(folder, name, content):
        ergebnis = real(folder, name, content)
        if ergebnis:
            written.append(name)
        return ergebnis

    nd.write_file_atomic = counting_writes
    try:
        nd.write_assets(cfg)
        check(not written,
              "style and script are not rewritten on every cycle",
              ", ".join(written))
    finally:
        nd.write_file_atomic = real


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="synchron",
                          choices=["synchron", "sync", "leer"])
    parser.add_argument("--language", default="de", choices=["de", "en"],
                          help="display language of the generated page")
    args = parser.parse_args()

    print(f"\n=== Test run, case '{args.case}', "
          f"language '{args.language}' ===")
    mock = subprocess.Popen(
        [sys.executable, str(HERE / "attrappe.py"),
         "--port", str(PORT), "--case", args.case],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=.2)
            except urllib.error.HTTPError:
                break          # antwortet — mehr braucht es nicht
            except OSError:
                time.sleep(.1)
        else:
            print("  The mock did not come up.")
            return 1

        nd = load_dashboard()
        replace_system_parts(nd, args.case)
        cfg = nd.read_config(write_config(args.language))
        nd.one_pass(cfg)

        page = (OUTPUT / "index.html").read_text(encoding="utf-8")
        print(f"\n  {len(page)} bytes written to {OUTPUT / 'index.html'}")
        check(f'<html lang={args.language}' in page,
              f"the page declares itself as lang={args.language}")
        check_page(page, args.case, nd, args.language)
        check_translation(nd)
        check_classes(page, nd, args.case)
        check_status(args.case)
        check_write_thrift(nd, cfg)
        check_tor_notice(nd, cfg)
        if args.case != "leer":
            check_peers_on_hiccup(nd, cfg)
        check_denied_methods(nd, cfg)
        check_tolerance(nd, cfg)
        if args.case == "synchron":
            check_cache(nd, cfg)
    finally:
        mock.terminate()
        mock.wait(timeout=5)

    print()
    if failures:
        print(f"=== {len(failures)} check(s) failed ===")
        for f in failures:
            print(f"    {f}")
        return 1
    print("=== all checks passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
