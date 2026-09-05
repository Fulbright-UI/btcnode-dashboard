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
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

VERSION = "3.5"

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
    "this node": "dieser Node",
    "no reachable address announced": "keine erreichbare Adresse bekanntgegeben",
    "Protocol": "Protokoll",
    "Minimum relay fee": "Mindestgebühr zum Weiterleiten",
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
    "Network & Electrum": "Netzwerk & Electrum",
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
    "{label} · peak {v}": "{label} · Spitze {v}",
    "Average fee per block over the last 24 hours":
        "Mittlere Gebühr je Block der letzten 24 Stunden",

    # -- Card "Electrum server" ----------------------------------------------
    "Service": "Dienst",
    "running": "läuft",
    "stopped": "gestoppt",
    "Responding": "Antwortet",
    "yes": "ja",
    "no, still indexing": "nein, indiziert noch",
    "complete": "vollständig",
    "{n} of {tip} bloecke": "{n} von {tip} Blöcken",
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
    "announced the last block first": "kündigte den letzten Block zuerst an",
    "{ok} of {n} probes matched our height":
        "{ok} von {n} Stichproben bestätigen unsere Höhe",
    ", {behind} behind": ", {behind} hinterher",
    ", none ahead": ", keine voraus",
    "one claimed": "eine behauptete",
    "{k} claimed": "{k} behaupteten",
    ", {claim} up to {b} blocks more without delivering headers":
        ", {claim} bis zu {b} Blöcke mehr, ohne Header zu liefern",
    " · last {when}": " · zuletzt {when}",
    "{k} probes report up to {n} blocks more than we have":
        "{k} Stichproben melden bis zu {n} Blöcke mehr als wir",
    "Chain check: every few minutes Core asks a random node for its height. Last {when}":
        "Kettenabgleich: Core fragt alle paar Minuten einen zufälligen Knoten nach seiner Höhe. Zuletzt {when}",
    "Chain check: recent probes report {n} blocks more":
        "Kettenabgleich: die jüngsten Stichproben melden {n} Blöcke mehr",
    "Block {n} · announced {when} by {peer}": "Block {n} · angekündigt {when} von {peer}",
    "peer {n}": "Peer {n}",
    "(no longer connected)": "(nicht mehr verbunden)",
    "numbered peers are no longer connected": "nummerierte Peers sind nicht mehr verbunden",
    "numbered peers are from before the restart, no longer connected":
        "nummerierte Peers stammen aus der Zeit vor dem Neustart, nicht mehr verbunden",
    "first to announce, {total} blocks in 24 h: {parts}":
        "zuerst angekündigt, {total} Blöcke in 24 h: {parts}",
    "Electrum · local": "Electrum · lokal",
    "{n} bloecke to go": "noch {n} Blöcke",
    "progress not readable": "Fortschritt nicht lesbar",
    "Index": "Index",
    "Blocks from here": "Blöcke von hier",
    "Blocks to here": "Blöcke dorthin",
    "last {when}": "zuletzt {when}",
    "median fee in the last block": "Median-Gebühr im letzten Block",
    "fee for the next block": "Gebühr für den nächsten Block",
    "vs. a year ago · hashrate, curve since 2009": "zum Vorjahr · Hashrate, Kurve seit 2009",
    "safe: {fee}": "sicher: {fee}",
    "Syncing the blockchain": "Synchronisiert die Blockchain",
    "of {n} bloecke": "von {n} Blöcken",
    "Node in sync, nothing unusual": "Node synchron, keine Auffälligkeiten",
    "days": "Tage",
    "Block {n} · {v} · {k} transactions": "Block {n} · {v} · {k} Transaktionen",
    "Block {n} · {f} sat/vB": "Block {n} · {f} sat/vB",
    "{hour}:00 · peak {c} °C": "{hour}:00 · Spitze {c} °C",
    "email to Mike Hearn": "E-Mail an Mike Hearn",
    "halving · about {date}": "Halbierung · ca. {date}",
    "{n} blocks to go": "noch {n} Blöcke",
    "fees waiting": "wartende Gebühren",
    "Chain & Mempool": "Kette & Mempool",
    "peak per hour · 24 h": "Spitze je Stunde · 24 h",
    "Peak temperature per hour over the last 24 hours":
        "Höchsttemperatur je Stunde der letzten 24 Stunden",
    "below the 24 h mean": "unter dem 24-h-Mittel",
    "up to twice the mean": "bis zum Doppelten",
    "above that": "darüber",
    "up to 2 sat/vB": "bis 2 sat/vB",
    "up to 5 sat/vB": "bis 5 sat/vB",
    "above 5 sat/vB": "über 5 sat/vB",
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

# The current temperature, for the metrics tile.
TEMP_NOW = [None]
# A fixed scale instead of one that adapts: a calm line at 50 degrees should
# also look calm. With a growing scale every bit of noise would look like a
# spike.
TEMP_LOW, TEMP_HIGH = 30.0, 90.0
# The last day as one peak per clock hour, [(hour start, max)], 24 entries.
# Lives in the process like every other history; a restart empties it and
# the bars fill up again over the day (2026-09-03).
TEMP_HOURLY = []

HALVING_INTERVAL = 210_000   # the reward halves every 210,000 blocks
RETARGET_INTERVAL = 2016     # difficulty is adjusted every 2016 blocks

# Per-block figures for the 24 hour graphs. Bitcoin Core computes the sums
# itself (getblockstats) — that saves us reading 144 blocks worth hundreds of
# megabytes.
BLOCK_DATA = []      # (height, time, output_sat, fee_sat_vb, count)
BLOCK_KEEP = 144     # roughly 24 hours

# Network hashrate since the genesis block for the curve behind the state
# bar (Jakob, 2026-09-03: "price follows hashrate" — show it like a price
# chart, and all of it). One point per difficulty period, plus the tip.
# getnetworkhashps with a height parameter is answered from the headers
# alone; the first fill is ~480 cheap calls, taken in portions so that no
# single pass hangs, afterwards one call every two weeks. Fourteen orders of
# magnitude between 2009 and now — the curve is drawn on a log scale.
HASHRATE = []           # (height, hashes per second)
HASHRATE_STEP = 2016    # blocks per point: one difficulty period
HASHRATE_PER_PASS = 120
HASHRATE_YEAR = 26      # periods in a year, for the ticker's change


def record_long_progress(progress_fraction):
    """Keep a coarse history for the curve."""
    now = time.time()
    if not PROGRESS_LONG or now - PROGRESS_LONG[-1][0] >= PROGRESS_LONG_STEP:
        PROGRESS_LONG.append((now, progress_fraction))
        del PROGRESS_LONG[:-PROGRESS_LONG_MAX]


def record_temperature(celsius):
    """Keep the current value and the peak of every clock hour."""
    now = time.time()
    TEMP_NOW[0] = celsius
    hour = now - now % 3600
    if TEMP_HOURLY and TEMP_HOURLY[-1][0] == hour:
        TEMP_HOURLY[-1] = (hour, max(TEMP_HOURLY[-1][1], celsius))
    else:
        TEMP_HOURLY.append((hour, celsius))
        del TEMP_HOURLY[:-24]


def temperature_colour(celsius):
    """Green up to 60, yellow up to 75, red above that."""
    if celsius is None:
        return "var(--leise)"
    if celsius >= 75:
        return "var(--error)"
    if celsius >= 60:
        return "var(--warn)"
    return "var(--akzent)"


def build_bar(fraction, level="", height=6, title=None):
    """Horizontal fill bar, fraction between 0 and 1. 'title' replaces the
    percentage in the tooltip (escaped here).

    SVG on purpose instead of a <div style="width:…">: the Content Security
    Policy reads 'style-src self' without 'unsafe-inline', and that applies to
    style attributes in the markup as well. A width given as an inline style
    would be dropped by the browser — the bar would then always show full.
    That is exactly what happened on 2026-08-23. Width and colour in SVG are
    presentation attributes and are not affected.
    """
    width = min(100.0, max(0.0, fraction * 100))
    cls = f"balkenfuellung {level}".strip()
    tip = html_escape(title) if title else f"{width:.0f} %"

    # The rounded corners come from CSS on the wrapping element, not from
    # 'rx' on the rectangle: the SVG is stretched many times over by
    # preserveAspectRatio="none", and an 'rx' would be stretched with it. At
    # small fractions the radius then exceeds the fill itself and the bar
    # turns into a blob. That was on screen on 2026-08-23.
    return (f'<span class="balken hoch{height}">'
            f'<svg viewBox="0 0 100 {height}" preserveAspectRatio="none" '
            f'role="img" aria-label="{tip}">'
            f'<rect width="{width:.2f}" height="{height}" class="{cls}">'
            f"<title>{tip}</title></rect>"
            f"</svg></span>")


def build_columns(values, colour="var(--akzent)", label="", width=260, height=38,
                  colours=None, floor=0.0, ceiling=None, titles=None):
    """Small column chart. The scale starts at zero unless told otherwise.

    'colours' gives one colour per value and wins over 'colour'. 'floor'
    and 'ceiling' fix the scale — the temperature bars run 30–90 °C, so
    that a calm day looks calm and 45 against 55 is still a visible step.
    'titles' puts one <title> per bar into the SVG: the browser shows it
    on hover, no script needed (Jakob, 2026-09-03). Our own numbers only,
    escaped anyway.
    """
    titles = titles or []
    values = [w for w in values if w is not None]
    if len(values) < 2:
        return None
    highest = ((ceiling if ceiling is not None else max(values)) - floor) or 1
    count = len(values)
    gap = 260 / count * 0.18
    column = (width - (count - 1) * gap) / count

    parts = []
    for i, w in enumerate(values):
        h = max(0.8, ((w - floor) / highest) * height)
        x = i * (column + gap)
        fill = colours[i] if colours else colour
        tip = (f"<title>{html_escape(titles[i])}</title>" if i < len(titles) and titles[i] else "")
        parts.append(f'<rect x="{x:.2f}" y="{height - h:.2f}" '
                     f'width="{column:.2f}" height="{h:.2f}" fill="{fill}">{tip}</rect>')
    return (f'<svg class="minikurve saeulen" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(label)}">{"".join(parts)}</svg>')


# Fee tiers for the 24-hour bars (Jakob, 2026-09-03): up to 2 sat/vB, up
# to 5, above that. One hue in three steps since 2026-09-05 (Jakob): muted
# green-grey, the accent, a bright mint — it reads as intensity and
# collides with nothing (yellow stays warning, orange the block, red
# faults). The volume bars use the same three steps (VOLUME_TIERS).
FEE_TIERS = ((2, "s1", "var(--stufe1)"), (5, "s2", "var(--stufe2)"),
             (None, "s3", "var(--stufe3)"))
# Volume per block, relative to the 24-hour mean: below it, up to twice
# it, above that. Fixed BTC limits would make a whole day one colour.
VOLUME_TIERS = ((1.0, "s1", "var(--stufe1)"), (2.0, "s2", "var(--stufe2)"),
                (None, "s3", "var(--stufe3)"))


def fee_tier(fee, tiers=FEE_TIERS):
    for limit, cls, colour in tiers:
        if limit is None or fee <= limit:
            return cls, colour
    return tiers[-1][1:]


