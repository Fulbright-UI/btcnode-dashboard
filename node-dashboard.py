#!/usr/bin/env python3
"""
node-dashboard — writes a static HTML page showing the state of the node.

Deliberately plain by design: this program queries the node and writes a
finished HTML file. The web server only hands out that file. It does not know
the node, accepts no input and executes nothing. Whoever takes over the web
server gets an HTML file and nothing else.

Only modules from the Python standard library are used — no third-party
packages, no pip, no npm.

Usage:  node-dashboard [--config /etc/node-dashboard.conf] [--once]
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

VERSION = "3.3"

# ================================================================= Language ==
# English is the source language: the code carries the English text, the table
# translates it. This is the usual approach (gettext works the same way) and
# has one solid advantage over keys like "card.system" — anyone reading the
# code sees straight away what appears on the page instead of having to look
# a key up in a table.
#
# A missing entry falls back to the English text, so the page stays intact
# instead of dying with a KeyError. The test run finds such gaps: it collects
# every visible string and checks that DE knows it.
LANGUAGE = "de"


# Timestamp in the page header. German puts the day first, English follows
# ISO — the American form 08/23/2026 would be the worst choice here because it
# cannot be told apart from 23.08.2026 as long as the day is below 13.
TIME_FORMAT = {"de": "%d.%m.%Y %H:%M:%S", "en": "%Y-%m-%d %H:%M:%S"}


def set_language(value):
    """Set the display language. Anything but 'en' means German.

    Deliberately forgiving: a typo in the configuration file should not leave
    the page empty, at worst it shows the wrong language.
    """
    global LANGUAGE
    LANGUAGE = "en" if str(value).strip().lower().startswith("en") else "de"


def t(text, **values):
    """Translate an English source string into the configured language.

    Placeholders are filled in after the lookup, not before — otherwise the
    table would have to know every single number.

    A vertical bar separates a hint that only serves the table and is dropped
    in English: t("in {n} blocks|dativ"). It is needed because two German forms
    can collapse onto the same English text — "1.234 Blöcke" versus "in 1.234
    Blöcken" are both "1,234 blocks". Without the hint one of the two places
    would have to stay wrongly inflected forever.
    """
    if LANGUAGE == "en":
        raw = text.split("|", 1)[0]
    else:
        raw = DE.get(text) or DE.get(text.split("|", 1)[0]) or text.split("|", 1)[0]
    return raw.format(**values) if values else raw


# The translation table. Grouped by area so that things belonging together do
# not end up scattered over half the file.
DE = {
    # -- Units and durations -------------------------------------------------
    "{n} d {h} h": "{n} T {h} Std",
    "{n} h {m} min": "{n} Std {m} Min",
    "{n} min": "{n} Min",
    "{n} s ago": "vor {n} s",
    "{n} min ago": "vor {n} Min",
    "{n} h ago": "vor {n} Std",
    "{n} d ago": "vor {n} Tagen",

    # -- Card "System" -------------------------------------------------------
    "System": "System",
    "Temperature": "Temperatur",
    "last hour": "letzte Stunde",
    "Temperature history, still measuring": "Temperaturverlauf, wird noch gemessen",
    "Load": "Auslastung",
    "Memory": "Arbeitsspeicher",
    "{used} of {total}": "{used} von {total}",
    "Disk space": "Speicherplatz",
    "{pct} % used, {free} free": "{pct} % belegt, {free} frei",
    "Pi up for": "Pi läuft seit",
    "Power supply": "Stromversorgung",
    "stable": "stabil",
    "UNDERVOLTAGE NOW": "AKTUELL Unterspannung",
    "undervoltage since boot": "Unterspannung seit Start",
    "throttled": "gedrosselt",
    "irregular": "auffällig",

    # -- Chain, network facts, mempool ---------------------------------------
    "verified through {date}": "geprüft bis {date}",
    "{n} pp/h": "{n} %-Punkte/Std",
    "Block reward": "Blockbelohnung",
    "remaining": "noch",
    "{n} bloecke": "{n} Blöcke",
    "Difficulty": "Schwierigkeit",
    "last adjustment": "letzte Anpassung",
    "last adjustments": "letzte Anpassungen",
    "Difficulty of the last adjustments": "Schwierigkeit der letzten Anpassungen",
    "Difficulty of the last {n} adjustments": "Schwierigkeit der letzten {n} Anpassungen",
    "last {n} adjustments": "letzte {n} Anpassungen",
    "Connections": "Verbindungen",
    "eingehend": "davon eingehend",
    "outbound": "davon ausgehend",
    "Version": "Version",
    "Node up for": "Node läuft seit",
    "Transactions": "Transaktionen",
    "Memory use": "Speicher",
    "Minimum fee": "Mindestgebühr",
    "next block": "nächster Block",
    "in ~1 hour": "in ~1 Stunde",
    "in ~4 hours": "in ~4 Stunden",
    "Estimate": "Schätzung",
    "not available during sync": "während der Synchronisation nicht möglich",

    # -- Card titles ---------------------------------------------------------
    # These strings double as the identity of a card (see CARDS_WIDE,
    # CARDS_FULL, CARD_ORDER). Change one here and you must change it there
    # too — otherwise the card silently slides into the wrong grid.
    "Volume · 24 hours": "Volumen · 24 Stunden",
    "Fee history · 24 hours": "Gebührenverlauf · 24 Stunden",
    "Network": "Netzwerk",
    "Chain": "Kette",
    "Mempool": "Mempool",
    "Halving": "Halbierung",
    "next adjustment": "nächste Anpassung",
    "in {n} blocks|dativ": "in {n} Blöcken",
    "fill level": "Füllstand",
    "Electrum server": "Electrum-Server",
    "Connected nodes": "Verbundene Knoten",
    "Log": "Protokoll",

    # -- 24 hour cards -------------------------------------------------------
    "Appears once the chain is up to date. Until then the "
    "most recent bloecke are years old and would say nothing.":
        "Erscheint, sobald die Kette steht. Bis dahin liegen die "
        "letzten Blöcke Jahre zurück und wären ohne Aussage.",
    "Total": "Summe",
    "Blocks": "Blöcke",
    "{n} · {h} h": "{n} · {h} Std",
    "Volume per block": "Volumen je Block",
    "Volume moved per block over the last 24 hours":
        "Bewegtes Volumen je Block der letzten 24 Stunden",
    "latest": "zuletzt",
    "24 h average": "Mittel 24 h",
    "Range": "Spanne",
    "{von} to {bis} sat/vB": "{von} bis {bis} sat/vB",
    "average fee per block": "mittlere Gebühr je Block",
    "Average fee per block over the last 24 hours":
        "Mittlere Gebühr je Block der letzten 24 Stunden",

    # -- Card "Electrum server" ----------------------------------------------
    "Service": "Dienst",
    "running": "läuft",
    "stopped": "gestoppt",
    "Responding": "Antwortet",
    "yes": "ja",
    "no, still indexing": "nein, indiziert noch",
    "On the local network": "Im Heimnetz",
    "Over Tor": "Über Tor",
    "Enter this in your wallet as a custom server — in the "
    "BitBoxApp under Settings → Advanced settings → Connect your "
    "own voll node. Clicking an address selects it, Ctrl+C copies.":
        "In der Wallet als eigenen Server eintragen — in der BitBoxApp "
        "unter Einstellungen → Erweiterte Einstellungen → Eigene Full "
        "Node verbinden. Ein Klick auf eine Adresse markiert sie, "
        "Strg+C kopiert.",

    # -- Network map ---------------------------------------------------------
    "unknown": "unbekannt",
    "none": "keine",
    "local": "lokal",
    "Tor · inbound": "Tor · eingehend",
    "Connected": "Verbunden",
    "Average round trip": "Laufzeit im Mittel",
    "fastest": "schnellster",
    "own response time": "eigene Antwortzeit",
    "Network of the {n} connected nodes": "Netz der {n} verbundenen Knoten",

    # -- Tor watchdog --------------------------------------------------------
    "<b>The chain is up to date.</b> The watchdog will switch the "
    "node to Tor once it has been in sync for one hour without a "
    "break — measurement {treffer} of {noetig}. Cancel with "
    "<code>sudo bash 08-tor-automatik.sh --aus</code>.":
        "<b>Die Kette steht.</b> Der Wächter stellt den Node auf Tor um, "
        "sobald sie eine Stunde durchgehend synchron war — Messung "
        "{treffer} von {noetig}. Abbrechen mit "
        "<code>sudo bash 08-tor-automatik.sh --aus</code>.",
    "<b>Switching to Tor.</b> bitcoind is being restarted and port "
    "8333 closed. Follow along with "
    "<code>journalctl -u node-torwaechter -f</code>.":
        "<b>Umstellung auf Tor läuft.</b> bitcoind wird dabei neu "
        "gestartet und Port 8333 geschlossen. Verlauf im Protokoll unter "
        "<code>journalctl -u node-torwaechter -f</code>.",
    "Tor switchover failed": "Tor-Umstellung gescheitert",
    "up for {duration}": "läuft seit {duration}",
    "versions checked {age}": "Versionen geprüft {age}",
    ", fetched in the clear": ", Abruf im Klartext",

    # -- Network map fallback and log ----------------------------------------
    "drawing follows once the method is allowed":
        "Zeichnung folgt nach der Freischaltung",
    "The network drawing needs the <code>getpeerinfo</code> "
    "call. <code>06-tor.sh</code> allows it.":
        "Die Netzgrafik braucht die Abfrage <code>getpeerinfo</code>. "
        "Sie wird von <code>06-tor.sh</code> freigeschaltet.",
    "querying peers": "Gegenstellen werden abgefragt",
    "The node has not delivered the peer list yet. During "
    "the initial sync that occasionally takes longer than "
    "the timeout.":
        "Der Node hat die Liste der Gegenstellen noch nicht "
        "geliefert. Während der Synchronisation dauert das "
        "gelegentlich länger als das Zeitlimit.",
    "filled = outbound": "gefüllt = ausgehend",
    "Point at a line for identifier, dienste and connection time.":
        "Auf eine Zeile zeigen für Kennung, Dienste und Verbindungsdauer.",
    "No log source configured.": "Keine Protokollquelle eingerichtet.",
    "no eintraege": "keine Einträge",
    "log not readable: {e}": "Protokoll nicht lesbar: {e}",
    "no access to the journal": "kein Zugriff auf das Journal",

    # -- State bar and metrics bar -------------------------------------------
    "Not reachable": "Nicht erreichbar",
    "Bitcoin Core is not answering": "Bitcoin Core antwortet nicht",
    "Waiting for Bitcoin Core": "Warte auf Bitcoin Core",
    "No answer since this display started":
        "Seit dem Start dieser Anzeige kam noch keine Antwort",
    "Node answering slowly": "Node antwortet verzögert",
    "Values shown were measured {age}":
        "Angezeigte Werte sind {age} gemessen",
    "One notice": "Ein Hinweis",
    "{n} notices": "{n} Hinweise",
    "All good": "Alles läuft",
    "{n} on disk": "{n} auf der SSD",
    " · pruning active": " · Pruning aktiv",
    "of": "von",
    "bloecke verified · known to the network": "Blöcke geprüft · im Netz",
    "bloecke rueckstand": "Blöcke Rückstand",
    "in the mempool": "im Mempool",
    "The node that delivered the last block is no longer connected.":
        "Der Knoten, der den letzten Block lieferte, ist nicht mehr verbunden.",
    "Block {n} arrived {when} from {peer}": "Block {n} kam {when} von {peer}",
    "The last block arrived {when} from {peer}":
        "Der letzte Block kam {when} von {peer}",
    "block data sent to {n} nodes|dativ": "Blockdaten an {n} Knoten gesendet",
    "no node has requested it from us": "kein Knoten hat ihn von uns angefordert",
    "announced the last block first": "kündigte den letzten Block zuerst an",
    "delivered it": "lieferte ihn",
    "{ok} of {n} probes matched our height, {behind} behind, none ahead · last {when}":
        "{ok} von {n} Stichproben bestätigen unsere Höhe, {behind} hinterher, keine voraus · zuletzt {when}",
    "a probe reports {n} blocks more than we have":
        "eine Stichprobe meldet {n} Blöcke mehr als wir",
    "Chain check: every few minutes Core asks a random node for its height. Last {when}":
        "Kettenabgleich: Core fragt alle paar Minuten einen zufälligen Knoten nach seiner Höhe. Zuletzt {when}",
    "Chain check: a probe reports {n} blocks more":
        "Kettenabgleich: eine Stichprobe meldet {n} Blöcke mehr",
    "Block {n} · announced {when} by {peer}": "Block {n} · angekündigt {when} von {peer}",
    " · delivered by {peer}": " · geliefert von {peer}",
    "peer {n} (no longer connected)": "Peer {n} (nicht mehr verbunden)",
    "first to announce, {total} blocks in 24 h: {parts}":
        "zuerst angekündigt, {total} Blöcke in 24 h: {parts}",
    "Electrum · local": "Electrum · lokal",
    "complete · block {n}": "vollständig · Block {n}",
    "{n} of {tip} bloecke · {rest}": "{n} von {tip} Blöcken · {rest}",
    "{n} bloecke to go": "noch {n} Blöcke",
    "progress not readable": "Fortschritt nicht lesbar",
    "Index": "Index",
    "got it from us": "bekam ihn von uns",
    "Blocks from here": "Blöcke von hier",
    "Blocks to here": "Blöcke dorthin",
    "last {when}": "zuletzt {when}",
    "median fee in the last block": "Median-Gebühr im letzten Block",
    "estimate for the next block: {fee}": "Schätzung nächster Block: {fee}",
    "Syncing the blockchain": "Synchronisiert die Blockchain",
    "of {n} bloecke": "von {n} Blöcken",
    "Node in sync, nothing unusual": "Node synchron, keine Auffälligkeiten",
    "Block · {age}": "Block · {age}",
    "Block height": "Blockhöhe",
    "about {remaining} left": "noch etwa {remaining}",
    "Still measuring the rate": "Tempo wird noch gemessen",
    "History, still measuring": "Verlauf, wird noch gemessen",
    "History appears after about 15 minutes":
        "Verlauf ab etwa 15 Minuten Laufzeit",

    # -- Trouble card --------------------------------------------------------
    "<b>No answer from the node yet.</b> This display has just "
    "started and is waiting for its first reply. During the "
    "initial sync that can take a minute.":
        "<b>Noch keine Antwort vom Node.</b> Diese Anzeige wurde eben erst "
        "gestartet und wartet auf die erste Auskunft. Während der "
        "Erstsynchronisation kann das eine Minute dauern.",
    # The placeholder is {age}, not {alter}: it has to match the call in
    # build_trouble, not the German wording. On 2026-08-24 it read {alter}
    # here — the key no longer matched the source string, the lookup came
    # back empty and the German page showed an English sentence with a
    # German time value inside ("from vor 55 s").
    "<b>The node is not answering right now.</b> Shown is the last "
    "measured state from {age}. During the initial sync this is "
    "normal: Bitcoin Core pauses its query interface while it "
    "writes its cache to disk.":
        "<b>Node antwortet gerade nicht.</b> Angezeigt wird der letzte "
        "gemessene Stand von {age}. Während der Erstsynchronisation ist "
        "das normal: Bitcoin Core hält seine Abfrageschnittstelle an, "
        "solange es den Zwischenspeicher auf die SSD schreibt.",
    "Bitcoin Core is starting": "Bitcoin Core startet",
    "<b>Bitcoin Core is starting up.</b> It reports: "
    "{message} The figures shown are from before the restart.":
        "<b>Bitcoin Core startet gerade.</b> Es meldet: "
        "{message} Die gezeigten Zahlen stammen von vor dem Neustart.",
    "Node not reachable": "Node nicht erreichbar",
    "Check with <code>systemctl status bitcoind</code> or "
    "<code>journalctl -u bitcoind -n 50</code>.":
        "Prüfen mit <code>systemctl status bitcoind</code> oder "
        "<code>journalctl -u bitcoind -n 50</code>.",
    "Node refused the login": "Anmeldung am Node abgelehnt",
    "The password in <code>/etc/node-dashboard.conf</code> does "
    "not match the <code>rpcauth</code> entry in bitcoin.conf.":
        "Das Passwort in <code>/etc/node-dashboard.conf</code> passt nicht "
        "zum <code>rpcauth</code>-Eintrag in der bitcoin.conf.",
    "Node answers with an error": "Node antwortet mit einem Fehler",
    "The node is running but rejects a call. The method may be "
    "missing from <code>rpcwhitelist</code>.":
        "Der Node läuft, lehnt aber eine Abfrage ab. Möglicherweise fehlt "
        "der Befehl in der <code>rpcwhitelist</code>.",

    # -- Strings substituted into dash.js ------------------------------------
    # 'inbound' appears twice: as a summary figure ("davon eingehend") and as
    # the direction of one connection ("eingehend"). The hint after the bar
    # keeps the two apart.
    "eingehend|richtung": "eingehend",
    "outbound|richtung": "ausgehend",
    "Identifier": "Kennung",
    "Services": "Dienste",
    "Connected for": "Verbunden seit",
    "Response right now": "Antwort gerade",
    "Received": "Empfangen",
    "Sent": "Gesendet",
    " d|kurz": " T",
    " h|kurz": " Std",
    " min|kurz": " Min",
    "{x} ago|kurz": "vor {x}",

    # -- Page frame ----------------------------------------------------------
    "newest first · every {n} s": "neueste oben · alle {n} s",
    "Temperature over the last hour": "Temperaturverlauf der letzten Stunde",
    "History of the last {span}": "Verlauf der letzten {span}",
    "no data yet": "noch keine Daten",
    "read-only access · data every {n} s, log every {m} s":
        "nur lesender Zugriff · Daten alle {n} s, Protokoll alle {m} s",
    "It will not be retried automatically. Look at "
    "<code>journalctl -u node-torwaechter -n 50</code>.":
        "Es wird nicht selbsttätig wiederholt. Nachsehen mit "
        "<code>journalctl -u node-torwaechter -n 50</code>.",
}

# ------------------------------------------------------ Tolerance window ----
# During the initial block download bitcoind blocks its RPC thread while it
# flushes the dbcache to disk. On a Pi that regularly takes longer than the
# timeout of a single call. The dashboard used to react by showing "node not
# reachable" and zeroing every figure, while the node kept running perfectly.
#
# Hence: the last successful state is kept. Only after several failures in a
# row is the node really considered gone. Until then the old numbers stay on
# screen with a quiet note about their age.
LAST_STATE = {}          # filled by the last successful pass
FAILURES_IN_ROW = 0

# Bitcoin Core's error code for "still starting up". It answers with HTTP 500
# and this code from the first second until the block index is loaded and the
# last blocks are verified — on a Pi that is regularly several minutes, and
# after a reindex much longer. It is not a fault and must never be shown as
# one.
RPC_IN_WARMUP = -28
# What the node last said while starting up ("Verifying blocks…"). A list so
# that one_pass can fill it without a global declaration.
WARMUP_MESSAGE = [""]

# Methods the node has refused (HTTP 403 because of rpcwhitelist), with the
# time of the refusal. Without this memory the dashboard would ask again every
# 30 seconds and bitcoind would write "RPC User dashboard not allowed to call
# method …" into the log every time — into the very display standing next to
# it.
DENIED = {}
DENIED_RETRY_AFTER = 1800      # try again after half an hour

# Series for rate and remaining time. Lives only in the memory of the running
# service — after a restart the estimate starts over.
PROGRESS = []
PROGRESS_MAX = 120       # at a 30 s step: roughly one hour of history
PROGRESS_MIN_GAP = 300   # no estimate before 5 minutes of distance

# Second, coarser series for the progress curve: one point every five
# minutes, 144 points — the last twelve hours.
PROGRESS_LONG = []
PROGRESS_LONG_STEP = 300
PROGRESS_LONG_MAX = 144

# Temperature history: one sample per minute, 120 points — the last hour.
TEMP_HISTORY = []
TEMP_STEP = 30
TEMP_KEEP = 120
# A fixed scale instead of one that adapts: a calm line at 50 degrees should
# also look calm. With a growing scale every bit of noise would look like a
# spike.
TEMP_LOW, TEMP_HIGH = 30.0, 90.0

HALVING_INTERVAL = 210_000   # the reward halves every 210,000 blocks
RETARGET_INTERVAL = 2016     # difficulty is adjusted every 2016 blocks

# Per-block figures for the 24 hour graphs. Bitcoin Core computes the sums
# itself (getblockstats) — that saves us reading 144 blocks worth hundreds of
# megabytes.
BLOCK_DATA = []      # (height, time, output_sat, fee_sat_vb, count)
BLOCK_KEEP = 144     # roughly 24 hours

# Difficulty of the recent adjustments. Read once from old block headers and
# only appended to afterwards — it changes only every two weeks.
DIFFICULTY = []      # (height, value)
DIFFICULTY_KEEP = 16 # about half a year


def record_long_progress(progress_fraction):
    """Keep a coarse history for the curve."""
    now = time.time()
    if not PROGRESS_LONG or now - PROGRESS_LONG[-1][0] >= PROGRESS_LONG_STEP:
        PROGRESS_LONG.append((now, progress_fraction))
        del PROGRESS_LONG[:-PROGRESS_LONG_MAX]


def record_temperature(celsius):
    """Keep the temperature history, at most one point per minute."""
    now = time.time()
    if not TEMP_HISTORY or now - TEMP_HISTORY[-1][0] >= TEMP_STEP:
        TEMP_HISTORY.append((now, celsius))
        del TEMP_HISTORY[:-TEMP_KEEP]


def temperature_colour(celsius):
    """Green up to 60, yellow up to 75, red above that."""
    if celsius is None:
        return "var(--leise)"
    if celsius >= 75:
        return "var(--error)"
    if celsius >= 60:
        return "var(--warn)"
    return "var(--akzent)"


def build_temperature_curve(width=260, height=34):
    """Small curve of the last hour, tinted by the current value."""
    if len(TEMP_HISTORY) < 2:
        return None
    values = [c for _, c in TEMP_HISTORY]
    colour = temperature_colour(values[-1])
    margin = 2
    inner_w, inner_h = width - 2 * margin, height - 2 * margin
    span = TEMP_HIGH - TEMP_LOW

    points = []
    for i, c in enumerate(values):
        x = margin + (i / max(1, len(values) - 1)) * inner_w
        fraction = min(1.0, max(0.0, (c - TEMP_LOW) / span))
        y = margin + (1 - fraction) * inner_h
        points.append(f"{x:.1f},{y:.1f}")
    line = " ".join(points)
    area = f"{margin},{height - margin} {line} {width - margin},{height - margin}"

    return (f'<svg class=minikurve viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(t("Temperature over the last hour"))}">'
            f'<polygon points="{area}" fill="{colour}" opacity=".13"/>'
            f'<polyline points="{line}" fill="none" stroke="{colour}" '
            f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
            f"</svg>")


def build_bar(fraction, level="", height=6):
    """Horizontal fill bar, fraction between 0 and 1.

    SVG on purpose instead of a <div style="width:…">: the Content Security
    Policy reads 'style-src self' without 'unsafe-inline', and that applies to
    style attributes in the markup as well. A width given as an inline style
    would be dropped by the browser — the bar would then always show full.
    That is exactly what happened on 2026-08-23. Width and colour in SVG are
    presentation attributes and are not affected.
    """
    width = min(100.0, max(0.0, fraction * 100))
    cls = f"balkenfuellung {level}".strip()

    # The rounded corners come from CSS on the wrapping element, not from
    # 'rx' on the rectangle: the SVG is stretched many times over by
    # preserveAspectRatio="none", and an 'rx' would be stretched with it. At
    # small fractions the radius then exceeds the fill itself and the bar
    # turns into a blob. That was on screen on 2026-08-23.
    return (f'<span class="balken hoch{height}">'
            f'<svg viewBox="0 0 100 {height}" preserveAspectRatio="none" '
            f'role="img" aria-label="{width:.0f} %">'
            f'<rect width="{width:.2f}" height="{height}" class="{cls}"/>'
            f"</svg></span>")


def build_columns(values, colour="var(--akzent)", label="", width=260, height=38):
    """Small column chart. The scale always starts at zero."""
    values = [w for w in values if w is not None]
    if len(values) < 2:
        return None
    highest = max(values) or 1
    count = len(values)
    gap = 260 / count * 0.18
    column = (width - (count - 1) * gap) / count

    parts = []
    for i, w in enumerate(values):
        h = max(0.8, (w / highest) * height)
        x = i * (column + gap)
        parts.append(f'<rect x="{x:.2f}" y="{height - h:.2f}" '
                     f'width="{column:.2f}" height="{h:.2f}" fill="{colour}"/>')
    return (f'<svg class=minikurve viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(label)}">{"".join(parts)}</svg>')


def build_line(values, colour="var(--akzent)", label="",
               width=260, height=38, ab_null=True):
    """Small line chart with a filled area underneath."""
    values = [w for w in values if w is not None]
    if len(values) < 3:
        return None
    oben = max(values)
    unten = 0.0 if ab_null else min(values)
    span = (oben - unten) or 1
    margin = 1.5
    inner_w, inner_h = width - 2 * margin, height - 2 * margin

    points = []
    for i, w in enumerate(values):
        x = margin + (i / max(1, len(values) - 1)) * inner_w
        y = margin + (1 - (w - unten) / span) * inner_h
        points.append(f"{x:.1f},{y:.1f}")
    line = " ".join(points)
    area = f"{margin},{height - margin} {line} {width - margin},{height - margin}"
    return (f'<svg class=minikurve viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(label)}">'
            f'<polygon points="{area}" fill="{colour}" opacity=".13"/>'
            f'<polyline points="{line}" fill="none" stroke="{colour}" '
            f'stroke-width="1.5" stroke-linejoin="round"/></svg>')


def build_skeleton(label="", width=260, height=38, columns=False):
    """A visibly empty skeleton where a graph will later appear.

    Explicitly NO invented sample data: on a display that reports the state of
    a node an invented curve is dangerous — on the next screenshot nobody can
    tell any more what was measured and what was drawn. The skeleton shows the
    shape, not values.
    """
    margin = 1.5
    if columns:
        count = 24
        gap = width / count * 0.22
        bar = (width - (count - 1) * gap) / count
        content = "".join(
            f'<rect x="{i * (bar + gap):.2f}" y="{height * 0.55:.1f}" '
            f'width="{bar:.2f}" height="{height * 0.45:.1f}" class="geruestteil"/>'
            for i in range(count)
        )
    else:
        centre = height / 2
        content = (f'<line x1="{margin}" y1="{centre:.1f}" '
                  f'x2="{width - margin}" y2="{centre:.1f}" class="geruestlinie"/>')

    return (f'<svg class="minikurve geruest" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(label or t("no data yet"))}">'
            f"{content}</svg>")


def placeholder_card(labels, grafiktitel, note, columns=False):
    """A complete karte carrying a geruest instead of values."""
    fields = [(b, "—", "leer") for b in labels]
    fields.append((grafiktitel, build_skeleton(grafiktitel, columns=columns),
                   "grafik"))
    return fields, note


def format_btc(satoshi):
    btc = satoshi / 100_000_000
    if btc >= 1000:
        return format_number(round(btc)) + " BTC"
    return decimal_sep(f"{btc:,.2f}") + " BTC"


def fetch_block_data(cfg, tip):
    """Fetch missing per-block figures and keep the last 144.

    The first pass loads 144 blocks, after that only the newest one. Bitcoin
    Core computes the figures itself; we explicitly ask for only the four
    fields we need.
    """
    if not tip:
        return
    present = {h for h, *_ in BLOCK_DATA}
    start = max(1, tip - BLOCK_KEEP + 1)
    missing = [h for h in range(start, tip + 1) if h not in present]
    if not missing:
        return

    # The very first pass can take a few seconds; after that it is at most
    # one block per cycle.
    for height in missing[-BLOCK_KEEP:]:
        try:
            st = rpc(cfg, "getblockstats",
                     [height, ["height", "time", "total_out", "txs",
                              "feerate_percentiles"]])
        except RpcError:
            return          # not allowed or block missing — stop quietly
        percentiles = st.get("feerate_percentiles") or [0, 0, 0, 0, 0]
        BLOCK_DATA.append((
            st.get("height", height),
            st.get("time", 0),
            st.get("total_out", 0),
            percentiles[2],              # median fee in sat/vB
            st.get("txs", 0),
        ))

    BLOCK_DATA.sort(key=lambda e: e[0])
    del BLOCK_DATA[:-BLOCK_KEEP]


def fetch_difficulty(cfg, tip):
    """Read the difficulty of recent adjustments from old block headers.

    Needed only once: the values never change again, and a new adjustment
    arrives only after roughly two weeks.
    """
    if not tip:
        return
    last = (tip // RETARGET_INTERVAL) * RETARGET_INTERVAL
    present = {h for h, _ in DIFFICULTY}
    wanted = [last - i * RETARGET_INTERVAL for i in range(DIFFICULTY_KEEP)]
    missing = [h for h in wanted if h > 0 and h not in present]
    if not missing:
        return

    for height in sorted(missing):
        try:
            block_id = rpc(cfg, "getblockhash", [height])
            header = rpc(cfg, "getblockheader", [block_id])
        except RpcError:
            return
        DIFFICULTY.append((height, float(header.get("difficulty", 0))))

    DIFFICULTY.sort(key=lambda e: e[0])
    del DIFFICULTY[:-DIFFICULTY_KEEP]


def estimate_remaining(progress_fraction):
    """Estimate the remaining time from the growth of verification progress.

    Deliberately not from the block count: early blocks are nearly empty and
    fly past, later ones are full. 'verificationprogress' already weights this
    by the work involved and therefore gives more usable numbers.
    """
    now = time.time()
    PROGRESS.append((now, progress_fraction))
    del PROGRESS[:-PROGRESS_MAX]

    # find the oldest sample that lies far enough back
    base = None
    for ts, value in PROGRESS:
        if now - ts >= PROGRESS_MIN_GAP:
            base = (ts, value)
        else:
            break
    if base is None:
        return None, None

    d_time = now - base[0]
    d_progress = progress_fraction - base[1]
    if d_time <= 0 or d_progress <= 0:
        return None, None

    per_hour = d_progress / d_time * 3600 * 100      # percentage points per hour
    remaining = (1.0 - progress_fraction) / (d_progress / d_time)
    return per_hour, remaining


# ============================================================== Konfiguration
def read_config(path):
    """Read a plain KEY=VALUE file. Lines starting with # are comments."""
    values = {
        "RPC_HOST": "127.0.0.1",
        "RPC_PORT": "8332",
        "RPC_USER": "dashboard",
        "RPC_PASSWORD": "",
        "OUT_DIR": "/var/www/node",
        "DATA_DIR": "/mnt/bitcoin/bitcoin",
        "ELECTRS_PORT": "50001",
        "INTERVAL": "30",
        # Display language of the page: de or en. Affects only what appears
        # in the browser — log lines come from the node and stay as they are.
        "LANGUAGE": "de",
        "LOG_SERVICES": "bitcoind",
        # The log fills the right column all the way down and scrolls inside
        # it. More lines cost nothing but scrollback — journalctl does not
        # take longer for 150 than for 40.
        "LOG_LINES": "150",
        "LOG_INTERVAL": "5",
        # Timeout per RPC call. 45 s instead of 15: bitcoind stalls its RPC
        # thread while it writes the dbcache.
        "RPC_TIMEOUT": "45",
        # This many failures in a row before the node counts as gone.
        # 3 x 30 s = ninety seconds of silence, only then the red card.
        "TOLERANCE": "3",
        # Maximum number of dots in the network map. More is unreadable.
        "PEERS_MAX": "64",
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            for row in f:
                row = row.strip()
                if not row or row.startswith("#") or "=" not in row:
                    continue
                key, _, value = row.partition("=")
                values[key.strip()] = value.strip().strip("'\"")
    except FileNotFoundError:
        print(f"Configuration not found: {path}", file=sys.stderr)
        sys.exit(1)
    # The language must be set before anything is formatted: it decides not
    # only the words but also comma versus period in every number on the
    # page.
    set_language(values.get("LANGUAGE", "de"))
    return values


# ======================================================================== RPC
class RpcError(Exception):
    pass


def rpc(cfg, method, params=None):
    """Call a JSON-RPC method on Bitcoin Core."""
    # What the node has already refused is not asked again for a while. The
    # whitelist can change (06-tor.sh extends it), so not forever.
    refused = DENIED.get(method)
    if refused is not None:
        if time.time() - refused < DENIED_RETRY_AFTER:
            raise RpcError(f"HTTP 403 on {method} (not allowed)")
        del DENIED[method]

    url = f"http://{cfg['RPC_HOST']}:{cfg['RPC_PORT']}/"
    body = json.dumps(
        {"jsonrpc": "1.0", "id": "dashboard", "method": method, "params": params or []}
    ).encode()

    auth = base64.b64encode(
        f"{cfg['RPC_USER']}:{cfg['RPC_PASSWORD']}".encode()
    ).decode()

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        },
    )
    try:
        limit_value = max(5, int(cfg.get("RPC_TIMEOUT", 45)))
    except (TypeError, ValueError):
        limit_value = 45

    try:
        with urllib.request.urlopen(request, timeout=limit_value) as response:
            # Bounded: the largest answer we ever ask for (getpeerinfo with
            # 64 peers) is well under a megabyte.
            result = json.loads(response.read(16_000_000).decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            DENIED[method] = time.time()
            raise RpcError(f"HTTP 403 on {method} (not allowed)") from e
        # Bitcoin Core answers HTTP 500 while it is still starting up, and the
        # body says exactly what it is doing: {"error":{"code":-28,"message":
        # "Verifying blocks…"}}. Throwing the body away turned every restart
        # into a red "not reachable" card plus a wrong hint about the
        # rpcwhitelist (2026-08-24, right after the dbcache change). The
        # message costs one read and is the most useful line on the screen.
        detail = ""
        try:
            body = json.loads(e.read().decode())
            inner = body.get("error") or {}
            code, message = inner.get("code"), inner.get("message", "")
            if code == RPC_IN_WARMUP:
                raise RpcError(f"Node in warmup on {method}: {message}") from e
            if message:
                detail = f" ({message})"
        except RpcError:
            raise
        except (ValueError, OSError, AttributeError):
            pass
        raise RpcError(f"HTTP {e.code} on {method}{detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RpcError(f"Node not reachable: {e}") from e
    except json.JSONDecodeError as e:
        raise RpcError(f"Unreadable answer from {method}") from e

    if not isinstance(result, dict) or "result" not in result:
        raise RpcError(f"Unreadable answer from {method}")
    if result.get("error"):
        raise RpcError(f"{method}: {result['error']}")
    return result["result"]


# ================================================================= Helpers ==
# Numbers are the part of a translation that is easiest to overlook and
# quickest to get wrong: German writes 1.234.567,8 — English 1,234,567.8.
# Period and comma swap roles, both of them. That is why EVERY number on the
# page goes through one of these functions; nowhere is there a bare
# f"{value:.1f}". The test run checks both notations.
def decimal_sep(text):
    """Turn English notation into German where needed.

    The detour through \\x00 is not decoration: a direct
    .replace(",", ".").replace(".", ",") would turn the periods it has just
    written straight back into commas, making 1,234.5 into 1,234,5.
    """
    if LANGUAGE == "en":
        return text
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_number(n):
    """1234567 -> 1.234.567 (de) or 1,234,567 (en)."""
    return decimal_sep(f"{int(n):,}")


def format_decimal(value, digits=1):
    """A decimal number with thousands separators in the current notation."""
    return decimal_sep(f"{value:,.{digits}f}")


def format_bytes(count):
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(count)
    i = 0
    while value >= 1000 and i < len(units) - 1:
        value /= 1000
        i += 1
    return f"{decimal_sep(f'{value:.1f}')} {units[i]}"


def format_duration(seconds):
    seconds = int(seconds)
    days, remaining = divmod(seconds, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes = remaining // 60
    if days:
        return t("{n} d {h} h", n=days, h=hours)
    if hours:
        return t("{n} h {m} min", n=hours, m=minutes)
    return t("{n} min", n=minutes)


def format_magnitude(number):
    """126000000000000 -> 126.0 T — for the network difficulty.

    The prefixes k/M/G/T are the same in both languages and are therefore not
    translated.
    """
    for limit_value, suffix in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k")):
        if number >= limit_value:
            return f"{decimal_sep(f'{number / limit_value:.1f}')} {suffix}"
    return f"{number:.0f}"


def format_age(seconds):
    """How long ago was that? Short and without decimals."""
    seconds = int(seconds)
    if seconds < 90:
        return t("{n} s ago", n=seconds)
    if seconds < 5400:
        return t("{n} min ago", n=seconds // 60)
    if seconds < 172800:
        return t("{n} h ago", n=seconds // 3600)
    return t("{n} d ago", n=seconds // 86400)


def halving_facts(height):
    """Reward, blocks to the next halving, estimated date.

    Computed from the headers, not from the validated blocks: the headers sit
    on the real chain tip within minutes, while the blocks lag far behind
    during the initial sync.
    """
    epoch = height // HALVING_INTERVAL
    reward = 50.0 / (2 ** epoch)
    next_height = (epoch + 1) * HALVING_INTERVAL
    remaining = next_height - height
    # One block every ten minutes on average
    date = datetime.now(timezone.utc).astimezone().timestamp() + remaining * 600
    return reward, next_height, remaining, datetime.fromtimestamp(date)


def build_progress_curve(width=300, height=54):
    """Draw the sync progress as a small SVG curve.

    Computed by hand on purpose instead of pulling in a charting library: it
    is a handful of coordinates, and the page stays free of JavaScript.
    """
    if len(PROGRESS_LONG) < 3:
        return None

    times = [z for z, _ in PROGRESS_LONG]
    values = [w for _, w in PROGRESS_LONG]
    t0, t1 = times[0], times[-1]
    w0, w1 = min(values), max(values)
    if t1 <= t0 or w1 <= w0:
        return None

    margin = 3
    inner_w = width - 2 * margin
    inner_h = height - 2 * margin

    points = []
    for z, w in PROGRESS_LONG:
        x = margin + (z - t0) / (t1 - t0) * inner_w
        y = margin + (1 - (w - w0) / (w1 - w0)) * inner_h
        points.append(f"{x:.1f},{y:.1f}")

    line = " ".join(points)
    area = f"{margin},{height - margin} {line} {width - margin},{height - margin}"
    span = format_duration(t1 - t0)
    zuwachs = (w1 - w0) * 100

    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'role="img" aria-label="{html_escape(t("History of the last {span}", span=span))}">'
        f'<polygon points="{area}" fill="var(--balken)" opacity=".14"/>'
        f'<polyline points="{line}" fill="none" stroke="var(--balken)" '
        f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
        f"</svg>",
        decimal_sep(f"{span} · +{zuwachs:.2f} %-Punkte"),
    )


def read_file(path, standard=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return standard


CPU_LAST = None      # (busy, total) jiffies from the previous pass


def _cpu_sample():
    """(busy, total) jiffies from the first line of /proc/stat."""
    raw = read_file("/proc/stat", "") or ""
    first = raw.split("\n", 1)[0].split()
    if len(first) < 5 or first[0] != "cpu":
        return None
    values = [int(v) for v in first[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values) - idle, sum(values)


def cpu_percent():
    """CPU use since the previous pass, from the first line of /proc/stat.

    The first call has nothing to compare against and takes a second sample
    a quarter of a second later — once, at startup. Later calls measure the
    whole 30-second interval, which is what the page should show anyway.
    """
    global CPU_LAST
    now = _cpu_sample()
    if now is None:
        return None
    if CPU_LAST is None:
        time.sleep(0.25)
        CPU_LAST, now = now, _cpu_sample()
        if now is None:
            return None
    busy = now[0] - CPU_LAST[0]
    total = now[1] - CPU_LAST[1]
    CPU_LAST = now
    if total <= 0:
        return None
    return min(100.0, 100.0 * busy / total)


def service_running(name):
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", name], timeout=5, check=False
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def port_open(host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


# ==================================================================== Sammeln
HWMON_DIR = "/sys/class/hwmon"


def undervoltage_alarm():
    """1 or 0 from the rpi_volt hwmon driver, None when there is none."""
    try:
        entries = os.listdir(HWMON_DIR)
    except OSError:
        return None
    for entry in entries:
        base = os.path.join(HWMON_DIR, entry)
        if (read_file(os.path.join(base, "name")) or "") != "rpi_volt":
            continue
        raw = read_file(os.path.join(base, "in0_lcrit_alarm"))
        if raw and raw.strip() in ("0", "1"):
            return int(raw.strip())
    return None


def collect_system(cfg):
    """State of the machine itself — regardless of whether the node runs."""
    fields = []

    raw = read_file("/sys/class/thermal/thermal_zone0/temp")
    if raw and raw.isdigit():
        temp = int(raw) / 1000
        record_temperature(temp)
        fields.append((t("Temperature"), decimal_sep(f"{temp:.1f} °C"),
                       "warn" if temp >= 75 else ""))
        # After a service restart it takes a minute until two samples
        # exist. Until then the skeleton stands here so the card does not
        # change its height.
        fields.append((t("last hour"),
                       build_temperature_curve()
                       or build_skeleton(t("Temperature history, still measuring")),
                       "grafik"))

    # CPU use as a percentage, measured between two passes. The load average
    # shown before ("0.02 on 4 cores") is a queue length, and nobody reading
    # the page knows what that is — the number needs a unit (2026-09-01).
    cpu = cpu_percent()
    if cpu is not None:
        fields.append((t("Load"), decimal_sep(f"{cpu:.0f} % CPU"),
                       "warn" if cpu >= 85 else ""))

    meminfo = read_file("/proc/meminfo", "") or ""
    total = available = 0
    for row in meminfo.splitlines():
        if row.startswith("MemTotal:"):
            total = int(row.split()[1]) * 1024
        elif row.startswith("MemAvailable:"):
            available = int(row.split()[1]) * 1024
    if total:
        used = total - available
        fields.append(
            (t("Memory"),
             t("{used} of {total}", used=format_bytes(used),
               total=format_bytes(total)),
             "warn" if used / total > 0.92 else "")
        )

    try:
        s = os.statvfs(cfg["DATA_DIR"])
        free = s.f_bavail * s.f_frsize
        gesamt_platz = s.f_blocks * s.f_frsize
        anteil_frei = free / gesamt_platz if gesamt_platz else 0
        used_fraction = 1 - anteil_frei
        if used_fraction >= 0.95:
            bar_level, level = "error", "warn"
        elif used_fraction >= 0.88:
            bar_level, level = "warn", "warn"
        else:
            bar_level, level = "", ""
        fields.append(
            (t("Disk space"),
             t("{pct} % used, {free} free", pct=f"{used_fraction * 100:.0f}",
               free=format_bytes(free)),
             level)
        )
        fields.append(("", build_bar(used_fraction, bar_level), "grafik"))
    except OSError:
        pass

    uptime = read_file("/proc/uptime")
    if uptime:
        fields.append((t("Pi up for"),
                       format_duration(float(uptime.split()[0])), ""))

    # Undervoltage is the most common cause of corrupted blockchain data.
    #
    # Read from sysfs first: 'vcgencmd' talks to the firmware through
    # /dev/vchiq, and the service runs with PrivateDevices=true, which hides
    # that device. So the call failed silently on the Pi from the first day
    # and the row was simply missing — the mock answers the call, the test
    # never noticed. Found on 2026-09-01 by comparing a screenshot with the
    # test page. The sysfs file is exposed by the firmware driver and only
    # needs read access (2026-09-01).
    value = None
    raw = read_file("/sys/devices/platform/soc/soc:firmware/get_throttled")
    if raw:
        try:
            value = int(raw.strip().lower().removeprefix("0x"), 16)
        except ValueError:
            value = None
    # A current Pi 4 kernel (seen 2026-09-01) has no such file. It has the hwmon
    # driver 'rpi_volt' instead, whose in0_lcrit_alarm is 1 while the
    # supply is below threshold — "undervoltage now", the same bit 0x1
    # vcgencmd reports, without the "since boot" history.
    if value is None:
        alarm = undervoltage_alarm()
        if alarm is not None:
            value = 0x1 if alarm else 0
    if value is None:
        try:
            r = subprocess.run(
                ["vcgencmd", "get_throttled"], capture_output=True, text=True,
                timeout=5, check=False)
            if r.returncode == 0 and "=" in r.stdout:
                value = int(r.stdout.strip().split("=")[1], 16)
        except (OSError, ValueError, subprocess.SubprocessError):
            value = None
    try:
        if value is not None:
            if value == 0:
                fields.append((t("Power supply"), t("stable"), "gut"))
            else:
                notes = []
                if value & 0x1:
                    notes.append(t("UNDERVOLTAGE NOW"))
                if value & 0x40000:
                    notes.append(t("undervoltage since boot"))
                if value & 0x4:
                    notes.append(t("throttled"))
                fields.append((t("Power supply"),
                               ", ".join(notes) or t("irregular"), "warn"))
    except (TypeError, ValueError):
        pass

    # The card title stays ENGLISH inside the tuple. It is the identity of
    # the card: CARDS_WIDE, CARDS_FULL and CARD_ORDER look it up. It is
    # translated only in render_card. Field labels are pure display and get
    # translated right here.
    return ("System", fields)


def collect_node(cfg):
    """Query Bitcoin Core. Uses only allowed, read-only methods."""
    chain = rpc(cfg, "getblockchaininfo")
    net = rpc(cfg, "getnetworkinfo")
    mempool = rpc(cfg, "getmempoolinfo")
    verbindungen = rpc(cfg, "getconnectioncount")
    laufzeit = rpc(cfg, "uptime")

    progress = float(chain.get("verificationprogress", 0)) * 100
    progress = min(progress, 100.0)
    in_sync = progress >= 99.999 and not chain.get("initialblockdownload", False)

    # "blocks" and "headers" are Bitcoin Core's field names and must stay
    # English. On 2026-08-23 the rename to English ran over these two strings
    # as well, so both reads missed and returned 0. The page then showed block
    # height 0, a block reward of 50 BTC and the next halving at 210,000 —
    # numbers from the genesis block, presented as measurements. Nothing
    # crashed, and the mock answers correctly, so no test noticed.
    blocks = chain.get("blocks", 0)
    headers = chain.get("headers", 0)
    behind = max(0, headers - blocks)

    # 'Blockchain' is no longer a card of its own. The figures live in the
    # metrics bar and in the state bar at the top — a card repeating them a
    # third time is just filler. What is needed goes into the summary as a
    # single value.
    block_time = chain.get("time")
    state_text = None
    if block_time:
        age = time.time() - float(block_time)
        # During the initial sync this is a block from 2010, not the chain
        # tip — "9 years ago" would be misleading here. The date format
        # belongs to the language: 23.08.2026 versus 2026-08-23. The American
        # form 08/23/2026 would be the worst choice, being indistinguishable
        # from 23.08. as long as the day is below 13.
        pattern = "%d.%m.%Y" if LANGUAGE == "de" else "%Y-%m-%d"
        state_text = (format_age(age) if in_sync else
                      t("verified through {date}",
                        date=datetime.fromtimestamp(float(block_time)).strftime(pattern)))

    fraction = float(chain.get("verificationprogress", 0))
    record_long_progress(fraction)

    # Only once the chain is up to date — during the initial sync the figures
    # would be from 2010 and fetching them a pure waste.
    if in_sync:
        fetch_difficulty(cfg, headers)
        fetch_block_data(cfg, blocks)

    # Rate and remaining time are shown large in the state bar above.
    rate_text = eta_text = None
    if not in_sync:
        per_hour, seconds_left = estimate_remaining(fraction)
        if per_hour is not None:
            rate_text = t("{n} pp/h", n=decimal_sep(f"{per_hour:.2f}"))
            eta_text = format_duration(seconds_left)

    # --- Network facts: halving and difficulty -------------------------------
    reward, next_height, blocks_left, when = halving_facts(headers)
    # Since 3.3 this is the right-hand column of the 'Network' card, next to
    # the mempool. Halving on one line — height and month together — so
    # both columns come out the same height (2026-09-01).
    chain_fields = [
        (t("Chain"), "", "spalte"),
        (t("Block reward"), decimal_sep(f"{reward:.3f} BTC"), ""),
        # Short on purpose: "Nächste Halbierung · bei 1.050.000 · 04/2028"
        # wrapped in the inner column and pushed the chain side one row
        # below the mempool side (seen on the Pi, 2026-09-01).
        (t("Halving"),
         f"{format_number(next_height)} · {when.strftime('%m/%Y')}", ""),
        (t("remaining"), t("{n} bloecke", n=format_number(blocks_left)), ""),
        (t("Difficulty"), format_magnitude(float(chain.get("difficulty", 0))), ""),
    ]

    # The count to the next adjustment is always known — it depends only on
    # the header height, not on the history buffer.
    retarget_left = RETARGET_INTERVAL - (headers % RETARGET_INTERVAL)
    chain_fields.append(
        (t("next adjustment"), t("in {n} blocks|dativ", n=format_number(retarget_left)), ""))

    values = [w for _, w in DIFFICULTY]
    if len(values) < 2:
        # The history is fetched only after the sync has finished.
        chain_fields.append((t("last adjustment"), "—", "leer"))
        chain_fields.append(
            (t("last adjustments"),
             build_skeleton(t("Difficulty of the last adjustments"), columns=True),
             "grafik"))
    else:
        change = (values[-1] / values[-2] - 1) * 100 if values[-2] else 0
        chain_fields.append(
            (t("last adjustment"),
             decimal_sep(f"{change:+.1f} %"),
             "warn" if abs(change) > 8 else "")
        )
        columns = build_columns(
            values, "var(--leise)",
            t("Difficulty of the last {n} adjustments", n=len(values)))
        if columns:
            chain_fields.append(
                (t("last {n} adjustments", n=len(values)), columns, "grafik"))

    net_fields = [
        (t("Connections"), str(verbindungen),
         "warn" if int(verbindungen) < 8 else ""),
        (t("eingehend"), str(net.get("connections_in", "?")), ""),
        (t("outbound"), str(net.get("connections_out", "?")), ""),
        (t("Version"), net.get("subversion", "?").strip("/"), ""),
        (t("Node up for"), format_duration(laufzeit), ""),
    ]

    usage = int(mempool.get("usage", 0) or 0)
    max_usage = int(mempool.get("maxmempool", 0) or 0)
    mempool_fields = [
        (t("Mempool"), "", "spalte"),
        (t("Transactions"), format_number(mempool.get("size", 0)), ""),
        (t("Memory use"),
         (t("{used} of {total}", used=format_bytes(usage), total=format_bytes(max_usage))
          if max_usage else format_bytes(usage)),
         ""),
        (t("Minimum fee"),
         decimal_sep(f"{mempool.get('mempoolminfee', 0) * 100000:.1f} sat/vB"), ""),
    ]

    # Fee estimates only once the chain is up to date. During the sync Core
    # reliably answers "no data" — three calls for an answer we already know.
    # At twelve seconds per call that is a third of the whole cycle.
    fee_fields = []
    # Raw estimates in sat/vB, keyed by target — the metrics bar shows the
    # first one large. Kept apart from the display fields so nobody has to
    # parse "4,1 sat/vB" back into a number (2026-09-01).
    fee_rates = {}
    if in_sync:
        for target, label in ((1, "next block"), (6, "in ~1 hour"),
                                  (24, "in ~4 hours")):
            try:
                response = rpc(cfg, "estimatesmartfee", [target])
            except RpcError:
                break
            rate = response.get("feerate")
            if rate:
                fee_rates[target] = float(rate) * 100000
                fee_fields.append(
                    (t(label),
                     decimal_sep(f"{fee_rates[target]:.1f} sat/vB"), "")
                )
    if not fee_fields:
        fee_fields = [
            (t("Estimate"), t("not available during sync"), "leer")]
    # How full the mempool is against maxmempool — once it fills up, the
    # minimum fee rises and cheap transactions are dropped. Yellow from 80 %.
    if max_usage:
        fill = min(1.0, usage / max_usage)
        fee_fields.append((t("fill level"),
                           build_bar(fill, "warn" if fill >= 0.8 else ""),
                           "grafik"))

    summary = {
        "bloecke": blocks,
        "kopfzeilen": headers,
        "blockalter": (time.time() - float(block_time)) if (block_time and in_sync) else None,
        "verbindungen": int(verbindungen),
        "mempool": int(mempool.get("size", 0)),
        "gebuehren": fee_rates,
        # The median fee of the most recent block, from getblockstats. This
        # is what the tile shows large: what actually got in last time, not
        # what Core guesses for next time (2026-09-01).
        "median_gebuehr": (BLOCK_DATA[-1][3] if in_sync and BLOCK_DATA else None),
        "rueckstand": behind,
        "belegt": chain.get("size_on_disk", 0),
        "tempo": rate_text,
        "restzeit": eta_text,
        "stand": state_text,
        "gepruned": bool(chain.get("pruned")),
        "version": str(net.get("subversion", "")).strip("/"),
        "laufzeit": laufzeit,
        # Fallback for the 'Connected nodes' card while getpeerinfo is not
        # allowed. Without it the connection figures would be nowhere to be
        # seen until then.
        "netzfelder": net_fields,
    }

    # --- 24-Stunden-Grafiken -----------------------------------------------
    # The cards are present from the start so the layout is complete. While
    # the chain is not up to date they carry a skeleton and dashes instead of
    # numbers — nothing that could be mistaken for a measurement.
    waiting_note = t("Appears once the chain is up to date. Until then the "
                     "most recent bloecke are years old and would say nothing.")
    volume_fields, volume_note = placeholder_card(
        [t("Total"), t("Transactions"), t("Blocks")],
        t("Volume per block"), waiting_note, columns=True)
    fee_fields_24, fee_note = placeholder_card(
        [t("latest"), t("24 h average"), t("Range")],
        t("average fee per block"), waiting_note)

    if in_sync and len(BLOCK_DATA) >= 3:
        volume_note = fee_note = ""
        period = (BLOCK_DATA[-1][1] - BLOCK_DATA[0][1]) or 1
        hours = period / 3600
        outputs = [e[2] for e in BLOCK_DATA]
        fees = [e[3] for e in BLOCK_DATA]
        counts = [e[4] for e in BLOCK_DATA]

        volume_fields = [
            (t("Total"), format_btc(sum(outputs)), ""),
            (t("Transactions"), format_number(sum(counts)), ""),
            (t("Blocks"),
             t("{n} · {h} h", n=len(BLOCK_DATA), h=f"{hours:.0f}"), ""),
            (t("Volume per block"),
             build_columns(outputs, "var(--akzent)",
                          t("Volume moved per block over the last 24 hours")),
             "grafik"),
        ]

        latest_fee = fees[-1] if fees else 0
        known = [g for g in fees if g]
        volume_fields = [f for f in volume_fields if f[1]]
        fee_fields_24 = [
            (t("latest"), decimal_sep(f"{latest_fee:.1f} sat/vB"), ""),
            (t("24 h average"),
             decimal_sep(f"{(sum(known) / len(known) if known else 0):.1f} sat/vB"), ""),
            (t("Range"),
             t("{von} to {bis} sat/vB",
               von=f"{min(known) if known else 0:.0f}",
               bis=f"{max(known) if known else 0:.0f}"), ""),
            (t("average fee per block"),
             build_line(fees, "var(--warn)",
                        t("Average fee per block over the last 24 hours")),
             "grafik"),
        ]
        fee_fields_24 = [f for f in fee_fields_24 if f[1]]

    # 'Network' is no longer a card of its own either — the connections live
    # in 'Connected nodes', version and uptime in the page header.
    # One card 'Network' with two inner columns — mempool left, chain right
    # — instead of two narrow cards. Together with 'System' that makes two
    # equal cards in the row (2026-09-01).
    groups = [
        ("Network", mempool_fields + fee_fields + chain_fields),
        ("Volume · 24 hours", volume_fields, volume_note),
        ("Fee history · 24 hours", fee_fields_24, fee_note),
    ]
    return progress, in_sync, groups, summary


# ============================================================ Network map ===
# Colours per network type. Deliberately only four — the eye cannot tell more
# apart in small dots anyway.
NETWORK_COLOURS = {
    "electrs": "var(--netz-electrs)",
    "ipv4": "var(--netz-ipv4)",
    "ipv6": "var(--netz-ipv6)",
    "onion": "var(--netz-onion)",
    "i2p": "var(--netz-i2p)",
    "cjdns": "var(--netz-i2p)",
}
def network_name(kind):
    """Display name of a network type.

    Apart from 'local' these are proper names and identical in both
    languages — hence no per-language table here, only the single value that
    does need translating.
    """
    if kind == "not_publicly_routable":
        return t("local")
    if kind == "electrs":
        return t("Electrum server")
    return {"ipv4": "IPv4", "ipv6": "IPv6", "onion": "Tor",
            "i2p": "I2P", "cjdns": "CJDNS"}.get(kind, kind)


def peer_network_label(p):
    """Network type of a single peer, with the direction where it matters.

    An inbound Tor peer carries no address of its own — it arrived through our
    onion service and shows up as 127.0.0.1. "Tor" alone next to that address
    reads like a contradiction; "Tor · inbound" explains it. Outbound peers
    keep the plain name, the direction is obvious from the onion address.
    """
    if p["netz"] == "onion" and p["eingehend"]:
        return t("Tor · inbound")
    if p["netz"] == "electrs":
        return t("Electrum · local")
    return network_name(p["netz"])


def shorten_address(url):
    """Onion addresses are 62 characters — too long for any label."""
    url = str(url)
    if len(url) <= 28:
        return url
    return url[:12] + "…" + url[-13:]


LAST_PEERS = []

# Which peer handed us each block, and whom we handed it on to. Keyed by
# Core's peer id, lives only in the running process like every other history.
#   von      blocks received from this peer, counted since start
#   von_zeit 'last_block' from getpeerinfo — absolute, so it is known at once
#   an       blocks sent to this peer, counted since start
#   an_zeit  when we last saw block bytes go out to it
# Core does not count blocks per peer; it only reports the time of the last
# one received and the bytes sent per message type. Both are compared with
# the previous pass, so a change means "one more" (2026-09-01).
BLOCK_TRAFFIC = {}
BLOCK_MESSAGES = ("cmpctblock", "block", "blocktxn")


def track_block_traffic(peers):
    """Update BLOCK_TRAFFIC from the raw peer list and attach the figures."""
    now = time.time()
    seen = set()
    for p in peers:
        pid = p["id"]
        seen.add(pid)
        entry = BLOCK_TRAFFIC.get(pid)
        if entry is None:
            entry = BLOCK_TRAFFIC[pid] = {
                "von": 0, "von_zeit": p["letzter_block"],
                "an": 0, "an_zeit": None, "_gesendet": p["_blockbytes"]}
        else:
            last = p["letzter_block"]
            if last and (entry["von_zeit"] is None or last > entry["von_zeit"]):
                entry["von"] += 1
                entry["von_zeit"] = last
            if p["_blockbytes"] > entry["_gesendet"]:
                entry["an"] += 1
                entry["an_zeit"] = now
                entry["_gesendet"] = p["_blockbytes"]
        p["bloecke_von"] = entry["von"]
        p["zuletzt_von"] = entry["von_zeit"]
        p["bloecke_an"] = entry["an"]
        p["zuletzt_an"] = entry["an_zeit"]
    # Gone peers are dropped: a reconnecting peer gets a new id anyway.
    for pid in [k for k in BLOCK_TRAFFIC if k not in seen]:
        del BLOCK_TRAFFIC[pid]


# Who announced each block first. Bitcoin Core logs one line per new block:
#   Saw new cmpctblock header hash=… height=965079 peer=311
# The peer that announces first is the one with the best view of the
# network — a more telling figure than who delivered the block, which on a
# Tor node is nearly always the same high-bandwidth peer (2026-09-01).
#   height -> (peer id, unix time of the announcement)
ANNOUNCED = {}
ANNOUNCED_KEEP = 24 * 3600
ANNOUNCE_LINE = re.compile(
    r"Saw new (?:cmpctblock )?header hash=\S+ height=(\d+)\S* .*?peer=(\d+)")
JOURNAL_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+\-]\d{2}:\d{2})")
ANNOUNCED_PRIMED = [False]

# Chain check. Besides its regular peers Core opens a short-lived
# block-relay-only connection every few minutes and asks a stranger for its
# height — the defence against an eclipse: if the stranger is ahead, the
# regular peers are holding something back. The line
#   New block-relay-only peer connected: … blocks=965079 peer=818
# is that sample. Kept for an hour: (time, peer id, height) (2026-09-01).
CHAIN_SAMPLES = []
CHAIN_SAMPLES_KEEP = 3600
SAMPLE_LINE = re.compile(
    r"New (?:block-relay-only|outbound-full-relay|feeler) peer connected: "
    r".*?blocks=(-?\d+) peer=(\d+)")


def collect_announcements(cfg):
    """Read the announcement lines and update ANNOUNCED.

    The first pass reads a day back so the ranking is complete right after
    a restart; later passes only need the newest lines. A height already
    known is not overwritten — the first announcement is the one that
    counts, and the journal is read oldest first.
    """
    service = "bitcoind"
    if not os.path.exists(f"/etc/systemd/system/{service}.service"):
        return
    if ANNOUNCED_PRIMED[0]:
        window = ["-n", "300"]
    else:
        window = ["--since", "-25h"]
    try:
        r = subprocess.run(
            ["journalctl", "-u", service, *window, "--no-pager",
             "--output=short-iso"],
            capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return
    if r.returncode != 0:
        return
    ANNOUNCED_PRIMED[0] = True
    seen = {(w, pid) for w, pid, _ in CHAIN_SAMPLES}
    for row in r.stdout.splitlines():
        stamp = JOURNAL_TIME.match(row)
        try:
            when = datetime.fromisoformat(stamp.group(1)).timestamp() if stamp else time.time()
        except ValueError:
            when = time.time()

        match = ANNOUNCE_LINE.search(row)
        if match:
            height, peer_id = int(match.group(1)), int(match.group(2))
            if height not in ANNOUNCED:
                ANNOUNCED[height] = (peer_id, when)
            continue

        match = SAMPLE_LINE.search(row)
        if match:
            height, peer_id = int(match.group(1)), int(match.group(2))
            if (when, peer_id) not in seen:
                seen.add((when, peer_id))
                CHAIN_SAMPLES.append((when, peer_id, height))
    cutoff = time.time() - ANNOUNCED_KEEP
    for h in [h for h, (_, w) in ANNOUNCED.items() if w < cutoff]:
        del ANNOUNCED[h]
    cutoff = time.time() - CHAIN_SAMPLES_KEEP
    CHAIN_SAMPLES[:] = sorted(e for e in CHAIN_SAMPLES if e[0] >= cutoff)


def own_height_at(when, tip):
    """Our own height at a past moment, from the announcement history.

    A sample from fifty minutes ago must be compared with what we had
    then, not with the tip of now — otherwise every stranger from before
    the last block looks as if it were behind.
    """
    known = [h for h, (_, w) in ANNOUNCED.items() if w <= when + 2]
    if known:
        return max(known)
    return tip


def chain_check(kz):
    """Judge the samples of the last hour. Only meaningful once the chain is
    up to date — during the initial sync every stranger is ahead, rightly,
    and the callers leave it out.

    Returns (dots, ok, total, ahead) — dots as a list of 'gleich', 'hinten'
    or 'voraus' in time order, ahead as the largest lead a stranger
    reported, or 0.
    """
    tip = (kz or {}).get("bloecke")
    if not tip or not CHAIN_SAMPLES:
        return [], 0, 0, 0
    dots, ahead = [], 0
    for when, _, height in CHAIN_SAMPLES:
        ours = own_height_at(when, tip)
        if height > ours + 1:
            dots.append("voraus")
            ahead = max(ahead, height - ours)
        elif height < ours - 1:
            dots.append("hinten")
        else:
            dots.append("gleich")
    ok = sum(1 for d in dots if d == "gleich")
    return dots, ok, len(dots), ahead


def chain_check_markup(kz):
    """Dots and sentence for the head of the network card."""
    dots, ok, total, ahead = chain_check(kz)
    if not total:
        return ""
    last = format_age(time.time() - CHAIN_SAMPLES[-1][0])
    # Each sample is compared with our height AT THAT TIME, so the sentence
    # must not name the current tip: "confirm block 965,082" thirty seconds
    # after that block arrived, from a sample taken at 22:24, was wrong
    # (2026-09-01). What the samples say is whether strangers saw the same
    # chain as we did — behind is harmless, ahead is the alarm.
    behind = total - ok - sum(1 for d in dots if d == "voraus")
    if ahead:
        sentence = t("a probe reports {n} blocks more than we have", n=ahead)
        cls = " warn"
    else:
        sentence = t("{ok} of {n} probes matched our height, {behind} behind, "
                     "none ahead · last {when}",
                     ok=ok, n=total, behind=behind, when=last)
        cls = ""
    marks = "".join(f'<i class="stich {d}"></i>' for d in dots[-12:])
    return (f'<span class="abgleich{cls}" title="{html_escape(t("Chain check: every few minutes Core asks a random node for its height. Last {when}", when=last))}">'
            f"<span class=stiche>{marks}</span>{html_escape(sentence)}</span>")


def chain_check_warning(kz):
    """The warning for the state bar, or None."""
    _, _, _, ahead = chain_check(kz)
    if ahead:
        return t("Chain check: a probe reports {n} blocks more", n=ahead)
    return None


def announcer_of_tip(peers, kz):
    """(peer index or None, peer id, height, time) of the newest announcement.

    The index is None when the announcing peer is no longer connected; the
    id is still returned so the sentence can name it.
    """
    if not ANNOUNCED:
        return None, None, None, None
    height = max(ANNOUNCED)
    peer_id, when = ANNOUNCED[height]
    tip = (kz or {}).get("bloecke")
    # A stale entry — the node has moved on without a logged announcement
    # (e.g. a block found by header sync after a restart) — is not "the
    # last block" and must not be shown as such.
    if tip and height < tip - 1:
        return None, None, None, None
    index = next((i for i, p in enumerate(peers) if p.get("id") == peer_id), None)
    return index, peer_id, height, when


def announcer_ranking(peers, limit=3):
    """The peers that announced most blocks first in the last 24 hours.

    Returns [(label, count)], connected peers by short address, others by
    their Core peer id.
    """
    counts = {}
    for peer_id, _ in ANNOUNCED.values():
        counts[peer_id] = counts.get(peer_id, 0) + 1
    by_id = {p.get("id"): p for p in peers}
    ranking = []
    for peer_id, n in sorted(counts.items(), key=lambda e: -e[1])[:limit]:
        p = by_id.get(peer_id)
        label = (shorten_address(p["adresse"]) if p
                 else t("peer {n} (no longer connected)", n=peer_id))
        ranking.append((label, n))
    return ranking


def block_path(peers, kz):
    """Who delivered the most recent block, and who got it from us.

    Returns (source index, receiver indices, height or None). The source is
    the peer with the newest 'last_block'. The height is only claimed when
    that block arrived about when the chain tip's timestamp says — if the
    delivering peer has disconnected since, the newest time left belongs to
    an older block and must not be labelled with the tip height.
    """
    if not peers:
        return None, [], None
    known = [(p["zuletzt_von"], i) for i, p in enumerate(peers)
             if p.get("zuletzt_von")]
    if not known:
        return None, [], None
    arrived, source = max(known)
    # Our own electrs fetches every new block from us over P2P. That is
    # not relay into the network, so it is neither a receiver here nor lit
    # on the map — it turned its spoke blue on every block (2026-09-01).
    receivers = [i for i, p in enumerate(peers)
                 if i != source and p["netz"] != "electrs"
                 and p.get("zuletzt_an") and p["zuletzt_an"] >= arrived - 5]
    height = None
    age = (kz or {}).get("blockalter")
    if age is not None and time.time() - arrived <= age + 120:
        height = (kz or {}).get("bloecke")
    return source, receivers, height


def block_path_text(peers, kz):
    """The sentence in the detail box while nothing is pointed at.

    Announcer first, deliverer second — when both are the same peer, only
    once. The announcement carries the height and the time: it is logged,
    so both are exact; the delivery time is only known to the pass.
    """
    source, receivers, height = block_path(peers, kz)
    if not peers:
        return ""
    ann_index, ann_id, ann_height, ann_when = announcer_of_tip(peers, kz)
    if ann_height:
        height = ann_height
    if ann_id is None and source is None:
        return t("The node that delivered the last block is no longer connected.")

    if ann_id is not None:
        ann_name = (shorten_address(peers[ann_index]["adresse"])
                    if ann_index is not None else t("peer {n} (no longer connected)", n=ann_id))
        when = format_age(time.time() - ann_when)
        head = t("Block {n} · announced {when} by {peer}", n=format_number(height),
                 when=when, peer=ann_name)
        if source is not None and source != ann_index:
            head += t(" · delivered by {peer}",
                      peer=shorten_address(peers[source]["adresse"]))
    else:
        p = peers[source]
        when = format_age(time.time() - p["zuletzt_von"])
        if height:
            head = t("Block {n} arrived {when} from {peer}", n=format_number(height),
                     when=when, peer=shorten_address(p["adresse"]))
        else:
            head = t("The last block arrived {when} from {peer}",
                     when=when, peer=shorten_address(p["adresse"]))
    # "sent block data", not "passed on": only peers that actually asked
    # for the block (or take compact blocks unasked) show up here. On a Tor
    # node most peers already have it and request nothing — a small number
    # is the honest one (2026-09-01).
    if receivers:
        tail = t("block data sent to {n} nodes|dativ", n=len(receivers))
    else:
        tail = t("no node has requested it from us")
    return f"{head} · {tail}"


def ranking_text(peers):
    """Second line of the detail box: who was first most often, 24 h."""
    ranking = announcer_ranking(peers)
    if not ranking:
        return ""
    total = len(ANNOUNCED)
    parts = " · ".join(f"{label} × {n}" for label, n in ranking)
    return t("first to announce, {total} blocks in 24 h: {parts}",
             total=total, parts=parts)


def collect_peers(cfg, limit):
    """Read the connected nodes.

    The return value is deliberately pure structure, no HTML: everything here
    comes from foreign machines — address, identifier and service list are
    chosen by the peer, not by us. The values are escaped only at the very
    end, or set in the browser via textContent, where markup cannot arise by
    construction.
    """
    global LAST_PEERS

    try:
        raw = rpc(cfg, "getpeerinfo")
    except RpcError:
        # The same tolerance window as everywhere else: one hiccup is no
        # reason to throw the list away. During the sync a call sometimes
        # takes longer than the timeout — the last known peers are then far
        # better than none.
        return list(LAST_PEERS)

    now = time.time()
    peers = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        # Both values arrive in decimal seconds. Displayed is 'minping' —
        # the fastest reply ever measured, i.e. the latency of the link.
        # 'pingtime' mostly measures how busy our own node is: ping and pong
        # run in the same thread as connecting blocks. During the sync it
        # reads ten seconds for every peer and no longer distinguishes
        # anything.
        ping = p.get("pingtime")
        better = p.get("minping")

        kind = str(p.get("network", "?"))
        address = str(p.get("addr", "?"))
        inbound = bool(p.get("inbound", False))
        # A connection that arrives through our own onion service reaches
        # bitcoind from 127.0.0.1. Core cannot see where it really came from
        # and files it under 'not_publicly_routable' — which the map used to
        # label "local". That hides the one number that says whether the onion
        # service is reachable from outside at all, and it is wrong: with
        # onlynet=onion no inbound connection can be anything but Tor.
        #
        # The address is part of the test, not just the direction. A real
        # second node on the home network is 'not_publicly_routable' too, but
        # carries a 192.168.… address and stays "local", rightly so.
        # Our own Electrum server is a peer too: electrs fetches blocks over
        # P2P (daemon_p2p_addr in its config) and arrives from 127.0.0.1 like
        # the onion connections — which is what it used to be labelled as.
        # It says 'electrs' in its user agent; should that ever change, a
        # round trip under two milliseconds gives it away: nothing coming
        # through Tor is that fast (2026-09-01).
        subver = str(p.get("subver", "")).lower()
        if (inbound and address.startswith("127.0.0.1")
                and ("electrs" in subver
                     or (better is not None and float(better) * 1000 < 2))):
            kind = "electrs"
        elif (kind == "not_publicly_routable" and inbound
                and address.startswith("127.0.0.1")):
            kind = "onion"

        last_block = p.get("last_block")
        per_msg = p.get("bytessent_per_msg") or {}
        peers.append({
            "id": int(p.get("id", -1)),
            "letzter_block": int(last_block) if last_block else None,
            "_blockbytes": sum(int(per_msg.get(m, 0)) for m in BLOCK_MESSAGES),
            "adresse": address,
            "netz": kind,
            # Left is our own key, right is Core's field. They must not be
            # the same word — see the note on blocks/headers above.
            "eingehend": inbound,
            "ping_ms": round(float(better) * 1000, 1) if better else (
                round(float(ping) * 1000, 1) if ping else None),
            "jetzt_ms": round(float(ping) * 1000, 1) if ping else None,
            "dauer_s": max(0, int(now - float(p.get("conntime", now)))),
            "version": str(p.get("subver", "")).strip("/") or t("unknown"),
            "dienste": ", ".join(p.get("servicesnames") or []) or t("none"),
            "gesendet": int(p.get("bytessent", 0)),
            "empfangen": int(p.get("bytesrecv", 0)),
        })

    # Group by network type first, fastest first within each group. The
    # grouping keeps dots of the same colour together instead of letting them
    # mix.
    rank = {"electrs": 0, "onion": 1, "ipv4": 2, "ipv6": 3, "i2p": 4, "cjdns": 5}
    peers.sort(key=lambda e: (rank.get(e["netz"], 9),
                              e["ping_ms"] if e["ping_ms"] is not None else 9e9))
    track_block_traffic(peers)
    LAST_PEERS = peers[:limit]
    return list(LAST_PEERS)


def format_latency(ms):
    """Milliseconds up to 1000, seconds above that.

    During the initial sync response times of a minute are normal — the node
    simply does not get round to sending the pong. "64101 ms" makes the label
    needlessly long and reads badly.
    """
    if ms is None:
        return None
    if ms < 1000:
        return f"{ms:.0f} ms"
    return decimal_sep(f"{ms / 1000:.1f} s")


def peer_line_text(p):
    """The key figures shown along the line. Short enough for one line."""
    parts = [shorten_address(p["adresse"]), peer_network_label(p)]
    latenz = format_latency(p["ping_ms"])
    if latenz:
        parts.append(latenz)
    parts.append(format_bytes(p["gesendet"] + p["empfangen"]))
    return " · ".join(parts)


# The Bitcoin mark as a PNG, 128 x 128, cut out and reduced to 16 colours —
# just under 1.9 kB. This used to be a shape rebuilt by hand from rectangles
# and arcs. It was wrong three times, and every attempt cost a round trip via
# a screenshot because nothing can be looked at in this environment. A logo is
# not a geometry exercise: the image is the source, so the image is the
# answer.
#
# It ships as its own file, not as a data: URI in the markup — otherwise it
# would sit inside every index.html and every status.json all over again.
BITCOIN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAACABAMAAAAxEHz4AAAAMFBMVEX3kxv3+Pn++fT4sFr6"
    "0Z/3z573s2L5wHz3z5/3sVz3zp338OP3tmf3oDb34MT2wHw/xcuIAAAAEHRSTlP+AP/9/Vhf"
    "/BinpVoaqp2hZ93g3AAABrlJREFUeNqlWltsFFUY/vaccZcttt0FYsUYO5bgJYZ0YIkEHnAJ"
    "GuKDcZ+8hERqeDAYxRoxkvCgifLQEEO9FGNipFFJxIRkjSHig7FBrsqwQ2KQBBimUpDWbmdt"
    "xbqls+vD7szOzLnsrJ6X7syc881//f7/zGksDcn4CgDwlGxKTAigPFacdH9u/+a7VgHaTvfZ"
    "cIzaBdU2nH+hryWAxyfoJKDDBYDS/vylwcgAbQN7vcXwQBJdI5y5hHOv/cH9Tng9HL08MxgN"
    "4OByu2hwpur2+x9GUeHQwKQuMvma7S+Fb9Fk6EbywpRwPW5cfedwE4D2+4sFcdRUr11584gU"
    "oO2BUkEWd5j4rdeSGXHAdqTr4RTG+yUAn+/XDTRBOP2XWAXlSul3NBvVckCJgASFko7mQx8X"
    "SUBPHkeUcX39Na4Ebb3FSOvh/JHlAnxcNKIBwBjjAbTtRdThVLIcgOgCAPoYC6BEFwBAQwQP"
    "YH8LAvhF8Nw4epI7k1b5CEu3nAoC0AXcGKSvrp3jPpj4uxgEeG+I+yKSmFjQeYMX0EUlYANl"
    "mC/pasuMUYBqzJO5UgDgU0EQOoCVAjQWwVkdABjku4DW/2xIb2QQzvb7AJIlgbtSADTgrPn1"
    "Yu4zF2CdiIcqAL4HgMoYo8MBH4Ad1oBm/FeaBdzNBlO/B5BkTKhlNQBQLKAbiPEN3AB4ka03"
    "57oymQxWAIBBBSVwkwfQzVi/w7xop5dkgqYMj3MugMJ3onk53dEwJacyz+bqALcxPqi9rmJa"
    "jQuen7Q6wDrWBP7fdKXlhVRwfFEHeJLRwJ/DVjoFYJ6n5S81gMSgtG2omCYAlSfBbB4gAGHV"
    "M0o9oTtjGS49AzSJageT8NXxVNtsMKbvSnOIpVRELI3PnuAm4iOhMt7NIc34QhBAkImrQmk1"
    "uiTDS3eaTFy/wi3Cc7OhG/ZShtyq335J4JSkBE4a9h9lgsEZBuHEoZ+M0P1njwtRWaKxsUhQ"
    "NsRkBJCibq9yES5vDE96BQRZCRnVnP3DdD0qKsy74iDISU2gAo5+ZqYuw6jGKEowLO+IAABn"
    "SMgyXnUASYj2G5YXrYBjq37D+EaeOJqwpDTIAEbILt4YJjXW5pMFQOpPRcHigCj4X0Mjc5DG"
    "UbPRTwBZHDUfpL9ZHNUESgn2NwuJw4/kkGlWugzCSiDIBMfydnwAaNYCn9znRE7QpmqKpLFp"
    "EkifFdqgmRdNgKgw3dz+vmUAABVTtskkrUSNWowMQNdzU9OILgFv6vJidBXqSUj8BUodN6J7"
    "oV6eVbsHFbfAWJ2tuNECAFLVqRZT6wgVbnrIvaDB0avTLinHtOgA9VQaAaBXb9YtYS7mAWjS"
    "zREA6KfdFFLYyXEiYwO3USnURbjMoRkyqIn5SDVcRlRFZrxJqCQMGntiCSPFJWHAd24wYtkG"
    "iWZAwzMdoakHFYaVV6Y2FTumImeqsoCG8zAP1OgIRVlu1Wb3kXLIsprRUpXIEfRFsJ/QBHEQ"
    "5MWWdl9MY5bXLYRDVYF2KHCrY6qREOlHe0cAIOa7GZZAwT6xtiYslU4i7VIC65bjWZrEff7u"
    "r7q4qxRoDlPJpEcpMSfcKJYvENCgiQvTPQFNTdP0rMJsjdAHgnJwM+NUZ4iKiFSbzEFB2I86"
    "1bCow4pEPg5AWCc7ul6YjlZX4gABPtJEHVZIAPZjxm5A4bVeteFj9JoAkwYnQglQ3iroMW3S"
    "RID2XO0TSOxX5tGd/wCVwqKukruQ3NvJ27HUCstJNgNTJdfmrk+rnPV73qoB3BKmrwWotpgS"
    "jHppm+/rF6dv1RF+oGrPu8ExI2j0pWRULxsE4ERCrFFVVojpcLcHUOHGrSHa9gcUJQBQXhYW"
    "jqgogaakzBzPNRLkREgHwyaq04zQf/ZlWJWGc8G2BSzYcHXJ16GU1dD34NrlKktC6DTnz/Ef"
    "ucQ90kPmhV7cHCCJW6/x2ouC3Sn8UpzMB5ssi9ugSD500xBNDWVa3CwdDgGU1dbWx3Nhojyq"
    "tQSwmWHa+WUtCZBnqbolETY3jOkdD1QyiRuRBTB5xeLofxHAf0BR2WpHFOH2iz7q8J10JS62"
    "R1qfPJLj17vysmh2fMb/0SNwyhPNjg+fEFfc41sjKLAnSJ/B0z7lnmYHRfS5vKzmzz/dzAzv"
    "hrq68GnfsW2TUjN8sgtyABxbmxAfl9GhnWgGgEtrFKEMQ7vQHACXttldXIjM2zs5WwvesfGO"
    "jpd5/ttyEBEBUH32J+bc+YOHuF/dREfnO7Zn/XU9E+89KNgkCw/vX7/akGLf+QOiaTHZvw+8"
    "ceoOAOgZkMz5F6WfAWt9OVdUAAAAAElFTkSuQmCC"
)

LOGO_R = 19            # radius of the mark at the hub

PEER_FONT = 12.5       # must match .peerzeile in the style block
PEER_CHAR_W = 0.63     # width of one character at that font size
SPOKE = 104            # distance from hub to the inner end of the spoke


def build_network_map(peers, kz=None):
    """A fan: our own node in the middle, the peers to the left and right.

    No world map — 'getpeerinfo' carries no geodata, and the generator must
    not go online to look any up. The key figures sit right on the line so
    they can be read without pointing; pointing only highlights the row and
    fetches the longer details.

    Foreign text enters the SVG escaped, always. The long form (identifier,
    services) is set by dash.js via textContent, where markup cannot arise.
    """
    if not peers:
        return None
    source, receivers, _ = block_path(peers, kz)
    announcer = announcer_of_tip(peers, kz)[0]

    row_height = 30
    margin_top = 34
    half = (len(peers) + 1) // 2
    height = margin_top * 2 + half * row_height

    # The width follows from the longest label, not from a fixed value. An
    # SVG clips everything beyond its viewBox — with a fixed width the end of
    # the line disappeared for long addresses and three-digit second values.
    longest = max(len(peer_line_text(p)) for p in peers)
    label = longest * PEER_FONT * PEER_CHAR_W
    half_width = SPOKE + 14 + label + 26
    width = round(half_width * 2)
    mx, my = width / 2, height / 2

    # Inner ends of the spokes. The gap between them leaves room for the
    # hub so lines and labels do not get in each other's way.
    links_innen, rechts_innen = mx - SPOKE, mx + SPOKE
    links_aussen, rechts_aussen = 26, width - 26

    parts = []
    for i, p in enumerate(peers):
        rechts = i >= half
        reihe = i - half if rechts else i
        y = margin_top + reihe * row_height + row_height / 2

        if rechts:
            x_inner, x_aussen = rechts_innen, rechts_aussen
            x_text, anchor = rechts_innen + 14, "start"
            nabe_x = mx + LOGO_R + 5
        else:
            x_inner, x_aussen = links_innen, links_aussen
            x_text, anchor = links_innen - 14, "end"
            nabe_x = mx - LOGO_R - 5

        kind = p["netz"] if p["netz"] in NETWORK_COLOURS else "neutral"
        filled = "" if p["eingehend"] else " voll"
        # The path of the most recent block: solid orange spoke to the peer
        # that announced it first, a solid spoke in its own colour to the
        # one that delivered it (only when that is a different peer — with
        # a headers announcement Core fetches the block from the announcer,
        # and then there is just the orange one), lit spoke to every peer
        # we handed it to.
        if i == announcer:
            role = " ansager"
        elif i == source:
            role = " quelle"
        elif i in receivers:
            role = " empfaenger"
        else:
            role = ""

        parts.append(
            f'<g class="peer {kind}{role}" tabindex="0" data-nr="{i}">'
            # A large transparent area: the visible parts are thin, the
            # target for the mouse may be generous.
            f'<rect x="{min(x_inner, x_aussen) - 6:.1f}" y="{y - 15:.1f}" '
            f'width="{abs(x_aussen - x_inner) + 12:.1f}" height="{row_height}" '
            f'class="peerflaeche"/>'
            # Only the spoke from hub to dot. There used to be a horizontal
            # running outward from there with the label above it — which put
            # dot and text at different heights. Now both sit on one line.
            f'<line x1="{nabe_x:.1f}" y1="{my:.1f}" x2="{x_inner:.1f}" '
            f'y2="{y:.1f}" class="peerlinie"/>'
            f'<circle cx="{x_inner:.1f}" cy="{y:.1f}" r="4.5" '
            f'class="peerpunkt{filled}"/>'
            f'<text x="{x_text:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'dominant-baseline="central" '
            f'class="peerzeile">{html_escape(peer_line_text(p))}</text>'
            "</g>"
        )

    # The hub carries the Bitcoin mark, placed by its corner and scaled to
    # twice the desired radius.
    hub = (
        f'<image href="bitcoin.png?v={BITCOIN_V}" '
        f'x="{mx - LOGO_R:.1f}" y="{my - LOGO_R:.1f}" '
        f'width="{LOGO_R * 2}" height="{LOGO_R * 2}"/>'
        f'<text x="{mx:.1f}" y="{my + LOGO_R + 26:.1f}" class="eigentext" '
        f'text-anchor="middle">dieser Node</text>'
    )

    return (f'<svg class=netzkarte viewBox="0 0 {width:.0f} {height:.0f}" '
            f'role="img" aria-label="'
            f'{html_escape(t("Network of the {n} connected nodes", n=len(peers)))}">'
            f"{hub}{''.join(parts)}</svg>")


def peer_summary(peers):
    """Summary line for the kopf of the network map."""
    if not peers:
        return []
    by_network = {}
    for p in peers:
        by_network[p["netz"]] = by_network.get(p["netz"], 0) + 1
    # Latency figures without our own electrs: its 0 ms would always be the
    # "fastest" and pull the average down, and it says nothing about the net.
    pings = [p["ping_ms"] for p in peers
             if p["ping_ms"] is not None and p["netz"] != "electrs"]
    inbound = sum(1 for p in peers if p["eingehend"])

    fields = [(t("Connected"), f"{len(peers)}", ""),
              (t("eingehend"), f"{inbound}", "")]
    for net, n in sorted(by_network.items(), key=lambda e: -e[1]):
        fields.append((network_name(net), str(n), ""))
    if pings:
        fields.append((t("Average round trip"),
                       format_latency(sum(pings) / len(pings)), ""))
        fields.append((t("fastest"), format_latency(min(pings)), ""))

    # How long our own node takes to answer. During the sync that is
    # seconds — not the peers' fault but a measure of our own load.
    now = [p["jetzt_ms"] for p in peers
           if p.get("jetzt_ms") is not None and p["netz"] != "electrs"]
    if now:
        mean = sum(now) / len(now)
        fields.append((t("own response time"), format_latency(mean),
                       "warn" if mean > 2000 else ""))
    return fields


def collect_updates(cfg):
    """Read the result of the weekly version check.

    The dashboard generator never goes online itself — it only reads the file
    a separate service has written. The return value is pure structure;
    build_header_info turns it into markup.
    """
    path = cfg.get("UPDATE_FILE", "/var/lib/node-dashboard/updates.json")
    raw = read_file(path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if data.get("eintraege") else None


def collect_tor(cfg):
    """Read what the Tor watchdog last reported.

    Same shape as the version check: a separate service writes a file, the
    dashboard reads it. There is no way back from here.
    """
    raw = read_file(cfg.get("TOR_FILE", "/var/lib/node-dashboard/tor.json"))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def build_tor_notice(tor):
    """The banner announcing the upcoming or completed switchover."""
    if not tor:
        return ""
    zustand = tor.get("zustand")

    if zustand == "bereit":
        treffer, noetig = tor.get("treffer", 0), tor.get("noetig", 6)
        return (
            '<div class="meldung warn"><span class=punkt></span><div>'
            + t("<b>The chain is up to date.</b> The watchdog will switch the "
                "node to Tor once it has been in sync for one hour without a "
                "break — measurement {treffer} of {noetig}. Cancel with "
                "<code>sudo bash 08-tor-automatik.sh --aus</code>.",
                treffer=html_escape(str(treffer)),
                noetig=html_escape(str(noetig)))
            + "</div></div>"
        )
    if zustand == "laeuft":
        return (
            '<div class="meldung warn"><span class=punkt></span><div>'
            + t("<b>Switching to Tor.</b> bitcoind is being restarted and port "
                "8333 closed. Follow along with "
                "<code>journalctl -u node-torwaechter -f</code>.")
            + "</div></div>"
        )
    if zustand == "fehler":
        return (
            "<div class=fehlerkarte><h2>"
            + html_escape(t("Tor switchover failed")) + "</h2>"
            + f"<p>{html_escape(tor.get('meldung', ''))}</p><p>"
            + t("It will not be retried automatically. Look at "
                "<code>journalctl -u node-torwaechter -n 50</code>.")
            + "</p></div>"
        )
    return ""


def build_header_info(updates, kz):
    """The middle part of the page header: versions, state, uptime.

    The 'Updates' card was dropped for this. It occupied a full card in order
    to say "current" three times in the normal case. Here the same information
    fits on one line — and only catches the eye when something is wrong.
    """
    pieces, level = [], "gut"

    if updates:
        for e in updates.get("eintraege", []):
            name = e.get("name", "?")
            kurz = "Core" if name.startswith("Bitcoin") else name
            inst = e.get("installiert", "?")
            if e.get("veraltet"):
                pieces.append(f"{kurz} {inst} → {e.get('neueste')}")
                level = "warn"
            else:
                pieces.append(f"{kurz} {inst}")
    elif kz.get("version"):
        # Without a version check at least what the node reports itself.
        pieces.append(kz["version"])

    if kz.get("laufzeit"):
        pieces.append(t("up for {duration}", duration=format_duration(kz["laufzeit"])))

    if not pieces:
        return ""

    # The check time lives in the title attribute rather than in the line:
    # it matters when you go looking and is ballast when you just glance.
    title = ""
    if updates and updates.get("geprueft"):
        title = t("versions checked {age}",
                  age=format_age(time.time() - updates["geprueft"]))
        if not updates.get("ueber_tor", False):
            title += t(", fetched in the clear")

    return (f'<div class="kopfinfo {level}"'
            + (f' title="{html_escape(title)}"' if title else "")
            + '><span class=kpunkt></span>'
            + f"<span>{html_escape(' · '.join(pieces))}</span></div>")


# journalctl --output=short-iso yields:
#   2026-08-23T11:31:50+02:00 btcnode bitcoind[32327]: 2026-08-23T09:31:50Z UpdateTip: ...
#   \_________ journald ____________________________/  \__ bitcoind __/
# That is about 55 characters of prefix carrying the time twice, pushing the
# interesting part out of view. We keep the local time and drop the remaining.
LOG_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})[+\-]\d{2}:\d{2}\s+"   # journald
    r"\S+\s+"                                                       # Rechnername
    r"[\w.@-]+(?:\[\d+\])?:\s*"                                     # Dienst[PID]:
    r"(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+)?"                # bitcoind, UTC
)


def shorten_log_line(row):
    """Strip the duplicated timestamp. If the pattern does not match the line
    stays as it is — better wide than incomplete."""
    match = LOG_PREFIX.match(row)
    if not match:
        return row
    return f"{match.group(1)}  {row[match.end():]}"


def collect_log(cfg):
    """Fetch the last journal lines of the configured services.

    The lines are written into the HTML escaped later on. That matters:
    Bitcoin Core logs, among other things, the self-chosen identifiers of
    foreign nodes, and those are picked by the peer, not by us.
    """
    sections = []
    max_lines = max(5, min(200, int(cfg.get("LOG_LINES", 40))))

    for service in [d.strip() for d in cfg.get("LOG_SERVICES", "").split(",") if d.strip()]:
        if not os.path.exists(f"/etc/systemd/system/{service}.service"):
            continue
        try:
            r = subprocess.run(
                ["journalctl", "-u", service, "-n", str(max_lines),
                 "--no-pager", "--output=short-iso"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            sections.append((service, t("log not readable: {e}", e=e)))
            continue

        if r.returncode != 0:
            note = (r.stderr or "").strip() or t("no access to the journal")
            sections.append((service, note))
            continue

        rows = [shorten_log_line(z)
                  for z in r.stdout.splitlines() if z.strip()]
        if not rows:
            sections.append((service, t("no eintraege")))
        else:
            # Newest first: a static page cannot scroll to the bottom
            sections.append((service, "\n".join(reversed(rows))))

    return sections


def own_ip():
    """Determine our own address on the local network.

    No traffic goes anywhere: a UDP socket is merely 'connected' so the kernel
    picks the matching source address. Not a single packet leaves the machine.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))     # Adresse aus dem Doku-Bereich
            return s.getsockname()[0]
    except OSError:
        return None


def electrs_indexed_height(cfg):
    """How far electrs has indexed the chain, or None if unknown.

    Two sources, both on localhost, neither reaching beyond this machine:

    1. The Prometheus endpoint (monitoring_addr in config.toml, on by
       default in 05) is up from the first second, even while electrs is
       still indexing. The gauge is 'electrs_index_height{type="tip"}' —
       measured on the Pi on 2026-09-01 with electrs 0.11.1. Should the
       name differ in another release, any gauge with 'height' in its name
       is taken as a fallback. Asked first because it is a plain HTTP read
       that electrs does not log.
    2. The Electrum protocol on the RPC port answers
       'blockchain.headers.subscribe' with the height of its index — the
       same question every wallet asks first. Exact, but electrs may log
       every connection, and it only works once electrs is serving. So it
       is the fallback, not the rule.
    """
    metrics = cfg.get("ELECTRS_METRICS", "127.0.0.1:4224")
    try:
        with urllib.request.urlopen(f"http://{metrics}/", timeout=3) as r:
            text = r.read(500_000).decode("utf-8", "replace")
    except (OSError, ValueError):
        text = ""
    exact, rough = [], []
    for row in text.splitlines():
        if not row or row.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_:][\w:]*)(\{[^}]*\})?\s+([0-9.eE+-]+)", row)
        if not match:
            continue
        name, value = match.group(1), match.group(3)
        try:
            number = int(float(value))
        except ValueError:
            continue
        if name == "electrs_index_height":
            exact.append(number)
        elif "height" in name:
            rough.append(number)
    if exact:
        return max(exact)
    if rough:
        return max(rough)

    port = int(cfg.get("ELECTRS_PORT", 50001))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall(b'{"jsonrpc":"2.0","id":0,'
                         b'"method":"blockchain.headers.subscribe","params":[]}\n')
            sock.settimeout(5)
            raw = b""
            while b"\n" not in raw and len(raw) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        answer = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
        height = int(answer["result"]["height"])
        return height if height > 0 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def collect_electrum(cfg, tip=None):
    """State of the Electrum server, if one is set up.

    It is what attaches a wallet to your own node — BitBoxApp, Sparrow,
    Electrum. Without it the wallet asks foreign servers, and those learn
    which addresses belong to you.
    """
    if not os.path.exists("/etc/systemd/system/electrs.service"):
        return None

    running = service_running("electrs")
    port = cfg["ELECTRS_PORT"]
    reachable = port_open("127.0.0.1", port) if running else False

    fields = [
        (t("Service"), t("running") if running else t("stopped"),
         "gut" if running else "warn"),
        (t("Responding"), t("yes") if reachable else t("no, still indexing"),
         "gut" if reachable else "warn"),
    ]

    # How far the index has got, against the node's own height. Until the
    # bar is full the wallet cannot connect — this is the one thing to watch
    # after setting electrs up or after a long downtime (2026-09-01).
    indexed = electrs_indexed_height(cfg) if running else None
    # An index above the node's own height is not a measurement but a
    # stale or foreign answer — shown as unreadable rather than as 100 %.
    if indexed and tip and indexed <= tip:
        behind = max(0, tip - indexed)
        fraction = min(1.0, indexed / tip) if tip else 0
        if behind <= 1:
            fields.append((t("Index"),
                           t("complete · block {n}", n=format_number(indexed)),
                           "gut"))
            fields.append(("", build_bar(1.0), "grafik"))
        else:
            # Close to the tip a percentage says "100,0 %" while blocks are
            # still missing — there the count is the honest figure.
            rest = (t("{n} bloecke to go", n=format_number(behind))
                    if fraction >= 0.999
                    else decimal_sep(f"{fraction * 100:.1f} %"))
            fields.append((t("Index"),
                           t("{n} of {tip} bloecke · {rest}",
                             n=format_number(indexed), tip=format_number(tip),
                             rest=rest),
                           "warn"))
            fields.append(("", build_bar(fraction, "warn"), "grafik"))
    elif running:
        fields.append((t("Index"), t("progress not readable"), "leer"))

    # --- Connection details for the wallet, to click and copy ---------------
    ip = own_ip()
    if ip:
        fields.append((t("On the local network"), f"{ip}:{port}", "kopier"))

    # /var/lib/tor/... is unreadable for other services (directory 700), so
    # script 05 places a copy at a neutral path.
    onion = (read_file("/etc/electrs/onion")
             or read_file("/var/lib/tor/electrs/hostname"))
    if onion:
        fields.append((t("Over Tor"), f"{onion}:{port}", "kopier"))

    note = t("Enter this in your wallet as a custom server — in the "
                "BitBoxApp under Settings → Advanced settings → Connect your "
                "own voll node. Clicking an address selects it, Ctrl+C copies.")

    return ("Electrum server", fields, note)


# ==================================================================== Output
# The style lives in a file of its own, no longer inline in the HTML. The
# reason is not file size but the Content Security Policy: only without inline
# style and inline script can the server set "default-src 'self'" without
# 'unsafe-inline' — and that is the actual protection should a foreign value
# ever slip past the escaping.
STYLE = """
:root{
/* Surfaces — bottom to top: ground, card, raised surface */
--bg:#0a0c11;--fl:#111419;--fl2:#161a22;--vertief:#0d1015;
--rand:#1e232c;--randhell:#2a313d;--randhervor:#3a4353;
/* Type — three levels, no more are needed */
--text:#e7eaf1;--leise:#98a1b2;--sehrleise:#68717f;
/* Meaning. Green says 'as expected', not 'problem solved'. */
--akzent:#2fd39a;--warn:#f0b23f;--fehler:#f2645f;--info:#5aa2f0;
/* Network types in the network map */
--netz-ipv4:#5aa2f0;--netz-ipv6:#9b8cff;--netz-onion:#2fd39a;--netz-i2p:#f0b23f;
/* The path of the most recent block through the map: Bitcoin orange, used
   nowhere else so it stays unmistakable */
--block:#f7931a;
/* Our own electrs among the peers: a lighter, cooler blue than IPv4 */
--netz-electrs:#4cc3ff;
--balken:var(--akzent);
/* Spacing scale: everything else is a multiple of these */
--e1:.25rem;--e2:.5rem;--e3:.75rem;--e4:1rem;--e5:1.5rem;--e6:2rem;
/* Baseline row. Every row in every card is exactly this tall or a multiple
   of it — only then do the rows of neighbouring cards line up. Graphs get
   two or three units. */
--zeile:1.6rem;
--rad:10px;--rad-gross:14px;
--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
--schatten:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6)}

@media(prefers-color-scheme:light){:root{
--bg:#f4f6f9;--fl:#fff;--fl2:#fafbfd;--vertief:#f0f2f6;
--rand:#e2e6ee;--randhell:#cdd4e0;--randhervor:#aab3c4;
--text:#101319;--leise:#586074;--sehrleise:#828b9c;
--akzent:#0d9c6b;--warn:#b8791a;--fehler:#d33f3c;--info:#2b6fd0;
--netz-ipv4:#2b6fd0;--netz-ipv6:#6a52e0;--netz-onion:#0d9c6b;--netz-i2p:#b8791a;
--block:#d9780a;
--netz-electrs:#0e8ed0;
--schatten:0 1px 2px rgba(16,19,25,.05),0 8px 24px -14px rgba(16,19,25,.22)}}

*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--text);font-family:var(--sans);
font-size:15px;line-height:1.5;padding:var(--e5) var(--e4) var(--e6);
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
/* Full screen width, no maximum: the dashboard is a display, not running
   text. */
.huelle{max-width:none;display:flex;flex-direction:column;gap:var(--e4)}

/* Two columns: everything interpreted on the left, the raw log on the
   right. The 50rem column width is not arbitrary — a shortened log line is
   about 110 characters and needs exactly that much to stand unbroken. Below
   that the split is not worth it, hence the breakpoint. */
/* Not one grid over the whole page but TWO rows, each a grid of its own:
   above the network card next to the state bar, band and charts, below the
   log next to the card grid. Only that puts the seam between network card
   and log onto the seam of the left column — a single grid sizes the right
   column from its own content, and on 2026-08-31 the network card ended
   19 px above the chart cards next to it.
   Rows instead of 'subgrid': a subgrid may not carry layout containment
   (CSS Grid 2, §6), and 'container-type:inline-size' on .links does exactly
   that — the card columns need it and would lose their container. */
/* 'stretch': in each row both columns are as tall as the taller one. The
   log claims whatever the card grid leaves over. */
.inhalt{display:flex;flex-direction:column;gap:var(--e4)}
.reihe{display:grid;grid-template-columns:1fr;gap:var(--e4);
align-items:stretch}
/* container-type: so the card grid can follow the width of THIS column
   rather than that of the window. The left column is only half as wide as the
   screen — a column count measured against the window is bound to be wrong
   here. */
.links,.rechts{display:flex;flex-direction:column;gap:var(--e4);min-width:0;
container-type:inline-size}
/* min-width:0 is not a nicety here, it is required. Grid items default to
   min-width:auto, i.e. the minimum width of their content — and for the log
   with 'white-space:pre' that is the longest line. Without this rule the log
   pushes the whole column wider than the window and the entire page is
   clipped on the right. */
.links>*,.rechts>*{min-width:0}
/* Hide empty zones. A <div> without content is invisible but still counts
   as an item in the flex column and creates a second gap — that looks like an
   uneven margin but is a hole. Mostly affects the trouble zone, which is
   empty in the normal case. */
.links>*:empty,.rechts>*:empty{display:none}
/* Exactly half and half, in every browser and at every resolution. No fixed
   width in rem — that would be a strip on a 4K screen and half the page on a
   laptop. */
@media(min-width:80rem){
.reihe{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
/* Whichever of the two blocks in a row is shorter gives way, so that no hole
   opens between the cards: the charts take the difference when the network
   card is the taller one (many peers make it grow), and while the chain is
   still syncing, where there are no charts, the metric band does. */
#z-weit{flex-grow:1}
.links:has(#z-weit:empty) #z-band{flex-grow:1}

/* ----------------------------------------------------------- Kopfzeile --- */
header{display:flex;flex-wrap:wrap;gap:var(--e1) var(--e4);align-items:center;
justify-content:space-between;padding-bottom:var(--e3);
border-bottom:1px solid var(--rand)}
h1{font-size:.95rem;font-weight:600;letter-spacing:-.01em;display:flex;
align-items:center;gap:var(--e2)}
h1 b{font-weight:400;color:var(--sehrleise);letter-spacing:0}
.marke{width:.55rem;height:.55rem;border-radius:99px;background:var(--akzent);flex:none}
.kopfrechts{display:flex;align-items:center;gap:var(--e3);
color:var(--sehrleise);font-size:.73rem;font-family:var(--mono)}
/* Middle of the header: versions and uptime. Replaces the 'Updates' card —
   visible but quiet as long as everything is fine. */
.kopfinfo{display:flex;align-items:center;gap:var(--e2);
color:var(--leise);font-size:.73rem;font-family:var(--mono);
margin-inline:auto;cursor:default}
.kopfinfo .kpunkt{width:.4rem;height:.4rem;border-radius:99px;flex:none;
background:var(--akzent)}
.kopfinfo.warn{color:var(--warn)}
.kopfinfo.warn .kpunkt{background:var(--warn)}
@media(max-width:60rem){.kopfinfo{order:3;flex-basis:100%;margin-inline:0}}
/* The pulse shows that the page updates itself. Without JavaScript it
   simply stands still — which is more honest than blinking into the void. */
.puls{width:.4rem;height:.4rem;border-radius:99px;background:var(--sehrleise);
transition:background .2s,box-shadow .2s}
[data-frisch=ja] .puls{background:var(--akzent);
box-shadow:0 0 0 3px color-mix(in srgb,var(--akzent) 22%,transparent)}
[data-frisch=alt] .puls{background:var(--warn);
box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 22%,transparent)}

/* -------------------------------------------------------- Zustandsleiste - */
.zustand{background:var(--fl);border:1px solid var(--rand);
border-radius:var(--rad-gross);padding:var(--e5);display:grid;
grid-template-columns:1fr auto;gap:var(--e4) var(--e6);align-items:center;
box-shadow:var(--schatten);position:relative;overflow:hidden}
/* A narrow coloured strip along the edge instead of a tinted area: carries
   the same information without making the card loud. */
.zustand::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;
background:var(--akzent)}
[data-stufe=warn] .zustand::before{background:var(--warn)}
[data-stufe=fehler] .zustand::before{background:var(--fehler)}
[data-stufe=veraltet] .zustand::before,
[data-stufe=anlauf] .zustand::before{background:var(--warn)}
.zlinks{display:flex;align-items:center;gap:var(--e3);min-width:0}
.punkt{width:.62rem;height:.62rem;border-radius:99px;flex:none;background:var(--akzent);
box-shadow:0 0 0 4px color-mix(in srgb,var(--akzent) 18%,transparent)}
[data-stufe=warn] .punkt,[data-stufe=veraltet] .punkt,
[data-stufe=anlauf] .punkt{background:var(--warn);
box-shadow:0 0 0 4px color-mix(in srgb,var(--warn) 18%,transparent)}
[data-stufe=fehler] .punkt{background:var(--fehler);
box-shadow:0 0 0 4px color-mix(in srgb,var(--fehler) 18%,transparent)}
.zwort{font-size:1.7rem;font-weight:650;letter-spacing:-.03em;line-height:1.1}
[data-stufe=sync] .zwort{font-family:var(--mono);font-variant-numeric:tabular-nums}
.zzusatz{color:var(--leise);font-size:.8rem;margin-top:var(--e1)}
.zrechts{text-align:right;min-width:0}
.zzahl{font-family:var(--mono);font-size:1.6rem;font-weight:600;letter-spacing:-.035em;
line-height:1.1;font-variant-numeric:tabular-nums}
.zlabel{color:var(--sehrleise);font-size:.68rem;text-transform:uppercase;
letter-spacing:.1em;margin-top:var(--e1)}
.balkenbox{grid-column:1/-1}
/* Rate on the left, remaining time on the right — right under the bar,
   where the eye already is when reading the progress. */
.zfuss{display:flex;justify-content:space-between;gap:var(--e4);
margin-top:var(--e2);color:var(--leise);font-size:.76rem;
font-family:var(--mono);font-variant-numeric:tabular-nums}
.zrest{color:var(--text);font-weight:600}
.kurve{margin-top:var(--e3)}
.kurve svg{display:block;width:100%;height:44px}
.kurvenfuss{display:block;margin-top:var(--e1);color:var(--sehrleise);font-size:.7rem;
text-align:right;font-family:var(--mono)}

/* ------------------------------------------------------- Kennzahlenband -- */
/* Four numbers that always apply. They sit above everything else so that a
   glance from three metres away is enough. */
/* Always four columns. They set the rhythm of the left area, and their
   number must not change when the log expands or collapses — otherwise half
   the page rearranges on every click. */
.band{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--e3)}
@media(max-width:62rem){.band{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:34rem){.band{grid-template-columns:1fr}}
/* A flex column instead of loose margins: value and label sit in the same
   rhythm in every tile, and the extra line rests at the bottom edge instead
   of somewhere in between. Before this the one tile with an extra line had
   visibly different spacing from the three without. */
.kachel{background:var(--fl);border:1px solid var(--rand);border-radius:var(--rad);
padding:var(--e3) var(--e4);display:flex;flex-direction:column;gap:var(--e1)}
.kachel .kwert{font-family:var(--mono);font-size:1.15rem;font-weight:600;
letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.2}
.kachel .klabel{color:var(--sehrleise);font-size:.66rem;text-transform:uppercase;
letter-spacing:.1em;line-height:1.3}
/* The small line underneath carries what the 'Blockchain' card used to say. */
.kachel .kzusatz{color:var(--leise);font-size:.72rem;font-family:var(--mono);
margin-top:auto;padding-top:var(--e3);border-top:1px solid var(--rand)}
/* The comparison tile keeps the width of the others. When space runs short
   it wraps at the "of" — two lines beat a truncated number, and all tiles are
   the same height anyway. */
.kachel.breit .kwert{flex-wrap:wrap;display:flex;align-items:baseline;
gap:0 .3em}
.kwert .kvon{color:var(--sehrleise);font-size:.8rem;font-weight:400}
.kachel.warn .kwert{color:var(--warn)}
.kachel.gut .kwert{color:var(--akzent)}

/* -------------------------------------------------------------- Karten --- */
/* A grid instead of CSS columns: matching top edges look tidy, and the
   reading order matches the order in the source. */
/* No 'align-items:start': cards should stretch to the height of the tallest
   card in their row. The grid does that by itself as long as it is not
   prevented — and because .karte is a flex container in column direction the
   content still stays at the top. */
/* auto-FIT, not auto-fill: 'fill' creates as many tracks as fit and leaves
   the surplus ones empty — three cards in a column that holds four tracks
   then occupy three quarters of the width and a hole gapes on the right.
   'fit' collapses empty tracks and the remaining ones stretch to full
   width. */
.raster{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));
gap:var(--e3)}
.weit{display:grid;grid-template-columns:1fr;gap:var(--e3);grid-auto-rows:1fr}
/* A fixed column count as soon as the COLUMN is wide enough — not the
   window. The generator picks it so the last row comes out full: six cards
   give three plus three instead of four plus two. */
@container (min-width:56rem){
.raster.s2{grid-template-columns:repeat(2,minmax(0,1fr))}
.raster.s3{grid-template-columns:repeat(3,minmax(0,1fr))}
.raster.s4{grid-template-columns:repeat(4,minmax(0,1fr))}}
@container (min-width:38rem){.weit{grid-template-columns:1fr 1fr}}
.weit .minikurve{height:calc(var(--zeile) * 3.2)}
.weit dd.grafik{min-height:calc(var(--zeile) * 3.6)}
.karte{background:var(--fl);border:1px solid var(--rand);border-radius:var(--rad);
padding:var(--e4);display:flex;flex-direction:column;container-type:inline-size}
.karte h2{font-size:.66rem;text-transform:uppercase;letter-spacing:.12em;
color:var(--sehrleise);font-weight:600;margin-bottom:var(--e3)}
/* No row-gap, a fixed minimum row height instead: that keeps every row on
   the same grid even when one card contains a graph. Before this every graph
   pushed everything below it out of line and the neighbouring cards no longer
   matched. */
dl{display:grid;grid-template-columns:auto 1fr;row-gap:0;column-gap:var(--e3);
font-size:.83rem}
dt,dd{min-height:var(--zeile);display:flex;align-items:center}
dt{color:var(--leise);white-space:nowrap}
dd{justify-content:flex-end;text-align:right;font-family:var(--mono);
font-variant-numeric:tabular-nums;font-size:.8rem;word-break:break-word}
dd.warn{color:var(--warn)}
dd.gut{color:var(--akzent)}
dd.leer{color:var(--sehrleise)}
dd.grafik{grid-column:1/-1;justify-content:flex-start;
min-height:calc(var(--zeile) * 2)}
/* Inner columns of a card: two value lists side by side, each with a small
   heading, a hairline between them. Below the card's own width threshold
   they stack. The graph at the foot of each column ends the column, so
   both come out the same height when the rows match. */
.spalten{display:grid;grid-template-columns:1fr;gap:var(--e3) var(--e5)}
@container (min-width:30rem){.spalten{grid-template-columns:1fr 1fr}
.spalte+.spalte{border-left:1px solid var(--rand);padding-left:var(--e5)}}
.spalte h3{font-size:.62rem;text-transform:uppercase;letter-spacing:.12em;
color:var(--sehrleise);font-weight:600;min-height:var(--zeile);
display:flex;align-items:center;border-bottom:1px solid var(--rand)}
.spalte dl{flex:1}
.spalte{display:flex;flex-direction:column;min-width:0}
dt.grafiklabel{grid-column:1/-1;color:var(--sehrleise);font-size:.65rem;
text-transform:uppercase;letter-spacing:.09em;align-items:flex-end;
padding-bottom:var(--e1)}
.minikurve{display:block;width:100%;height:calc(var(--zeile) * 1.6)}
/* Skeleton instead of graph: dashed and muted so that nobody mistakes it
   for a measurement on any screenshot. */
.geruestlinie{stroke:var(--randhell);stroke-width:1.5;stroke-dasharray:4 5}
.geruestteil{fill:var(--randhell);opacity:.45}
.geruest{opacity:.65}
/* Bar: track and rounded corners from CSS, exact width from the SVG. A
   style attribute would be dropped by the CSP, and an 'rx' on the rectangle
   would be distorted by preserveAspectRatio="none". */
.balken{display:block;width:100%;background:var(--rand);border-radius:99px;
overflow:hidden;line-height:0}
.balken svg{display:block;width:100%;height:100%}
.hoch6{height:6px}
.hoch10{height:10px}
.balkenfuellung{fill:var(--akzent)}
.balkenfuellung.warn{fill:var(--warn)}
.balkenfuellung.fehler{fill:var(--fehler)}

/* Full width: values on the left, addresses to copy on the right. */
@media(min-width:60rem){
.karte.voll{display:grid;grid-template-columns:minmax(0,17rem) minmax(0,1fr);
gap:var(--e1) var(--e6);align-items:start}
.karte.voll h2,.karte.voll .kartenfuss{grid-column:1/-1}}
.kopierblock{display:grid;gap:var(--e2);align-content:start}
.kopierfeld .kopierlabel{display:block;color:var(--sehrleise);font-size:.65rem;
text-transform:uppercase;letter-spacing:.1em;margin-bottom:var(--e1)}
/* The text wraps instead of running out of the card. This used to be
   'nowrap' with a scrollbar of its own so a click would select the whole
   line — since there is a copy button that is no longer needed, and a
   62-character onion address fits into no half card width. */
.kopierzeile{display:flex;align-items:stretch;gap:var(--e2);min-width:0}
.kopierfeld .kopier{flex:1;min-width:0;font-family:var(--mono);font-size:.79rem;
user-select:all;-webkit-user-select:all;color:var(--text);
background:var(--vertief);border:1px solid var(--randhell);border-radius:8px;
padding:var(--e2) var(--e3);line-height:1.5;
white-space:normal;word-break:break-all;overflow-wrap:anywhere}
/* Only appears once dash.js has wired it up — a button that does nothing
   would be worse than no button. */
.kopierknopf{display:none;flex:none;align-items:center;justify-content:center;
width:2.2rem;background:var(--vertief);color:var(--leise);
border:1px solid var(--randhell);border-radius:8px;cursor:pointer;padding:0}
.kopierknopf.bereit{display:flex}
.kopierknopf svg{width:1rem;height:1rem;fill:none;stroke:currentColor;
stroke-width:1.4;stroke-linecap:round;stroke-linejoin:round}
.kopierknopf:hover{color:var(--text);border-color:var(--randhervor)}
.kopierknopf:focus-visible{outline:2px solid var(--akzent);outline-offset:2px}
.kopierknopf.fertig{color:var(--akzent);border-color:var(--akzent)}
.kartenfuss{margin-top:var(--e3);padding-top:var(--e2);border-top:1px solid var(--rand);
color:var(--leise);font-size:.73rem;line-height:1.55}

/* ------------------------------------------------------------ Netzkarte -- */
.netz .kopfzeile{display:flex;justify-content:space-between;align-items:baseline;
gap:var(--e4);margin-bottom:var(--e2);flex-wrap:wrap}
.netz h2{margin:0}
.netzzahlen{display:flex;flex-wrap:wrap;gap:var(--e1) var(--e4);
color:var(--sehrleise);font-size:.72rem}
.netzzahlen b{color:var(--text);font-family:var(--mono);font-weight:600;
font-variant-numeric:tabular-nums}
/* Chain check: one dot per sample of the last hour. Green = same height,
   grey = the stranger is behind, red = the stranger is ahead of us. */
.abgleich{display:inline-flex;align-items:center;gap:var(--e2)}
.abgleich.warn{color:var(--fehler)}
.stiche{display:inline-flex;gap:3px}
.stich{width:.42rem;height:.42rem;border-radius:99px;display:block}
.stich.gleich{background:var(--akzent)}
.stich.hinten{background:var(--randhell)}
.stich.voraus{background:var(--fehler);box-shadow:0 0 0 2px color-mix(in srgb,var(--fehler) 30%,transparent)}
/* The card fills its half of the row instead of ending wherever its content
   happens to end: zone, card and drawing box each grow, so the lower edge of
   the card comes to lie on the lower edge of the block to its left. The
   drawing itself is never stretched — it keeps the aspect ratio of its
   viewBox (whose width follows the longest label) and the surplus stays as
   air between drawing and legend.
   No 'display:flex' on the drawing box: an <svg> as a flex item does not
   shrink below its intrinsic width and would push out of a narrow column. */
.netzzone{display:flex;flex-direction:column;flex-grow:1}
.netz{display:flex;flex-direction:column;min-width:0;flex-grow:1}
#netzkarte{min-width:0;flex-grow:1}
.netzkarte{display:block;width:100%;height:auto;max-height:100%;margin:0 auto}
/* Fallback while getpeerinfo is not allowed. */
.netzersatz{grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
gap:var(--e1) var(--e5)}
.netzersatz dd{text-align:left}

/* Fan: the hub in the middle, one spoke per row to the left and right,
   with the key figures on the line. */
/* No dashed halo around the hub any more: the mark carries itself, the ring
   was decoration and got in the way of the spokes. */

.eigentext{fill:var(--sehrleise);font-family:var(--sans);font-size:11px;
letter-spacing:.09em;text-transform:uppercase}
.peerlinie{stroke:var(--randhell);stroke-width:1;stroke-opacity:.55}
.peerzeile{font-family:var(--mono);font-size:12.5px;fill:var(--leise)}
.peerpunkt{fill:none;stroke-width:1.6}
.peerpunkt.voll{fill:currentColor}
/* Colour per network type set once on the group; dot and line inherit it. */
.peer{cursor:pointer;outline:none;color:var(--leise)}
.peer.ipv4{color:var(--netz-ipv4)}
.peer.ipv6{color:var(--netz-ipv6)}
.peer.onion{color:var(--netz-onion)}
.peer.electrs{color:var(--netz-electrs)}
/* No permanently coloured spoke for electrs: tried on 2026-09-01, looked
   wrong next to the grey fan. Dot and label carry the colour. */
.peer.i2p,.peer.cjdns{color:var(--netz-i2p)}
.peerpunkt{stroke:currentColor}
/* The large transparent area makes pointing easy — the visible parts are
   thin, the target may be generous. */
.peerflaeche{fill:transparent}
.peer:hover .peerzeile,.peer:focus-visible .peerzeile,
.peer[data-aktiv] .peerzeile{fill:var(--text)}
.peer:hover .peerlinie,.peer:focus-visible .peerlinie,
.peer[data-aktiv] .peerlinie{stroke:currentColor;stroke-opacity:1;
stroke-width:1.6}
.peer:hover .peerflaeche,.peer:focus-visible .peerflaeche,
.peer[data-aktiv] .peerflaeche{fill:color-mix(in srgb,currentColor 9%,transparent)}
/* Where the last block came from and where it went. The source spoke is
   orange and a little wider; the receivers keep their network colour but
   the spoke is lit as if pointed at, so the fan shows the path at a glance.
   The dot of the source gets a second ring rather than a fill — filled
   already means outbound. */
.peer.ansager .peerlinie{stroke:var(--block);stroke-opacity:1;stroke-width:2}
.peer.ansager .peerpunkt{stroke:var(--block);stroke-width:2.2;
filter:drop-shadow(0 0 3px var(--block))}
/* The deliverer, when it is not also the announcer: a solid spoke in the
   peer's own network colour. Dashed orange was tried first and dropped on
   2026-09-01 — two oranges read as one thing. */
.peer.quelle .peerlinie{stroke:currentColor;stroke-opacity:1;stroke-width:2}
.peer.quelle .peerpunkt{stroke-width:2.2}
.peer.empfaenger .peerlinie{stroke:currentColor;stroke-opacity:.85;
stroke-width:1.6}
.netzfarbe.ansager{background:var(--block)}
.netzfarbe.quelle{background:var(--leise)}
.netzfarbe.empfaenger{background:transparent;border:1.5px solid var(--leise)}
.peerlegende{display:flex;flex-wrap:wrap;gap:var(--e1) var(--e3);
color:var(--sehrleise);font-size:.68rem;margin-top:var(--e2)}
.peerlegende span{display:flex;align-items:center;gap:var(--e1)}
.netzfarbe{width:.5rem;height:.5rem;border-radius:99px;display:block;flex:none;
background:var(--leise)}
.netzfarbe.ipv4{background:var(--netz-ipv4)}
.netzfarbe.ipv6{background:var(--netz-ipv6)}
.netzfarbe.onion{background:var(--netz-onion)}
.netzfarbe.electrs{background:var(--netz-electrs)}
.netzfarbe.i2p,.netzfarbe.cjdns{background:var(--netz-i2p)}
/* The detail box has a fixed height. Otherwise the layout jumps every time
   you point at a different dot, and that looks cheap. */
/* Fixed minimum height. Without it the layout jumps every time you point at
   a different row, and that looks cheap. */
.peerdetail{background:var(--vertief);border:1px solid var(--rand);
border-radius:var(--rad);padding:var(--e3);min-height:4.6rem;
margin-top:var(--e3);display:flex;flex-wrap:wrap;align-items:center;
gap:var(--e2) var(--e5)}
.peerdetail .leer{color:var(--sehrleise);font-size:.76rem;line-height:1.6}
/* The block sentence is the default content of the box and reads as a
   statement, not as a hint — hence the normal text colour. */
.peerdetail .blockweg{color:var(--leise);font-size:.76rem;line-height:1.6;
flex-basis:100%}
.peerdetail .blockweg:empty{display:none}
/* The detail box runs as a line, not in the grid — the baseline row does
   not apply here. */
.peerdetail dl{display:flex;flex-wrap:wrap;gap:var(--e1) var(--e5);
font-size:.76rem;grid-template-columns:none}
.peerdetail dt{color:var(--sehrleise);font-size:.66rem;min-height:0;
text-transform:uppercase;letter-spacing:.09em;align-self:center}
.peerdetail dd{font-size:.76rem;text-align:left;min-height:0;
justify-content:flex-start;margin-right:var(--e3)}
.peerdetail .padresse{font-family:var(--mono);font-size:.76rem;
word-break:break-all;color:var(--text);line-height:1.45;flex-basis:100%}
.peerdetail .pkopf{display:flex;align-items:center;gap:var(--e2);
font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;
color:var(--sehrleise)}
/* The colour dots here use the same .netzfarbe rules as the legend. */

/* ------------------------------------------------------------- Stoerung -- */
.fehlerkarte{background:var(--fl);border:1px solid var(--fehler);
border-radius:var(--rad);padding:var(--e4)}
.fehlerkarte h2{color:var(--fehler);font-size:.9rem;margin-bottom:var(--e1);
text-transform:none;letter-spacing:0}
.fehlerkarte p{color:var(--leise);font-size:.82rem;line-height:1.6}
.fehlerkarte p+p{margin-top:var(--e2)}
.fehlerkarte code{background:var(--vertief);padding:.1rem .35rem;border-radius:5px;
font-family:var(--mono);font-size:.78rem;color:var(--text)}
/* The note about stale values is deliberately quiet: the node is there, it
   just is not answering right now. A red card would be a lie here. */
.veraltet,.meldung{background:color-mix(in srgb,var(--warn) 8%,var(--fl));
border:1px solid color-mix(in srgb,var(--warn) 35%,var(--rand));
border-radius:var(--rad);padding:var(--e3) var(--e4);color:var(--leise);
font-size:.8rem;display:flex;align-items:center;gap:var(--e3);line-height:1.6}
.veraltet b,.meldung b{color:var(--warn);font-weight:600}
.veraltet .punkt,.meldung .punkt{background:var(--warn);
box-shadow:0 0 0 4px color-mix(in srgb,var(--warn) 18%,transparent)}
.meldung code{background:var(--vertief);padding:.1rem .35rem;border-radius:5px;
font-family:var(--mono);font-size:.76rem;color:var(--text)}
/* Mehrere Meldungen untereinander brauchen Luft dazwischen. */
#z-stoerung>*+*{margin-top:var(--e3)}

/* ------------------------------------------------------------ Protokoll -- */
/* The log fills the rest of the right column. The box inside claims
   whatever the header leaves over — which makes it flush at the bottom with
   the card grid on the left. */
.protokoll{display:flex;flex-direction:column;min-width:0;flex:1;
min-height:16rem}
.protokoll .kopfzeile{display:flex;justify-content:space-between;align-items:baseline;
gap:var(--e4);margin-bottom:var(--e2)}
.protokoll h2{margin:0}
.protokoll .kopfzeile{align-items:center}
.protokoll .hinweis{color:var(--sehrleise);font-size:.7rem;text-transform:none;
letter-spacing:0;font-weight:400;margin-left:auto}
/* The box claims the remaining space of the column; the <pre> inside sits
   absolutely and therefore contributes nothing to height calculation. Without
   this trick the number of log lines dictates the height of the whole page:
   150 lines are 2674 px, the left column is about 920 px tall. A grid item is
   sized by its content, and 'min-height:0' changes nothing about that — it
   only caps the minimum height, not the natural one. */
.logbox{flex:1;min-height:0;min-width:0;position:relative}
.protokoll pre{position:absolute;inset:0;margin:0;overflow:auto;
background:var(--vertief);border:1px solid var(--rand);border-radius:8px;
padding:var(--e2) var(--e3);font-family:var(--mono);font-size:11.5px;
line-height:1.55;color:var(--leise);white-space:pre;tab-size:4}
.protokoll pre::-webkit-scrollbar{width:8px;height:8px}
.protokoll pre::-webkit-scrollbar-thumb{background:var(--randhell);border-radius:9px}

footer{color:var(--sehrleise);font-size:.72rem;text-align:center;
padding-top:var(--e3);border-top:1px solid var(--rand)}

@media(max-width:36rem){
body{padding:var(--e4) var(--e3) var(--e5)}
.zustand{padding:var(--e4);grid-template-columns:1fr}
.zrechts{text-align:left}
.zwort{font-size:1.4rem}.zzahl{font-size:1.35rem}
.netzkarte{max-height:20rem}}

@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


SCRIPT = r"""
/* node-dashboard — the entire moving layer of the page.
 *
 * What it does: it fetches status.json and log.txt from the same web
 * server and fills the values in without reloading the page. Nothing else.
 *
 * What it does NOT do, and that is the point: it sends nothing to the node,
 * it accepts no input, it calls no foreign address. Both files are static
 * and live in the same folder as this one.
 *
 * On safety: foreign strings — log lines, peer addresses, identifiers of
 * other nodes — are set exclusively via textContent. No markup can arise
 * there, whatever the content. innerHTML receives only what the generator
 * built itself and already escaped there.
 */
(function () {
  "use strict";

  /* Strings and number notation are substituted by the generator while
     writing this file. It is therefore language-bound — which is harmless
     because an installation has exactly one language. The fingerprint on the
     URL is built over the finished text, so a language change shows up as a
     new file rather than an old one from the cache. */
  var T = __TEXTE__;
  var KOMMA = __KOMMA__;       /* true = deutsches Dezimalkomma */

  var wurzel = document.documentElement;
  var takt = (Number(wurzel.dataset.intervall) || 30) * 1000;
  var logtakt = (Number(wurzel.dataset.logintervall) || 5) * 1000;
  var peers = [];
  var gemerkt = null;          // pinned peer, survives the refresh
  var blockweg = "";           // sentence about the last block's path
  var rangliste = "";          // who announced first most often, 24 h
  var erzeugt = 0;             // when status.json was written, unix seconds

  /* Without JS a <meta refresh> reloads the page periodically. With JS that
     would be harmful: it would reload in the middle of pointing at a dot. */
  var refresh = document.querySelector('meta[http-equiv="refresh"]');
  if (refresh) { refresh.remove(); }

  function hole(pfad, alsText) {
    return fetch(pfad, { cache: "no-store", credentials: "omit" })
      .then(function (a) {
        if (!a.ok) { throw new Error(a.status); }
        return alsText ? a.text() : a.json();
      });
  }

  /* ------------------------------------------------------- Netzkarte --- */

  function zeile(dl, bezeichnung, wert) {
    var dt = document.createElement("dt");
    dt.textContent = bezeichnung;
    var dd = document.createElement("dd");
    dd.textContent = wert;      // foreign text — never as markup
    dl.appendChild(dt);
    dl.appendChild(dd);
  }

  /* The same rule as on the server side: every number goes through here so
     comma and period never end up in the wrong role at some forgotten
     spot. */
  function komma(text) { return KOMMA ? text.replace(".", ",") : text; }

  function bytes(n) {
    var e = ["B", "KB", "MB", "GB"], i = 0;
    while (n >= 1000 && i < e.length - 1) { n /= 1000; i++; }
    return komma(n.toFixed(1)) + " " + e[i];
  }

  function dauer(s) {
    if (s >= 86400) { return Math.floor(s / 86400) + T.tag + " " + Math.floor((s % 86400) / 3600) + T.std; }
    if (s >= 3600) { return Math.floor(s / 3600) + T.std + " " + Math.floor((s % 3600) / 60) + T.min; }
    if (s >= 60) { return Math.floor(s / 60) + T.min; }
    return s + " s";
  }

  function zeigePeer(nr) {
    var kasten = document.getElementById("peerdetail");
    if (!kasten) { return; }
    var p = peers[nr];
    kasten.textContent = "";
    if (!p) {
      var satz = document.createElement("p");
      satz.className = "blockweg";
      satz.textContent = blockweg;
      kasten.appendChild(satz);
      var rang = document.createElement("p");
      rang.className = "blockweg";
      rang.textContent = rangliste;
      kasten.appendChild(rang);
      var hinweis = document.createElement("p");
      hinweis.className = "leer";
      hinweis.textContent = T.hinweis;
      kasten.appendChild(hinweis);
      return;
    }

    var kopf = document.createElement("div");
    kopf.className = "pkopf";
    var farbe = document.createElement("i");
    /* A class rather than style: the same rule as in the generated markup, so
       the same colours apply here and there and nothing depends on an inline
       rule. */
    farbe.className = "netzfarbe " + (p.netzart || "neutral");
    kopf.appendChild(farbe);
    var art = document.createElement("span");
    art.textContent = p.netzname + " · " + (p.eingehend ? T.eingehend : T.ausgehend);
    kopf.appendChild(art);
    kasten.appendChild(kopf);

    var adr = document.createElement("div");
    adr.className = "padresse";
    adr.textContent = p.adresse;
    kasten.appendChild(adr);

    /* Address, network type, latency and data volume already stand on the
       line. Only what does not fit there is added here. */
    var dl = document.createElement("dl");
    zeile(dl, T.kennung, p.version);
    zeile(dl, T.dienste, p.dienste);
    zeile(dl, T.verbunden, dauer(p.dauer_s));
    /* The response time measures our own load, not the peer — which is why it
       sits here and not on the line. */
    if (p.jetzt_ms !== null && p.jetzt_ms !== undefined) {
      zeile(dl, T.antwort, p.jetzt_ms < 1000
        ? Math.round(p.jetzt_ms) + " ms"
        : komma((p.jetzt_ms / 1000).toFixed(1)) + " s");
    }
    zeile(dl, T.empfangen, bytes(p.empfangen));
    zeile(dl, T.gesendet, bytes(p.gesendet));
    /* Blocks in both directions, counted since the generator started. The
       time is relative to when status.json was written, not to now — the
       box is rebuilt on every refresh anyway. */
    zeile(dl, T.bloecke_von, blockzahl(p.bloecke_von, p.zuletzt_von));
    zeile(dl, T.bloecke_an, blockzahl(p.bloecke_an, p.zuletzt_an));
    kasten.appendChild(dl);
  }

  function blockzahl(anzahl, zeit) {
    if (!zeit) { return "—"; }
    var her = Math.max(0, erzeugt - zeit);
    var wann = T.zuletzt.replace("{when}", her < 90 ? T.vor.replace("{x}", her + " s")
      : T.vor.replace("{x}", dauer(her)));
    return (anzahl ? anzahl + " · " : "") + wann;
  }

  function verdrahtePeers() {
    var karte = document.getElementById("netzkarte");
    if (!karte) { return; }
    karte.querySelectorAll(".peer").forEach(function (g) {
      var nr = Number(g.dataset.nr);
      g.addEventListener("mouseenter", function () { zeigePeer(nr); });
      g.addEventListener("focus", function () { zeigePeer(nr); });
      /* A click freezes the display — handy when selecting the address without
         it vanishing as the mouse moves away. */
      g.addEventListener("click", function () {
        gemerkt = (gemerkt === nr) ? null : nr;
        karte.querySelectorAll(".peer").forEach(function (a) {
          a.removeAttribute("data-aktiv");
        });
        if (gemerkt !== null) { g.setAttribute("data-aktiv", ""); }
      });
    });
    karte.addEventListener("mouseleave", function () {
      zeigePeer(gemerkt === null ? -1 : gemerkt);
    });
    zeigePeer(gemerkt === null ? -1 : gemerkt);
  }

  /* -------------------------------------------------------- Nachtragen --- */

  function setzeZone(kennung, markup) {
    var ziel = document.getElementById(kennung);
    if (!ziel || markup === undefined || markup === null) { return; }
    /* innerHTML is defensible here: the content comes from the same file this
       server hands out as index.html anyway, and went through the same
       escaping in the generator. */
    if (ziel.innerHTML !== markup) { ziel.innerHTML = markup; }
  }

  function nachtragen(daten) {
    if (!daten || !daten.zonen) { return; }
    if (daten.titel) { document.title = daten.titel; }
    wurzel.dataset.stufe = daten.stufe || "ok";
    wurzel.dataset.frisch = daten.veraltet ? "alt" : "ja";

    setzeZone("z-kopf", daten.zonen.kopf);
    setzeZone("z-zustand", daten.zonen.zustand);
    setzeZone("z-stoerung", daten.zonen.stoerung);
    setzeZone("z-band", daten.zonen.band);
    setzeZone("z-raster", daten.zonen.raster);

    /* The column count depends on how many cards exist right now — three more
       appear on the jump to 100 %. */
    var raster = document.getElementById("z-raster");
    if (raster && daten.spalten) {
      raster.className = "raster s" + daten.spalten;
    }
    setzeZone("z-weit", daten.zonen.weit);
    setzeZone("z-voll", daten.zonen.voll);

    var stempel = document.getElementById("stempel");
    if (stempel && daten.stempel) { stempel.textContent = daten.stempel; }

    /* The network map is not replaced while the mouse is inside it or a dot is
       pinned. Otherwise the dot vanishes from under the pointer and the
       detail box jumps away while you are reading it. */
    var karte = document.getElementById("netzkarte");
    if (karte && (karte.matches(":hover") || gemerkt !== null)) { return; }

    setzeZone("z-netz", daten.zonen.netz);
    peers = daten.peers || [];
    blockweg = daten.blockweg || "";
    rangliste = daten.rangliste || "";
    erzeugt = daten.erzeugt || 0;
    verdrahtePeers();
  }

  function holeStatus() {
    hole("status.json", false)
      .then(nachtragen)
      .catch(function () { wurzel.dataset.frisch = "alt"; });
  }

  function holeProtokoll() {
    var kasten = document.getElementById("logtext");
    if (!kasten) { return; }
    hole("log.txt", true).then(function (text) {
      /* Plain text from a foreign source: Bitcoin Core logs the self-chosen
         identifiers of other nodes. textContent, always. */
      if (kasten.textContent !== text) {
        var oben = kasten.parentNode.scrollTop;
        kasten.textContent = text;
        kasten.parentNode.scrollTop = oben;
      }
    }).catch(function () { });
  }

  /* ------------------------------------------------------- Kopierknopf --- */

  function inZwischenablage(text) {
    /* navigator.clipboard exists only in a "secure context", i.e. over HTTPS
       or on localhost. This page runs on the local network over http:// —
       there the interface simply is not present. Hence the old route as a
       fallback: a text field off screen, select, copy, discard. */
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (fertig, daneben) {
      var feld = document.createElement("textarea");
      feld.value = text;
      feld.setAttribute("readonly", "");
      feld.style.position = "fixed";
      feld.style.left = "-9999px";
      document.body.appendChild(feld);
      feld.select();
      var geklappt = false;
      try { geklappt = document.execCommand("copy"); } catch (e) { }
      document.body.removeChild(feld);
      geklappt ? fertig() : daneben();
    });
  }

  document.querySelectorAll(".kopierknopf").forEach(function (knopf) {
    knopf.addEventListener("click", function () {
      inZwischenablage(knopf.dataset.wert).then(function () {
        knopf.classList.add("fertig");
        setTimeout(function () { knopf.classList.remove("fertig"); }, 1200);
      }).catch(function () {
        /* If that fails we at least select the text so Ctrl+C works. */
        var feld = knopf.parentNode.querySelector(".kopier");
        if (!feld) { return; }
        var bereich = document.createRange();
        bereich.selectNodeContents(feld);
        var auswahl = window.getSelection();
        auswahl.removeAllRanges();
        auswahl.addRange(bereich);
      });
    });
    knopf.classList.add("bereit");
  });

  /* Fetch once immediately on the first run: the page is already complete
     server-side, but the peer details for the detail box exist only in
     status.json — embedding them as an inline script would weaken the strict
     Content Security Policy, and that is not worth it. */
  holeStatus();
  holeProtokoll();
  setInterval(holeStatus, takt);
  setInterval(holeProtokoll, logtakt);
})();
"""


# Fingerprint of style and script. It rides on the URL in the page
# ("stil.css?v=1a2b3c4d") so the browser fetches a new version at once and
# keeps taking an unchanged one from its cache.
#
# Without it the page was broken for up to ten minutes after every program
# swap: new HTML met old rules, and the bars grew into green blocks because
# their height class did not yet exist in the old style.
def _fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


STYLE_V = _fingerprint(STYLE)


def script_text():
    """dash.js in the configured language.

    The strings do not travel through status.json but are substituted here.
    The reason: without an inline script there is no other way to hand the
    browser any strings, and status.json is meant to carry data, not user
    interface. An installation has exactly one language anyway.
    """
    strings = {
        # 'hinweis', not 'note': dash.js reads T.hinweis. Same rename damage
        # as 'antwort' below — the detail box showed nothing at all while
        # not pointing, and that looked like an empty box by design.
        "hinweis": t("Point at a line for identifier, dienste and connection time."),
        "eingehend": t("eingehend|richtung"),
        "ausgehend": t("outbound|richtung"),
        "kennung": t("Identifier"),
        "dienste": t("Services"),
        "verbunden": t("Connected for"),
        # 'antwort', not 'response': dash.js reads T.antwort. The rename of
        # 2026-08-23 moved this key and the row label in the detail box has
        # read "undefined" since. check_script_strings compares both sides
        # now (2026-09-01).
        "antwort": t("Response right now"),
        "bloecke_von": t("Blocks from here"),
        "bloecke_an": t("Blocks to here"),
        # These two keep their placeholder: the browser fills it in. Passing
        # the placeholder as its own value is what makes t() leave it alone
        # — and satisfies the check that every call names its placeholders.
        "zuletzt": t("last {when}", when="{when}"),
        "vor": t("{x} ago|kurz", x="{x}"),
        "empfangen": t("Received"),
        "gesendet": t("Sent"),
        # Short forms for durations. German puts a space in front ("3 T"),
        # English does not ("3d") — so the space is part of the string.
        "tag": t(" d|kurz"),
        "std": t(" h|kurz"),
        "min": t(" min|kurz"),
    }
    return (SCRIPT
            .replace("__TEXTE__", json.dumps(strings, ensure_ascii=False))
            .replace("__KOMMA__", "true" if LANGUAGE == "de" else "false"))


def script_v():
    """Fingerprint over the finished text, not over the template.

    Otherwise a page would get the old dash.js from the browser cache after a
    language change — with German labels inside an English page.
    """
    return _fingerprint(script_text())
BITCOIN_V = hashlib.sha256(BITCOIN_PNG).hexdigest()[:8]


def html_escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )



def assess_state(error, in_sync, groups, stale_for=None, warming_up=False,
                 extra_warnings=None):
    """Boil the overall state down to one word.

    The warnings are not listed one by one but harvested from the cards: every
    field marked "warn" counts. This function therefore needs to know nothing
    about individual measurements.

    'stale_for' is the special case from the tolerance window: the node is
    not answering right now, but we still hold a fresh state. That is not a
    fault but a delay — and it is named as such.
    """
    if error:
        return "fehler", t("Not reachable"), t("Bitcoin Core is not answering")
    if warming_up:
        if WARMUP_MESSAGE[0]:
            return ("anlauf", t("Bitcoin Core is starting"), WARMUP_MESSAGE[0])
        return ("anlauf", t("Waiting for Bitcoin Core"),
                t("No answer since this display started"))
    if stale_for is not None:
        return ("veraltet", t("Node answering slowly"),
                t("Values shown were measured {age}",
                  age=format_age(stale_for)))

    warnungen = [f"{f[0]}: {f[1]}"
                 for g in groups for f in g[1]
                 if len(f) > 2 and f[2] == "warn"]
    # Warnings that belong to no card — the chain check lives in the head
    # of the network map. They come first: a stranger ahead of us is the
    # one thing here that means the node might be lied to.
    warnungen = [w for w in (extra_warnings or []) if w] + warnungen

    if not in_sync:
        return "sync", None, None
    if warnungen:
        count = len(warnungen)
        word = t("One notice") if count == 1 else t("{n} notices", n=count)
        return "warn", word, warnungen[0]
    return "ok", t("All good"), None


# Which card goes where. Everything else lands in the grid.
#   "wide" — half the page width, for graphs with many values
#   "full" — the whole width, so long addresses fit on one line
CARDS_WIDE = ("Volume · 24 hours", "Fee history · 24 hours")
CARDS_FULL = ("Electrum server",)

# Order inside the grid, spelled out. It used to fall out of the order in
# which the collect_* functions happen to be called — which is an accident,
# not a design decision. Anything not listed here is appended at the end.
CARD_ORDER = (
    "System",
    "Network",
)


def render_card(group, extra_class=""):
    """Build one card. Copy fields go into a block of their own so that in
    wide cards they sit next to the value list instead of wrapping."""
    title, fields = group[0], group[1]
    footnote = group[2] if len(group) > 2 else ""
    if not fields:
        return ""

    # A field of class "spalte" opens an inner column; its label is the
    # column's small heading. Rows before the first such field would be
    # lost, so a card either uses columns throughout or not at all.
    rows, copy_fields = [], []
    columns = []          # [(heading, [row markup])]
    for entry in fields:
        label, value = entry[0], entry[1]
        cls = entry[2] if len(entry) > 2 else ""
        if cls == "spalte":
            columns.append((label, []))
            rows = columns[-1][1]
            continue
        if cls == "kopier":
            copy_fields.append((label, value))
        elif cls == "grafik":
            # CAREFUL: nothing is escaped here, on purpose. The content is
            # SVG curves and bars that this program produced itself. NEVER
            # mark a value from a foreign source as "grafik".
            if label:
                rows.append(f"<dt class=grafiklabel>{html_escape(label)}</dt>")
            rows.append(f"<dd class=grafik>{value}</dd>")
        else:
            css = f" class={cls}" if cls in ("warn", "gut", "leer") else ""
            rows.append(f"<dt>{html_escape(label)}</dt>"
                          f"<dd{css}>{html_escape(value)}</dd>")

    classes = "karte" + (f" {extra_class}" if extra_class else "")
    # Only here is the title translated: up to this point it is the identity
    # of the card and must be the same string in both languages.
    parts = [f'<section class="{classes}"><h2>{html_escape(t(title))}</h2>']
    if columns:
        parts.append('<div class=spalten>')
        for heading, column_rows in columns:
            parts.append(f"<div class=spalte><h3>{html_escape(heading)}</h3>"
                         f"<dl>{''.join(column_rows)}</dl></div>")
        parts.append("</div>")
    elif rows:
        parts.append("<dl>" + "".join(rows) + "</dl>")
    if copy_fields:
        parts.append("<div class=kopierblock>")
        for label, value in copy_fields:
            # The button touches the browser clipboard, nothing else.
            # Nothing reaches the node — that stays the boundary of this
            # page. Without JavaScript it is hidden, and the text can still
            # be selected by hand.
            parts.append(
                "<div class=kopierfeld>"
                f"<span class=kopierlabel>{html_escape(label)}</span>"
                "<div class=kopierzeile>"
                f"<code class=kopier>{html_escape(value)}</code>"
                '<button type=button class=kopierknopf '
                f'data-wert="{html_escape(value)}" '
                f'aria-label="{html_escape(label)} kopieren">'
                # Two offset rectangles — the usual "copy" glyph, drawn as
                # a path rather than taken from a font.
                '<svg viewBox="0 0 16 16" aria-hidden="true">'
                '<rect x="5.5" y="1.5" width="9" height="11" rx="1.5"/>'
                '<path d="M10.5 14.5H3A1.5 1.5 0 0 1 1.5 13V4.5"/>'
                "</svg></button></div></div>"
            )
        parts.append("</div>")
    if footnote:
        parts.append(f"<p class=kartenfuss>{html_escape(footnote)}</p>")
    parts.append("</section>")
    return "".join(parts)


def build_metrics_bar(kz, level):
    """Four numbers that always apply — the first thing the eye lands on."""
    # (value, label, class, small note underneath)
    tiles = []

    # The first tile replaced the 'Blockchain' card: two figures to compare
    # — what the node has verified and what the network knows — with the
    # space taken on disk in small print underneath.
    if kz.get("kopfzeilen"):
        # Only the space used. The verification state already stands in the
        # state bar above and need not be repeated here.
        extra = t("{n} on disk", n=format_bytes(kz.get("belegt", 0)))
        if kz.get("gepruned"):
            extra += t(" · pruning active")
        tiles.append((
            f'{format_number(kz.get("bloecke", 0))}'
            f'<span class=kvon>{html_escape(t("of"))}</span>'
            f'{format_number(kz["kopfzeilen"])}',
            t("bloecke verified · known to the network"), "", extra, True,
        ))

    fees = kz.get("gebuehren") or {}
    median = kz.get("median_gebuehr")
    if level == "sync":
        tiles.append((format_number(kz.get("rueckstand", 0)),
                        t("bloecke rueckstand"), "", "", False))
    elif median is not None:
        # The fee to enter with a transaction — the one number you want
        # without looking for it. It took the mempool tile's place on
        # 2026-09-01; the count is still in the 'Mempool & fees' card.
        # Shown large is the median of the last block: half of what got in
        # paid more, half paid less. Core's own estimate for the next block
        # goes underneath in small print for comparison.
        extra = ""
        if fees.get(1):
            extra = t("estimate for the next block: {fee}",
                      fee=decimal_sep(f"{fees[1]:.1f}"))
        tiles.append((
            decimal_sep(f"{median:.1f}") + "<span class=kvon>sat/vB</span>",
            t("median fee in the last block"), "gut", extra, True))
    elif kz.get("mempool") is not None:
        tiles.append((format_number(kz["mempool"]), t("in the mempool"),
                        "", "", False))

    verbindungen = kz.get("verbindungen")
    if verbindungen is not None:
        tiles.append((str(verbindungen), t("Connections"),
                        "warn" if verbindungen < 8 else "gut", "", False))

    if TEMP_HISTORY:
        temp = TEMP_HISTORY[-1][1]
        kind = "warn" if temp >= 75 else ("" if temp >= 60 else "gut")
        tiles.append((decimal_sep(f"{temp:.1f} °C"), t("Temperature"),
                        kind, "", False))

    if not tiles:
        return ""

    parts = []
    for value, label, kind, extra, raw in tiles:
        # 'raw' means: the value is markup this program built itself (the
        # small "of" between the numbers). Everything else is escaped — the
        # same rule as for the "grafik" class.
        classes = f"kachel {kind} breit" if raw else f"kachel {kind}"
        parts.append(
            f'<div class="{classes.strip()}">'
            f'<div class=kwert>{value if raw else html_escape(value)}</div>'
            f"<div class=klabel>{html_escape(label)}</div>"
            + (f"<div class=kzusatz>{html_escape(extra)}</div>" if extra else "")
            + "</div>"
        )
    return "".join(parts)


def format_percent(value, digits=2):
    """The big number at the top goes through number formatting as well.

    This is exactly where a "11.24 %" slipped through on 2026-08-23, because
    the test then only looked at the cards and not at the metrics bar.
    """
    return decimal_sep(f"{value:.{digits}f}")


def build_state_bar(level, word, extra, progress, kz):
    parts = ["<section class=zustand>"]
    percent = format_percent(progress)

    if level == "sync":
        subline = t("Syncing the blockchain")
        if kz.get("stand"):
            subline += " · " + kz["stand"]
        parts.append(
            '<div class=zlinks><span class=punkt></span><div>'
            f'<div class=zwort>{percent}&nbsp;%</div>'
            f"<div class=zzusatz>{html_escape(subline)}</div>"
            "</div></div>"
        )
        right_number = format_number(kz.get("bloecke", 0))
        right_label = html_escape(
            t("of {n} bloecke", n=format_number(kz.get("kopfzeilen", 0))))
    else:
        parts.append(
            '<div class=zlinks><span class=punkt></span><div>'
            f"<div class=zwort>{html_escape(word)}</div>"
            '<div class=zzusatz>'
            + html_escape(extra if extra else t("Node in sync, nothing unusual"))
            + "</div></div></div>"
        )
        right_number = format_number(kz.get("bloecke", 0))
        age = kz.get("blockalter")
        right_label = html_escape(
            t("Block · {age}", age=format_age(age)) if age
            else t("Block height"))

    parts.append(
        f'<div class=zrechts><div class=zzahl>{right_number}</div>'
        f'<div class=zlabel>{right_label}</div></div>'
    )

    if level == "sync":
        parts.append("<div class=balkenbox>")
        parts.append(build_bar(progress / 100, "", height=10))

        # During the sync, rate and remaining time are the only numbers that
        # really matter. They belong here and not in small print in some card
        # further down.
        tempo, eta = kz.get("tempo"), kz.get("restzeit")
        if tempo is not None:
            parts.append(
                '<div class=zfuss>'
                f'<span>{html_escape(tempo)}</span>'
                f'<span class=zrest>'
                f'{html_escape(t("about {remaining} left", remaining=eta))}</span>'
                "</div>"
            )
        else:
            parts.append('<div class=zfuss><span>'
                         + html_escape(t("Still measuring the rate"))
                         + "</span><span class=zrest></span></div>")

        curve = build_progress_curve()
        if curve:
            svg, label = curve
        else:
            svg = build_skeleton(t("History, still measuring"), 300, 54)
            label = t("History appears after about 15 minutes")
        parts.append(
            f'<div class=kurve>{svg}'
            f"<span class=kurvenfuss>{html_escape(label)}</span></div>"
        )
        parts.append("</div>")

    parts.append("</section>")
    return "".join(parts)


def build_trouble(error, stale_for, warming_up=False, tor=None):
    """The red card appears only once the node is really gone.

    Before that there is a quiet note that the numbers are a little old. That
    is the whole difference between "the node is broken" and "the node is
    writing its cache to disk right now".
    """
    # The Tor notice sits above everything else: if the node is about to
    # restart, that is the more important message.
    leading = build_tor_notice(tor)

    if warming_up:
        # Two different reasons land here. If the node told us what it is doing
        # ("Verifying blocks…"), that sentence beats anything we could write:
        # it names the phase and says the wait is expected.
        if WARMUP_MESSAGE[0]:
            return leading + (
                '<div class=veraltet><span class=punkt></span><div>'
                + t("<b>Bitcoin Core is starting up.</b> It reports: "
                    "{message} The figures shown are from before the restart.",
                    message=html_escape(WARMUP_MESSAGE[0]))
                + "</div></div>"
            )
        # Right after the service starts there is no previous state for the
        # tolerance window to hold on to. A missing first answer is still no
        # outage — bitcoind is probably writing its cache.
        return leading + (
            '<div class=veraltet><span class=punkt></span><div>'
            + t("<b>No answer from the node yet.</b> This display has just "
                "started and is waiting for its first reply. During the "
                "initial sync that can take a minute.")
            + "</div></div>"
        )
    if stale_for is not None:
        return leading + (
            '<div class=veraltet><span class=punkt></span><div>'
            + t("<b>The node is not answering right now.</b> Shown is the last "
                "measured state from {age}. During the initial sync this is "
                "normal: Bitcoin Core pauses its query interface while it "
                "writes its cache to disk.",
                age=html_escape(format_age(stale_for)))
            + "</div></div>"
        )
    if not error:
        return leading

    # The error strings come from rpc() and are English — the matching works
    # on them, not on the display. Otherwise we would have a branch here that
    # depended on the configured language.
    if "not reachable" in error or "timed out" in error:
        heading = t("Node not reachable")
        advice = t("Check with <code>systemctl status bitcoind</code> or "
                "<code>journalctl -u bitcoind -n 50</code>.")
    elif "401" in error:
        heading = t("Node refused the login")
        advice = t("The password in <code>/etc/node-dashboard.conf</code> does "
                "not match the <code>rpcauth</code> entry in bitcoin.conf.")
    else:
        heading = t("Node answers with an error")
        advice = t("The node is running but rejects a call. The method may be "
                "missing from <code>rpcwhitelist</code>.")
    return leading + (
        f"<div class=fehlerkarte><h2>{html_escape(heading)}</h2>"
        f"<p>{html_escape(error)}</p><p>{advice}</p></div>"
    )


# The colour lives in a class, not in a style attribute: an inline style
# would be dropped by the Content Security Policy and the dots would stay
# colourless. See the comment on build_bar.
LEGEND = (
    ("electrs", "Electrum"),
    ("onion", "Tor"),
    ("ipv4", "IPv4"),
    ("ipv6", "IPv6"),
    ("i2p", "I2P"),
)


def raster_spalten(count):
    """Pick a column count that leaves the last row as full as possible.

    Six cards in four columns leave two holes; in three columns none. When the
    network map and the 24 hour cards join later, the same rule picks four
    again on its own.
    """
    if count <= 2:
        return max(1, count)
    best, fewest = 4, 99
    for cols in (4, 3, 2):
        remaining = (-count) % cols
        if remaining < fewest:
            best, fewest = cols, remaining
    return best


def build_network_zone(peers, fallback_fields=None, blocked=False, kz=None,
                       in_sync=True):
    """The whole network card as a finished block: graph, legend, detail box.

    Without peer data — 'getpeerinfo' is not allowed until 06-tor.sh has run —
    the old value list takes the place of the drawing. Otherwise the
    connection figures would be nowhere to be seen for weeks, ever since the
    'Network' card was dropped.
    """
    svg = build_network_map(peers, kz)
    if not svg:
        if not fallback_fields:
            return ""
        rows = "".join(
            f"<dt>{html_escape(b)}</dt><dd>{html_escape(w)}</dd>"
            for b, w, _ in fallback_fields
        )
        # Two very different reasons for the same empty list, and they must
        # not get the same text: either the call is not allowed — then the
        # page must say what to do — or the node simply did not answer just
        # now, which is a delay.
        if blocked:
            kurz = t("drawing follows once the method is allowed")
            fuss = t("The network drawing needs the <code>getpeerinfo</code> "
                     "call. <code>06-tor.sh</code> allows it.")
        else:
            kurz = t("querying peers")
            fuss = t("The node has not delivered the peer list yet. During "
                     "the initial sync that occasionally takes longer than "
                     "the timeout.")
        return (
            '<section class="karte netz">'
            f"<div class=kopfzeile><h2>{html_escape(t('Connected nodes'))}</h2>"
            f"<span class=hinweis>{html_escape(kurz)}</span>"
            f"</div><dl class=netzersatz>{rows}</dl>"
            f"<p class=kartenfuss>{fuss}</p></section>"
        )

    # The summary figures run as a narrow strip in the header so the drawing
    # below gets the full width.
    values = "".join(
        f"<span><b>{html_escape(w)}</b> {html_escape(b)}</span>"
        for b, w, _ in peer_summary(peers)
    ) + (chain_check_markup(kz or {}) if in_sync else "")
    legend = "".join(
        f'<span><i class="netzfarbe {kind}"></i>{name}</span>'
        for kind, name in LEGEND
    )
    legend += (
        '<span><i class="netzfarbe ansager"></i>'
        f"{html_escape(t('announced the last block first'))}</span>"
        '<span><i class="netzfarbe quelle"></i>'
        f"{html_escape(t('delivered it'))}</span>"
        '<span><i class="netzfarbe empfaenger"></i>'
        f"{html_escape(t('got it from us'))}</span>"
    )

    return (
        '<section class="karte netz">'
        f"<div class=kopfzeile><h2>{html_escape(t('Connected nodes'))}</h2>"
        f"<div class=netzzahlen>{values}</div></div>"
        f"<div id=netzkarte>{svg}</div>"
        f"<div class=peerlegende>{legend}"
        '<span><i class="netzfarbe neutral"></i>'
        f"{html_escape(t('filled = outbound'))}</span>"
        "</div>"
        '<div class=peerdetail id=peerdetail>'
        f"<p class=blockweg>{html_escape(block_path_text(peers, kz))}</p>"
        f"<p class=blockweg>{html_escape(ranking_text(peers))}</p>"
        f"<p class=leer>{html_escape(t('Point at a line for identifier, dienste and connection time.'))}</p>"
        "</div></section>"
    )


def log_text(logs):
    """The log lines as plain text — without any markup at all.

    They are delivered exactly like this and set in the browser via
    textContent. Foreign text then cannot become markup by construction.
    """
    if not logs:
        return t("No log source configured.")
    if len(logs) == 1:
        return logs[0][1]
    return "\n\n".join(f"--- {service} ---\n{content}" for service, content in logs)


def build_zones(cfg, progress, in_sync, groups, error=None,
               logs=None, summary=None, peers=None, stale_for=None,
               warming_up=False, updates=None, tor=None):
    """Build every moving part of the page separately.

    These very pieces also go into status.json. That lets the page be updated
    in the browser without there being two routes from the same data to
    markup — and without the two ever drifting apart.
    """
    kz = summary or {}
    level, word, extra = assess_state(error, in_sync, groups,
                                          stale_for, warming_up,
                                          [chain_check_warning(kz) if in_sync else None])

    narrow = [g for g in groups
              if g[1] and g[0] not in CARDS_WIDE and g[0] not in CARDS_FULL]
    narrow.sort(key=lambda g: CARD_ORDER.index(g[0])
                if g[0] in CARD_ORDER else len(CARD_ORDER))
    wide = [g for g in groups if g[1] and g[0] in CARDS_WIDE]
    full = [g for g in groups if g[1] and g[0] in CARDS_FULL]

    return {
        "stufe": level,
        "wort": word,
        "kopf": build_header_info(updates, kz),
        "zustand": build_state_bar(level, word, extra, progress, kz),
        "stoerung": build_trouble(error, stale_for, warming_up, tor),
        "band": build_metrics_bar(kz, level),
        "netz": build_network_zone(peers or [], kz.get("netzfelder"),
                              "getpeerinfo" in DENIED, kz, in_sync),
        "spalten": raster_spalten(len(narrow)),
        "raster": "".join(render_card(g) for g in narrow),
        "weit": "".join(render_card(g) for g in wide),
        "voll": "".join(render_card(g, "voll") for g in full),
    }


def build_page(cfg, progress, in_sync, groups, error=None,
               logs=None, summary=None, peers=None, stale_for=None,
               zones=None, warming_up=False, updates=None, tor=None):
    now = datetime.now(timezone.utc).astimezone()
    hostname = html_escape(socket.gethostname())
    interval = html_escape(cfg["INTERVAL"])
    log_step = html_escape(str(cfg.get("LOG_INTERVAL", "5")))

    if zones is None:
        zones = build_zones(cfg, progress, in_sync, groups, error,
                           logs, summary, peers, stale_for,
                           warming_up, updates, tor)
    level, word = zones["stufe"], zones["wort"]

    # The browser tab title carries the progress — handy when the page sits
    # in a background tab for days.
    title = (f"{format_percent(progress, 1)} % · {hostname}" if level == "sync"
             else f"{word} · {hostname}")

    # The level names are deliberately the same as in the style sheet
    # ([data-stufe=fehler]) and in the test run. They are identifiers, not
    # display text — which is why they stay untranslated.
    point = {"ok": "%232fd39a", "warn": "%23f0b23f", "fehler": "%23f2645f",
             "veraltet": "%23f0b23f", "anlauf": "%23f0b23f",
             "sync": "%232fd39a"}[level]
    favicon = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' "
               f"viewBox='0 0 32 32'><circle cx='16' cy='16' r='11' fill='{point}'/></svg>")

    # The policy is also set as a header by the web server configuration. It
    # is repeated here so the file stays protected even when someone serves it
    # without that server.
    csp = ("default-src 'none'; style-src 'self'; script-src 'self'; "
           "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
           "form-action 'none'; frame-ancestors 'none'")

    parts = [
        "<!doctype html>",
        # lang= belongs to the language: without it a screen reader speaks
        # German text with English pronunciation, and the browser hyphenates
        # by the wrong rules.
        f'<html lang={LANGUAGE} data-stufe="{level}" data-frisch=nein '
        f'data-interval="{interval}" data-logintervall="{log_step}">',
        "<head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        '<meta name=referrer content=no-referrer>',
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">',
        # Without JavaScript the page reloads through this. With JavaScript
        # the element is removed at startup.
        f'<meta http-equiv=refresh content="{interval}">',
        f'<link rel=icon href="{favicon}">',
        f'<link rel=stylesheet href="stil.css?v={STYLE_V}">',
        # <title>, not <titel>: the rename of 2026-08-23 hit this tag too.
        # The browser rendered the unknown element as visible text above the
        # header for nine days — "Alles läuft · btcnode" in the top left
        # corner, taken for a design choice (2026-09-01).
        f"<title>{html_escape(title)}</title>",
        "</head><body><div class=huelle>",
        f'<header><h1><span class=marke></span>{hostname} '
        f"<b>· Bitcoin Fullnode</b></h1>",
        f'<div id=z-kopf>{zones["kopf"]}</div>',
        f'<div class=kopfrechts><span class=puls></span>'
        f'<span id=stempel>{now.strftime(TIME_FORMAT[LANGUAGE])}</span></div>'
        "</header>",
        # Two columns: everything interpreted on the left, the raw log at
        # full height on the right. On narrow screens the grid collapses back
        # to one column by itself.
        # The split runs in TWO rows, not as two columns over the whole page:
        # the network card stands beside state bar, band and charts, the log
        # beside the card grid. Each row is a grid of its own, so both of its
        # blocks end on the same line — that is what puts the seam between
        # network card and log onto the seam of the left column.
        "<div class=inhalt><div class=reihe><div class=links>",
        f'<div id=z-zustand>{zones["zustand"]}</div>',
        f'<div id=z-stoerung>{zones["stoerung"]}</div>',
        f'<div class=band id=z-band>{zones["band"]}</div>',
        f'<div class=weit id=z-weit>{zones["weit"]}</div>',
    ]

    parts.append("</div>")     # Ende der linken Spalte, obere Reihe

    # Upper row on the right: the peers. The card grows to the height of the
    # block on its left instead of ending wherever its content happens to end.
    parts.append("<div class=rechts>")
    parts.append(f'<div class=netzzone id=z-netz>{zones["netz"]}</div>')
    parts.append("</div></div>")   # Ende rechte Spalte, Ende obere Reihe

    # Lower row: the card grid on the left, the log on the right. The log
    # claims whatever the grid leaves over and therefore ends flush with it.
    parts.append("<div class=reihe><div class=links>")
    parts.append(
        f'<div class="raster s{zones["spalten"]}" id=z-raster>'
        f'{zones["raster"]}</div>'
    )
    parts.append(f'<div id=z-voll>{zones["voll"]}</div>')
    parts.append("</div><div class=rechts>")

    parts.append(
        '<section class="karte protokoll"><div class=kopfzeile>'
        f"<h2>{html_escape(t('Log'))}</h2>"
        f'<span class=hinweis>'
        f"{html_escape(t('newest first · every {n} s', n=log_step))}</span>"
        "</div>"
        # The box around it is not decoration: it claims the remaining space
        # and the <pre> inside sits absolutely. Only that keeps the length of
        # the log from dictating the height of the page.
        f'<div class=logbox><pre><code id=logtext>'
        f"{html_escape(log_text(logs))}</code></pre></div></section>"
    )

    parts.append("</div></div>")   # Ende rechte Spalte, Ende untere Reihe
    parts.append("</div>")         # Ende der Zwei-Spalten-Aufteilung
    parts.append(
        f"<footer>node-dashboard {VERSION} · "
        + html_escape(t("read-only access · data every {n} s, log every {m} s",
                        n=interval, m=log_step))
        + "</footer>"
    )
    parts.append(f'</div><script src="dash.js?v={script_v()}"></script>'
                 "</body></html>")
    return "".join(parts)


def build_status(cfg, zones, peers, now, stale_for=None, progress=0.0,
                 summary=None):
    """The static read-only API.

    Deliberately not an interface in the usual sense: it is a file the
    generator writes and the web server hands out. The server still does not
    know the node and still accepts nothing. The security model is therefore
    exactly the old one.
    """
    hostname = socket.gethostname()
    level, word = zones["stufe"], zones["wort"]
    title = (f"{format_percent(progress, 1)} % · {hostname}" if level == "sync"
             else f"{word} · {hostname}")

    schlanke_peers = []
    for p in peers or []:
        schlanke_peers.append({
            "adresse": p["adresse"],
            # 'netzname', not 'network_name': dash.js reads p.netzname. This
            # key is data, not an identifier — the rename on 2026-08-23 hit it
            # anyway and the detail box has been showing "undefined · outgoing"
            # ever since. Only visible when hovering a dot, which is why nobody
            # reported it. check_status compares both sides now.
            "netzname": peer_network_label(p),
            "netzart": p["netz"] if p["netz"] in NETWORK_COLOURS else "neutral",
            "eingehend": p["eingehend"],
            "ping_ms": p["ping_ms"],
            "jetzt_ms": p.get("jetzt_ms"),
            "dauer_s": p["dauer_s"],
            "version": p["version"],
            "dienste": p["dienste"],
            "gesendet": p["gesendet"],
            "empfangen": p["empfangen"],
            "bloecke_von": p.get("bloecke_von", 0),
            "zuletzt_von": p.get("zuletzt_von"),
            "bloecke_an": p.get("bloecke_an", 0),
            "zuletzt_an": p.get("zuletzt_an"),
        })

    return json.dumps({
        "erzeugt": int(now.timestamp()),
        "stempel": now.strftime(TIME_FORMAT[LANGUAGE]),
        "titel": title,
        "stufe": level,
        "veraltet": stale_for is not None,
        "fortschritt": round(progress, 3),
        "spalten": zones["spalten"],
        "zonen": {
            "kopf": zones["kopf"],
            "zustand": zones["zustand"],
            "stoerung": zones["stoerung"],
            "band": zones["band"],
            "netz": zones["netz"],
            "raster": zones["raster"],
            "weit": zones["weit"],
            "voll": zones["voll"],
        },
        "peers": schlanke_peers,
        # The sentence for the detail box, ready made: dash.js sets it via
        # textContent and does not rebuild it from the peer list.
        "blockweg": block_path_text(peers or [], summary or {}),
        "rangliste": ranking_text(peers or []),
    }, ensure_ascii=False, separators=(",", ":"))


# =================================================================== Writing
# What was written last. Unchanged content is not written to disk again — at a
# five second step that saves a considerable number of pointless writes in
# continuous operation.
LAST_WRITTEN = {}


def write_file_atomic(target_dir, filename, content):
    """Write to a temporary file first, then rename. That way the web server
    never sees a half-written page."""
    target = os.path.join(target_dir, filename)
    # Unchanged content is not written again — unless the file is gone
    # from disk (someone cleared the folder), then it comes back at once.
    if LAST_WRITTEN.get(filename) == content and os.path.exists(target):
        return False

    os.makedirs(target_dir, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(temp, 0o644)
        os.replace(temp, target)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    LAST_WRITTEN[filename] = content
    return True


def write_bytes_atomic(target_dir, filename, content):
    """The same for files that are not text — currently only the logo."""
    target = os.path.join(target_dir, filename)
    if LAST_WRITTEN.get(filename) == content and os.path.exists(target):
        return False
    os.makedirs(target_dir, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.chmod(temp, 0o644)
        os.replace(temp, target)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    LAST_WRITTEN[filename] = content
    return True


def write_page_atomic(target_dir, content):
    write_file_atomic(target_dir, "index.html", content)


def write_assets(cfg):
    """Style and script live as their own files next to the page.

    They only change when this program is replaced — the change check makes
    sure they are then written exactly once and not on every cycle.
    """
    write_file_atomic(cfg["OUT_DIR"], "stil.css", STYLE)
    write_file_atomic(cfg["OUT_DIR"], "dash.js", script_text())
    write_bytes_atomic(cfg["OUT_DIR"], "bitcoin.png", BITCOIN_PNG)

    # Left over from version 2.x: back then the log sat in a frame as a page
    # of its own. It is no longer produced and should not linger as a stale
    # file either. The only file touched is one this program used to write
    # itself.
    alt = os.path.join(cfg["OUT_DIR"], "log.html")
    if os.path.exists(alt):
        try:
            os.unlink(alt)
        except OSError:
            pass


def write_log_text(cfg, logs=None):
    if logs is None:
        logs = collect_log(cfg)
    write_file_atomic(cfg["OUT_DIR"], "log.txt",
                          log_text(logs))


# ======================================================================= Run
def one_pass(cfg):
    """One complete cycle: query, build, write.

    The special case is the tolerance window. If the node does not answer,
    not everything is discarded at once — the last good state stays until
    several attempts in a row have failed.
    """
    global FAILURES_IN_ROW

    groups = []
    progress, in_sync, error = 0.0, False, None
    summary, peers, stale_for, warming_up = {}, [], None, False

    try:
        tolerance = max(1, int(cfg.get("TOLERANCE", 3)))
    except (TypeError, ValueError):
        tolerance = 3
    try:
        peers_max = max(1, int(cfg.get("PEERS_MAX", 64)))
    except (TypeError, ValueError):
        peers_max = 64

    try:
        progress, in_sync, node_gruppen, summary = collect_node(cfg)
        groups.extend(node_gruppen)
        peers = collect_peers(cfg, peers_max)
        FAILURES_IN_ROW = 0
        LAST_STATE.update({
            "zeit": time.time(),
            "fortschritt": progress,
            "synchron": in_sync,
            "gruppen": node_gruppen,
            "kennzahlen": summary,
            "peers": peers,
        })
    except RpcError as e:
        FAILURES_IN_ROW += 1
        # The node says it is starting up. That is not an outage and the
        # tolerance window must not run out on it: verifying blocks after a
        # restart can take a quarter of an hour, and counting to three would
        # put a red card on screen while the log next to it scrolls happily.
        if "in warmup" in str(e):
            FAILURES_IN_ROW = 0
            WARMUP_MESSAGE[0] = str(e).split(": ", 1)[-1].strip()
            if LAST_STATE.get("gruppen"):
                progress = LAST_STATE["fortschritt"]
                in_sync = LAST_STATE["synchron"]
                groups.extend(LAST_STATE["gruppen"])
                summary = LAST_STATE["kennzahlen"]
                peers = LAST_STATE["peers"]
            warming_up = True
        elif FAILURES_IN_ROW >= tolerance:
            error = str(e)
        elif LAST_STATE.get("gruppen"):
            # Still inside the tolerance window: keep showing the old state
            # but say that it is old.
            progress = LAST_STATE["fortschritt"]
            in_sync = LAST_STATE["synchron"]
            groups.extend(LAST_STATE["gruppen"])
            summary = LAST_STATE["kennzahlen"]
            peers = LAST_STATE["peers"]
            stale_for = time.time() - LAST_STATE["zeit"]
        else:
            # Right after startup there is no old state yet. The tolerance
            # window used to fall straight back to the red error card here —
            # exactly what was on screen on 2026-08-23 after every restart of
            # the service, while the node kept running next to it.
            warming_up = True
    else:
        WARMUP_MESSAGE[0] = ""

    electrum = collect_electrum(cfg, (summary or {}).get("bloecke"))
    if electrum:
        groups.append(electrum)

    # System before updates: first the state of the machine, then the note
    # about whether new releases are waiting.
    groups.append(collect_system(cfg))

    # The version check is no longer a card but a line in the page header —
    # it used to say "current" three times in the normal case and take up a
    # full card doing so.
    updates = collect_updates(cfg)
    tor = collect_tor(cfg)
    logs = collect_log(cfg)
    collect_announcements(cfg)

    now = datetime.now(timezone.utc).astimezone()
    zones = build_zones(cfg, progress, in_sync, groups, error,
                       logs, summary, peers, stale_for,
                       warming_up, updates, tor)

    write_assets(cfg)
    write_page_atomic(
        cfg["OUT_DIR"],
        build_page(cfg, progress, in_sync, groups, error, logs,
                   summary, peers, stale_for, zones, warming_up,
                   updates, tor),
    )
    write_file_atomic(
        cfg["OUT_DIR"], "status.json",
        build_status(cfg, zones, peers, now,
                    stale_for if not warming_up else 0.0, progress, summary),
    )
    write_log_text(cfg, logs)
    return error


def main():
    p = argparse.ArgumentParser(description="Erzeugt eine statische Statusseite fuer den Node.")
    p.add_argument("--config", default="/etc/node-dashboard.conf")
    p.add_argument("--once", action="store_true",
                   help="Nur einmal erzeugen statt dauerhaft zu laufen")
    args = p.parse_args()

    cfg = read_config(args.config)

    if args.once:
        error = one_pass(cfg)
        print("Seite geschrieben nach", os.path.join(cfg["OUT_DIR"], "index.html"))
        if error:
            print("Hinweis:", error, file=sys.stderr)
        return 0

    # A typo in the configuration must not turn into a restart loop.
    try:
        interval = max(5, int(cfg["INTERVAL"]))
    except (TypeError, ValueError):
        interval = 30
    try:
        log_step = max(1, min(interval, int(cfg.get("LOG_INTERVAL", 5))))
    except (TypeError, ValueError):
        log_step = 5

    # Two rhythms in one loop: query the node rarely, refresh the log often.
    # No second process, no concurrency.
    last_full_pass = 0.0
    while True:
        try:
            if time.time() - last_full_pass >= interval:
                # Stamp first: if the pass dies, the next attempt waits a
                # full interval instead of hitting the node every log step.
                last_full_pass = time.time()
                one_pass(cfg)
            else:
                write_log_text(cfg)
        except Exception as e:  # noqa: BLE001 — the service must never die
            print(f"Error while generating: {e}", file=sys.stderr)
        time.sleep(log_step)


if __name__ == "__main__":
    sys.exit(main() or 0)