def build_legend(entries):
    """The colour dots under a chart, same markup as the network legend."""
    legend = "".join(
        f'<span><i class="netzfarbe {cls}"></i>{html_escape(text)}</span>'
        for cls, text in entries)
    return f'<span class="peerlegende gebuehrenlegende">{legend}</span>'


def build_volume_columns():
    """Volume moved per block, one bar each, tinted against the 24-hour
    mean (Jakob, 2026-09-05): the same three steps as the fee bars."""
    outputs = [e[2] for e in BLOCK_DATA]
    mean = (sum(outputs) / len(outputs)) if outputs else 0
    svg = build_columns(outputs, label=t("Volume moved per block over the last 24 hours"),
                        colours=[fee_tier(v / mean if mean else 0, VOLUME_TIERS)[1]
                                 for v in outputs],
                        titles=[t("Block {n} · {v} · {k} transactions", n=format_number(e[0]),
                                  v=format_btc(e[2]), k=format_number(e[4]))
                                for e in BLOCK_DATA])
    if not svg:
        return None
    return svg + build_legend((("s1", t("below the 24 h mean")),
                               ("s2", t("up to twice the mean")),
                               ("s3", t("above that"))))


def build_fee_columns(fees, heights=()):
    """Average fee per block, one bar each, rounded to whole sat/vB and
    tinted by tier, with the legend underneath. The line chart before it
    (2026-09-01) hid how many blocks sat at the floor."""
    pairs = [(h, f) for h, f in zip(heights, fees) if f is not None]
    rounded = [max(0, round(f)) for _, f in pairs]
    svg = build_columns(rounded, label=t("Average fee per block over the last 24 hours"),
                        colours=[fee_tier(f)[1] for f in rounded],
                        titles=[t("Block {n} · {f} sat/vB", n=format_number(h), f=decimal_sep(f"{f:.1f}"))
                                for h, f in pairs])
    if not svg:
        return None
    return svg + build_legend((("s1", t("up to 2 sat/vB")), ("s2", t("up to 5 sat/vB")),
                               ("s3", t("above 5 sat/vB"))))


def build_temperature_columns():
    """One bar per hour of the last day, the hour's peak, each in the colour
    of its own value (Jakob, 2026-09-03). Twenty-four slots, oldest on the
    left; after a restart the hours not yet measured stay empty on the
    right. Fixed scale 30–90 °C like the curve before it, so a calm day
    looks calm."""
    if not TEMP_HOURLY:
        return None
    values = [c for _, c in TEMP_HOURLY]
    titles = [t("{hour}:00 · peak {c} °C", hour=time.strftime("%H", time.localtime(h)),
                c=decimal_sep(f"{c:.1f}")) for h, c in TEMP_HOURLY]
    values += [None] * (24 - len(values))
    # Hours not yet measured: a low stub in the frame colour, so that a
    # single green bar after a restart reads as "filling up", not as a
    # fault (Jakob's screenshot, 2026-09-03).
    stub = TEMP_LOW + (TEMP_HIGH - TEMP_LOW) * 0.06
    drawn = [c if c is not None else stub for c in values]
    colours = [temperature_colour(c) if c is not None else "var(--randhell)"
               for c in values]
    return build_columns(drawn, label=t("Peak temperature per hour over the last 24 hours"),
                         colours=colours, floor=TEMP_LOW, ceiling=TEMP_HIGH, titles=titles)


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


def hashrate_anchors(tip):
    """The heights the curve is made of: every difficulty period from the
    first, then the tip itself as the live point."""
    fixed = list(range(HASHRATE_STEP, tip + 1, HASHRATE_STEP))
    return fixed + ([tip] if tip not in fixed else [])


def fetch_hashrate(cfg, tip):
    """Fill the curve, HASHRATE_PER_PASS points per cycle, newest first so
    that the ticker has its value right away. The tip point is replaced
    whenever the tip moves; the period points never change. getnetworkhashps
    is not in the whitelist of older installations: the first refusal ends
    the fetch quietly, and the state bar simply has no curve.
    """
    if not tip:
        return
    anchors = hashrate_anchors(tip)
    keep = set(anchors)
    HASHRATE[:] = [e for e in HASHRATE if e[0] in keep]
    present = {h for h, _ in HASHRATE}
    missing = [h for h in reversed(anchors) if h not in present][:HASHRATE_PER_PASS]
    for height in missing:
        window = HASHRATE_STEP if height % HASHRATE_STEP == 0 else height % HASHRATE_STEP or 1
        try:
            rate = rpc(cfg, "getnetworkhashps", [window, height])
        except RpcError:
            return
        HASHRATE.append((height, float(rate)))
    HASHRATE.sort(key=lambda e: e[0])


def hashrate_summary():
    """(current H/s, change against a year ago as a fraction) or None.
    The comparison point is HASHRATE_YEAR periods back; while the curve is
    still filling from the tip downwards, the oldest point present."""
    if len(HASHRATE) < 3:
        return None
    last = HASHRATE[-1][1]
    ago = HASHRATE[max(0, len(HASHRATE) - 1 - HASHRATE_YEAR)][1]
    return last, ((last - ago) / ago if ago else 0.0)


def format_hashrate(hashes):
    """900 EH/s style, one decimal below 100."""
    for unit, size in (("EH/s", 1e18), ("PH/s", 1e15), ("TH/s", 1e12)):
        if hashes >= size:
            value = hashes / size
            return decimal_sep(f"{value:.0f}" if value >= 100 else f"{value:.1f}") + f" {unit}"
    return decimal_sep(f"{hashes / 1e9:.1f}") + " GH/s"


def build_hashrate_chart(width=600, height=120):
    """The curve behind the state bar: a line with a soft gradient below,
    drawn like a price chart. Geometry in attributes, colour in classes —
    the CSP forbids style attributes (see 2026-08-23).

    Linear scale since 2026-09-05 (Jakob): across 14 orders of magnitude
    that is a flat line with the climb at the right — which is what the
    hashrate actually did, and what he wants to see. Log was 2026-09-03."""
    values = [v for _, v in HASHRATE if v > 0]
    if len(values) < 3:
        return ""
    lo, hi = 0.0, max(values)
    span = (hi - lo) or 1
    pts = []
    for i, v in enumerate(values):
        x = i / (len(values) - 1) * width
        y = height * 0.15 + (1 - (v - lo) / span) * height * 0.8
        pts.append(f"{x:.1f},{y:.1f}")
    line = " ".join(pts)
    area = f"0,{height} {line} {width},{height}"
    return (f'<svg class=hashkurve viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" aria-hidden="true">'
            '<defs><linearGradient id="hashverlauf" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" class="hv0"></stop><stop offset="1" class="hv1"></stop>'
            '</linearGradient></defs>'
            f'<polygon points="{area}" fill="url(#hashverlauf)"/>'
            f'<polyline points="{line}" fill="none" stroke-width="1.5" '
            'stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>')


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
        "INTERVAL": "21",   # 21/3 since 2026-09-03 (Jakob)
        # Display language of the page: de or en. Affects only what appears
        # in the browser — log lines come from the node and stay as they are.
        "LANGUAGE": "de",
        "LOG_SERVICES": "bitcoind",
        # The log fills the right column all the way down and scrolls inside
        # it. More lines cost nothing but scrollback — journalctl does not
        # take longer for 150 than for 40.
        "LOG_LINES": "150",
        "LOG_INTERVAL": "3",
        # Timeout per RPC call. 45 s instead of 15: bitcoind stalls its RPC
        # thread while it writes the dbcache.
        "RPC_TIMEOUT": "45",
        # This many failures in a row before the node counts as gone.
        # 3 x 21 s = a minute of silence, only then the red card.
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


# ============================================================= Chronicle ===
# A terminal line under the header, full width (Jakob, 2026-09-03): a
# sentence from the early days — the cryptography mailing list, the P2P
# Foundation thread, bitcointalk — typed into the page like a person types,
# one quote per data cycle, so the animation never runs against the
# refresh. A headline ticker with dates and prices lived on the right for
# an hour the same night and was dropped again on Jakob's word.
#
# The texts are the English originals in both languages: they are
# quotations, not interface. Short on purpose — one header line each, so
# the longer ones are cut to their key sentence (2026-09-03).
# Wording checked against satoshi.nakamotoinstitute.org on 2026-09-03.
#
# Translated fields are lambdas: t() must run after the language is set.
#   (date, who, where, text)
CHRONICLE_QUOTES = (
    # -- Before Bitcoin (Jakob, 2026-09-05): the money and the cryptography
    # it grew out of, from Bretton Woods through 1971 and the cypherpunks
    # to the failed digital currencies. Where only the month or the year is
    # known the date is written that short — no day is invented.
    # -- Central banking, from the Bank of England to the Fed and the end
    # of gold (Jakob, 2026-09-05, along the story Griffin tells in "The
    # Creature from Jekyll Island"). Events in our own words, the two
    # quotations are public domain (Jackson's veto message, Wilson's
    # "The New Freedom"); nothing from the book itself.
    ("1694-07-27", "Bank of England", "royal charter",
     "The Bank of England is chartered to lend the Crown 1.2 million pounds: the first modern central bank"),
    ("1791-02-25", "George Washington", "First Bank of the United States",
     "Hamilton's national bank is chartered for twenty years over Jefferson's objection"),
    ("1816-04-10", "James Madison", "Second Bank of the United States",
     "A second national bank is chartered after the inflation of the War of 1812"),
    ("1832-07-10", "Andrew Jackson", "veto message",
     "It is to be regretted that the rich and powerful too often bend the acts of government to their selfish purposes."),
    ("1862-02-25", "Legal Tender Act", "greenbacks",
     "The Union prints paper money that is legal tender by law, backed by nothing but the government's word"),
    ("1873-02-12", "Coinage Act", "the Crime of '73",
     "Silver is demonetised; the United States moves to a de facto gold standard"),
    ("1900-03-14", "Gold Standard Act", "law",
     "The dollar is defined as 25.8 grains of gold, nine-tenths fine"),
    ("1907-10-22", "Knickerbocker Trust", "Panic of 1907",
     "A bank run in New York spreads through the country; J. P. Morgan organises the rescue himself"),
    ("1908-05-30", "Aldrich-Vreeland Act", "law",
     "Emergency currency and a National Monetary Commission to study a central bank"),
    ("1910-11-22", "Jekyll Island", "secret meeting",
     "Senator Aldrich and bankers from Morgan, Rockefeller and Kuhn, Loeb draft a central bank plan in secret"),
    ("1913-02-03", "16th Amendment", "ratified",
     "Congress may tax incomes; the federal income tax follows the same year"),
    ("1913-12-23", "Woodrow Wilson", "Federal Reserve Act",
     "The Federal Reserve Act is signed two days before Christmas"),
    ("1913", "Woodrow Wilson", "The New Freedom",
     "A great industrial nation is controlled by its system of credit. Our system of credit is concentrated."),
    ("1914-11-16", "Federal Reserve", "opening",
     "The twelve Federal Reserve Banks open for business"),
    ("1929-10-24", "Wall Street", "Black Thursday",
     "The stock market crashes; the Great Depression begins"),
    ("1933-04-05", "Franklin D. Roosevelt", "Executive Order 6102",
     "Americans must hand their gold to the Federal Reserve at 20.67 dollars an ounce"),
    ("1934-01-30", "Gold Reserve Act", "law",
     "The dollar is devalued to 35 dollars an ounce; the gold belongs to the Treasury now"),
    ("1965-07-23", "Coinage Act", "law",
     "Silver is removed from dimes and quarters; coins become tokens"),
    ("1968-03-18", "Congress", "law",
     "The last gold cover for Federal Reserve notes is removed"),
    ("1944-07-22", "Bretton Woods", "conference",
     "44 nations peg their currencies to the dollar, and the dollar to gold at 35 an ounce"),
    ("1971-08-15", "Richard Nixon", "television address",
     "The dollar's convertibility into gold is suspended; money is now backed by trust alone"),
    ("1974-12-31", "United States", "law",
     "Americans may own gold again, forty-one years after the confiscation"),
    ("1999-11-12", "Gramm-Leach-Bliley Act", "law",
     "Glass-Steagall is repealed; commercial and investment banking merge again"),
    ("1976-11", "Whitfield Diffie, Martin Hellman", "New Directions in Cryptography",
     "Public-key cryptography is born"),
    ("1983", "David Chaum", "Blind signatures for untraceable payments",
     "Digital cash that cannot be traced is described for the first time"),
    ("1990", "David Chaum", "DigiCash",
     "DigiCash is founded in Amsterdam to bring untraceable e-cash to banks"),
    ("1992-09", "Cypherpunks", "mailing list",
     "The cypherpunks mailing list starts in the Bay Area"),
    ("1993-03-09", "Eric Hughes", "A Cypherpunk's Manifesto",
     "Privacy is necessary for an open society in the electronic age."),
    ("1996", "e-gold", "launch",
     "e-gold launches: digital money backed by gold in a vault"),
    ("1997-03-28", "Adam Back", "cypherpunks",
     "Hashcash: proof of work against spam, later the puzzle Bitcoin miners solve"),
    ("1998-10-01", "Liberty Dollar", "launch",
     "A private silver-backed currency goes on sale in the United States"),
    ("1998-11", "Wei Dai", "b-money",
     "b-money: an anonymous, distributed electronic cash system, cited in the Bitcoin paper"),
    ("2004-08-15", "Hal Finney", "RPOW",
     "Reusable proofs of work: Hashcash tokens that can be passed on"),
    ("2005-12", "Nick Szabo", "Bit gold",
     "Bit gold, the design closest to Bitcoin, is published"),
    ("2007-04-27", "e-gold", "indictment",
     "e-gold is indicted for money laundering; it never recovers"),
    ("2007-11-14", "FBI", "Liberty Dollar",
     "The Liberty Dollar's offices are raided and its silver seized"),
    ("2008-08-18", "bitcoin.org", "domain",
     "The domain bitcoin.org is registered"),
    ("2008-09-15", "Lehman Brothers", "bankruptcy",
     "Lehman Brothers files for bankruptcy, the largest in US history"),
    ("2011-03-18", "Liberty Dollar", "verdict",
     "Its founder is convicted; the currency is finished"),
    ("2013-05-28", "Liberty Reserve", "shutdown",
     "Liberty Reserve is shut down; prosecutors speak of 6 billion dollars laundered"),
    # -- Bitcoin's own words
    ("2008-10-31", "Satoshi Nakamoto", "Cryptography Mailing List",
     "I've been working on a new electronic cash system that's fully peer-to-peer, with no trusted third party."),
    ("2008-11-07", "Hal Finney", "Cryptography Mailing List",
     "Bitcoin seems to be a very promising idea."),
    ("2008-11-07", "Satoshi Nakamoto", "Cryptography Mailing List",
     "Pure P2P networks like Gnutella and Tor seem to be holding their own."),
    ("2008-11-08", "Satoshi Nakamoto", "Cryptography Mailing List",
     "There will be deflation and early holders of money will see its value increase."),
    ("2008-11-13", "Satoshi Nakamoto", "Cryptography Mailing List",
     "The proof-of-work chain is a solution to the Byzantine Generals' Problem."),
    ("2008-11-14", "Satoshi Nakamoto", "Cryptography Mailing List",
     "I'm better with code than with words though."),
    ("2009-01-09", "Satoshi Nakamoto", "Cryptography Mailing List",
     "Total circulation will be 21,000,000 coins."),
    ("2009-01-11", "Hal Finney", "Twitter",
     "Running bitcoin"),
    ("2009-01-16", "Satoshi Nakamoto", "Cryptography Mailing List",
     "It might make sense just to get some in case it catches on."),
    ("2009-02-11", "Satoshi Nakamoto", "P2P Foundation",
     "The root problem with conventional currency is all the trust that's required to make it work."),
    ("2009-02-11", "Satoshi Nakamoto", "P2P Foundation",
     "They lend it out in waves of credit bubbles with barely a fraction in reserve."),
    ("2010-02-06", "Satoshi Nakamoto", "bitcointalk",
     "At most only 21 million coins for 6.8 billion people in the world."),
    ("2010-02-14", "Satoshi Nakamoto", "bitcointalk",
     "I'm sure that in 20 years there will either be very large transaction volume or no volume."),
    ("2010-05-18", "Laszlo Hanyecz", "bitcointalk",
     "I'll pay 10,000 bitcoins for a couple of pizzas."),
    ("2010-06-17", "Satoshi Nakamoto", "bitcointalk",
     "Once version 0.1 was released, the core design was set in stone for the rest of its lifetime."),
    ("2010-06-21", "Satoshi Nakamoto", "bitcointalk",
     "Lost coins only make everyone else's coins worth slightly more. Think of it as a donation to everyone."),
    ("2010-07-05", "Satoshi Nakamoto", "bitcointalk",
     "Writing a description for this thing for general audiences is bloody hard."),
    ("2010-08-07", "Satoshi Nakamoto", "bitcointalk",
     "Not having Bitcoin would be the net waste."),
    ("2010-08-27", "Satoshi Nakamoto", "bitcointalk",
     "Bitcoins have no dividend, therefore not like a stock. More like a collectible or commodity."),
    ("2010-12-11", "Satoshi Nakamoto", "bitcointalk",
     "WikiLeaks has kicked the hornet's nest, and the swarm is headed towards us."),
    ("2011-04-23", "Satoshi Nakamoto", lambda: t("email to Mike Hearn"),
     "I've moved on to other things. It's in good hands with Gavin and everyone."),
    ("2013-03-19", "Hal Finney", "bitcointalk",
     "I think I was the first person besides Satoshi to run bitcoin."),
    # -- Headlines and milestones (Jakob, 2026-09-05): the events that
    # made the chain's history, dated, in the order they happened. The
    # text is a headline in our own words unless it is a quotation (the
    # genesis block's Times headline is verbatim). Dates from the chain
    # where the event is a block, otherwise the day the news broke.
    ("2009-01-03", "The Times", "front page, in the genesis block",
     "Chancellor on brink of second bailout for banks"),
    ("2009-01-12", "Block 170", "first transaction",
     "Satoshi sends 10 BTC to Hal Finney, the first Bitcoin transaction"),
    ("2010-05-22", "bitcointalk", "Bitcoin Pizza Day",
     "Two pizzas bought for 10,000 BTC, the first real-world purchase"),
    ("2010-07-11", "Slashdot", "news",
     "Bitcoin 0.3 hits the front page, users and price climb within days"),
    ("2010-07-18", "Mt. Gox", "launch",
     "Mt. Gox opens as a Bitcoin exchange"),
    ("2011-02-09", "Slashdot", "news",
     "Bitcoin reaches parity with the US dollar"),
    ("2011-04-20", "Forbes", "Crypto Currency",
     "First big-press feature on Bitcoin"),
    ("2011-06-01", "Gawker", "Silk Road",
     "The underground website where you can buy any drug imaginable"),
    ("2011-06-19", "Mt. Gox", "hack",
     "Mt. Gox is hacked, the price collapses to a cent on the exchange"),
    ("2012-11-28", "Block 210,000", "first halving",
     "The block reward drops from 50 to 25 BTC"),
    ("2013-03-28", "market", "milestone",
     "Bitcoin's market capitalisation passes one billion dollars"),
    ("2013-10-02", "FBI", "Silk Road",
     "Silk Road is seized and Ross Ulbricht arrested in San Francisco"),
    ("2013-10-29", "Vancouver", "Robocoin",
     "The world's first Bitcoin ATM opens in a coffee shop"),
    ("2013-11-27", "Mt. Gox", "price",
     "Bitcoin trades above 1,000 dollars for the first time"),
    ("2013-12-05", "People's Bank of China", "ban",
     "Chinese financial institutions are barred from handling Bitcoin"),
    ("2014-02-28", "Mt. Gox", "bankruptcy",
     "Mt. Gox files for bankruptcy, 850,000 BTC reported missing"),
    ("2014-03-06", "Newsweek", "The Face Behind Bitcoin",
     "Newsweek names Dorian Nakamoto as Satoshi; he denies it"),
    ("2016-07-09", "Block 420,000", "second halving",
     "The block reward drops from 25 to 12.5 BTC"),
    ("2017-08-01", "Block 478,558", "fork",
     "Bitcoin Cash splits off from the chain"),
    ("2017-08-24", "Block 481,824", "SegWit",
     "Segregated Witness activates"),
    ("2017-12-17", "market", "all-time high",
     "Bitcoin nears 20,000 dollars, CME futures start the next day"),
    ("2020-05-11", "Block 630,000", "third halving",
     "The block reward drops from 12.5 to 6.25 BTC"),
    ("2021-02-08", "Tesla", "SEC filing",
     "Tesla discloses 1.5 billion dollars in Bitcoin"),
    ("2021-09-07", "El Salvador", "Bitcoin Law",
     "Bitcoin becomes legal tender in El Salvador"),
    ("2021-11-10", "market", "all-time high",
     "Bitcoin passes 69,000 dollars"),
    ("2021-11-14", "Block 709,632", "Taproot",
     "Taproot activates"),
    ("2022-11-11", "FTX", "bankruptcy",
     "FTX files for bankruptcy"),
    ("2024-01-10", "SEC", "ETF",
     "The SEC approves spot Bitcoin ETFs in the United States"),
    ("2024-04-20", "Block 840,000", "fourth halving",
     "The block reward drops from 6.25 to 3.125 BTC"),
    ("2024-12-05", "market", "milestone",
     "Bitcoin crosses 100,000 dollars"),
)

def chronicle_date(iso):
    """'2008-10-31' -> '31.10.2008'; a month or a year alone stays that
    short ('11.1976', '1983') — early events have no known day."""
    if LANGUAGE != "de":
        return iso
    return ".".join(reversed(iso.split("-")))


def chronicle_entries():
    """The quotes as the browser and the page use them."""
    # Three parts, typed one after the other, each with its own colour:
    # the date (green), the name (the log's muted orange), the quote
    # (white) — "DD.MM.YYYY Name : quote" (Jakob, 2026-09-05; the angle
    # brackets around the name lasted an hour). The source stays in
    # CHRONICLE_QUOTES for the record but is no longer shown.
    # Sorted by date here, whatever the order in the table: the line must
    # run forward through history (Jakob, 2026-09-05). The colon between
    # name and text went the same evening — the colours separate them.
    return [{"teile": [f"[{chronicle_date(date)}] ", who, f" {text}"]}
            for date, who, where, text in sorted(CHRONICLE_QUOTES, key=lambda q: q[0])]


# When the generator started, whole seconds. The chronicle counts its
# entries from here, so every restart of the service begins at Bretton
# Woods (Jakob, 2026-09-05) — before, entry number floor(now / takt)
# landed anywhere in history. The browser gets the same origin through
# chronik.json and does the same arithmetic.
CHRONICLE_ORIGIN = int(time.time())


def chronicle_text():
    """chronik.json — written once at start, like stil.css."""
    return json.dumps({"start": CHRONICLE_ORIGIN, "zitate": chronicle_entries()},
                      ensure_ascii=False, separators=(",", ":"))


def build_chronicle(interval):
    """The two lines as the page carries them without JavaScript: entry
    number (now // interval) — the same arithmetic dash.js uses, so a page
    reload lands on the entry the animation would be at."""
    quotes = chronicle_entries()
    entry = quotes[int((time.time() - CHRONICLE_ORIGIN) // max(1, interval)) % len(quotes)]
    # A fixed box on the right half of the header, flush with the right
    # column of the page below; the text starts at that edge and wraps
    # only at the right margin (Jakob, 2026-09-05, fourth arrangement of
    # the day). Two lines are reserved, so nothing moves while typing and
    # the cursor of the empty line always stands at the same spot. The
    # hidden full-text shadow of the afternoon is not needed any more.
    spans = "".join(f'<span class=tz{i + 1}>{html_escape(part)}</span>'
                    for i, part in enumerate(entry["teile"]))
    return ('<div class=chronik id=chronik><div class="term zitat">'
            f"<span class=tipp>{spans}<span class=cursor></span></span></div></div>")


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
        # One bar per hour, the hour's peak, 24 hours (Jakob, 2026-09-03).
        # It replaced the one-hour curve; the first bar stands after the
        # first sample, the rest fill in over the day.
        fields.append((t("peak per hour · 24 h"),
                       build_temperature_columns()
                       or build_skeleton(t("Temperature history, still measuring"), columns=True),
                       "grafik"))

    # CPU use as a percentage, measured between two passes. The load average
    # shown before ("0.02 on 4 cores") is a queue length, and nobody reading
    # the page knows what that is — the number needs a unit (2026-09-01).
    cpu = cpu_percent()
    if cpu is not None:
        fields.append((t("Load"), decimal_sep(f"{cpu:.0f} % CPU"),
                       "warn" if cpu >= 85 else ""))
        # As a bar too, like the disk (Jakob, 2026-09-05).
        fields.append(("", build_bar(cpu / 100, "warn" if cpu >= 85 else "",
                                     title=decimal_sep(f"{cpu:.0f} % CPU")), "grafik"))

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
        fields.append(("", build_bar(used / total, "warn" if used / total > 0.92 else "",
                                     title=t("{used} of {total}", used=format_bytes(used),
                                             total=format_bytes(total))), "grafik"))

    try:
        # shutil.disk_usage instead of os.statvfs: the test run also has to
        # work on Windows, where statvfs does not exist (2026-09-03).
        usage = shutil.disk_usage(cfg["DATA_DIR"])
        free = usage.free
        gesamt_platz = usage.total
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


def block_age(height, block_time):
    """Seconds since the tip block reached us — not since it was mined.

    The timestamp inside a block is the miner's clock, and the rules allow
    it up to two hours ahead of ours. On 2026-09-02 block 965,085 carried a
    time 95 s in the future and the page said "Block · vor -95 s". The
    arrival is known from the journal (ANNOUNCED); where it is not, the
    miner's time is used but never allowed to go negative.
    """
    seen = ANNOUNCED.get(height)
    if seen:
        return max(0.0, time.time() - seen[1])
    return max(0.0, time.time() - block_time)


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
        age = block_age(blocks, float(block_time))
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
        fetch_block_data(cfg, blocks)
        fetch_hashrate(cfg, blocks)

    # Rate and remaining time are shown large in the state bar above.
    rate_text = eta_text = None
    if not in_sync:
        per_hour, seconds_left = estimate_remaining(fraction)
        if per_hour is not None:
            rate_text = t("{n} pp/h", n=decimal_sep(f"{per_hour:.2f}"))
            eta_text = format_duration(seconds_left)

    # --- Network facts: difficulty --------------------------------------------
    # Cut down on 2026-09-03 (Jakob): the right-hand column of the 'Network'
    # card keeps only the difficulty and the count to the next adjustment;
    # reward and halving moved to the state bar, the adjustment history is
    # gone. The energy estimate and the electricity comparison that stood
    # here for two days left on 2026-09-05 (Jakob) — the card now shares its
    # width with the Electrum column instead.
    difficulty = float(chain.get("difficulty", 0))
    # The count to the next adjustment is always known — it depends only on
    # the header height, not on the history buffer.
    retarget_left = RETARGET_INTERVAL - (headers % RETARGET_INTERVAL)
    chain_fields = [
        (t("Difficulty"), format_magnitude(difficulty), ""),
        (t("next adjustment"), t("in {n} blocks|dativ", n=format_number(retarget_left)), ""),
    ]

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
    # Cut down on 2026-09-03 (Jakob): memory, the fees waiting, and the fill
    # bar. Core does not know the value of the transactions in the mempool
    # — only their fees (total_fee); summing the outputs would mean a call
    # per transaction, tens of thousands every cycle.
    fill = min(1.0, usage / max_usage) if max_usage else 0.0
    # One column 'Chain & Mempool' since 2026-09-05 (Jakob): the card is
    # about 30 rem wide on the Pi's page, and three columns — chain, mempool,
    # Electrum — would break every value in two. Two fit; the Electrum
    # column is the other one, and both end on a bar at the same height.
    mempool_fields = [
        (t("Chain & Mempool"), "", "spalte"),
        *chain_fields,
        (t("Memory use"),
         (t("{used} of {total}", used=format_bytes(usage), total=format_bytes(max_usage))
          if max_usage else format_bytes(usage)),
         ""),
        (t("fees waiting"), format_btc(float(mempool.get("total_fee", 0) or 0) * 100_000_000), ""),
        # Once it fills up, the minimum fee rises and cheap transactions
        # are dropped. Yellow from 80 %.
        (t("fill level"), build_bar(fill, "warn" if fill >= 0.8 else ""), "grafik"),
    ]

    # Fee estimates only once the chain is up to date. During the sync Core
    # reliably answers "no data" — a call for an answer we already know.
    # Raw estimates in sat/vB — the metrics bar shows them. Kept as numbers
    # so nobody has to parse "4,1 sat/vB" back (2026-09-01). The 6- and
    # 24-block targets left with the card rows on 2026-09-03.
    fee_rates = {}
    if in_sync:
        # 'economical' since 2026-09-03: what is enough to get in, not what
        # is safe under any circumstances. Core's default 'conservative'
        # adds a margin for a fee market that could turn — that is the
        # number for the small print ("safe"), keyed as "sicher".
        for key, mode in ((1, "economical"), ("sicher", "conservative")):
            try:
                response = rpc(cfg, "estimatesmartfee", [1, mode])
            except RpcError:
                break
            rate = response.get("feerate")
            if rate:
                fee_rates[key] = float(rate) * 100000

    summary = {
        "bloecke": blocks,
        "kopfzeilen": headers,
        "blockalter": block_age(blocks, float(block_time)) if (block_time and in_sync) else None,
        "verbindungen": int(verbindungen),
        "mempool": int(mempool.get("size", 0)),
        "gebuehren": fee_rates,
        # The median fee of the most recent block, from getblockstats. Was
        # the tile's large number from 2026-09-01 to 2026-09-03; now only
        # a fallback while Core has no estimate yet.
        "median_gebuehr": (BLOCK_DATA[-1][3] if in_sync and BLOCK_DATA else None),
        "rueckstand": behind,
        "belegt": chain.get("size_on_disk", 0),
        "tempo": rate_text,
        "restzeit": eta_text,
        "stand": state_text,
        "gepruned": bool(chain.get("pruned")),
        "version": str(net.get("subversion", "")).strip("/"),
        "laufzeit": laufzeit,
        # How this node looks from the other side — what a peer's getpeerinfo
        # would say about us. Shown in the detail box when pointing at the
        # hub (Jakob, 2026-09-03). Pure structure, dash.js sets it via
        # textContent like everything else in that box.
        "eigen": {
            "version": str(net.get("subversion", "")).strip("/"),
            "protokoll": net.get("protocolversion"),
            "dienste": ", ".join(net.get("localservicesnames") or []),
            "dauer_s": laufzeit,
            "eingehend": net.get("connections_in"),
            "ausgehend": net.get("connections_out"),
            "adressen": [
                f'{a.get("address", "")}:{a.get("port", "")}'
                for a in (net.get("localaddresses") or []) if a.get("address")
            ],
            "relay": float(net.get("relayfee", 0) or 0) * 100000,
        },
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
            # The label names the scale's top — the bars have no axis, and
            # a bar without a number is a shape (Jakob, 2026-09-05). Each
            # bar still carries its exact value as a tooltip.
            (t("{label} · peak {v}", label=t("Volume per block"),
               v=format_btc(max(outputs) if outputs else 0)),
             build_volume_columns(), "grafik"),
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
            (t("{label} · peak {v}", label=t("average fee per block"),
               v=decimal_sep(f"{(max(known) if known else 0):.1f} sat/vB")),
             build_fee_columns(fees, [e[0] for e in BLOCK_DATA]),
             "grafik"),
        ]
        fee_fields_24 = [f for f in fee_fields_24 if f[1]]

    # 'Network' is no longer a card of its own either — the connections live
    # in 'Connected nodes', version and uptime in the page header.
    # One card 'Network' with two inner columns — mempool left, chain right
    # — instead of two narrow cards. Together with 'System' that makes two
    # equal cards in the row (2026-09-01).
    # Since 2026-09-05 the Electrum server is its third column; one_pass
    # merges it in, so the identity carries both names.
    groups = [
        ("Network & Electrum", mempool_fields),
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
# Core numbers its peers from zero at every start. An announcement from
# before the last restart therefore names a peer id that a stranger of
# today may carry again — 13 minutes after a restart the ranking showed
# "peer 310 × 44", and a new peer 310 would have inherited those 44
# (2026-09-03). The start line marks the boundary; whatever was announced
# before it is kept in the count but never matched to a connected peer.
#   Bitcoin Core version v31.1.0 (release build)
NODE_START = [0.0]
START_LINE = re.compile(r"Bitcoin Core version v?\d")


def before_restart(when):
    return when < NODE_START[0]

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

        if START_LINE.search(row):
            NODE_START[0] = max(NODE_START[0], when)
            continue

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

    Returns (dots, ok, total, ahead, alarm) — dots as a list of 'gleich',
    'hinten' or 'voraus' in time order, ahead as the largest lead a stranger
    reported (or 0), alarm whether that lead deserves the state bar.

    The height in the version handshake is a bare claim, and strangers lie:
    on 2026-09-03 several peers announced 1,340–1,440 blocks more than the
    chain had, hours apart and consistently, while `headers == blocks` and
    `getchaintips` showed nothing above our tip. Core itself never trusts
    the claim — it only follows headers that actually arrive. So a single
    red dot in a row of green ones stays a red dot, but the state bar turns
    only when at least two samples of the hour are ahead AND the newest one
    is among them. A real lag looks exactly like that: every fresh stranger
    reports more than we have.
    """
    tip = (kz or {}).get("bloecke")
    if not tip or not CHAIN_SAMPLES:
        return [], 0, 0, 0, False
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
    alarm = dots.count("voraus") >= 2 and dots[-1] == "voraus"
    return dots, ok, len(dots), ahead, alarm


def chain_check_markup(kz):
    """Dots and sentence for the head of the network card."""
    dots, ok, total, ahead, alarm = chain_check(kz)
    if not total:
        return ""
    last = format_age(time.time() - CHAIN_SAMPLES[-1][0])
    # Each sample is compared with our height AT THAT TIME, so the sentence
    # must not name the current tip: "confirm block 965,082" thirty seconds
    # after that block arrived, from a sample taken at 22:24, was wrong
    # (2026-09-01). What the samples say is whether strangers saw the same
    # chain as we did — behind is harmless, ahead is the alarm.
    behind = total - ok - sum(1 for d in dots if d == "voraus")
    claimed = total - ok - behind
    cls = ""
    if alarm:
        sentence = t("{k} probes report up to {n} blocks more than we have", k=claimed, n=format_number(ahead))
        cls = " warn"
    else:
        # Assembled from pieces so that every sample is accounted for
        # (23 + 1 of 26 left two unexplained, 2026-09-03) and the verb
        # agrees with the count ("1 behaupteten").
        sentence = t("{ok} of {n} probes matched our height", ok=ok, n=total)
        if behind:
            sentence += t(", {behind} behind", behind=behind)
        if ahead:
            # Claims without headers: keep the number visible, keep the tone calm.
            claim = (t("one claimed") if claimed == 1
                     else t("{k} claimed", k=claimed))
            sentence += t(", {claim} up to {b} blocks more without delivering headers",
                          claim=claim, b=format_number(ahead))
        else:
            sentence += t(", none ahead")
        sentence += t(" · last {when}", when=last)
    marks = "".join(f'<i class="stich {d}"></i>' for d in dots[-12:])
    return (f'<span class="abgleich{cls}" title="{html_escape(t("Chain check: every few minutes Core asks a random node for its height. Last {when}", when=last))}">'
            f"<span class=stiche>{marks}</span>{html_escape(sentence)}</span>")


def chain_check_warning(kz):
    """The warning for the state bar, or None."""
    _, _, _, ahead, alarm = chain_check(kz)
    if alarm:
        return t("Chain check: recent probes report {n} blocks more", n=format_number(ahead))
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
    if before_restart(when):
        return None, peer_id, height, when
    index = next((i for i, p in enumerate(peers) if p.get("id") == peer_id), None)
    return index, peer_id, height, when


def peer_label(peers, peer_id, when):
    """A connected peer by short address, anyone else by Core's id."""
    if not before_restart(when):
        p = next((p for p in peers if p.get("id") == peer_id), None)
        if p:
            return shorten_address(p["adresse"])
    return t("peer {n}", n=peer_id)


def announcer_ranking(peers, limit=3):
    """The peers that announced most blocks first in the last 24 hours.

    Returns [(label, count, gone)], connected peers by short address,
    others by their Core peer id. The same id before and after a restart
    is two different peers and counted apart.
    """
    counts = {}
    for peer_id, when in ANNOUNCED.values():
        key = (peer_id, before_restart(when))
        counts[key] = counts.get(key, 0) + 1
    ranking = []
    for (peer_id, old), n in sorted(counts.items(), key=lambda e: -e[1])[:limit]:
        when = 0 if old else NODE_START[0]
        label = peer_label(peers, peer_id, when)
        ranking.append((label, n, label == t("peer {n}", n=peer_id), old))
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
        ann_name = peer_label(peers, ann_id, ann_when)
        if ann_index is None:
            ann_name += " " + t("(no longer connected)")
        when = format_age(time.time() - ann_when)
        head = t("Block {n} · announced {when} by {peer}", n=format_number(height),
                 when=when, peer=ann_name)
    else:
        p = peers[source]
        when = format_age(time.time() - p["zuletzt_von"])
        if height:
            head = t("Block {n} arrived {when} from {peer}", n=format_number(height),
                     when=when, peer=shorten_address(p["adresse"]))
        else:
            head = t("The last block arrived {when} from {peer}",
                     when=when, peer=shorten_address(p["adresse"]))
    return head


def ranking_text(peers):
    """Second line of the detail box: who was first most often, 24 h."""
    ranking = announcer_ranking(peers)
    if not ranking:
        return ""
    total = len(ANNOUNCED)
    parts = " · ".join(f"{label} × {n}" for label, n, _, _ in ranking)
    text = t("first to announce, {total} blocks in 24 h: {parts}",
             total=total, parts=parts)
    # One note for all numbered entries instead of "(no longer connected)"
    # three times in a row (2026-09-03). Announcements from before the
    # restart say so — that is why they cannot be matched to a peer.
    if any(gone for _, _, gone, _ in ranking):
        text += " · " + (t("numbered peers are from before the restart, no longer connected")
                         if all(old for _, _, gone, old in ranking if gone)
                         else t("numbered peers are no longer connected"))
    return text


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


MAP_ROWS_MIN = 8      # peers per side the frame always holds (16 in all)
MAP_CHARS_MIN = 50    # "127.0.0.1:35824 · Tor · eingehend · 547 ms · 44,0 KB"


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
    announcer = announcer_of_tip(peers, kz)[0]

    row_height = 30
    margin_top = 34
    # A fixed frame: rows for MAP_ROWS_MIN peers per side and a label width
    # for MAP_CHARS_MIN characters, whatever is connected right now. Before
    # this every peer that came or went changed the viewBox, the rendered
    # height with it, and the whole left column shifted — a restless page
    # (Jakob, 2026-09-03). Beyond the frame the map still grows.
    # Split evenly, left side first when odd; the frame's rows are the
    # larger of that and the minimum. Both sides are centred in the frame
    # (Jakob, 2026-09-03: "symmetrical wherever possible").
    split = (len(peers) + 1) // 2
    half = max(split, MAP_ROWS_MIN)
    height = margin_top * 2 + half * row_height
    offset = {False: (half - split) / 2, True: (half - (len(peers) - split)) / 2}

    # The width follows from the longest label, not from a fixed value. An
    # SVG clips everything beyond its viewBox — with a fixed width the end of
    # the line disappeared for long addresses and three-digit second values.
    longest = max(max(len(peer_line_text(p)) for p in peers), MAP_CHARS_MIN)
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
        rechts = i >= split
        reihe = (i - split if rechts else i) + offset[rechts]
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
        # Filled = outbound, hollow ring = inbound. Was dropped on
        # 2026-09-02 (direction stands on the line anyway) and came back on
        # 2026-09-05 (Jakob): the word is on the line, but the eye needs it
        # on the dot. Two legend entries explain it.
        filled = "" if p["eingehend"] else " voll"
        # The path of the most recent block: solid orange spoke to the peer
        # that announced it first, a solid spoke in its own colour to the
        # one that delivered it (only when that is a different peer — with
        # a headers announcement Core fetches the block from the announcer,
        # and then there is just the orange one), lit spoke to every peer
        # we handed it to.
        # The deliverer used to get a spoke of its own; dropped on
        # 2026-09-02 — who announced first is the interesting peer, who
        # then handed over the bytes is not.
        # The receivers ("got it from us") were lit too until 2026-09-02;
        # dropped with the deliverer — the map marks the announcer, full
        # stop. The per-peer counts live on in the detail box.
        role = " ansager" if i == announcer else ""

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
    # A group so that dash.js can hang the hover on it; the invisible circle
    # gives it one shape to hit, image and label included.
    hub = (
        '<g class="nabe" tabindex="0">'
        f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="{LOGO_R + 8}" class="nabefeld"/>'
        f'<image href="bitcoin.png?v={BITCOIN_V}" '
        f'x="{mx - LOGO_R:.1f}" y="{my - LOGO_R:.1f}" '
        f'width="{LOGO_R * 2}" height="{LOGO_R * 2}"/>'
        f'<text x="{mx:.1f}" y="{my + LOGO_R + 26:.1f}" class="eigentext" '
        f'text-anchor="middle">{html_escape(t("this node"))}</text>'
        '</g>'
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

    # Since 2026-09-05 (Jakob) this is the third inner column of the
    # 'Network & Electrum' card, not a card of its own: the "spalte" marker
    # opens the column, the copy fields land under all three columns. Where
    # there is no network card (node unreachable), one_pass shows the
    # fields as a card of their own.
    fields = [
        ("Electrum", "", "spalte"),
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
        # The value is short since 2026-09-05 — in the half card the old
        # "complete · block 965.530" broke in two (Jakob). The heights
        # moved into the bar's tooltip.
        heights = t("{n} of {tip} bloecke", n=format_number(indexed),
                    tip=format_number(tip))
        if behind <= 1:
            fields.append((t("Index"), t("complete"), "gut"))
            fields.append(("", build_bar(1.0, title=heights), "grafik"))
        else:
            # Close to the tip a percentage says "100,0 %" while blocks are
            # still missing — there the count is the honest figure.
            rest = (t("{n} bloecke to go", n=format_number(behind))
                    if fraction >= 0.999
                    else decimal_sep(f"{fraction * 100:.1f} %"))
            fields.append((t("Index"), rest, "warn"))
            fields.append(("", build_bar(fraction, "warn", title=heights), "grafik"))
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
/* Network types in the network map. I2P is magenta since 2026-09-02: it
   used to share the warning yellow and sat next to the block orange. */
--netz-ipv4:#5aa2f0;--netz-ipv6:#9b8cff;--netz-onion:#2fd39a;--netz-i2p:#e070c8;
/* The path of the most recent block through the map: Bitcoin orange, used
   nowhere else so it stays unmistakable */
--block:#f7931a;
/* High fees in the 24-hour bars: a dark violet, not the block orange
   (Jakob, 2026-09-05) — orange stays the block's colour alone */
/* The three steps of the 24-hour bars, low to high: neutral grey, the
   accent, block orange only at the top (Jakob, 2026-09-05, variant E of
   five; the one-hue ramp before it read as noise) */
--stufe1:#4a5361;--stufe2:#2fd39a;--stufe3:#f7931a;
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
--netz-ipv4:#2b6fd0;--netz-ipv6:#6a52e0;--netz-onion:#0d9c6b;--netz-i2p:#b0399a;
--block:#d9780a;
--stufe1:#b4b2a9;--stufe2:#0d9c6b;--stufe3:#d9780a;
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
/* Two halves with the same gap as the rows below: brand, versions and
   clock on the left, the chronicle on the right, its left edge the left
   edge of the network card (Jakob, 2026-09-05). */
/* align-items:start, not center: the terminal reserves two lines, and
   the brand row must sit on its FIRST line — one baseline across the
   header (Jakob, 2026-09-05). Same font size and line-height on both
   sides, and the brand mark no taller than the line, or it lifts the row. */
header{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--e1) var(--e4);
align-items:start;padding-bottom:var(--e3);border-bottom:1px solid var(--rand)}
@media(min-width:80rem){header{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
.kopfgruppe{display:flex;flex-wrap:wrap;gap:var(--e1) var(--e4);
align-items:center;min-width:0;line-height:1.4;min-height:1.4em}
/* One typeface and size across the header row — brand, quote, versions,
   clock all in the mono at .73rem, on one baseline (Jakob, 2026-09-03). */
h1{font-size:.73rem;font-family:var(--mono);font-weight:600;display:flex;
align-items:center;gap:var(--e2);white-space:nowrap}
h1 b{font-weight:400;color:var(--sehrleise);letter-spacing:0}
.marke{width:1em;height:1em;flex:none;display:block}
.kopfrechts{display:flex;align-items:center;gap:var(--e3);
color:var(--sehrleise);font-size:.73rem;font-family:var(--mono)}
/* Middle of the header: versions and uptime. Replaces the 'Updates' card —
   visible but quiet as long as everything is fine. */
.kopfinfo{display:flex;align-items:center;gap:var(--e2);
color:var(--leise);font-size:.73rem;font-family:var(--mono);
cursor:default;white-space:nowrap}
.kopfinfo .kpunkt{width:.4rem;height:.4rem;border-radius:99px;flex:none;
background:var(--akzent)}
.kopfinfo.warn{color:var(--warn)}
.kopfinfo.warn .kpunkt{background:var(--warn)}
/* Narrow windows (phone): the versions take a row of their own under
   the brand. */
@media(max-width:60rem){#z-kopf{flex-basis:100%;margin-left:0}
.kopfrechts{margin-right:0}}
/* The pulse shows that the page updates itself. Without JavaScript it
   simply stands still — which is more honest than blinking into the void. */
.puls{width:.4rem;height:.4rem;border-radius:99px;background:var(--sehrleise);
transition:background .2s,box-shadow .2s}
[data-frisch=ja] .puls{background:var(--akzent);
box-shadow:0 0 0 3px color-mix(in srgb,var(--akzent) 22%,transparent)}
[data-frisch=alt] .puls{background:var(--warn);
box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 22%,transparent)}

/* ------------------------------------------------------------- Chronik --- */
/* Two terminal lines under the header: a voice from the early days on the
   left, a headline with the day's price on the right. Fixed height so the
   typing never moves the page; the cursor blinks, the text is set by
   dash.js via textContent, one entry per data cycle (2026-09-03). */
/* In the header row, between brand and versions: two lines at most, fixed
   height, so the typing never moves the row. */
/* Versions and clock centred in what the brand leaves of the left half:
   auto margins on both sides of the pair (on the ZONE wrapper #z-kopf,
   not on .kopfinfo inside it — there it did nothing, 2026-09-03). The
   chronicle fills the right half: a fixed box,
   the text starts at its left edge and wraps only at the right margin;
   two lines are reserved so the header never moves while typing and the
   cursor of the empty line always stands at the same spot (Jakob,
   2026-09-05 — the centred, shrink-to-fit box before it re-centred for
   every entry and the cursor jumped). */
#z-kopf{margin-left:auto}
.kopfrechts{margin-right:auto}
.chronik{min-width:0}
.term{padding:0;font-family:var(--mono);font-size:.73rem;line-height:1.4;
color:var(--text);width:100%;min-height:2.8em;white-space:normal;
overflow-wrap:anywhere;text-align:left;box-sizing:border-box}
@media(max-width:60rem){.term{min-height:4.2em}}
.term .tipp{display:block}
/* A faint phosphor glow on the line — a hint of the tube, not a CRT
   costume (Jakob, 2026-09-05). */
.term .tipp{text-shadow:0 0 6px color-mix(in srgb,var(--akzent) 25%,transparent)}
/* No box, no green: written straight into the header, white text, the
   attribution muted (Jakob, 2026-09-03). */
/* The prompt in a dark, quiet green — block orange was too loud up here
   (Jakob, 2026-09-03). */
/* Date and name in the muted green, quote white (Jakob, 2026-09-05; the
   name was the log's orange for an hour). */
.term.zitat .tz1{color:color-mix(in srgb,var(--akzent) 55%,var(--leise))}
.term.zitat .tz2{color:color-mix(in srgb,var(--akzent) 55%,var(--leise))}
.term .tz3{color:var(--text)}
.term .cursor::after{content:"▌";color:var(--akzent);animation:blink 1s steps(1) infinite}
/* Solid while writing, blinking only at rest — as a terminal does. */
.term.tippt .cursor::after{animation:none}
@keyframes blink{50%{opacity:0}}
@media(prefers-reduced-motion:reduce){.term .cursor::after{animation:none}}

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
/* Hashrate behind the bar, drawn like a price chart: a thin line and a
   gradient that fades out towards the bottom. Kept faint on purpose — it is
   a mood, the text above stays the message. Everything else in .zustand
   gets position:relative so it paints above the curve. */
.hashkurve{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.hashkurve polyline{stroke:var(--akzent);opacity:.55}
.hv0{stop-color:var(--akzent);stop-opacity:.22}
.hv1{stop-color:var(--akzent);stop-opacity:0}
.zlinks,.zrechts,.balkenbox{position:relative}
.zhash{margin-top:var(--e2);font-size:.76rem;color:var(--leise);
font-family:var(--mono);font-variant-numeric:tabular-nums}
.zhash b{color:var(--text);font-weight:600}
.zdelta.gut{color:var(--akzent)}.zdelta.warn{color:var(--warn)}
.zhashlabel{color:var(--sehrleise);font-family:var(--sans);text-transform:uppercase;
letter-spacing:.1em;font-size:.62rem;margin-left:var(--e1)}
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
/* The copy fields for the wallet (Electrum column, since 2026-09-05) sit
   under the columns, one address per line — a 62-character onion address
   fits into no half card. */
.spalten+.kopierblock{margin-top:var(--e4)}
.spalte h3{font-size:.62rem;text-transform:uppercase;letter-spacing:.12em;
color:var(--sehrleise);font-weight:600;min-height:var(--zeile);
display:flex;align-items:center;border-bottom:1px solid var(--rand)}
.spalte dl{flex:1}
.spalte{display:flex;flex-direction:column;min-width:0}
.grafiklabel{grid-column:1/-1;color:var(--sehrleise);font-size:.65rem;
text-transform:uppercase;letter-spacing:.09em;align-items:flex-end;
padding-bottom:var(--e1)}
/* The card's foot: a graph after the last row, outside the list, so it can
   grow with the card. In the wide cards it takes whatever the row's height
   leaves over; elsewhere it keeps the graph's own height. */
span.grafiklabel{display:flex;min-height:var(--zeile)}
.grafikfuss{display:flex;flex-direction:column;justify-content:center;
min-height:calc(var(--zeile) * 2)}
.weit .grafikfuss{flex:1;min-height:calc(var(--zeile) * 3.2)}
.weit .grafikfuss .minikurve{flex:1;height:auto}
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
.hoch8{height:8px}
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
   grey = the stranger is behind, red = the stranger is ahead of us.
   Always on a line of its own, left-aligned: with more than three dots it
   wrapped by itself, and the header jumped between one and two lines from
   cycle to cycle (Jakob, 2026-09-05). */
.abgleich{display:inline-flex;align-items:center;gap:var(--e2);
flex-basis:100%;justify-content:flex-start}
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

/* The hub is hoverable like a peer: the detail box then shows this node as
   its peers see it. The hit circle is invisible, only the label answers. */
.nabe{cursor:pointer;outline:none}
.nabefeld{fill:transparent}
.nabe:hover .eigentext,.nabe:focus-visible .eigentext{fill:var(--text)}
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
/* Who announced the last block first: orange spoke, a little wider, and a
   second ring on the dot rather than a fill — filled already means
   outbound. Deliverer and receivers had marks of their own until
   2026-09-02; one mark is enough. */
.peer.ansager .peerlinie{stroke:var(--block);stroke-opacity:1;stroke-width:2}
.peer.ansager .peerpunkt{stroke:var(--block);stroke-width:2.2;
filter:drop-shadow(0 0 3px var(--block))}
.netzfarbe.ansager{background:var(--block)}
/* Direction in the legend: ring = inbound, filled = outbound, in the
   neutral text colour so no network is implied. */
.netzfarbe.richtung{background:none;border:1.6px solid var(--leise);box-sizing:border-box}
.netzfarbe.richtung.voll{background:var(--leise)}
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
/* Fee tiers under the 24-hour bars, same dots as the map legend. */
.netzfarbe.gut{background:var(--akzent)}
.netzfarbe.warn{background:var(--warn)}
.netzfarbe.s1{background:var(--stufe1)}
.netzfarbe.s2{background:var(--stufe2)}
.netzfarbe.s3{background:var(--stufe3)}
.gebuehrenlegende{margin-top:var(--e1)}
.balken svg rect:hover,.saeulen rect:hover{opacity:.75}
/* The detail box has a FIXED height, not a minimum: the peer view (head,
   address, two rows of details) is taller than the resting sentence, and
   with min-height the box grew on every hover and pushed the log down
   (Jakob, 2026-09-03). 7.4 rem holds the tallest view at the usual card
   width; should a narrow screen wrap more, the box scrolls inside rather
   than growing. */
.peerdetail{background:var(--vertief);border:1px solid var(--rand);
border-radius:var(--rad);padding:var(--e3);height:7.4rem;overflow:auto;
box-sizing:border-box;margin-top:var(--e3);display:flex;flex-wrap:wrap;
align-items:center;align-content:center;gap:var(--e2) var(--e5)}
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
/* Line colours. Only what one looks for in a log: trouble at full colour,
   the accepted tip (UpdateTip) as a tint, and nothing else. The first
   version (2026-09-02) had the announcements in a second orange and the
   chain-check probes in green: with a log that is mostly blocks and
   probes, that coloured nearly every line and highlighted none. 60 % was
   still loud on the Pi; 35 % is a warm grey that reads when you look for
   it. */
.lz.fehler{color:var(--fehler)}
.lz.warn{color:var(--warn)}
.lz.spitze{color:color-mix(in srgb,var(--block) 35%,var(--leise))}
/* The height: clearly warmer than its line, but not a full-orange badge
   — 70 % looked like a warning on the Pi (2026-09-02). Weight stays
   normal, the colour does the work. */
.lz .hervor{color:color-mix(in srgb,var(--block) 65%,var(--leise))}
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
  /* Log line kinds, same table as the generator's. [kind, pattern] */
  var MUSTER = __MUSTER__.map(function (e) { return [e[0], new RegExp(e[1], e[2])]; });
  var HERVOR = __HERVOR__;     /* kind -> pattern for the piece that stands out */
  var letztesLog = null;

  var wurzel = document.documentElement;
  var takt = (Number(wurzel.dataset.interval) || 30) * 1000;
  var logtakt = (Number(wurzel.dataset.logintervall) || 5) * 1000;
  var peers = [];
  var eigen = null;            // this node as its peers see it
  var gemerkt = null;          // pinned peer, survives the refresh
  var blockweg = "";           // sentence about the last block's path
  var rangliste = "";          // who announced first most often, 24 h
  var erzeugt = 0;             // when status.json was written, unix seconds

  /* Without JS a <meta refresh> reloads the page periodically; it sits in
     <noscript>, so with JS it never exists. Removing it from here, as this
     script did until 2026-09-05, does not stop a reload the browser has
     already scheduled. */

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

  function zeigeEigen(kasten) {
    /* The hub: this node as the others see it — the same rows a peer's
       getpeerinfo would show about us. Every value is from our own
       getnetworkinfo, set via textContent all the same. */
    var kopf = document.createElement("div");
    kopf.className = "pkopf";
    var farbe = document.createElement("i");
    farbe.className = "netzfarbe ansager";
    kopf.appendChild(farbe);
    var art = document.createElement("span");
    art.textContent = T.dieser_node + " · " + eigen.eingehend + " " + T.eingehend
      + " · " + eigen.ausgehend + " " + T.ausgehend;
    kopf.appendChild(art);
    kasten.appendChild(kopf);
    var adr = document.createElement("div");
    adr.className = "padresse";
    adr.textContent = (eigen.adressen && eigen.adressen.length) ? eigen.adressen.join("  ") : T.keine_adresse;
    kasten.appendChild(adr);
    var dl = document.createElement("dl");
    zeile(dl, T.kennung, eigen.version);
    zeile(dl, T.protokoll, eigen.protokoll);
    zeile(dl, T.dienste, eigen.dienste);
    zeile(dl, T.laeuft, dauer(eigen.dauer_s));
    zeile(dl, T.relay, komma(Number(eigen.relay).toFixed(2)) + " sat/vB");
    kasten.appendChild(dl);
  }

  function zeigePeer(nr) {
    var kasten = document.getElementById("peerdetail");
    if (!kasten) { return; }
    var p = peers[nr];
    kasten.textContent = "";
    if (nr === "eigen" && eigen) { zeigeEigen(kasten); return; }
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
    var nabe = karte.querySelector(".nabe");
    if (nabe) {
      nabe.addEventListener("mouseenter", function () { zeigePeer("eigen"); });
      nabe.addEventListener("focus", function () { zeigePeer("eigen"); });
    }
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
    richteKopierknoepfe();

    var stempel = document.getElementById("stempel");
    if (stempel && daten.stempel) { stempel.textContent = daten.stempel; }

    /* The network map is not replaced while the mouse is inside it or a dot is
       pinned. Otherwise the dot vanishes from under the pointer and the
       detail box jumps away while you are reading it. */
    var karte = document.getElementById("netzkarte");
    if (karte && (karte.matches(":hover") || gemerkt !== null)) { return; }

    setzeZone("z-netz", daten.zonen.netz);
    peers = daten.peers || [];
    eigen = daten.eigen || null;
    blockweg = daten.blockweg || "";
    rangliste = daten.rangliste || "";
    erzeugt = daten.erzeugt || 0;
    verdrahtePeers();
  }

  var ersterAbruf = true;
  function holeStatus() {
    hole("status.json", false)
      .then(function (daten) {
        nachtragen(daten);
        /* The chronicle advances with the data, never on its own clock —
           the first fetch is covered by the load of chronik.json. */
        if (ersterAbruf) { ersterAbruf = false; } else { chronikSchritt(); }
      })
      .catch(function () { wurzel.dataset.frisch = "alt"; });
  }

  function holeProtokoll() {
    var kasten = document.getElementById("logtext");
    if (!kasten) { return; }
    hole("log.txt", true).then(function (text) {
      /* Plain text from a foreign source: Bitcoin Core logs the self-chosen
         identifiers of other nodes. Each line becomes a span whose text is
         set via textContent — always — and whose class comes from the
         pattern table. Nothing in the line can become markup. */
      if (letztesLog === text) { return; }
      letztesLog = text;
      var oben = kasten.parentNode.scrollTop;
      kasten.textContent = "";
      var zeilen = text.split("\n");
      for (var i = 0; i < zeilen.length; i++) {
        var span = document.createElement("span");
        var art = "";
        for (var k = 0; k < MUSTER.length; k++) {
          if (MUSTER[k][1].test(zeilen[i])) { art = MUSTER[k][0]; break; }
        }
        span.className = art ? "lz " + art : "lz";
        var treffer = art && HERVOR[art] ? zeilen[i].match(new RegExp(HERVOR[art])) : null;
        if (treffer) {
          /* Three text pieces, the middle one in its own element. All via
             textContent — the line is still never parsed as markup. */
          span.appendChild(document.createTextNode(zeilen[i].slice(0, treffer.index)));
          var b = document.createElement("b");
          b.className = "hervor";
          b.textContent = treffer[0];
          span.appendChild(b);
          span.appendChild(document.createTextNode(zeilen[i].slice(treffer.index + treffer[0].length)));
        } else {
          span.textContent = zeilen[i];
        }
        kasten.appendChild(span);
        if (i < zeilen.length - 1) { kasten.appendChild(document.createTextNode("\n")); }
      }
      kasten.parentNode.scrollTop = oben;
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

  /* Wired after every update, not once: setzeZone replaces the Electrum
     card's markup on the first fetch, and the fresh buttons had neither a
     listener nor the 'bereit' class — the page on the Pi showed no copy
     button at all, while the test page (no fetch) did (2026-09-03). */
  function richteKopierknoepfe() {
  document.querySelectorAll(".kopierknopf:not(.bereit)").forEach(function (knopf) {
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
  }
  richteKopierknoepfe();

  /* The chronicle: chronik.json is fetched once; entry number
     floor((now - start) / takt) — the same arithmetic as the generator's —
     so page and script agree. One entry per data cycle.

     Built as a reconciler, not as a sequence (2026-09-05, after an evening
     of "deletes the wrong text"): the data cycle only sets the TARGET.
     One loop compares what stands in the line with the target — if the
     text is a prefix of the target it types the next character, otherwise
     it deletes the last one; empty after deleting, it breathes 1.4 s with
     the cursor alone. Whatever the timing of fetches, restarts or tab
     switches, the loop always deletes exactly what is visible and types
     exactly the target. A hidden tab gets the target at once: browsers
     throttle timers there to seconds, and an animation nobody watches
     would still be running when the tab comes back. Text goes in via
     textContent only. */
  var chronik = null, tippTimer = null, letzteNr = -1;
  var ziel = null, geloescht = false;
  function chronikFelder() {
    var kasten = document.querySelector(".term.zitat");
    if (!kasten) { return null; }
    var felder = [1, 2, 3].map(function (n) { return kasten.querySelector(".tz" + n); });
    if (felder.some(function (f) { return !f; })) { return null; }
    return { kasten: kasten, felder: felder, cursor: kasten.querySelector(".cursor") };
  }
  function laufe() {
    if (tippTimer) { clearTimeout(tippTimer); tippTimer = null; }
    var e = chronikFelder();
    if (!e || !ziel) { return; }
    var soll = ziel.join(""), ist = e.felder.map(function (f) { return f.textContent; }).join("");
    if (document.hidden) {
      e.felder.forEach(function (f, j) { f.textContent = ziel[j]; });
      if (e.cursor) { e.felder[2].after(e.cursor); }
      e.kasten.classList.remove("tippt");
      return;
    }
    if (ist === soll) { e.kasten.classList.remove("tippt"); geloescht = false; return; }
    var schritt = Math.max(14, Math.min(48, Math.floor(takt * 0.6 / (soll.length || 1))));
    var loeschen = Math.max(4, Math.min(16, Math.floor(takt * 0.1 / (ist.length || 1))));
    var pause;
    if (soll.indexOf(ist) !== 0 || (geloescht && ist !== "")) {
      /* Not on the way to the target — or already deleting: then all the
         way down, not just to the common prefix ("[" is a prefix of every
         entry, and the line would restart from the bracket). */
      var feld = null;
      for (var j = 2; j >= 0; j -= 1) { if (e.felder[j].textContent) { feld = e.felder[j]; break; } }
      e.kasten.classList.add("tippt");
      if (e.cursor && feld.nextSibling !== e.cursor) { feld.after(e.cursor); }
      feld.textContent = feld.textContent.slice(0, -1);
      geloescht = true;
      pause = loeschen;
    } else if (ist === "" && geloescht) {
      /* Emptied: a short breath with only the cursor blinking. */
      geloescht = false;
      e.kasten.classList.remove("tippt");
      pause = 1400;
    } else {
      /* On the way: the next character, into the field it belongs to. */
      var pos = ist.length, n = 0, k = pos;
      while (n < 2 && k >= ziel[n].length) { k -= ziel[n].length; n += 1; }
      var zeichen = soll.charAt(pos), ziel_feld = e.felder[n];
      e.kasten.classList.add("tippt");
      if (e.cursor && ziel_feld.nextSibling !== e.cursor) { ziel_feld.after(e.cursor); }
      ziel_feld.textContent += zeichen;
      /* Rhythm of a hand, not of a ticker: a breath at every space, a
         longer one at punctuation, quick inside a word. */
      pause = schritt * (0.6 + Math.random() * 0.5);
      if (zeichen === " ") { pause = schritt * (1.8 + Math.random()); }
      if (".,;:\u2014".indexOf(zeichen) >= 0) { pause = schritt * 6; }
    }
    tippTimer = setTimeout(laufe, pause);
  }
  function chronikSchritt() {
    if (!chronik || !chronik.zitate.length) { return; }
    /* Counted from the generator's start (chronik.json), not from the
       epoch: a restart of the service begins the story at the top. The
       fetches are not aligned to the takt windows, so the same number can
       come twice near a window's edge — same number, nothing to do. */
    var n = Math.max(0, Math.floor((Date.now() - (chronik.start || 0) * 1000) / takt));
    if (n === letzteNr) { return; }
    letzteNr = n;
    var eintrag = chronik.zitate[n % chronik.zitate.length];
    ziel = (eintrag.teile || []).slice(0, 3).map(function (x) { return x || ""; });
    while (ziel.length < 3) { ziel.push(""); }
    laufe();
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { laufe(); } });
  hole("chronik.json", false).then(function (c) {
    if (c && c.zitate) { chronik = c; chronikSchritt(); }
  }).catch(function () { });

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
        # The hub: this node as its peers see it (2026-09-03).
        "dieser_node": t("this node"),
        "keine_adresse": t("no reachable address announced"),
        "protokoll": t("Protocol"),
        "laeuft": t("Node up for"),
        "relay": t("Minimum relay fee"),
    }
    return (SCRIPT
            .replace("__TEXTE__", json.dumps(strings, ensure_ascii=False))
            # (?i) is Python's spelling; JavaScript takes the flag apart.
            .replace("__HERVOR__", json.dumps(LOG_HIGHLIGHT))
            .replace("__MUSTER__", json.dumps(
                [(k, p.replace("(?i)", ""), "i" if p.startswith("(?i)") else "")
                 for k, p in LOG_KINDS]))
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
# Empty since 2026-09-05: the Electrum card became a column of the network
# card. The zone and the mechanism stay for the day something needs the
# full width again.
CARDS_FULL = ()

# Order inside the grid, spelled out. It used to fall out of the order in
# which the collect_* functions happen to be called — which is an accident,
# not a design decision. Anything not listed here is appended at the end.
CARD_ORDER = (
    "System",
    "Network & Electrum",
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
    rows, copy_fields, feet = [], [], []
    columns = []          # [(heading, [row markup])]
    for entry in fields:
        label, value = entry[0], entry[1]
        cls = entry[2] if len(entry) > 2 else ""
        if cls == "spalte":
            columns.append((label, []))
            rows = columns[-1][1]
            continue
        if cls == "fuss":
            # A graph under both inner columns, full card width. Same rule
            # as "grafik": the value is SVG this program built, unescaped.
            feet.append(f"<span class=grafiklabel>{html_escape(label)}</span>"
                        f"<div class=grafikfuss>{value}</div>")
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
        parts.extend(feet)
    elif rows:
        # A graph that ends the card leaves the list and becomes the card's
        # foot: as a flex child it can take the height the grid gives the
        # card, where a grid row cannot — the wide cards showed 40 px of
        # nothing under their graphs (2026-09-03).
        # Not in cards with copy fields: there the list and the copy block
        # sit side by side, and a third child breaks the row — the Electrum
        # card's bar stretched across, the addresses fell below (03.09.2026).
        foot = ""
        if rows[-1].startswith("<dd class=grafik>") and not copy_fields:
            foot = "<div class=grafikfuss>" + rows.pop()[len("<dd class=grafik>"):-len("</dd>")] + "</div>"
            if rows and rows[-1].startswith("<dt class=grafiklabel>"):
                label = rows.pop()[len("<dt class=grafiklabel>"):-len("</dt>")]
                foot = f"<span class=grafiklabel>{label}</span>" + foot
        if rows:
            parts.append("<dl>" + "".join(rows) + "</dl>")
        parts.append(foot)
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
    height = kz.get("kopfzeilen")
    if height and level != "sync":
        # Once the chain is up to date the comparison says nothing any more
        # (965.371 of 965.371) — the tile carries the halving instead
        # (Jakob, 2026-09-03). Whole days, no clock: at ten minutes a block
        # the date drifts by weeks, and a countdown to the minute would
        # only pretend otherwise.
        _, _, blocks_left, when = halving_facts(height)
        days = max(0, round(blocks_left * 600 / 86400))
        pattern = "%d.%m.%Y" if LANGUAGE == "de" else "%Y-%m-%d"
        tiles.append((
            f'{format_number(days)}<span class=kvon>{html_escape(t("days"))}</span>',
            t("halving · about {date}", date=when.strftime(pattern)), "",
            t("{n} blocks to go", n=format_number(blocks_left)), True,
        ))
    elif height:
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
    elif fees.get(1):
        # The fee to enter with a transaction — the one number you want
        # without looking for it. It took the mempool tile's place on
        # 2026-09-01; the count is still in the 'Mempool & fees' card.
        # Large: Core's economical estimate for the next block — enough to
        # get in without overpaying (Jakob, 2026-09-03). Small: the
        # conservative estimate, for when it must not fail.
        # Hidden when both estimates round to the same figure — "3,0" under
        # "3,0" carries nothing (2026-09-03).
        extra = ""
        safe = fees.get("sicher")
        if safe and f"{safe:.1f}" != f"{fees[1]:.1f}":
            extra = t("safe: {fee}", fee=decimal_sep(f"{safe:.1f}"))
        tiles.append((
            decimal_sep(f"{fees[1]:.1f}") + "<span class=kvon>sat/vB</span>",
            t("fee for the next block"), "gut", extra, True))
    elif median is not None:
        # No estimate yet (Core needs a few blocks after a restart) — the
        # last block's median bridges the gap.
        tiles.append((
            decimal_sep(f"{median:.1f}") + "<span class=kvon>sat/vB</span>",
            t("median fee in the last block"), "gut", "", True))
    elif kz.get("mempool") is not None:
        tiles.append((format_number(kz["mempool"]), t("in the mempool"),
                        "", "", False))

    verbindungen = kz.get("verbindungen")
    if verbindungen is not None:
        tiles.append((str(verbindungen), t("Connections"),
                        "warn" if verbindungen < 8 else "gut", "", False))

    if TEMP_NOW[0] is not None:
        temp = TEMP_NOW[0]
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
    # The hashrate curve runs behind the whole bar like a ticker — only
    # once the chain is up to date, and only if the node allows the call.
    hashrate = hashrate_summary() if level != "sync" else None
    if hashrate:
        parts.append(build_hashrate_chart())

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
            t("Block · {age}", age=format_age(age)) if age is not None
            else t("Block height"))
    ticker = ""
    if hashrate:
        rate, change = hashrate
        sign = "+" if change >= 0 else "−"
        ticker = (f'<div class=zhash><b>{html_escape(format_hashrate(rate))}</b> '
                  f'<span class="zdelta {"gut" if change >= 0 else "warn"}">'
                  f'{sign}{decimal_sep(f"{abs(change) * 100:.1f}")}&nbsp;%</span> '
                  f'<span class=zhashlabel>{html_escape(t("vs. a year ago · hashrate, curve since 2009"))}</span></div>')
    parts.append(
        f'<div class=zrechts><div class=zzahl>{right_number}</div>'
        f'<div class=zlabel>{right_label}</div>{ticker}</div>'
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
                       in_sync=True, sentences=("", "")):
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
        f'<span><i class="netzfarbe richtung voll"></i>{html_escape(t("outbound|richtung"))}</span>'
        f'<span><i class="netzfarbe richtung"></i>{html_escape(t("eingehend|richtung"))}</span>'
    )

    return (
        '<section class="karte netz">'
        f"<div class=kopfzeile><h2>{html_escape(t('Connected nodes'))}</h2>"
        f"<div class=netzzahlen>{values}</div></div>"
        f"<div id=netzkarte>{svg}</div>"
        f"<div class=peerlegende>{legend}"
        "</div>"
        '<div class=peerdetail id=peerdetail>'
        f"<p class=blockweg>{html_escape(sentences[0])}</p>"
        f"<p class=blockweg>{html_escape(sentences[1])}</p>"
        f"<p class=leer>{html_escape(t('Point at a line for identifier, dienste and connection time.'))}</p>"
        "</div></section>"
    )


# Which log lines get a colour. Order matters: the first match wins, so
# trouble comes before everything else. Announcements and chain-check
# probes had classes of their own for one evening (2026-09-02) — dropped:
# one coloured line per block, and the probes are grey like the rest. The patterns are compiled here for
# the server-side page and handed to dash.js as strings, so both routes
# colour the same lines (2026-09-02). Colour only — the text of a line is
# always set as text, never as markup.
LOG_KINDS = (
    ("fehler", r"(?i)\berror\b|misbehaving|disconnecting|Potential stale tip|corrupt"),
    ("warn", r"(?i)\bwarning\b"),
    ("spitze", r"UpdateTip:"),
)
LOG_KINDS_RE = [(kind, re.compile(pattern)) for kind, pattern in LOG_KINDS]

# Inside a coloured line, one piece may stand out more: the height in an
# UpdateTip line, at full block orange while the rest of the line keeps the
# tint (2026-09-02). Still text — the match is escaped like everything else
# and only wrapped in its own span.
LOG_HIGHLIGHT = {"spitze": r"height=\d+"}
LOG_HIGHLIGHT_RE = {k: re.compile(p) for k, p in LOG_HIGHLIGHT.items()}


def log_kind(row):
    for kind, pattern in LOG_KINDS_RE:
        if pattern.search(row):
            return kind
    return ""


def log_markup(text):
    """The log as lines in spans, each escaped, classed by log_kind."""
    parts = []
    for row in text.split("\n"):
        kind = log_kind(row)
        cls = f' class="lz {kind}"' if kind else ' class=lz'
        inner = html_escape(row)
        pattern = LOG_HIGHLIGHT_RE.get(kind)
        if pattern:
            match = pattern.search(row)
            if match:
                inner = (html_escape(row[:match.start()])
                         + f"<b class=hervor>{html_escape(match.group(0))}</b>"
                         + html_escape(row[match.end():]))
        parts.append(f"<span{cls}>{inner}</span>")
    return "\n".join(parts)


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

    sentences = (block_path_text(peers or [], kz), ranking_text(peers or []))
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
                              "getpeerinfo" in DENIED, kz, in_sync, sentences),
        # Built once, used in the page and in status.json: the two used to
        # be computed twice, and across a second boundary "vor 43 s" met
        # "vor 44 s" (flaky test, 2026-09-03).
        "blockweg": sentences[0],
        "rangliste": sentences[1],
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
    log_step = html_escape(str(cfg.get("LOG_INTERVAL", "3")))

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
        # Without JavaScript the page reloads through this. Inside
        # <noscript> since 2026-09-05: dash.js used to remove the element at
        # startup, but Chrome schedules the reload the moment it parses the
        # meta, and removing it later cancels nothing. The page therefore
        # reloaded every cycle under the script — the chronicle never got
        # to type its second entry, and every "the animation is broken"
        # report of that evening was this. Seen only by driving the real
        # page in a browser: performance navigation type "reload" every
        # 21 s. A <noscript> in <head> may hold meta and is honoured only
        # when scripts are off.
        f'<noscript><meta http-equiv=refresh content="{interval}"></noscript>',
        f'<link rel=icon href="{favicon}">',
        f'<link rel=stylesheet href="stil.css?v={STYLE_V}">',
        # <title>, not <titel>: the rename of 2026-08-23 hit this tag too.
        # The browser rendered the unknown element as visible text above the
        # header for nine days — "Alles läuft · btcnode" in the top left
        # corner, taken for a design choice (2026-09-01).
        f"<title>{html_escape(title)}</title>",
        "</head><body><div class=huelle>",
        # The brand mark is the same logo the network map's hub carries
        # (Jakob, 2026-09-03), not a green dot.
        # Two halves like the rows below: brand at the left edge, versions
        # and clock centred in the rest of the left half, the chronicle on
        # the right, flush with the right column of the page (Jakob,
        # 2026-09-05, after the screenshot). Before that the chronicle sat
        # between brand and versions in one row.
        '<header><div class=kopfgruppe>'
        f'<h1><img class=marke src="bitcoin.png?v={BITCOIN_V}" alt="">{hostname} '
        f"<b>· Bitcoin Fullnode</b></h1>"
        f'<div id=z-kopf>{zones["kopf"]}</div>'
        f'<div class=kopfrechts><span class=puls></span>'
        f'<span id=stempel>{now.strftime(TIME_FORMAT[LANGUAGE])}</span></div>'
        "</div>"
        + build_chronicle(int(cfg.get("INTERVAL", 21))) +
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
        f"{log_markup(log_text(logs))}</code></pre></div></section>"
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
        "eigen": (summary or {}).get("eigen"),
        # The sentence for the detail box, ready made: dash.js sets it via
        # textContent and does not rebuild it from the peer list.
        "blockweg": zones["blockweg"],
        "rangliste": zones["rangliste"],
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
    write_file_atomic(cfg["OUT_DIR"], "chronik.json", chronicle_text())
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
        # Into the network card as its third column. A new tuple, never
        # extend() — in the tolerance window 'groups' comes from LAST_STATE,
        # and an in-place change would grow that card by one Electrum
        # column per cycle.
        merged = False
        for i, group in enumerate(groups):
            if group[0] == "Network & Electrum":
                groups[i] = (group[0], list(group[1]) + electrum[1], electrum[2])
                merged = True
                break
        if not merged:
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
        log_step = max(1, min(interval, int(cfg.get("LOG_INTERVAL", 3))))
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
