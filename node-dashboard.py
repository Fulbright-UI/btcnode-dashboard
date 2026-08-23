#!/usr/bin/env python3
"""
node-dashboard — erzeugt eine statische HTML-Seite mit dem Zustand des Nodes.

Aufbau bewusst simpel: Dieses Skript fragt den Node ab und schreibt eine
fertige HTML-Datei. Der Webserver liefert nur diese Datei aus. Er kennt den
Node nicht, nimmt keine Eingaben entgegen und fuehrt nichts aus. Wer den
Webserver uebernimmt, bekommt eine HTML-Datei und sonst nichts.

Es werden ausschliesslich Module der Python-Standardbibliothek benutzt —
keine Fremdpakete, kein pip, kein npm.

Aufruf:  node-dashboard [--config /etc/node-dashboard.conf] [--once]
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

VERSION = "3.0"

# ---------------------------------------------------------- Toleranzfenster --
# Waehrend der Erstsynchronisation blockiert bitcoind seinen RPC-Thread, wenn
# es den dbcache auf die SSD schreibt. Auf einem Pi dauert das regelmaessig
# laenger als das Zeitlimit einer Abfrage. Frueher hat das Dashboard daraufhin
# sofort "Node nicht erreichbar" angezeigt und alle Werte auf null gesetzt,
# obwohl der Node einwandfrei weiterlief.
#
# Deshalb: Der letzte erfolgreiche Stand wird gehalten. Erst nach mehreren
# Fehlschlaegen in Folge gilt der Node wirklich als weg. Bis dahin stehen die
# alten Zahlen da, mit einem dezenten Hinweis auf ihr Alter.
LETZTER_STAND = {}          # gefuellt vom letzten geglueckten Durchlauf
FEHLER_IN_FOLGE = 0

# Methoden, die der Node uns verboten hat (HTTP 403 wegen rpcwhitelist), mit
# dem Zeitpunkt der Ablehnung. Ohne dieses Gedaechtnis fragt das Dashboard
# alle 30 Sekunden erneut nach, und bitcoind schreibt jedes Mal
# "RPC User dashboard not allowed to call method …" ins Protokoll — das
# ausgerechnet in die Anzeige, die daneben steht.
VERBOTEN = {}
VERBOTEN_ERNEUT = 1800      # nach einer halben Stunde einmal wieder probieren

# Messreihe fuer Tempo und Restzeit. Liegt nur im Arbeitsspeicher des
# laufenden Dienstes — nach einem Neustart beginnt die Schaetzung von vorn.
VERLAUF = []
VERLAUF_MAX = 120          # bei 30 s Takt: etwa eine Stunde Rueckschau
VERLAUF_MIN_ABSTAND = 300  # erst ab 5 Minuten Abstand wird geschaetzt

# Zweite, groeber getaktete Reihe fuer die Verlaufskurve: ein Punkt alle
# fuenf Minuten, 144 Punkte — also die letzten zwoelf Stunden.
VERLAUF_LANG = []
VERLAUF_LANG_TAKT = 300
VERLAUF_LANG_MAX = 144

# Temperaturverlauf: ein Messpunkt je Minute, 60 Punkte — die letzte Stunde.
TEMP_VERLAUF = []
TEMP_TAKT = 30
TEMP_MAX = 120
# Feste Skala statt automatischer Anpassung: Eine ruhige Linie bei 50 Grad
# soll auch ruhig aussehen. Bei mitwachsender Skala wuerde jedes Rauschen
# wie ein Ausschlag wirken.
TEMP_UNTEN, TEMP_OBEN = 30.0, 90.0

HALVING_ABSTAND = 210_000   # alle 210.000 Bloecke halbiert sich die Belohnung
RETARGET_ABSTAND = 2016     # alle 2016 Bloecke wird die Schwierigkeit angepasst

# Kennzahlen je Block fuer die 24-Stunden-Grafiken. Bitcoin Core rechnet die
# Summen selbst aus (getblockstats) — das erspart uns, 144 Bloecke im Umfang
# von hunderten Megabyte zu lesen.
BLOCKDATEN = []             # (hoehe, zeit, ausgang_sat, gebuehr_sat_vb, anzahl)
BLOCK_MAX = 144             # rund 24 Stunden

# Schwierigkeit der letzten Anpassungen. Wird einmal aus alten Blockkoepfen
# gelesen und danach nur ergaenzt — sie aendert sich nur alle zwei Wochen.
SCHWIERIGKEIT = []          # (hoehe, wert)
SCHWIERIGKEIT_ANZAHL = 16   # etwa ein halbes Jahr


def merke_langzeit(fortschritt_anteil):
    """Haelt einen groben Verlauf fuer die Kurve fest."""
    jetzt = time.time()
    if not VERLAUF_LANG or jetzt - VERLAUF_LANG[-1][0] >= VERLAUF_LANG_TAKT:
        VERLAUF_LANG.append((jetzt, fortschritt_anteil))
        del VERLAUF_LANG[:-VERLAUF_LANG_MAX]


def merke_temperatur(celsius):
    """Haelt den Temperaturverlauf fest, hoechstens ein Punkt je Minute."""
    jetzt = time.time()
    if not TEMP_VERLAUF or jetzt - TEMP_VERLAUF[-1][0] >= TEMP_TAKT:
        TEMP_VERLAUF.append((jetzt, celsius))
        del TEMP_VERLAUF[:-TEMP_MAX]


def temperaturfarbe(celsius):
    """Gruen bis 60, Gelb bis 75, darueber Rot."""
    if celsius is None:
        return "var(--leise)"
    if celsius >= 75:
        return "var(--fehler)"
    if celsius >= 60:
        return "var(--warn)"
    return "var(--akzent)"


def baue_temperaturkurve(breite=260, hoehe=34):
    """Kleine Kurve der letzten Stunde, eingefaerbt nach aktuellem Wert."""
    if len(TEMP_VERLAUF) < 2:
        return None
    werte = [c for _, c in TEMP_VERLAUF]
    farbe = temperaturfarbe(werte[-1])
    rand = 2
    nutz_b, nutz_h = breite - 2 * rand, hoehe - 2 * rand
    spanne = TEMP_OBEN - TEMP_UNTEN

    punkte = []
    for i, c in enumerate(werte):
        x = rand + (i / max(1, len(werte) - 1)) * nutz_b
        anteil = min(1.0, max(0.0, (c - TEMP_UNTEN) / spanne))
        y = rand + (1 - anteil) * nutz_h
        punkte.append(f"{x:.1f},{y:.1f}")
    linie = " ".join(punkte)
    flaeche = f"{rand},{hoehe - rand} {linie} {breite - rand},{hoehe - rand}"

    return (f'<svg class=minikurve viewBox="0 0 {breite} {hoehe}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="Temperaturverlauf der letzten Stunde">'
            f'<polygon points="{flaeche}" fill="{farbe}" opacity=".13"/>'
            f'<polyline points="{linie}" fill="none" stroke="{farbe}" '
            f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
            f"</svg>")


def baue_balken(anteil, stufe="", hoehe=6):
    """Waagerechter Fuellbalken, Anteil zwischen 0 und 1.

    Bewusst SVG statt eines <div style="width:…">: Die Content-Security-Policy
    lautet 'style-src self' ohne 'unsafe-inline', und die gilt auch fuer
    style-Attribute im Markup. Eine Breite als Inline-Stil wuerde vom Browser
    verworfen — der Balken stuende dann immer auf voll. Genau das ist am
    23.08.2026 passiert. Breiten- und Farbangaben in SVG sind
    Praesentationsattribute und davon nicht betroffen.
    """
    breite = min(100.0, max(0.0, anteil * 100))
    klasse = f"balkenfuellung {stufe}".strip()

    # Die runden Ecken macht CSS am umschliessenden Element, nicht 'rx' im
    # Rechteck: Das SVG wird per preserveAspectRatio="none" um ein Vielfaches
    # in die Breite gezogen, und ein 'rx' wuerde mitgezogen. Bei kleinen
    # Anteilen ist der Radius dann breiter als die Fuellung selbst, und aus
    # dem Balken wird ein Klecks. Genau das war am 23.08.2026 zu sehen.
    return (f'<span class="balken hoch{hoehe}">'
            f'<svg viewBox="0 0 100 {hoehe}" preserveAspectRatio="none" '
            f'role="img" aria-label="{breite:.0f} Prozent">'
            f'<rect width="{breite:.2f}" height="{hoehe}" class="{klasse}"/>'
            f"</svg></span>")


def baue_saeulen(werte, farbe="var(--akzent)", beschriftung="", breite=260, hoehe=38):
    """Kleines Saeulendiagramm. Die Skala beginnt immer bei null."""
    werte = [w for w in werte if w is not None]
    if len(werte) < 2:
        return None
    hoechster = max(werte) or 1
    anzahl = len(werte)
    luecke = 260 / anzahl * 0.18
    saeule = (breite - (anzahl - 1) * luecke) / anzahl

    teile = []
    for i, w in enumerate(werte):
        h = max(0.8, (w / hoechster) * hoehe)
        x = i * (saeule + luecke)
        teile.append(f'<rect x="{x:.2f}" y="{hoehe - h:.2f}" '
                     f'width="{saeule:.2f}" height="{h:.2f}" fill="{farbe}"/>')
    return (f'<svg class=minikurve viewBox="0 0 {breite} {hoehe}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(beschriftung)}">{"".join(teile)}</svg>')


def baue_linie(werte, farbe="var(--akzent)", beschriftung="",
               breite=260, hoehe=38, ab_null=True):
    """Kleine Linienkurve mit gefuellter Flaeche darunter."""
    werte = [w for w in werte if w is not None]
    if len(werte) < 3:
        return None
    oben = max(werte)
    unten = 0.0 if ab_null else min(werte)
    spanne = (oben - unten) or 1
    rand = 1.5
    nutz_b, nutz_h = breite - 2 * rand, hoehe - 2 * rand

    punkte = []
    for i, w in enumerate(werte):
        x = rand + (i / max(1, len(werte) - 1)) * nutz_b
        y = rand + (1 - (w - unten) / spanne) * nutz_h
        punkte.append(f"{x:.1f},{y:.1f}")
    linie = " ".join(punkte)
    flaeche = f"{rand},{hoehe - rand} {linie} {breite - rand},{hoehe - rand}"
    return (f'<svg class=minikurve viewBox="0 0 {breite} {hoehe}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(beschriftung)}">'
            f'<polygon points="{flaeche}" fill="{farbe}" opacity=".13"/>'
            f'<polyline points="{linie}" fill="none" stroke="{farbe}" '
            f'stroke-width="1.5" stroke-linejoin="round"/></svg>')


def baue_geruest(beschriftung="", breite=260, hoehe=38, saeulen=False):
    """Ein sichtbar leeres Geruest an der Stelle einer spaeteren Grafik.

    Ausdruecklich KEINE erfundenen Beispieldaten: Auf einer Anzeige, die den
    Zustand eines Nodes meldet, ist eine erfundene Kurve gefaehrlich — auf dem
    naechsten Bildschirmfoto weiss niemand mehr, was gemessen und was gemalt
    war. Das Geruest zeigt die Form, nicht Werte.
    """
    rand = 1.5
    if saeulen:
        anzahl = 24
        luecke = breite / anzahl * 0.22
        stab = (breite - (anzahl - 1) * luecke) / anzahl
        inhalt = "".join(
            f'<rect x="{i * (stab + luecke):.2f}" y="{hoehe * 0.55:.1f}" '
            f'width="{stab:.2f}" height="{hoehe * 0.45:.1f}" class="geruestteil"/>'
            for i in range(anzahl)
        )
    else:
        mitte = hoehe / 2
        inhalt = (f'<line x1="{rand}" y1="{mitte:.1f}" '
                  f'x2="{breite - rand}" y2="{mitte:.1f}" class="geruestlinie"/>')

    return (f'<svg class="minikurve geruest" viewBox="0 0 {breite} {hoehe}" '
            f'preserveAspectRatio="none" role="img" '
            f'aria-label="{html_escape(beschriftung or "noch keine Daten")}">'
            f"{inhalt}</svg>")


def platzhalterkarte(bezeichnungen, grafiktitel, hinweis, saeulen=False):
    """Eine vollstaendige Karte mit Geruest statt Werten."""
    felder = [(b, "—", "leer") for b in bezeichnungen]
    felder.append((grafiktitel, baue_geruest(grafiktitel, saeulen=saeulen),
                   "grafik"))
    return felder, hinweis


def formatiere_btc(satoshi):
    btc = satoshi / 100_000_000
    if btc >= 1000:
        return formatiere_zahl(round(btc)) + " BTC"
    return f"{btc:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".") + " BTC"


def hole_blockdaten(cfg, spitze):
    """Holt fehlende Blockkennzahlen nach und haelt die letzten 144 vor.

    Beim ersten Lauf werden 144 Bloecke nachgeladen, danach nur noch der
    jeweils neue. Die Kennzahlen rechnet Bitcoin Core selbst aus, wir fragen
    ausdruecklich nur die vier Felder ab, die wir brauchen.
    """
    if not spitze:
        return
    vorhanden = {h for h, *_ in BLOCKDATEN}
    beginn = max(1, spitze - BLOCK_MAX + 1)
    fehlend = [h for h in range(beginn, spitze + 1) if h not in vorhanden]
    if not fehlend:
        return

    # Beim allerersten Lauf kann das ein paar Sekunden dauern; danach ist es
    # hoechstens ein Block je Durchlauf.
    for hoehe in fehlend[-BLOCK_MAX:]:
        try:
            st = rpc(cfg, "getblockstats",
                     [hoehe, ["height", "time", "total_out", "txs",
                              "feerate_percentiles"]])
        except RpcFehler:
            return          # nicht freigeschaltet oder Block fehlt — still aufhoeren
        perzentile = st.get("feerate_percentiles") or [0, 0, 0, 0, 0]
        BLOCKDATEN.append((
            st.get("height", hoehe),
            st.get("time", 0),
            st.get("total_out", 0),
            perzentile[2],              # mittlere Gebuehr in sat/vB
            st.get("txs", 0),
        ))

    BLOCKDATEN.sort(key=lambda e: e[0])
    del BLOCKDATEN[:-BLOCK_MAX]


def hole_schwierigkeit(cfg, spitze):
    """Liest die Schwierigkeit der letzten Anpassungen aus alten Blockkoepfen.

    Nur einmal noetig: Die Werte aendern sich nicht mehr, und eine neue
    Anpassung kommt erst nach rund zwei Wochen dazu.
    """
    if not spitze:
        return
    letzte = (spitze // RETARGET_ABSTAND) * RETARGET_ABSTAND
    vorhanden = {h for h, _ in SCHWIERIGKEIT}
    gesucht = [letzte - i * RETARGET_ABSTAND for i in range(SCHWIERIGKEIT_ANZAHL)]
    fehlend = [h for h in gesucht if h > 0 and h not in vorhanden]
    if not fehlend:
        return

    for hoehe in sorted(fehlend):
        try:
            block_id = rpc(cfg, "getblockhash", [hoehe])
            kopf = rpc(cfg, "getblockheader", [block_id])
        except RpcFehler:
            return
        SCHWIERIGKEIT.append((hoehe, float(kopf.get("difficulty", 0))))

    SCHWIERIGKEIT.sort(key=lambda e: e[0])
    del SCHWIERIGKEIT[:-SCHWIERIGKEIT_ANZAHL]


def schaetze_restzeit(fortschritt_anteil):
    """Schaetzt die verbleibende Zeit aus dem Zuwachs des Pruefungsfortschritts.

    Bewusst nicht aus der Blockzahl: Die fruehen Bloecke sind fast leer und
    fliegen durch, spaetere sind voll. 'verificationprogress' gewichtet das
    bereits nach Arbeitsaufwand und liefert deshalb brauchbarere Werte.
    """
    jetzt = time.time()
    VERLAUF.append((jetzt, fortschritt_anteil))
    del VERLAUF[:-VERLAUF_MAX]

    # aeltesten Messpunkt suchen, der weit genug zurueckliegt
    basis = None
    for zeit, wert in VERLAUF:
        if jetzt - zeit >= VERLAUF_MIN_ABSTAND:
            basis = (zeit, wert)
        else:
            break
    if basis is None:
        return None, None

    d_zeit = jetzt - basis[0]
    d_fortschritt = fortschritt_anteil - basis[1]
    if d_zeit <= 0 or d_fortschritt <= 0:
        return None, None

    pro_stunde = d_fortschritt / d_zeit * 3600 * 100      # Prozentpunkte je Stunde
    rest = (1.0 - fortschritt_anteil) / (d_fortschritt / d_zeit)
    return pro_stunde, rest


# ============================================================== Konfiguration
def lies_konfiguration(pfad):
    """Liest eine schlichte KEY=VALUE-Datei ein. Zeilen mit # sind Kommentare."""
    werte = {
        "RPC_HOST": "127.0.0.1",
        "RPC_PORT": "8332",
        "RPC_USER": "dashboard",
        "RPC_PASSWORD": "",
        "OUT_DIR": "/var/www/node",
        "DATA_DIR": "/mnt/bitcoin/bitcoin",
        "ELECTRS_PORT": "50001",
        "INTERVALL": "30",
        "LOG_DIENSTE": "bitcoind",
        # Das Protokoll fuellt die rechte Spalte bis nach unten und rollt
        # innen. Mehr Zeilen kosten nichts ausser Ruecklauf — journalctl
        # braucht fuer 150 nicht laenger als fuer 40.
        "LOG_ZEILEN": "150",
        "LOG_INTERVALL": "5",
        # Zeitlimit je RPC-Abfrage. 45 s statt 15: bitcoind haelt den
        # RPC-Thread an, waehrend es den dbcache schreibt.
        "RPC_TIMEOUT": "45",
        # So viele Fehlschlaege in Folge, bevor der Node als weg gilt.
        # 3 × 30 s = anderthalb Minuten Stille, erst dann die rote Karte.
        "TOLERANZ": "3",
        # Hoechstzahl der Punkte in der Netzkarte. Mehr wird unleserlich.
        "PEERS_MAX": "64",
    }
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.strip()
                if not zeile or zeile.startswith("#") or "=" not in zeile:
                    continue
                schluessel, _, wert = zeile.partition("=")
                werte[schluessel.strip()] = wert.strip().strip("'\"")
    except FileNotFoundError:
        print(f"Konfiguration nicht gefunden: {pfad}", file=sys.stderr)
        sys.exit(1)
    return werte


# ======================================================================== RPC
class RpcFehler(Exception):
    pass


def rpc(cfg, methode, parameter=None):
    """Ruft eine JSON-RPC-Methode bei Bitcoin Core auf."""
    # Was der Node schon einmal abgelehnt hat, wird eine Weile nicht wieder
    # gefragt. Die Whitelist kann sich aendern (06-tor.sh ergaenzt sie),
    # deshalb nicht fuer immer.
    abgelehnt = VERBOTEN.get(methode)
    if abgelehnt is not None:
        if time.time() - abgelehnt < VERBOTEN_ERNEUT:
            raise RpcFehler(f"HTTP 403 bei {methode} (nicht freigeschaltet)")
        del VERBOTEN[methode]

    adresse = f"http://{cfg['RPC_HOST']}:{cfg['RPC_PORT']}/"
    rumpf = json.dumps(
        {"jsonrpc": "1.0", "id": "dashboard", "method": methode, "params": parameter or []}
    ).encode()

    anmeldung = base64.b64encode(
        f"{cfg['RPC_USER']}:{cfg['RPC_PASSWORD']}".encode()
    ).decode()

    anfrage = urllib.request.Request(
        adresse,
        data=rumpf,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {anmeldung}",
        },
    )
    try:
        grenze = max(5, int(cfg.get("RPC_TIMEOUT", 45)))
    except (TypeError, ValueError):
        grenze = 45

    try:
        with urllib.request.urlopen(anfrage, timeout=grenze) as antwort:
            ergebnis = json.loads(antwort.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            VERBOTEN[methode] = time.time()
            raise RpcFehler(f"HTTP 403 bei {methode} (nicht freigeschaltet)") from e
        raise RpcFehler(f"HTTP {e.code} bei {methode}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RpcFehler(f"Node nicht erreichbar: {e}") from e
    except json.JSONDecodeError as e:
        raise RpcFehler(f"Unlesbare Antwort bei {methode}") from e

    if ergebnis.get("error"):
        raise RpcFehler(f"{methode}: {ergebnis['error']}")
    return ergebnis["result"]


# ============================================================== Hilfsfunktionen
def formatiere_bytes(anzahl):
    einheit = ["B", "KB", "MB", "GB", "TB"]
    wert = float(anzahl)
    i = 0
    while wert >= 1000 and i < len(einheit) - 1:
        wert /= 1000
        i += 1
    return f"{wert:.1f} {einheit[i]}".replace(".", ",")


def formatiere_dauer(sekunden):
    sekunden = int(sekunden)
    tage, rest = divmod(sekunden, 86400)
    stunden, rest = divmod(rest, 3600)
    minuten = rest // 60
    if tage:
        return f"{tage} T {stunden} Std"
    if stunden:
        return f"{stunden} Std {minuten} Min"
    return f"{minuten} Min"


def formatiere_zahl(n):
    """1234567 -> 1.234.567 (deutsche Schreibweise)"""
    return f"{int(n):,}".replace(",", ".")


def formatiere_gross(zahl):
    """126000000000000 -> 126,0 T — fuer die Netzwerk-Schwierigkeit."""
    for grenze, kuerzel in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k")):
        if zahl >= grenze:
            return f"{zahl / grenze:.1f} {kuerzel}".replace(".", ",")
    return f"{zahl:.0f}"


def formatiere_alter(sekunden):
    """Wie lange ist das her? Kurz und ohne Nachkommastellen."""
    sekunden = int(sekunden)
    if sekunden < 90:
        return f"vor {sekunden} s"
    if sekunden < 5400:
        return f"vor {sekunden // 60} Min"
    if sekunden < 172800:
        return f"vor {sekunden // 3600} Std"
    return f"vor {sekunden // 86400} Tagen"


def halving_infos(hoehe):
    """Belohnung, Bloecke bis zur naechsten Halbierung, geschaetztes Datum.

    Gerechnet wird mit den Kopfzeilen, nicht mit den validierten Bloecken:
    Die Kopfzeilen stehen schon nach Minuten auf der echten Kettenspitze,
    waehrend die Bloecke waehrend der Erstsynchronisation weit zurueckliegen.
    """
    epoche = hoehe // HALVING_ABSTAND
    belohnung = 50.0 / (2 ** epoche)
    naechstes = (epoche + 1) * HALVING_ABSTAND
    rest = naechstes - hoehe
    # Im Mittel ein Block alle zehn Minuten
    datum = datetime.now(timezone.utc).astimezone().timestamp() + rest * 600
    return belohnung, naechstes, rest, datetime.fromtimestamp(datum)


def baue_kurve(breite=300, hoehe=54):
    """Zeichnet den Synchronisationsverlauf als kleine SVG-Kurve.

    Bewusst von Hand gerechnet statt mit einer Diagramm-Bibliothek: Es sind
    ein paar Koordinaten, und die Seite bleibt ohne JavaScript.
    """
    if len(VERLAUF_LANG) < 3:
        return None

    zeiten = [z for z, _ in VERLAUF_LANG]
    werte = [w for _, w in VERLAUF_LANG]
    t0, t1 = zeiten[0], zeiten[-1]
    w0, w1 = min(werte), max(werte)
    if t1 <= t0 or w1 <= w0:
        return None

    rand = 3
    nutz_b = breite - 2 * rand
    nutz_h = hoehe - 2 * rand

    punkte = []
    for z, w in VERLAUF_LANG:
        x = rand + (z - t0) / (t1 - t0) * nutz_b
        y = rand + (1 - (w - w0) / (w1 - w0)) * nutz_h
        punkte.append(f"{x:.1f},{y:.1f}")

    linie = " ".join(punkte)
    flaeche = f"{rand},{hoehe - rand} {linie} {breite - rand},{hoehe - rand}"
    spanne = formatiere_dauer(t1 - t0)
    zuwachs = (w1 - w0) * 100

    return (
        f'<svg viewBox="0 0 {breite} {hoehe}" preserveAspectRatio="none" '
        f'role="img" aria-label="Verlauf der letzten {html_escape(spanne)}">'
        f'<polygon points="{flaeche}" fill="var(--balken)" opacity=".14"/>'
        f'<polyline points="{linie}" fill="none" stroke="var(--balken)" '
        f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
        f"</svg>",
        f"{spanne} · +{zuwachs:.2f} %-Punkte".replace(".", ","),
    )


def lies_datei(pfad, standard=None):
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return standard


def dienst_laeuft(name):
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", name], timeout=5, check=False
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def port_offen(host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


# ==================================================================== Sammeln
def sammle_system(cfg):
    """Zustand des Pi selbst — unabhaengig davon, ob der Node laeuft."""
    felder = []

    roh = lies_datei("/sys/class/thermal/thermal_zone0/temp")
    if roh and roh.isdigit():
        temp = int(roh) / 1000
        merke_temperatur(temp)
        felder.append(("Temperatur", f"{temp:.1f} °C".replace(".", ","),
                       "warn" if temp >= 75 else ""))
        # Nach einem Neustart des Dienstes dauert es eine Minute, bis zwei
        # Messpunkte da sind. Solange steht das Geruest an der Stelle, damit
        # die Karte nicht ihre Hoehe aendert.
        felder.append(("letzte Stunde",
                       baue_temperaturkurve()
                       or baue_geruest("Temperaturverlauf, wird noch gemessen"),
                       "grafik"))

    try:
        last1, _, _ = os.getloadavg()
        kerne = os.cpu_count() or 1
        felder.append(
            ("Auslastung", f"{last1:.2f} bei {kerne} Kernen".replace(".", ","),
             "warn" if last1 > kerne * 1.5 else "")
        )
    except OSError:
        pass

    meminfo = lies_datei("/proc/meminfo", "") or ""
    gesamt = verfuegbar = 0
    for zeile in meminfo.splitlines():
        if zeile.startswith("MemTotal:"):
            gesamt = int(zeile.split()[1]) * 1024
        elif zeile.startswith("MemAvailable:"):
            verfuegbar = int(zeile.split()[1]) * 1024
    if gesamt:
        benutzt = gesamt - verfuegbar
        felder.append(
            ("Arbeitsspeicher",
             f"{formatiere_bytes(benutzt)} von {formatiere_bytes(gesamt)}",
             "warn" if benutzt / gesamt > 0.92 else "")
        )

    try:
        s = os.statvfs(cfg["DATA_DIR"])
        frei = s.f_bavail * s.f_frsize
        gesamt_platz = s.f_blocks * s.f_frsize
        anteil_frei = frei / gesamt_platz if gesamt_platz else 0
        belegt = 1 - anteil_frei
        if belegt >= 0.95:
            balkenstufe, stufe = "fehler", "warn"
        elif belegt >= 0.88:
            balkenstufe, stufe = "warn", "warn"
        else:
            balkenstufe, stufe = "", ""
        felder.append(
            ("Speicherplatz",
             f"{belegt * 100:.0f} % belegt, {formatiere_bytes(frei)} frei",
             stufe)
        )
        felder.append(("", baue_balken(belegt, balkenstufe), "grafik"))
    except OSError:
        pass

    betriebszeit = lies_datei("/proc/uptime")
    if betriebszeit:
        felder.append(("Pi läuft seit", formatiere_dauer(float(betriebszeit.split()[0])), ""))

    # Unterspannung ist die haeufigste Ursache beschaedigter Blockchain-Daten
    try:
        r = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5, check=False
        )
        if r.returncode == 0 and "=" in r.stdout:
            wert = int(r.stdout.strip().split("=")[1], 16)
            if wert == 0:
                felder.append(("Stromversorgung", "stabil", "gut"))
            else:
                hinweise = []
                if wert & 0x1:
                    hinweise.append("AKTUELL Unterspannung")
                if wert & 0x40000:
                    hinweise.append("Unterspannung seit Start")
                if wert & 0x4:
                    hinweise.append("gedrosselt")
                felder.append(("Stromversorgung", ", ".join(hinweise) or "auffällig", "warn"))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    return ("System", felder)


def sammle_node(cfg):
    """Fragt Bitcoin Core ab. Nutzt nur freigeschaltete, lesende Methoden."""
    kette = rpc(cfg, "getblockchaininfo")
    netz = rpc(cfg, "getnetworkinfo")
    mempool = rpc(cfg, "getmempoolinfo")
    verbindungen = rpc(cfg, "getconnectioncount")
    laufzeit = rpc(cfg, "uptime")

    fortschritt = float(kette.get("verificationprogress", 0)) * 100
    fortschritt = min(fortschritt, 100.0)
    synchron = fortschritt >= 99.999 and not kette.get("initialblockdownload", False)

    bloecke = kette.get("blocks", 0)
    kopfzeilen = kette.get("headers", 0)
    rueckstand = max(0, kopfzeilen - bloecke)

    # 'Blockchain' ist keine eigene Karte mehr. Die Zahlen stehen oben im
    # Kennzahlenband und in der Zustandsleiste — eine Karte, die sie ein
    # drittes Mal wiederholt, ist nur Fuellmaterial. Was gebraucht wird, geht
    # als Einzelwert in die Kennzahlen.
    blockzeit = kette.get("time")
    stand_text = None
    if blockzeit:
        alter = time.time() - float(blockzeit)
        # Waehrend der Erstsynchronisation ist das der Block von 2010, nicht
        # die Kettenspitze — "vor 9 Jahren" waere hier irrefuehrend.
        stand_text = (formatiere_alter(alter) if synchron else
                      "geprüft bis "
                      + datetime.fromtimestamp(float(blockzeit)).strftime("%d.%m.%Y"))

    anteil = float(kette.get("verificationprogress", 0))
    merke_langzeit(anteil)

    # Erst wenn die Kette steht — waehrend der Erstsynchronisation waeren die
    # Zahlen von 2010 und das Nachladen reine Verschwendung.
    if synchron:
        hole_schwierigkeit(cfg, kopfzeilen)
        hole_blockdaten(cfg, bloecke)

    # Tempo und Restzeit zeigt die Zustandsleiste oben gross an.
    tempo_text = restzeit_text = None
    if not synchron:
        pro_stunde, rest_sek = schaetze_restzeit(anteil)
        if pro_stunde is not None:
            tempo_text = f"{pro_stunde:.2f} %-Punkte/Std".replace(".", ",")
            restzeit_text = formatiere_dauer(rest_sek)

    # --- Netzwerk-Eckdaten: Halbierung und Schwierigkeit ---------------------
    belohnung, naechstes, rest_bloecke, wann = halving_infos(kopfzeilen)
    kette_felder_netz = [
        ("Blockbelohnung", f"{belohnung:.3f} BTC".replace(".", ","), ""),
        ("Nächste Halbierung", f"bei {formatiere_zahl(naechstes)}", ""),
        ("noch", f"{formatiere_zahl(rest_bloecke)} Blöcke", ""),
        ("etwa", wann.strftime("%m/%Y"), ""),
        ("Schwierigkeit", formatiere_gross(float(kette.get("difficulty", 0))), ""),
    ]

    # Die Zahl bis zur naechsten Anpassung steht immer fest — sie haengt nur
    # an der Kopfzeilenhoehe, nicht am Verlaufsspeicher.
    rest_retarget = RETARGET_ABSTAND - (kopfzeilen % RETARGET_ABSTAND)
    kette_felder_netz.append(
        ("nächste in", f"{formatiere_zahl(rest_retarget)} Blöcken", ""))

    werte = [w for _, w in SCHWIERIGKEIT]
    if len(werte) < 2:
        # Der Verlauf wird erst nach der Synchronisation nachgeladen.
        kette_felder_netz.append(("letzte Anpassung", "—", "leer"))
        kette_felder_netz.append(
            ("letzte Anpassungen",
             baue_geruest("Schwierigkeit der letzten Anpassungen", saeulen=True),
             "grafik"))
    else:
        aenderung = (werte[-1] / werte[-2] - 1) * 100 if werte[-2] else 0
        kette_felder_netz.append(
            ("letzte Anpassung",
             f"{aenderung:+.1f} %".replace(".", ","),
             "warn" if abs(aenderung) > 8 else "")
        )
        saeulen = baue_saeulen(
            werte, "var(--leise)",
            f"Schwierigkeit der letzten {len(werte)} Anpassungen")
        if saeulen:
            kette_felder_netz.append(
                (f"letzte {len(werte)} Anpassungen", saeulen, "grafik"))

    netz_felder = [
        ("Verbindungen", str(verbindungen), "warn" if int(verbindungen) < 8 else ""),
        ("davon eingehend", str(netz.get("connections_in", "?")), ""),
        ("davon ausgehend", str(netz.get("connections_out", "?")), ""),
        ("Version", netz.get("subversion", "?").strip("/"), ""),
        ("Node läuft seit", formatiere_dauer(laufzeit), ""),
    ]

    mempool_felder = [
        ("Transaktionen", formatiere_zahl(mempool.get("size", 0)), ""),
        ("Speicher", formatiere_bytes(mempool.get("usage", 0)), ""),
        ("Mindestgebühr",
         f"{mempool.get('mempoolminfee', 0) * 100000:.1f} sat/vB".replace(".", ","), ""),
    ]

    # Gebuehrenschaetzung nur bei stehender Kette. Waehrend der
    # Synchronisation antwortet Core zuverlaessig mit "keine Daten" — drei
    # Abfragen fuer eine Auskunft, die wir vorher kennen. Bei zwoelf Sekunden
    # Antwortzeit je Abfrage ist das ein Drittel des ganzen Durchlaufs.
    gebuehren_felder = []
    if synchron:
        for ziel, bezeichnung in ((1, "nächster Block"), (6, "in ~1 Stunde"),
                                  (24, "in ~4 Stunden")):
            try:
                antwort = rpc(cfg, "estimatesmartfee", [ziel])
            except RpcFehler:
                break
            rate = antwort.get("feerate")
            if rate:
                gebuehren_felder.append(
                    (bezeichnung,
                     f"{float(rate) * 100000:.1f} sat/vB".replace(".", ","), "")
                )
    if not gebuehren_felder:
        gebuehren_felder = [
            ("Schätzung", "während der Synchronisation nicht möglich", "leer")]

    kennzahlen = {
        "bloecke": bloecke,
        "kopfzeilen": kopfzeilen,
        "blockalter": (time.time() - float(blockzeit)) if (blockzeit and synchron) else None,
        "verbindungen": int(verbindungen),
        "mempool": int(mempool.get("size", 0)),
        "rueckstand": rueckstand,
        "belegt": kette.get("size_on_disk", 0),
        "tempo": tempo_text,
        "restzeit": restzeit_text,
        "stand": stand_text,
        "gepruned": bool(kette.get("pruned")),
        "version": str(netz.get("subversion", "")).strip("/"),
        "laufzeit": laufzeit,
        # Ersatzanzeige fuer die Karte 'Verbundene Knoten', solange
        # getpeerinfo nicht freigeschaltet ist. Ohne sie waeren die
        # Verbindungsdaten bis dahin nirgends mehr zu sehen.
        "netzfelder": netz_felder,
    }

    # --- 24-Stunden-Grafiken -----------------------------------------------
    # Die Karten stehen von Anfang an da, damit das Layout vollstaendig ist.
    # Solange die Kette nicht steht, tragen sie ein Geruest und Striche
    # statt Zahlen — nichts, was man fuer eine Messung halten koennte.
    wartehinweis = ("Erscheint, sobald die Kette steht. Bis dahin liegen die "
                    "letzten Blöcke Jahre zurück und wären ohne Aussage.")
    volumen_felder, volumen_fuss = platzhalterkarte(
        ["Summe", "Transaktionen", "Blöcke"],
        "Volumen je Block", wartehinweis, saeulen=True)
    gebuehren_felder24, gebuehren_fuss = platzhalterkarte(
        ["zuletzt", "Mittel 24 h", "Spanne"],
        "mittlere Gebühr je Block", wartehinweis)

    if synchron and len(BLOCKDATEN) >= 3:
        volumen_fuss = gebuehren_fuss = ""
        zeitraum = (BLOCKDATEN[-1][1] - BLOCKDATEN[0][1]) or 1
        stunden = zeitraum / 3600
        ausgaenge = [e[2] for e in BLOCKDATEN]
        gebuehren = [e[3] for e in BLOCKDATEN]
        anzahlen = [e[4] for e in BLOCKDATEN]

        volumen_felder = [
            ("Summe", formatiere_btc(sum(ausgaenge)), ""),
            ("Transaktionen", formatiere_zahl(sum(anzahlen)), ""),
            ("Blöcke", f"{len(BLOCKDATEN)} · {stunden:.0f} Std".replace(".", ","), ""),
            (f"Volumen je Block",
             baue_saeulen(ausgaenge, "var(--akzent)",
                          "Bewegtes Volumen je Block der letzten 24 Stunden"),
             "grafik"),
        ]

        jetzt_gebuehr = gebuehren[-1] if gebuehren else 0
        bekannt = [g for g in gebuehren if g]
        volumen_felder = [f for f in volumen_felder if f[1]]
        gebuehren_felder24 = [
            ("zuletzt", f"{jetzt_gebuehr:.1f} sat/vB".replace(".", ","), ""),
            ("Mittel 24 h",
             f"{(sum(bekannt) / len(bekannt) if bekannt else 0):.1f} sat/vB".replace(".", ","), ""),
            ("Spanne",
             f"{min(bekannt) if bekannt else 0:.0f} bis {max(bekannt) if bekannt else 0:.0f} sat/vB", ""),
            ("mittlere Gebühr je Block",
             baue_linie(gebuehren, "var(--warn)",
                        "Mittlere Gebühr je Block der letzten 24 Stunden"),
             "grafik"),
        ]
        gebuehren_felder24 = [f for f in gebuehren_felder24 if f[1]]

    # 'Netzwerk' ist ebenfalls keine eigene Karte mehr — die Verbindungen
    # stehen in 'Verbundene Knoten', Version und Laufzeit in der Kopfzeile.
    gruppen = [
        ("Mempool & Gebühren", mempool_felder + gebuehren_felder),
        ("Volumen · 24 Stunden", volumen_felder, volumen_fuss),
        ("Gebührenverlauf · 24 Stunden", gebuehren_felder24, gebuehren_fuss),
        ("Netzwerk-Eckdaten", kette_felder_netz),
    ]
    return fortschritt, synchron, gruppen, kennzahlen


# =============================================================== Netzkarte ===
# Farben je Netzwerkart. Bewusst nur vier — mehr unterscheidet das Auge in
# kleinen Punkten ohnehin nicht.
NETZFARBEN = {
    "ipv4": "var(--netz-ipv4)",
    "ipv6": "var(--netz-ipv6)",
    "onion": "var(--netz-onion)",
    "i2p": "var(--netz-i2p)",
    "cjdns": "var(--netz-i2p)",
}
NETZNAMEN = {
    "ipv4": "IPv4", "ipv6": "IPv6", "onion": "Tor",
    "i2p": "I2P", "cjdns": "CJDNS", "not_publicly_routable": "lokal",
}


def kuerze_adresse(adresse):
    """Onion-Adressen sind 62 Zeichen lang — das sprengt jede Beschriftung."""
    adresse = str(adresse)
    if len(adresse) <= 28:
        return adresse
    return adresse[:12] + "…" + adresse[-13:]


LETZTE_PEERS = []


def sammle_peers(cfg, hoechstzahl):
    """Liest die verbundenen Knoten.

    Rueckgabe ist bewusst reine Struktur, kein HTML: Alles hier stammt von
    fremden Rechnern — Adresse, Kennung und Dienstliste bestimmt die
    Gegenstelle, nicht wir. Die Werte werden erst ganz am Ende maskiert,
    beziehungsweise im Browser ueber textContent gesetzt, wo gar kein
    Markup entstehen kann.
    """
    global LETZTE_PEERS

    try:
        roh = rpc(cfg, "getpeerinfo")
    except RpcFehler:
        # Dasselbe Toleranzfenster wie beim Rest: Ein Aussetzer ist kein
        # Grund, die Liste wegzuwerfen. Waehrend der Synchronisation braucht
        # eine Abfrage schon mal laenger als das Zeitlimit — die zuletzt
        # bekannten Gegenstellen sind dann allemal besser als keine.
        return list(LETZTE_PEERS)

    jetzt = time.time()
    peers = []
    for p in roh:
        if not isinstance(p, dict):
            continue
        # Beide Werte kommen in Dezimalsekunden. Angezeigt wird 'minping' —
        # die schnellste je gemessene Antwort, also die Laufzeit der Leitung.
        # 'pingtime' misst dagegen vor allem, wie beschaeftigt der eigene Node
        # gerade ist: Ping und Pong laufen im selben Strang wie das Anhaengen
        # der Bloecke. Waehrend der Synchronisation stehen dort zehn Sekunden
        # bei allen Gegenstellen, und die Zahl unterscheidet nichts mehr.
        ping = p.get("pingtime")
        besser = p.get("minping")
        peers.append({
            "adresse": str(p.get("addr", "?")),
            "netz": str(p.get("network", "?")),
            "eingehend": bool(p.get("inbound", False)),
            "ping_ms": round(float(besser) * 1000, 1) if besser else (
                round(float(ping) * 1000, 1) if ping else None),
            "jetzt_ms": round(float(ping) * 1000, 1) if ping else None,
            "dauer_s": max(0, int(jetzt - float(p.get("conntime", jetzt)))),
            "version": str(p.get("subver", "")).strip("/") or "unbekannt",
            "dienste": ", ".join(p.get("servicesnames") or []) or "keine",
            "gesendet": int(p.get("bytessent", 0)),
            "empfangen": int(p.get("bytesrecv", 0)),
        })

    # Zuerst nach Netzart gruppieren, darin die schnellsten zuerst. Die
    # Gruppierung sorgt dafuer, dass gleichfarbige Punkte im Kreis
    # beieinanderliegen statt sich zu durchmischen.
    rang = {"onion": 0, "ipv4": 1, "ipv6": 2, "i2p": 3, "cjdns": 4}
    peers.sort(key=lambda e: (rang.get(e["netz"], 9),
                              e["ping_ms"] if e["ping_ms"] is not None else 9e9))
    LETZTE_PEERS = peers[:hoechstzahl]
    return list(LETZTE_PEERS)


def formatiere_latenz(ms):
    """Millisekunden bis 1000, darueber Sekunden.

    Waehrend der Erstsynchronisation sind Antwortzeiten von einer Minute
    normal — der Node kommt schlicht nicht dazu, das Pong zu verschicken.
    "64101 ms" macht die Beschriftung unnoetig lang und liest sich schlecht.
    """
    if ms is None:
        return None
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.1f} s".replace(".", ",")


def peerzeile_text(p):
    """Die Eckdaten, die an der Linie stehen. Kurz genug fuer eine Zeile."""
    teile = [kuerze_adresse(p["adresse"]), NETZNAMEN.get(p["netz"], p["netz"])]
    latenz = formatiere_latenz(p["ping_ms"])
    if latenz:
        teile.append(latenz)
    teile.append(formatiere_bytes(p["gesendet"] + p["empfangen"]))
    return " · ".join(teile)


# Das Bitcoin-Zeichen als PNG, 128 x 128, freigestellt und auf 16 Farben
# reduziert — knapp 1,9 kB. Vorher stand hier eine von Hand aus Rechtecken
# und Boegen nachgebaute Form. Sie war dreimal falsch, und jeder Versuch
# kostete eine Runde ueber ein Bildschirmfoto, weil sich in dieser Umgebung
# nichts ansehen laesst. Ein Logo ist keine Geometrieaufgabe: Das Bild ist
# die Vorlage, also ist das Bild die Antwort.
#
# Es wird als eigene Datei ausgeliefert, nicht als data:-URI im Markup —
# sonst stuende es in jeder index.html und in jeder status.json erneut.
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

MARKE_R = 19                 # Radius des Zeichens an der Nabe

SCHRIFT_PEER = 12.5          # muss zu .peerzeile in STIL passen
ZEICHEN_PEER = 0.63          # Breite eines Zeichens in dieser Schriftgroesse
SPEICHE = 104                # Abstand Nabe bis zum inneren Ende der Waagerechten


def baue_netzkarte(peers):
    """Faecher: der eigene Node in der Mitte, die Gegenstellen links und rechts.

    Keine Weltkarte — 'getpeerinfo' liefert keine Geodaten, und der Generator
    darf nicht ins Internet, um welche nachzuschlagen. Die Eckdaten stehen
    direkt an der Linie, damit man sie ohne Zeigen ablesen kann; das Zeigen
    hebt die Zeile nur hervor und holt die ausfuehrlichen Angaben nach.

    Ins SVG geht fremder Text ausschliesslich maskiert. Die Langfassung
    (Kennung, Dienste) setzt dash.js per textContent, wo gar kein Markup
    entstehen kann.
    """
    if not peers:
        return None

    zeilenhoehe = 30
    rand_oben = 34
    haelfte = (len(peers) + 1) // 2
    hoehe = rand_oben * 2 + haelfte * zeilenhoehe

    # Die Breite ergibt sich aus der laengsten Beschriftung, nicht aus einem
    # festen Wert. Ein SVG schneidet alles ab, was ueber sein viewBox
    # hinausragt — mit einer festen Breite verschwand bei langen Adressen und
    # dreistelligen Sekundenwerten das Ende der Zeile.
    laengste = max(len(peerzeile_text(p)) for p in peers)
    beschriftung = laengste * SCHRIFT_PEER * ZEICHEN_PEER
    halb = SPEICHE + 14 + beschriftung + 26
    breite = round(halb * 2)
    mx, my = breite / 2, hoehe / 2

    # Innere Enden der waagerechten Linien. Dazwischen bleibt Platz fuer die
    # Nabe, damit sich Linien und Beschriftung nicht ins Gehege kommen.
    links_innen, rechts_innen = mx - SPEICHE, mx + SPEICHE
    links_aussen, rechts_aussen = 26, breite - 26

    teile = []
    for i, p in enumerate(peers):
        rechts = i >= haelfte
        reihe = i - haelfte if rechts else i
        y = rand_oben + reihe * zeilenhoehe + zeilenhoehe / 2

        if rechts:
            x_innen, x_aussen = rechts_innen, rechts_aussen
            x_text, ausrichtung = rechts_innen + 14, "start"
            nabe_x = mx + MARKE_R + 5
        else:
            x_innen, x_aussen = links_innen, links_aussen
            x_text, ausrichtung = links_innen - 14, "end"
            nabe_x = mx - MARKE_R - 5

        art = p["netz"] if p["netz"] in NETZFARBEN else "neutral"
        gefuellt = "" if p["eingehend"] else " voll"

        teile.append(
            f'<g class="peer {art}" tabindex="0" data-nr="{i}">'
            # Grosse durchsichtige Flaeche: die sichtbaren Teile sind duenn,
            # das Ziel fuer die Maus darf grosszuegig sein.
            f'<rect x="{min(x_innen, x_aussen) - 6:.1f}" y="{y - 15:.1f}" '
            f'width="{abs(x_aussen - x_innen) + 12:.1f}" height="{zeilenhoehe}" '
            f'class="peerflaeche"/>'
            # Nur die Speiche von der Nabe zum Punkt. Frueher lief von dort
            # noch eine Waagerechte nach aussen, und die Beschriftung stand
            # darueber — dadurch lagen Punkt und Text auf verschiedenen
            # Hoehen. Jetzt sitzen beide auf derselben Linie.
            f'<line x1="{nabe_x:.1f}" y1="{my:.1f}" x2="{x_innen:.1f}" '
            f'y2="{y:.1f}" class="peerlinie"/>'
            f'<circle cx="{x_innen:.1f}" cy="{y:.1f}" r="4.5" '
            f'class="peerpunkt{gefuellt}"/>'
            f'<text x="{x_text:.1f}" y="{y:.1f}" text-anchor="{ausrichtung}" '
            f'dominant-baseline="central" '
            f'class="peerzeile">{html_escape(peerzeile_text(p))}</text>'
            "</g>"
        )

    # Die Nabe traegt das Bitcoin-Zeichen. Der Pfad ist fuer ein Feld von
    # 64 x 64 gezeichnet, deshalb Verschiebung auf die Ecke und Verkleinerung
    # auf den doppelten gewuenschten Radius.
    nabe = (
        f'<image href="bitcoin.png?v={BITCOIN_V}" '
        f'x="{mx - MARKE_R:.1f}" y="{my - MARKE_R:.1f}" '
        f'width="{MARKE_R * 2}" height="{MARKE_R * 2}"/>'
        f'<text x="{mx:.1f}" y="{my + MARKE_R + 26:.1f}" class="eigentext" '
        f'text-anchor="middle">dieser Node</text>'
    )

    return (f'<svg class=netzkarte viewBox="0 0 {breite:.0f} {hoehe:.0f}" '
            f'role="img" aria-label="Netz der {len(peers)} verbundenen Knoten">'
            f"{nabe}{''.join(teile)}</svg>")


def peer_kennzahlen(peers):
    """Zusammenfassung fuer die Kopfzeile der Netzkarte."""
    if not peers:
        return []
    nach_netz = {}
    for p in peers:
        nach_netz[p["netz"]] = nach_netz.get(p["netz"], 0) + 1
    pings = [p["ping_ms"] for p in peers if p["ping_ms"] is not None]
    eingehend = sum(1 for p in peers if p["eingehend"])

    felder = [("Verbunden", f"{len(peers)}", ""),
              ("davon eingehend", f"{eingehend}", "")]
    for netz, n in sorted(nach_netz.items(), key=lambda e: -e[1]):
        felder.append((NETZNAMEN.get(netz, netz), str(n), ""))
    if pings:
        felder.append(("Laufzeit im Mittel",
                       formatiere_latenz(sum(pings) / len(pings)), ""))
        felder.append(("schnellster", formatiere_latenz(min(pings)), ""))

    # Wie lange der eigene Node zum Antworten braucht. Waehrend der
    # Synchronisation sind das Sekunden — nicht die Schuld der Gegenstellen,
    # sondern ein Mass fuer die eigene Auslastung.
    jetzt = [p["jetzt_ms"] for p in peers if p.get("jetzt_ms") is not None]
    if jetzt:
        mittel = sum(jetzt) / len(jetzt)
        felder.append(("eigene Antwortzeit", formatiere_latenz(mittel),
                       "warn" if mittel > 2000 else ""))
    return felder


def sammle_updates(cfg):
    """Liest das Ergebnis der woechentlichen Versionspruefung.

    Der Dashboard-Generator geht selbst nie ins Internet — er liest nur die
    Datei, die ein getrennter Dienst geschrieben hat. Rueckgabe ist reine
    Struktur; das Markup baut baue_kopfinfo daraus.
    """
    pfad = cfg.get("UPDATE_DATEI", "/var/lib/node-dashboard/updates.json")
    roh = lies_datei(pfad)
    if not roh:
        return None
    try:
        daten = json.loads(roh)
    except json.JSONDecodeError:
        return None
    return daten if daten.get("eintraege") else None


def sammle_tor(cfg):
    """Liest, was der Tor-Waechter zuletzt gemeldet hat.

    Wie bei der Versionspruefung: Ein getrennter Dienst schreibt eine Datei,
    das Dashboard liest sie. Es gibt keinen Weg von hier zurueck.
    """
    roh = lies_datei(cfg.get("TOR_DATEI", "/var/lib/node-dashboard/tor.json"))
    if not roh:
        return None
    try:
        return json.loads(roh)
    except json.JSONDecodeError:
        return None


def baue_tormeldung(tor):
    """Das Band, das die bevorstehende oder erfolgte Umstellung ankuendigt."""
    if not tor:
        return ""
    zustand = tor.get("zustand")

    if zustand == "bereit":
        treffer, noetig = tor.get("treffer", 0), tor.get("noetig", 6)
        return (
            '<div class="meldung warn"><span class=punkt></span><div>'
            "<b>Die Kette steht.</b> Der Wächter stellt den Node auf Tor um, "
            f"sobald sie eine Stunde durchgehend synchron war — Messung "
            f"{html_escape(str(treffer))} von {html_escape(str(noetig))}. "
            "Abbrechen mit <code>sudo bash 08-tor-automatik.sh --aus</code>."
            "</div></div>"
        )
    if zustand == "laeuft":
        return (
            '<div class="meldung warn"><span class=punkt></span><div>'
            "<b>Umstellung auf Tor läuft.</b> bitcoind wird dabei neu "
            "gestartet und Port 8333 geschlossen. Verlauf im Protokoll unter "
            "<code>journalctl -u node-torwaechter -f</code>.</div></div>"
        )
    if zustand == "fehler":
        return (
            '<div class=fehlerkarte><h2>Tor-Umstellung gescheitert</h2>'
            f"<p>{html_escape(tor.get('meldung', ''))}</p>"
            "<p>Es wird nicht selbsttätig wiederholt. Nachsehen mit "
            "<code>journalctl -u node-torwaechter -n 50</code>.</p></div>"
        )
    return ""


def baue_kopfinfo(updates, kz):
    """Der mittlere Teil der Kopfzeile: Fassungen, Zustand, Laufzeit.

    Die Karte 'Aktualisierungen' ist dafuer entfallen. Sie hat den Platz einer
    vollen Karte belegt, um im Regelfall dreimal 'aktuell' zu sagen. Hier
    steht dieselbe Auskunft in einer Zeile — und faellt nur auf, wenn etwas
    nicht stimmt.
    """
    stuecke, stufe = [], "gut"

    if updates:
        for e in updates.get("eintraege", []):
            name = e.get("name", "?")
            kurz = "Core" if name.startswith("Bitcoin") else name
            inst = e.get("installiert", "?")
            if e.get("veraltet"):
                stuecke.append(f"{kurz} {inst} → {e.get('neueste')}")
                stufe = "warn"
            else:
                stuecke.append(f"{kurz} {inst}")
    elif kz.get("version"):
        # Ohne Versionspruefung wenigstens das, was der Node selbst sagt.
        stuecke.append(kz["version"])

    if kz.get("laufzeit"):
        stuecke.append("läuft seit " + formatiere_dauer(kz["laufzeit"]))

    if not stuecke:
        return ""

    # Die Pruefzeit steht im title-Attribut statt in der Zeile: Sie ist beim
    # Nachsehen wichtig und beim Hinsehen nur Ballast.
    titel = ""
    if updates and updates.get("geprueft"):
        titel = "Versionen geprüft " + formatiere_alter(
            time.time() - updates["geprueft"])
        if not updates.get("ueber_tor", False):
            titel += ", Abruf im Klartext"

    return (f'<div class="kopfinfo {stufe}"'
            + (f' title="{html_escape(titel)}"' if titel else "")
            + '><span class=kpunkt></span>'
            + f"<span>{html_escape(' · '.join(stuecke))}</span></div>")


# journalctl --output=short-iso liefert:
#   2026-08-23T11:31:50+02:00 btcnode bitcoind[32327]: 2026-08-23T09:31:50Z UpdateTip: ...
#   \_________ journald ____________________________/  \__ bitcoind __/
# Das sind rund 55 Zeichen Vorspann, in denen die Uhrzeit zweimal steht — der
# interessante Teil wird dadurch aus dem Bild geschoben. Wir behalten die
# lokale Uhrzeit und werfen den Rest weg.
VORSPANN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})[+\-]\d{2}:\d{2}\s+"   # journald
    r"\S+\s+"                                                       # Rechnername
    r"[\w.@-]+(?:\[\d+\])?:\s*"                                     # Dienst[PID]:
    r"(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+)?"                # bitcoind, UTC
)


def kuerze_protokollzeile(zeile):
    """Entfernt den doppelten Zeitstempel. Passt das Muster nicht, bleibt die
    Zeile unveraendert — lieber breit als unvollstaendig."""
    treffer = VORSPANN.match(zeile)
    if not treffer:
        return zeile
    return f"{treffer.group(1)}  {zeile[treffer.end():]}"


def sammle_protokoll(cfg):
    """Holt die letzten Journal-Zeilen der Dienste.

    Die Zeilen werden spaeter maskiert ins HTML geschrieben. Das ist wichtig:
    Bitcoin Core protokolliert unter anderem die selbstgewaehlten Kennungen
    fremder Knoten, und die bestimmt nicht wir, sondern die Gegenstelle.
    """
    abschnitte = []
    zeilen_max = max(5, min(200, int(cfg.get("LOG_ZEILEN", 40))))

    for dienst in [d.strip() for d in cfg.get("LOG_DIENSTE", "").split(",") if d.strip()]:
        if not os.path.exists(f"/etc/systemd/system/{dienst}.service"):
            continue
        try:
            r = subprocess.run(
                ["journalctl", "-u", dienst, "-n", str(zeilen_max),
                 "--no-pager", "--output=short-iso"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            abschnitte.append((dienst, f"Protokoll nicht lesbar: {e}"))
            continue

        if r.returncode != 0:
            hinweis = (r.stderr or "").strip() or "kein Zugriff auf das Journal"
            abschnitte.append((dienst, hinweis))
            continue

        zeilen = [kuerze_protokollzeile(z)
                  for z in r.stdout.splitlines() if z.strip()]
        if not zeilen:
            abschnitte.append((dienst, "keine Einträge"))
        else:
            # Neueste zuerst: eine statische Seite kann nicht ans Ende scrollen
            abschnitte.append((dienst, "\n".join(reversed(zeilen))))

    return abschnitte


def eigene_ip():
    """Ermittelt die eigene Adresse im Heimnetz.

    Der Datenverkehr geht dabei nirgendwohin: Ein UDP-Socket wird nur
    'verbunden', damit der Kernel die passende Quelladresse waehlt. Es
    verlaesst kein einziges Paket den Pi.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))     # Adresse aus dem Doku-Bereich
            return s.getsockname()[0]
    except OSError:
        return None


def sammle_electrum(cfg):
    """Zustand des Electrum-Servers, sofern eingerichtet.

    Er ist es, der eine Wallet an den eigenen Node haengt — BitBoxApp,
    Sparrow, Electrum. Ohne ihn fragt die Wallet fremde Server, und die
    erfahren dabei, welche Adressen einem gehoeren.
    """
    if not os.path.exists("/etc/systemd/system/electrs.service"):
        return None

    laeuft = dienst_laeuft("electrs")
    port = cfg["ELECTRS_PORT"]
    erreichbar = port_offen("127.0.0.1", port) if laeuft else False

    felder = [
        ("Dienst", "läuft" if laeuft else "gestoppt", "gut" if laeuft else "warn"),
        ("Antwortet", "ja" if erreichbar else "nein, indiziert noch",
         "gut" if erreichbar else "warn"),
    ]

    # --- Verbindungsdaten fuer die Wallet, zum Anklicken und Kopieren -------
    ip = eigene_ip()
    if ip:
        felder.append(("Im Heimnetz", f"{ip}:{port}", "kopier"))

    # /var/lib/tor/... ist fuer fremde Dienste nicht lesbar (Verzeichnis 700),
    # deshalb legt Skript 05 eine Kopie an einen neutralen Ort.
    onion = (lies_datei("/etc/electrs/onion")
             or lies_datei("/var/lib/tor/electrs/hostname"))
    if onion:
        felder.append(("Über Tor", f"{onion}:{port}", "kopier"))

    hinweis = ("In der Wallet als eigenen Server eintragen — in der BitBoxApp "
               "unter Einstellungen → Erweiterte Einstellungen → Eigene Full "
               "Node verbinden. Ein Klick auf eine Adresse markiert sie, "
               "Strg+C kopiert.")

    return ("Electrum-Server", felder, hinweis)


# =================================================================== Ausgabe
# Der Stil liegt in einer eigenen Datei, nicht mehr inline im HTML. Grund ist
# nicht die Dateigroesse, sondern die Content-Security-Policy: Erst ohne
# inline-Stil und inline-Skript kann nginx "default-src 'self'" ohne
# 'unsafe-inline' setzen — und das ist der eigentliche Schutz, falls je ein
# fremder Wert an der Maskierung vorbeirutschen sollte.
STIL = """
:root{
/* Flaechen — von unten nach oben: Grund, Karte, erhoehte Flaeche */
--bg:#0a0c11;--fl:#111419;--fl2:#161a22;--vertief:#0d1015;
--rand:#1e232c;--randhell:#2a313d;--randhervor:#3a4353;
/* Schrift — drei Stufen, mehr braucht es nicht */
--text:#e7eaf1;--leise:#98a1b2;--sehrleise:#68717f;
/* Bedeutung. Gruen heisst 'wie erwartet', nicht 'Problem geloest'. */
--akzent:#2fd39a;--warn:#f0b23f;--fehler:#f2645f;--info:#5aa2f0;
/* Netzarten in der Netzkarte */
--netz-ipv4:#5aa2f0;--netz-ipv6:#9b8cff;--netz-onion:#2fd39a;--netz-i2p:#f0b23f;
--balken:var(--akzent);
/* Abstandsraster: alles Weitere ist ein Vielfaches davon */
--e1:.25rem;--e2:.5rem;--e3:.75rem;--e4:1rem;--e5:1.5rem;--e6:2rem;
/* Grundzeile. Jede Zeile in jeder Karte ist genau so hoch oder ein
   Vielfaches davon — nur so stehen die Zeilen benachbarter Karten auf
   gleicher Hoehe. Grafiken bekommen zwei oder drei Einheiten. */
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
--schatten:0 1px 2px rgba(16,19,25,.05),0 8px 24px -14px rgba(16,19,25,.22)}}

*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--text);font-family:var(--sans);
font-size:15px;line-height:1.5;padding:var(--e5) var(--e4) var(--e6);
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
/* Volle Bildschirmbreite, keine Begrenzung: Das Dashboard ist eine Anzeige,
   kein Fliesstext. */
.huelle{max-width:none;display:flex;flex-direction:column;gap:var(--e4)}

/* Zwei Spalten: links das Ausgewertete, rechts das rohe Protokoll. Die
   Spaltenbreite von 50rem ist nicht gegriffen — eine gekuerzte Protokollzeile
   ist rund 110 Zeichen lang und braucht genau so viel, um ungebrochen zu
   stehen. Darunter lohnt die Aufteilung nicht, deshalb der Umbruchpunkt. */
/* 'stretch': Die rechte Spalte wird so hoch wie die linke. Das Protokoll
   nimmt sich davon alles, was die Netzkarte uebrig laesst, und reicht damit
   genau bis zur Electrum-Karte, die unter beiden Spalten steht. */
.inhalt{display:grid;grid-template-columns:1fr;gap:var(--e4);
align-items:stretch}
/* container-type: Damit sich das Kartenraster nach der Breite DIESER Spalte
   richten kann und nicht nach der des Fensters. Die linke Spalte ist nur
   halb so breit wie der Bildschirm — eine am Fenster bemessene Spaltenzahl
   geht hier zwangslaeufig daneben. */
.links,.rechts{display:flex;flex-direction:column;gap:var(--e4);min-width:0;
container-type:inline-size}
/* min-width:0 ist hier keine Feinheit, sondern zwingend. Rasterelemente haben
   von sich aus min-width:auto, also die Mindestbreite ihres Inhalts — und die
   ist beim Protokoll mit 'white-space:pre' die laengste Zeile. Ohne diese
   Zeile drueckt das Protokoll die ganze Spalte breiter als das Fenster, und
   rechts wird auf der ganzen Seite abgeschnitten. */
.links>*,.rechts>*{min-width:0}
/* Leere Zonen ausblenden. Ein <div> ohne Inhalt ist unsichtbar, zaehlt in
   der Flex-Spalte aber als Element und erzeugt einen zweiten Abstand — das
   sieht aus wie ein ungleicher Rand, ist aber ein Loch. Betrifft vor allem
   die Stoerungszone, die im Normalfall leer ist. */
.links>*:empty,.rechts>*:empty{display:none}
/* Genau halbe/halbe, in jedem Browser und bei jeder Aufloesung. Keine feste
   Breite in rem — die waere auf einem 4K-Schirm ein Streifen und auf einem
   Notebook die halbe Seite. */
@media(min-width:80rem){
.inhalt{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}

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
/* Mitte der Kopfzeile: Fassungen und Laufzeit. Ersetzt die Karte
   'Aktualisierungen' — sichtbar, aber leise, solange alles stimmt. */
.kopfinfo{display:flex;align-items:center;gap:var(--e2);
color:var(--leise);font-size:.73rem;font-family:var(--mono);
margin-inline:auto;cursor:default}
.kopfinfo .kpunkt{width:.4rem;height:.4rem;border-radius:99px;flex:none;
background:var(--akzent)}
.kopfinfo.warn{color:var(--warn)}
.kopfinfo.warn .kpunkt{background:var(--warn)}
@media(max-width:60rem){.kopfinfo{order:3;flex-basis:100%;margin-inline:0}}
/* Der Lebendpunkt zeigt, dass die Seite sich selbst nachfuehrt. Ohne JS
   bleibt er einfach stehen — das ist ehrlicher als ein Blinken ins Leere. */
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
/* Schmaler Farbstreifen an der Kante statt einer eingefaerbten Flaeche:
   traegt dieselbe Information, ohne die Karte laut zu machen. */
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
/* Tempo links, Restzeit rechts — direkt unter dem Balken, wo man beim
   Ablesen des Fortschritts ohnehin hinsieht. */
.zfuss{display:flex;justify-content:space-between;gap:var(--e4);
margin-top:var(--e2);color:var(--leise);font-size:.76rem;
font-family:var(--mono);font-variant-numeric:tabular-nums}
.zrest{color:var(--text);font-weight:600}
.kurve{margin-top:var(--e3)}
.kurve svg{display:block;width:100%;height:44px}
.kurvenfuss{display:block;margin-top:var(--e1);color:var(--sehrleise);font-size:.7rem;
text-align:right;font-family:var(--mono)}

/* ------------------------------------------------------- Kennzahlenband -- */
/* Vier Zahlen, die immer gelten. Sie stehen ueber allem anderen, damit ein
   Blick aus drei Metern Entfernung reicht. */
/* Immer vier Spalten. Sie sind der Taktgeber des linken Bereichs, und ihre
   Zahl darf sich nicht aendern, wenn das Protokoll auf- oder zugeklappt wird
   — sonst springt bei jedem Klick die halbe Seite um. */
.band{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--e3)}
@media(max-width:62rem){.band{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:34rem){.band{grid-template-columns:1fr}}
/* Flex-Spalte statt loser Abstaende: Wert und Bezeichnung stehen in jeder
   Kachel im gleichen Rhythmus, und die Zusatzzeile sitzt unten am Rand statt
   irgendwo dazwischen. Vorher hatte die eine Kachel mit Zusatz sichtbar
   andere Abstaende als die drei ohne. */
.kachel{background:var(--fl);border:1px solid var(--rand);border-radius:var(--rad);
padding:var(--e3) var(--e4);display:flex;flex-direction:column;gap:var(--e1)}
.kachel .kwert{font-family:var(--mono);font-size:1.15rem;font-weight:600;
letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.2}
.kachel .klabel{color:var(--sehrleise);font-size:.66rem;text-transform:uppercase;
letter-spacing:.1em;line-height:1.3}
/* Die kleine Zeile darunter traegt, was frueher die Karte 'Blockchain' sagte. */
.kachel .kzusatz{color:var(--leise);font-size:.72rem;font-family:var(--mono);
margin-top:auto;padding-top:var(--e3);border-top:1px solid var(--rand)}
/* Die Vergleichskachel behaelt die Breite der anderen. Wird es eng, bricht
   sie am "von" um — zwei Zeilen sind besser als eine abgeschnittene Zahl,
   und alle Kacheln sind ohnehin gleich hoch. */
.kachel.breit .kwert{flex-wrap:wrap;display:flex;align-items:baseline;
gap:0 .3em}
.kwert .kvon{color:var(--sehrleise);font-size:.8rem;font-weight:400}
.kachel.warn .kwert{color:var(--warn)}
.kachel.gut .kwert{color:var(--akzent)}

/* -------------------------------------------------------------- Karten --- */
/* Raster statt Spaltensatz: gleiche Oberkanten wirken aufgeraeumt, und die
   Lesereihenfolge stimmt mit der Reihenfolge im Quelltext ueberein. */
/* Kein 'align-items:start': Die Karten sollen sich auf die Hoehe der
   hoechsten Karte ihrer Zeile ziehen. Das macht das Raster von selbst, sobald
   man es nicht daran hindert — und weil .karte ein Flex-Container mit
   Spaltenrichtung ist, bleibt der Inhalt trotzdem oben stehen. */
/* auto-FIT, nicht auto-fill: 'fill' legt so viele Spuren an, wie hineinpassen,
   und laesst die ueberzaehligen leer stehen — drei Karten in einer Spalte,
   die vier Spuren fasst, belegen dann drei Viertel der Breite und rechts
   klafft ein Loch. 'fit' klappt leere Spuren zusammen, die uebrigen dehnen
   sich auf die volle Breite. */
.raster{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));
gap:var(--e3)}
.weit{display:grid;grid-template-columns:1fr;gap:var(--e3);grid-auto-rows:1fr}
/* Feste Spaltenzahl, sobald die SPALTE breit genug ist — nicht das Fenster.
   Der Generator waehlt sie so, dass die letzte Reihe voll wird: sechs Karten
   ergeben zweimal drei statt vier plus zwei. */
@container (min-width:56rem){
.raster.s2{grid-template-columns:repeat(2,minmax(0,1fr))}
.raster.s3{grid-template-columns:repeat(3,minmax(0,1fr))}
.raster.s4{grid-template-columns:repeat(4,minmax(0,1fr))}}
@container (min-width:38rem){.weit{grid-template-columns:1fr 1fr}}
.weit .minikurve{height:calc(var(--zeile) * 3.2)}
.weit dd.grafik{min-height:calc(var(--zeile) * 3.6)}
.karte{background:var(--fl);border:1px solid var(--rand);border-radius:var(--rad);
padding:var(--e4);display:flex;flex-direction:column}
.karte h2{font-size:.66rem;text-transform:uppercase;letter-spacing:.12em;
color:var(--sehrleise);font-weight:600;margin-bottom:var(--e3)}
/* Kein row-gap, stattdessen eine feste Mindesthoehe je Zeile: Damit liegt
   jede Zeile auf demselben Raster, auch wenn eine Karte zwischendurch eine
   Grafik enthaelt. Vorher schob jede Grafik alles darunter aus der Reihe,
   und die Nachbarkarten passten nicht mehr zusammen. */
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
dt.grafiklabel{grid-column:1/-1;color:var(--sehrleise);font-size:.65rem;
text-transform:uppercase;letter-spacing:.09em;align-items:flex-end;
padding-bottom:var(--e1)}
.minikurve{display:block;width:100%;height:calc(var(--zeile) * 1.6)}
/* Geruest statt Grafik: gestrichelt und gedämpft, damit auf keinem
   Bildschirmfoto jemand eine Messung darin vermutet. */
.geruestlinie{stroke:var(--randhell);stroke-width:1.5;stroke-dasharray:4 5}
.geruestteil{fill:var(--randhell);opacity:.45}
.geruest{opacity:.65}
/* Balken: Schiene und runde Ecken aus CSS, exakte Breite aus dem SVG. Ein
   style-Attribut waere durch die CSP verworfen, ein 'rx' im Rechteck wuerde
   von preserveAspectRatio="none" verzerrt. */
.balken{display:block;width:100%;background:var(--rand);border-radius:99px;
overflow:hidden;line-height:0}
.balken svg{display:block;width:100%;height:100%}
.hoch6{height:6px}
.hoch10{height:10px}
.balkenfuellung{fill:var(--akzent)}
.balkenfuellung.warn{fill:var(--warn)}
.balkenfuellung.fehler{fill:var(--fehler)}

/* Volle Breite: links die Werte, rechts die Adressen zum Kopieren. */
@media(min-width:60rem){
.karte.voll{display:grid;grid-template-columns:minmax(0,17rem) minmax(0,1fr);
gap:var(--e1) var(--e6);align-items:start}
.karte.voll h2,.karte.voll .kartenfuss{grid-column:1/-1}}
.kopierblock{display:grid;gap:var(--e2);align-content:start}
.kopierfeld .kopierlabel{display:block;color:var(--sehrleise);font-size:.65rem;
text-transform:uppercase;letter-spacing:.1em;margin-bottom:var(--e1)}
/* Der Text bricht um, statt aus der Karte zu laufen. Frueher stand hier
   'nowrap' mit eigenem Rollbalken, damit ein Klick die ganze Zeile markiert —
   seit es einen Kopierknopf gibt, ist das nicht mehr noetig, und eine
   62-stellige Onion-Adresse passt in keine halbe Kartenbreite. */
.kopierzeile{display:flex;align-items:stretch;gap:var(--e2);min-width:0}
.kopierfeld .kopier{flex:1;min-width:0;font-family:var(--mono);font-size:.79rem;
user-select:all;-webkit-user-select:all;color:var(--text);
background:var(--vertief);border:1px solid var(--randhell);border-radius:8px;
padding:var(--e2) var(--e3);line-height:1.5;
white-space:normal;word-break:break-all;overflow-wrap:anywhere}
/* Erscheint erst, wenn dash.js ihn verdrahtet hat — ein Knopf, der nichts
   tut, waere schlimmer als keiner. */
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
/* Die Karte ist so hoch wie ihr Inhalt. Die Zeichnung nimmt sich die volle
   Breite und ihre Hoehe aus dem Seitenverhaeltnis des viewBox — dessen
   Breite wiederum richtet sich nach der laengsten Beschriftung. */
.netz{display:flex;flex-direction:column;min-width:0}
#netzkarte{min-width:0}
.netzkarte{display:block;width:100%;height:auto}
/* Ersatz, solange getpeerinfo nicht freigeschaltet ist. */
.netzersatz{grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
gap:var(--e1) var(--e5)}
.netzersatz dd{text-align:left}

/* Faecher: die Nabe in der Mitte, je eine Zeile Speiche + Waagerechte
   nach links und rechts, die Eckdaten stehen ueber der Waagerechten. */
/* Kein gestrichelter Hof mehr um die Nabe: Das Zeichen traegt sich selbst,
   der Ring war nur Beiwerk und stand den Speichen im Weg. */

.eigentext{fill:var(--sehrleise);font-family:var(--sans);font-size:11px;
letter-spacing:.09em;text-transform:uppercase}
.peerlinie{stroke:var(--randhell);stroke-width:1;stroke-opacity:.55}
.peerzeile{font-family:var(--mono);font-size:12.5px;fill:var(--leise)}
.peerpunkt{fill:none;stroke-width:1.6}
.peerpunkt.voll{fill:currentColor}
/* Farbe je Netzart einmal an der Gruppe, Punkt und Linie erben sie. */
.peer{cursor:pointer;outline:none;color:var(--leise)}
.peer.ipv4{color:var(--netz-ipv4)}
.peer.ipv6{color:var(--netz-ipv6)}
.peer.onion{color:var(--netz-onion)}
.peer.i2p,.peer.cjdns{color:var(--netz-i2p)}
.peerpunkt{stroke:currentColor}
/* Die grosse durchsichtige Flaeche macht das Zeigen leicht — die sichtbaren
   Teile sind duenn, das Ziel darf grosszuegig sein. */
.peerflaeche{fill:transparent}
.peer:hover .peerzeile,.peer:focus-visible .peerzeile,
.peer[data-aktiv] .peerzeile{fill:var(--text)}
.peer:hover .peerlinie,.peer:focus-visible .peerlinie,
.peer[data-aktiv] .peerlinie{stroke:currentColor;stroke-opacity:1;
stroke-width:1.6}
.peer:hover .peerflaeche,.peer:focus-visible .peerflaeche,
.peer[data-aktiv] .peerflaeche{fill:color-mix(in srgb,currentColor 9%,transparent)}
.peerlegende{display:flex;flex-wrap:wrap;gap:var(--e1) var(--e3);
color:var(--sehrleise);font-size:.68rem;margin-top:var(--e2)}
.peerlegende span{display:flex;align-items:center;gap:var(--e1)}
.netzfarbe{width:.5rem;height:.5rem;border-radius:99px;display:block;flex:none;
background:var(--leise)}
.netzfarbe.ipv4{background:var(--netz-ipv4)}
.netzfarbe.ipv6{background:var(--netz-ipv6)}
.netzfarbe.onion{background:var(--netz-onion)}
.netzfarbe.i2p,.netzfarbe.cjdns{background:var(--netz-i2p)}
/* Der Detailkasten hat feste Hoehe. Sonst springt das Layout bei jedem
   Zeigen auf einen anderen Punkt, und das wirkt billig. */
/* Feste Mindesthoehe. Ohne die springt das Layout bei jedem Zeigen auf eine
   andere Zeile, und das wirkt billig. */
.peerdetail{background:var(--vertief);border:1px solid var(--rand);
border-radius:var(--rad);padding:var(--e3);min-height:4.6rem;
margin-top:var(--e3);display:flex;flex-wrap:wrap;align-items:center;
gap:var(--e2) var(--e5)}
.peerdetail .leer{color:var(--sehrleise);font-size:.76rem;line-height:1.6}
/* Der Detailkasten laeuft als Zeile, nicht im Raster — hier gilt die
   Grundzeile nicht. */
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
/* Die Farbpunkte hier benutzen dieselben .netzfarbe-Regeln wie die Legende. */

/* ------------------------------------------------------------- Stoerung -- */
.fehlerkarte{background:var(--fl);border:1px solid var(--fehler);
border-radius:var(--rad);padding:var(--e4)}
.fehlerkarte h2{color:var(--fehler);font-size:.9rem;margin-bottom:var(--e1);
text-transform:none;letter-spacing:0}
.fehlerkarte p{color:var(--leise);font-size:.82rem;line-height:1.6}
.fehlerkarte p+p{margin-top:var(--e2)}
.fehlerkarte code{background:var(--vertief);padding:.1rem .35rem;border-radius:5px;
font-family:var(--mono);font-size:.78rem;color:var(--text)}
/* Der Hinweis auf alte Werte ist bewusst leise: Der Node ist ja da, er
   antwortet nur gerade nicht. Eine rote Karte waere hier eine Luege. */
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
/* Das Protokoll fuellt den Rest der rechten Spalte. Der Kasten darin nimmt
   sich alles, was die Kopfzeile uebrig laesst — dadurch ist er unten buendig
   mit dem Kartenraster links. */
.protokoll{display:flex;flex-direction:column;min-width:0;flex:1;
min-height:16rem}
.protokoll .kopfzeile{display:flex;justify-content:space-between;align-items:baseline;
gap:var(--e4);margin-bottom:var(--e2)}
.protokoll h2{margin:0}
.protokoll .kopfzeile{align-items:center}
.protokoll .hinweis{color:var(--sehrleise);font-size:.7rem;text-transform:none;
letter-spacing:0;font-weight:400;margin-left:auto}
/* Der Kasten nimmt sich den uebrigen Platz der Spalte, das <pre> darin liegt
   absolut und traegt deshalb nichts zur Hoehenberechnung bei. Ohne diesen
   Kniff bestimmt die Zeilenzahl des Protokolls die Hoehe der ganzen Seite:
   150 Zeilen sind 2674 px, die linke Spalte ist rund 920 px hoch. Ein
   Rasterelement wird nach seinem Inhalt bemessen, und 'min-height:0' aendert
   daran nichts — das begrenzt nur die Mindesthoehe, nicht die natuerliche. */
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


SKRIPT = r"""
/* node-dashboard — die gesamte bewegliche Schicht der Seite.
 *
 * Was sie tut: Sie holt status.json und protokoll.txt vom selben Webserver
 * und traegt die Werte nach, ohne die Seite neu zu laden. Sonst nichts.
 *
 * Was sie NICHT tut, und das ist der Punkt: Sie schickt nichts an den Node,
 * sie nimmt keine Eingaben entgegen, sie ruft keine fremde Adresse auf.
 * Beide Dateien sind statisch und liegen im selben Ordner wie diese Datei.
 *
 * Zur Sicherheit: Fremde Zeichenketten — Protokollzeilen, Peer-Adressen,
 * Kennungen anderer Knoten — werden ausschliesslich ueber textContent
 * gesetzt. Dort kann kein Markup entstehen, egal was drinsteht. Als
 * innerHTML wird nur eingesetzt, was der Generator selbst gebaut und dort
 * bereits maskiert hat.
 */
(function () {
  "use strict";

  var wurzel = document.documentElement;
  var takt = (Number(wurzel.dataset.intervall) || 30) * 1000;
  var logtakt = (Number(wurzel.dataset.logintervall) || 5) * 1000;
  var peers = [];
  var gemerkt = null;          // fest angeklickter Peer, ueberlebt die Erneuerung

  /* Ohne JS holt ein <meta refresh> die Seite regelmaessig neu. Mit JS waere
     das schaedlich: Es wuerde mitten im Zeigen auf einen Punkt neu laden. */
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
    dd.textContent = wert;      // fremder Text — niemals als Markup
    dl.appendChild(dt);
    dl.appendChild(dd);
  }

  function bytes(n) {
    var e = ["B", "KB", "MB", "GB"], i = 0;
    while (n >= 1000 && i < e.length - 1) { n /= 1000; i++; }
    return n.toFixed(1).replace(".", ",") + " " + e[i];
  }

  function dauer(s) {
    if (s >= 86400) { return Math.floor(s / 86400) + " T " + Math.floor((s % 86400) / 3600) + " Std"; }
    if (s >= 3600) { return Math.floor(s / 3600) + " Std " + Math.floor((s % 3600) / 60) + " Min"; }
    if (s >= 60) { return Math.floor(s / 60) + " Min"; }
    return s + " s";
  }

  function zeigePeer(nr) {
    var kasten = document.getElementById("peerdetail");
    if (!kasten) { return; }
    var p = peers[nr];
    kasten.textContent = "";
    if (!p) {
      var hinweis = document.createElement("p");
      hinweis.className = "leer";
      hinweis.textContent = "Auf eine Zeile zeigen für Kennung, Dienste "
        + "und Verbindungsdauer.";
      kasten.appendChild(hinweis);
      return;
    }

    var kopf = document.createElement("div");
    kopf.className = "pkopf";
    var farbe = document.createElement("i");
    /* Klasse statt style: dieselbe Regel wie im erzeugten Markup, damit hier
       und dort dieselben Farben gelten und nichts von einer Inline-Regel
       abhaengt. */
    farbe.className = "netzfarbe " + (p.netzart || "neutral");
    kopf.appendChild(farbe);
    var art = document.createElement("span");
    art.textContent = p.netzname + " · " + (p.eingehend ? "eingehend" : "ausgehend");
    kopf.appendChild(art);
    kasten.appendChild(kopf);

    var adr = document.createElement("div");
    adr.className = "padresse";
    adr.textContent = p.adresse;
    kasten.appendChild(adr);

    /* Adresse, Netzart, Latenz und Datenmenge stehen schon an der Linie.
       Hier kommt nur dazu, was dort nicht hinpasst. */
    var dl = document.createElement("dl");
    zeile(dl, "Kennung", p.version);
    zeile(dl, "Dienste", p.dienste);
    zeile(dl, "Verbunden seit", dauer(p.dauer_s));
    /* Die Antwortzeit misst die eigene Auslastung, nicht die Gegenstelle —
       deshalb steht sie hier und nicht an der Linie. */
    if (p.jetzt_ms !== null && p.jetzt_ms !== undefined) {
      zeile(dl, "Antwort gerade", p.jetzt_ms < 1000
        ? Math.round(p.jetzt_ms) + " ms"
        : (p.jetzt_ms / 1000).toFixed(1).replace(".", ",") + " s");
    }
    zeile(dl, "Empfangen", bytes(p.empfangen));
    zeile(dl, "Gesendet", bytes(p.gesendet));
    kasten.appendChild(dl);
  }

  function verdrahtePeers() {
    var karte = document.getElementById("netzkarte");
    if (!karte) { return; }
    karte.querySelectorAll(".peer").forEach(function (g) {
      var nr = Number(g.dataset.nr);
      g.addEventListener("mouseenter", function () { zeigePeer(nr); });
      g.addEventListener("focus", function () { zeigePeer(nr); });
      /* Ein Klick friert die Anzeige ein — praktisch, wenn man die Adresse
         markieren will, ohne dass sie beim Wegziehen der Maus verschwindet. */
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
    /* innerHTML ist hier vertretbar: Der Inhalt stammt aus derselben
       Datei, die dieser Webserver ohnehin als index.html ausliefert, und
       ist im Generator durch dieselbe Maskierung gelaufen. */
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

    /* Die Spaltenzahl haengt daran, wie viele Karten es gerade gibt — beim
       Sprung auf 100 % kommen drei dazu. */
    var raster = document.getElementById("z-raster");
    if (raster && daten.spalten) {
      raster.className = "raster s" + daten.spalten;
    }
    setzeZone("z-weit", daten.zonen.weit);
    setzeZone("z-voll", daten.zonen.voll);

    var stempel = document.getElementById("stempel");
    if (stempel && daten.stempel) { stempel.textContent = daten.stempel; }

    /* Die Netzkarte wird nicht ausgetauscht, solange die Maus darin liegt oder
       ein Punkt festgehalten ist. Sonst verschwindet der Punkt unter dem
       Zeiger und der Detailkasten springt weg, waehrend man ihn liest. */
    var karte = document.getElementById("netzkarte");
    if (karte && (karte.matches(":hover") || gemerkt !== null)) { return; }

    setzeZone("z-netz", daten.zonen.netz);
    peers = daten.peers || [];
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
    hole("protokoll.txt", true).then(function (text) {
      /* Reiner Text aus fremder Quelle: Bitcoin Core protokolliert die
         selbstgewaehlten Kennungen anderer Knoten. textContent, immer. */
      if (kasten.textContent !== text) {
        var oben = kasten.parentNode.scrollTop;
        kasten.textContent = text;
        kasten.parentNode.scrollTop = oben;
      }
    }).catch(function () { });
  }

  /* ------------------------------------------------------- Kopierknopf --- */

  function inZwischenablage(text) {
    /* navigator.clipboard gibt es nur in einem "sicheren Kontext", also
       ueber HTTPS oder auf localhost. Diese Seite laeuft im Heimnetz ueber
       http:// — dort ist die Schnittstelle schlicht nicht vorhanden.
       Deshalb der alte Weg als Rueckfall: ein Textfeld ausserhalb des
       Bildes, markieren, kopieren, wegwerfen. */
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
        /* Klappt es nicht, markieren wir wenigstens den Text, damit
           Strg+C greift. */
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

  /* Beim ersten Lauf einmal sofort holen: Die Seite ist serverseitig schon
     vollstaendig, aber die Peer-Angaben fuer den Detailkasten stehen nur in
     status.json — sie als Inline-Skript einzubetten wuerde die strenge
     Content-Security-Policy aufweichen, und das ist es nicht wert. */
  holeStatus();
  holeProtokoll();
  setInterval(holeStatus, takt);
  setInterval(holeProtokoll, logtakt);
})();
"""


# Fingerabdruck von Stil und Skript. Er haengt an der Adresse in der Seite
# ("stil.css?v=1a2b3c4d"), damit der Browser eine neue Fassung sofort holt und
# eine unveraenderte weiter aus seinem Zwischenspeicher nimmt.
#
# Ohne das war die Seite nach jedem Programmtausch bis zu zehn Minuten lang
# kaputt: neues HTML traf auf alte Regeln, und die Balken wuchsen zu gruenen
# Kloetzen auf, weil ihre Hoehenklasse im alten Stil noch nicht existierte.
def _fingerabdruck(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


STIL_V = _fingerabdruck(STIL)
SKRIPT_V = _fingerabdruck(SKRIPT)
BITCOIN_V = hashlib.sha256(BITCOIN_PNG).hexdigest()[:8]


def html_escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )



def bewerte_zustand(fehler, synchron, gruppen, veraltet_seit=None, anlauf=False):
    """Fasst den Gesamtzustand in ein Wort.

    Die Warnungen werden nicht einzeln aufgezaehlt, sondern aus den Karten
    eingesammelt: Jedes Feld, das als "warn" markiert ist, zaehlt. So muss
    diese Funktion nichts ueber einzelne Messwerte wissen.

    'veraltet_seit' ist der Sonderfall aus dem Toleranzfenster: Der Node
    antwortet gerade nicht, aber wir haben noch einen frischen Stand. Das ist
    kein Fehler, sondern eine Verzoegerung — und wird auch so benannt.
    """
    if fehler:
        return "fehler", "Nicht erreichbar", "Bitcoin Core antwortet nicht"
    if anlauf:
        return ("anlauf", "Warte auf Bitcoin Core",
                "Seit dem Start dieser Anzeige kam noch keine Antwort")
    if veraltet_seit is not None:
        return ("veraltet", "Node antwortet verzögert",
                f"Angezeigte Werte sind {formatiere_alter(veraltet_seit)} gemessen")

    warnungen = [f"{f[0]}: {f[1]}"
                 for g in gruppen for f in g[1]
                 if len(f) > 2 and f[2] == "warn"]

    if not synchron:
        return "sync", None, None
    if warnungen:
        anzahl = len(warnungen)
        wort = "Ein Hinweis" if anzahl == 1 else f"{anzahl} Hinweise"
        return "warn", wort, warnungen[0]
    return "ok", "Alles läuft", None


# Welche Karte wohin gehoert. Alles Uebrige laeuft ins Raster.
#   "weit" — halbe Seitenbreite, fuer Grafiken mit vielen Werten
#   "voll" — ganze Seitenbreite, damit lange Adressen in eine Zeile passen
KARTEN_WEIT = ("Volumen · 24 Stunden", "Gebührenverlauf · 24 Stunden")
KARTEN_VOLL = ("Electrum-Server",)

# Reihenfolge im Raster, ausdruecklich festgelegt. Vorher ergab sie sich
# nebenbei daraus, in welcher Reihenfolge die sammle_*-Funktionen aufgerufen
# werden — das ist keine Gestaltungsentscheidung, sondern ein Zufall.
# Was hier nicht steht, haengt sich hinten an.
KARTEN_REIHENFOLGE = (
    "System",
    "Netzwerk-Eckdaten",
    "Mempool & Gebühren",
)


def rendere_karte(gruppe, zusatzklasse=""):
    """Baut eine Karte. Kopierfelder kommen in einen eigenen Block, damit sie
    in breiten Karten neben der Werteliste stehen und nicht umbrechen."""
    gtitel, felder = gruppe[0], gruppe[1]
    fussnote = gruppe[2] if len(gruppe) > 2 else ""
    if not felder:
        return ""

    zeilen, kopierfelder = [], []
    for eintrag in felder:
        bezeichnung, wert = eintrag[0], eintrag[1]
        klasse = eintrag[2] if len(eintrag) > 2 else ""
        if klasse == "kopier":
            kopierfelder.append((bezeichnung, wert))
        elif klasse == "grafik":
            # ACHTUNG: Hier wird bewusst NICHT maskiert. Der Inhalt sind
            # SVG-Kurven und Balken, die dieses Programm selbst erzeugt.
            # Niemals Werte aus fremder Quelle als "grafik" markieren.
            if bezeichnung:
                zeilen.append(f"<dt class=grafiklabel>{html_escape(bezeichnung)}</dt>")
            zeilen.append(f"<dd class=grafik>{wert}</dd>")
        else:
            css = f" class={klasse}" if klasse in ("warn", "gut", "leer") else ""
            zeilen.append(f"<dt>{html_escape(bezeichnung)}</dt>"
                          f"<dd{css}>{html_escape(wert)}</dd>")

    klassen = "karte" + (f" {zusatzklasse}" if zusatzklasse else "")
    teile = [f'<section class="{klassen}"><h2>{html_escape(gtitel)}</h2>']
    if zeilen:
        teile.append("<dl>" + "".join(zeilen) + "</dl>")
    if kopierfelder:
        teile.append("<div class=kopierblock>")
        for bezeichnung, wert in kopierfelder:
            # Der Knopf kopiert in die Zwischenablage des Browsers, nicht
            # mehr. An den Node geht dabei nichts — das bleibt die Grenze
            # dieser Seite. Ohne JavaScript ist er verborgen, und der Text
            # laesst sich weiterhin von Hand markieren.
            teile.append(
                "<div class=kopierfeld>"
                f"<span class=kopierlabel>{html_escape(bezeichnung)}</span>"
                "<div class=kopierzeile>"
                f"<code class=kopier>{html_escape(wert)}</code>"
                '<button type=button class=kopierknopf '
                f'data-wert="{html_escape(wert)}" '
                f'aria-label="{html_escape(bezeichnung)} kopieren">'
                # Zwei versetzte Rechtecke — das uebliche Zeichen fuer
                # "Kopieren", als Pfad statt als Schriftzeichen.
                '<svg viewBox="0 0 16 16" aria-hidden="true">'
                '<rect x="5.5" y="1.5" width="9" height="11" rx="1.5"/>'
                '<path d="M10.5 14.5H3A1.5 1.5 0 0 1 1.5 13V4.5"/>'
                "</svg></button></div></div>"
            )
        teile.append("</div>")
    if fussnote:
        teile.append(f"<p class=kartenfuss>{html_escape(fussnote)}</p>")
    teile.append("</section>")
    return "".join(teile)


def baue_band(kz, stufe):
    """Vier Zahlen, die immer gelten — das Erste, was das Auge trifft."""
    # (Wert, Bezeichnung, Klasse, kleiner Zusatz darunter)
    kacheln = []

    # Die erste Kachel hat die Karte 'Blockchain' abgeloest: zwei
    # Vergleichszahlen — was der Node geprueft hat und was das Netz kennt —
    # und darunter klein, wieviel davon auf der SSD liegt.
    if kz.get("kopfzeilen"):
        # Nur der Platzbedarf. Der Pruefstand steht schon in der
        # Zustandsleiste darueber und muss hier nicht wiederholt werden.
        zusatz = formatiere_bytes(kz.get("belegt", 0)) + " auf der SSD"
        if kz.get("gepruned"):
            zusatz += " · Pruning aktiv"
        kacheln.append((
            f'{formatiere_zahl(kz.get("bloecke", 0))}'
            f'<span class=kvon>von</span>{formatiere_zahl(kz["kopfzeilen"])}',
            "Blöcke geprüft · im Netz", "", zusatz, True,
        ))

    if stufe == "sync":
        kacheln.append((formatiere_zahl(kz.get("rueckstand", 0)),
                        "Blöcke Rückstand", "", "", False))
    elif kz.get("mempool") is not None:
        kacheln.append((formatiere_zahl(kz["mempool"]), "im Mempool",
                        "", "", False))

    verbindungen = kz.get("verbindungen")
    if verbindungen is not None:
        kacheln.append((str(verbindungen), "Verbindungen",
                        "warn" if verbindungen < 8 else "gut", "", False))

    if TEMP_VERLAUF:
        temp = TEMP_VERLAUF[-1][1]
        art = "warn" if temp >= 75 else ("" if temp >= 60 else "gut")
        kacheln.append((f"{temp:.1f} °C".replace(".", ","), "Temperatur",
                        art, "", False))

    if not kacheln:
        return ""

    teile = []
    for wert, label, art, zusatz, roh in kacheln:
        # 'roh' heisst: der Wert ist Markup, das dieses Programm selbst
        # gebaut hat (das kleine "von" zwischen den Zahlen). Alles andere
        # wird maskiert — dieselbe Regel wie bei der Klasse "grafik".
        klassen = f"kachel {art} breit" if roh else f"kachel {art}"
        teile.append(
            f'<div class="{klassen.strip()}">'
            f'<div class=kwert>{wert if roh else html_escape(wert)}</div>'
            f"<div class=klabel>{html_escape(label)}</div>"
            + (f"<div class=kzusatz>{html_escape(zusatz)}</div>" if zusatz else "")
            + "</div>"
        )
    return "".join(teile)


def formatiere_prozent(wert, stellen=2):
    """Deutsches Komma, durchgaengig — auch in der grossen Zahl oben."""
    return f"{wert:.{stellen}f}".replace(".", ",")


def baue_zustandsleiste(stufe, wort, zusatz, fortschritt, kz):
    teile = ["<section class=zustand>"]
    prozent = formatiere_prozent(fortschritt)

    if stufe == "sync":
        unterzeile = "Synchronisiert die Blockchain"
        if kz.get("stand"):
            unterzeile += " · " + kz["stand"]
        teile.append(
            '<div class=zlinks><span class=punkt></span><div>'
            f'<div class=zwort>{prozent}&nbsp;%</div>'
            f"<div class=zzusatz>{html_escape(unterzeile)}</div>"
            "</div></div>"
        )
        rechts_zahl = formatiere_zahl(kz.get("bloecke", 0))
        rechts_text = f'von {formatiere_zahl(kz.get("kopfzeilen", 0))} Bl&ouml;cken'
    else:
        teile.append(
            '<div class=zlinks><span class=punkt></span><div>'
            f"<div class=zwort>{html_escape(wort)}</div>"
            f'<div class=zzusatz>{html_escape(zusatz) if zusatz else "Node synchron, keine Auff&auml;lligkeiten"}</div>'
            "</div></div>"
        )
        rechts_zahl = formatiere_zahl(kz.get("bloecke", 0))
        alter = kz.get("blockalter")
        rechts_text = ("Block · " + formatiere_alter(alter)) if alter else "Blockh&ouml;he"

    teile.append(
        f'<div class=zrechts><div class=zzahl>{rechts_zahl}</div>'
        f'<div class=zlabel>{rechts_text}</div></div>'
    )

    if stufe == "sync":
        teile.append("<div class=balkenbox>")
        teile.append(baue_balken(fortschritt / 100, "", hoehe=10))

        # Tempo und Restzeit sind waehrend der Synchronisation die einzigen
        # Zahlen, auf die es wirklich ankommt. Sie gehoeren hierher und nicht
        # klein in eine Karte weiter unten.
        tempo, restzeit = kz.get("tempo"), kz.get("restzeit")
        if tempo is not None:
            teile.append(
                '<div class=zfuss>'
                f'<span>{html_escape(tempo)}</span>'
                f'<span class=zrest>noch etwa {html_escape(restzeit)}</span>'
                "</div>"
            )
        else:
            teile.append('<div class=zfuss><span>Tempo wird noch gemessen</span>'
                         "<span class=zrest></span></div>")

        kurve = baue_kurve()
        if kurve:
            svg, beschriftung = kurve
        else:
            svg = baue_geruest("Verlauf, wird noch gemessen", 300, 54)
            beschriftung = "Verlauf ab etwa 15 Minuten Laufzeit"
        teile.append(
            f'<div class=kurve>{svg}'
            f"<span class=kurvenfuss>{html_escape(beschriftung)}</span></div>"
        )
        teile.append("</div>")

    teile.append("</section>")
    return "".join(teile)


def baue_stoerung(fehler, veraltet_seit, anlauf=False, tor=None):
    """Die rote Karte gibt es erst, wenn der Node wirklich weg ist.

    Vorher steht dort ein leiser Hinweis, dass die Zahlen ein wenig alt sind.
    Das ist der ganze Unterschied zwischen 'der Node ist kaputt' und 'der Node
    schreibt gerade seinen Zwischenspeicher auf die SSD'.
    """
    # Die Tor-Meldung steht ueber allem anderen: Wenn gleich der Node neu
    # startet, ist das die wichtigere Nachricht.
    vorn = baue_tormeldung(tor)

    if anlauf:
        # Gleich nach dem Start des Dienstes gibt es noch keinen alten Stand,
        # an dem sich das Toleranzfenster festhalten koennte. Trotzdem ist
        # eine ausbleibende erste Antwort kein Ausfall — bitcoind schreibt
        # vermutlich gerade seinen Zwischenspeicher.
        return vorn + (
            '<div class=veraltet><span class=punkt></span><div>'
            "<b>Noch keine Antwort vom Node.</b> Diese Anzeige wurde eben erst "
            "gestartet und wartet auf die erste Auskunft. Während der "
            "Erstsynchronisation kann das eine Minute dauern.</div></div>"
        )
    if veraltet_seit is not None:
        return vorn + (
            '<div class=veraltet><span class=punkt></span><div>'
            f"<b>Node antwortet gerade nicht.</b> Angezeigt wird der letzte "
            f"gemessene Stand von {html_escape(formatiere_alter(veraltet_seit))}. "
            "Während der Erstsynchronisation ist das normal: Bitcoin Core hält "
            "seine Abfrageschnittstelle an, solange es den Zwischenspeicher auf "
            "die SSD schreibt.</div></div>"
        )
    if not fehler:
        return vorn

    if "nicht erreichbar" in fehler or "timed out" in fehler:
        ueberschrift = "Node nicht erreichbar"
        rat = ("Pr&uuml;fen mit <code>systemctl status bitcoind</code> oder "
               "<code>journalctl -u bitcoind -n 50</code>.")
    elif "401" in fehler:
        ueberschrift = "Anmeldung am Node abgelehnt"
        rat = ("Das Passwort in <code>/etc/node-dashboard.conf</code> passt nicht "
               "zum <code>rpcauth</code>-Eintrag in der bitcoin.conf.")
    else:
        ueberschrift = "Node antwortet mit einem Fehler"
        rat = ("Der Node l&auml;uft, lehnt aber eine Abfrage ab. M&ouml;glicherweise "
               "fehlt der Befehl in der <code>rpcwhitelist</code>.")
    return vorn + (
        f"<div class=fehlerkarte><h2>{ueberschrift}</h2>"
        f"<p>{html_escape(fehler)}</p><p>{rat}</p></div>"
    )


# Die Farbe steckt in einer Klasse, nicht in einem style-Attribut: Inline-Stil
# waere durch die Content-Security-Policy verworfen und die Punkte blieben
# farblos. Siehe den Kommentar bei baue_balken.
LEGENDE = (
    ("onion", "Tor"),
    ("ipv4", "IPv4"),
    ("ipv6", "IPv6"),
    ("i2p", "I2P"),
)


def raster_spalten(anzahl):
    """Spaltenzahl so waehlen, dass die letzte Reihe moeglichst voll wird.

    Sechs Karten in vier Spalten lassen zwei Loecher; in drei Spalten keins.
    Wenn spaeter die Netzkarte und die 24-Stunden-Karten dazukommen, waehlt
    dieselbe Regel von selbst wieder vier.
    """
    if anzahl <= 2:
        return max(1, anzahl)
    beste, wenigste = 4, 99
    for spalten in (4, 3, 2):
        rest = (-anzahl) % spalten
        if rest < wenigste:
            beste, wenigste = spalten, rest
    return beste


def baue_netzzone(peers, ersatzfelder=None, gesperrt=False):
    """Die ganze Netzkarte als fertiger Block: Grafik, Legende, Detailkasten.

    Ohne Peer-Daten — 'getpeerinfo' ist auf dem Pi bis 06-tor.sh nicht
    freigeschaltet — tritt an die Stelle der Zeichnung die alte Werteliste.
    Sonst waeren die Verbindungsdaten wochenlang nirgends mehr zu sehen,
    seit die Karte 'Netzwerk' entfallen ist.
    """
    svg = baue_netzkarte(peers)
    if not svg:
        if not ersatzfelder:
            return ""
        zeilen = "".join(
            f"<dt>{html_escape(b)}</dt><dd>{html_escape(w)}</dd>"
            for b, w, _ in ersatzfelder
        )
        # Zwei sehr verschiedene Gruende fuer dieselbe leere Liste, und sie
        # duerfen nicht denselben Text bekommen: Entweder ist die Abfrage
        # nicht freigeschaltet — dann muss dastehen, was zu tun ist —, oder
        # der Node hat gerade nicht geantwortet, dann ist es eine Verzoegerung.
        if gesperrt:
            kurz = "Zeichnung folgt nach der Freischaltung"
            fuss = ("Die Netzgrafik braucht die Abfrage <code>getpeerinfo</code>. "
                    "Sie wird von <code>06-tor.sh</code> freigeschaltet.")
        else:
            kurz = "Gegenstellen werden abgefragt"
            fuss = ("Der Node hat die Liste der Gegenstellen noch nicht "
                    "geliefert. Während der Synchronisation dauert das "
                    "gelegentlich länger als das Zeitlimit.")
        return (
            '<section class="karte netz">'
            "<div class=kopfzeile><h2>Verbundene Knoten</h2>"
            f"<span class=hinweis>{kurz}</span>"
            f"</div><dl class=netzersatz>{zeilen}</dl>"
            f"<p class=kartenfuss>{fuss}</p></section>"
        )

    # Die Eckzahlen laufen als schmales Band in der Kopfzeile mit, damit die
    # Zeichnung darunter die ganze Breite bekommt.
    werte = "".join(
        f"<span><b>{html_escape(w)}</b> {html_escape(b)}</span>"
        for b, w, _ in peer_kennzahlen(peers)
    )
    legende = "".join(
        f'<span><i class="netzfarbe {art}"></i>{name}</span>'
        for art, name in LEGENDE
    )

    return (
        '<section class="karte netz">'
        "<div class=kopfzeile><h2>Verbundene Knoten</h2>"
        f"<div class=netzzahlen>{werte}</div></div>"
        f"<div id=netzkarte>{svg}</div>"
        f"<div class=peerlegende>{legende}"
        '<span><i class="netzfarbe neutral"></i>gefüllt = ausgehend</span>'
        "</div>"
        '<div class=peerdetail id=peerdetail>'
        '<p class=leer>Auf eine Zeile zeigen für Kennung, Dienste und '
        'Verbindungsdauer.</p>'
        "</div></section>"
    )


def protokolltext(protokolle):
    """Die Protokollzeilen als reiner Text — ohne jedes Markup.

    Genau so werden sie auch ausgeliefert und im Browser ueber textContent
    gesetzt. Fremder Text kann dann per Bauart kein Markup werden.
    """
    if not protokolle:
        return "Keine Protokollquelle eingerichtet."
    if len(protokolle) == 1:
        return protokolle[0][1]
    return "\n\n".join(f"--- {dienst} ---\n{inhalt}" for dienst, inhalt in protokolle)


def baue_zonen(cfg, fortschritt, synchron, gruppen, fehler=None,
               protokolle=None, kennzahlen=None, peers=None, veraltet_seit=None,
               anlauf=False, updates=None, tor=None):
    """Baut alle beweglichen Teile der Seite einzeln.

    Genau diese Stuecke gehen auch in status.json. Dadurch kann die Seite im
    Browser nachgefuehrt werden, ohne dass es hier zwei Wege gibt, aus
    denselben Daten Markup zu machen — und ohne dass die beiden auseinander
    laufen koennen.
    """
    kz = kennzahlen or {}
    stufe, wort, zusatz = bewerte_zustand(fehler, synchron, gruppen,
                                          veraltet_seit, anlauf)

    schmal = [g for g in gruppen
              if g[1] and g[0] not in KARTEN_WEIT and g[0] not in KARTEN_VOLL]
    schmal.sort(key=lambda g: KARTEN_REIHENFOLGE.index(g[0])
                if g[0] in KARTEN_REIHENFOLGE else len(KARTEN_REIHENFOLGE))
    weit = [g for g in gruppen if g[1] and g[0] in KARTEN_WEIT]
    voll = [g for g in gruppen if g[1] and g[0] in KARTEN_VOLL]

    return {
        "stufe": stufe,
        "wort": wort,
        "kopf": baue_kopfinfo(updates, kz),
        "zustand": baue_zustandsleiste(stufe, wort, zusatz, fortschritt, kz),
        "stoerung": baue_stoerung(fehler, veraltet_seit, anlauf, tor),
        "band": baue_band(kz, stufe),
        "netz": baue_netzzone(peers or [], kz.get("netzfelder"),
                              "getpeerinfo" in VERBOTEN),
        "spalten": raster_spalten(len(schmal)),
        "raster": "".join(rendere_karte(g) for g in schmal),
        "weit": "".join(rendere_karte(g) for g in weit),
        "voll": "".join(rendere_karte(g, "voll") for g in voll),
    }


def baue_seite(cfg, fortschritt, synchron, gruppen, fehler=None,
               protokolle=None, kennzahlen=None, peers=None, veraltet_seit=None,
               zonen=None, anlauf=False, updates=None, tor=None):
    jetzt = datetime.now(timezone.utc).astimezone()
    rechner = html_escape(socket.gethostname())
    intervall = html_escape(cfg["INTERVALL"])
    log_takt = html_escape(str(cfg.get("LOG_INTERVALL", "5")))

    if zonen is None:
        zonen = baue_zonen(cfg, fortschritt, synchron, gruppen, fehler,
                           protokolle, kennzahlen, peers, veraltet_seit,
                           anlauf, updates, tor)
    stufe, wort = zonen["stufe"], zonen["wort"]

    # Der Titel im Browser-Tab zeigt den Fortschritt mit — praktisch, wenn die
    # Seite tagelang in einem Hintergrund-Tab liegt.
    titel = (f"{formatiere_prozent(fortschritt, 1)} % · {rechner}" if stufe == "sync"
             else f"{wort} · {rechner}")

    punkt = {"ok": "%232fd39a", "warn": "%23f0b23f", "fehler": "%23f2645f",
             "veraltet": "%23f0b23f", "anlauf": "%23f0b23f",
             "sync": "%232fd39a"}[stufe]
    favicon = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' "
               f"viewBox='0 0 32 32'><circle cx='16' cy='16' r='11' fill='{punkt}'/></svg>")

    # Die Richtlinie steht zusaetzlich als Kopfzeile in der nginx-Einrichtung.
    # Hier steht sie noch einmal, damit die Datei auch dann geschuetzt ist,
    # wenn sie jemand ohne diesen Webserver ausliefert.
    csp = ("default-src 'none'; style-src 'self'; script-src 'self'; "
           "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
           "form-action 'none'; frame-ancestors 'none'")

    teile = [
        "<!doctype html>",
        f'<html lang=de data-stufe="{stufe}" data-frisch=nein '
        f'data-intervall="{intervall}" data-logintervall="{log_takt}">',
        "<head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        '<meta name=referrer content=no-referrer>',
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">',
        # Ohne JavaScript holt sich die Seite hierueber neu. Mit JavaScript
        # wird dieses Element beim Start entfernt.
        f'<meta http-equiv=refresh content="{intervall}">',
        f'<link rel=icon href="{favicon}">',
        f'<link rel=stylesheet href="stil.css?v={STIL_V}">',
        f"<title>{html_escape(titel)}</title>",
        "</head><body><div class=huelle>",
        f'<header><h1><span class=marke></span>{rechner} '
        f"<b>· Bitcoin Fullnode</b></h1>",
        f'<div id=z-kopf>{zonen["kopf"]}</div>',
        f'<div class=kopfrechts><span class=puls></span>'
        f'<span id=stempel>{jetzt.strftime("%d.%m.%Y %H:%M:%S")}</span></div>'
        "</header>",
        # Zwei Spalten: links alles Ausgewertete, rechts das rohe Protokoll
        # ueber die volle Hoehe. Auf schmalen Schirmen faellt das Raster von
        # selbst wieder auf eine Spalte zusammen.
        "<div class=inhalt><div class=links>",
        f'<div id=z-zustand>{zonen["zustand"]}</div>',
        f'<div id=z-stoerung>{zonen["stoerung"]}</div>',
        f'<div class=band id=z-band>{zonen["band"]}</div>',
        f'<div class=weit id=z-weit>{zonen["weit"]}</div>',
        f'<div class="raster s{zonen["spalten"]}" id=z-raster>{zonen["raster"]}</div>',
        f'<div id=z-voll>{zonen["voll"]}</div>',
    ]

    teile.append("</div>")     # Ende der linken Spalte

    # Rechte Spalte: oben die Gegenstellen, darunter das Protokoll. Beide
    # teilen sich die Hoehe der linken Spalte je zur Haelfte — dadurch liegen
    # ihre Kanten auf denen der Karten links, ohne dass irgendwo eine feste
    # Hoehe steht, die beim naechsten Umbau nicht mehr passt.
    teile.append("<div class=rechts>")
    teile.append(f'<div id=z-netz>{zonen["netz"]}</div>')

    teile.append(
        '<section class="karte protokoll"><div class=kopfzeile>'
        "<h2>Protokoll</h2>"
        f'<span class=hinweis>neueste oben · alle {log_takt} s</span>'
        "</div>"
        # Der Kasten drumherum ist nicht schmueckend: Er nimmt sich den
        # uebrigen Platz, und das <pre> darin liegt absolut. Nur so kann die
        # Laenge des Protokolls die Hoehe der Seite nicht bestimmen.
        f'<div class=logbox><pre><code id=logtext>'
        f"{html_escape(protokolltext(protokolle))}</code></pre></div></section>"
    )

    teile.append("</div>")     # Ende der rechten Spalte
    teile.append("</div>")     # Ende der Zwei-Spalten-Aufteilung
    teile.append(
        f"<footer>node-dashboard {VERSION} · nur lesender Zugriff · "
        f"Daten alle {intervall} s, Protokoll alle {log_takt} s</footer>"
    )
    teile.append(f'</div><script src="dash.js?v={SKRIPT_V}"></script>'
                 "</body></html>")
    return "".join(teile)


def baue_status(cfg, zonen, peers, jetzt, veraltet_seit=None, fortschritt=0.0):
    """Die statische Lese-API.

    Das ist bewusst keine Schnittstelle im ueblichen Sinn: Es ist eine Datei,
    die der Generator schreibt und nginx ausliefert. Der Webserver kennt den
    Node weiterhin nicht und nimmt weiterhin nichts entgegen. Das
    Sicherheitsmodell bleibt damit genau das alte.
    """
    rechner = socket.gethostname()
    stufe, wort = zonen["stufe"], zonen["wort"]
    titel = (f"{formatiere_prozent(fortschritt, 1)} % · {rechner}" if stufe == "sync"
             else f"{wort} · {rechner}")

    schlanke_peers = []
    for p in peers or []:
        schlanke_peers.append({
            "adresse": p["adresse"],
            "netzname": NETZNAMEN.get(p["netz"], p["netz"]),
            "netzart": p["netz"] if p["netz"] in NETZFARBEN else "neutral",
            "eingehend": p["eingehend"],
            "ping_ms": p["ping_ms"],
            "jetzt_ms": p.get("jetzt_ms"),
            "dauer_s": p["dauer_s"],
            "version": p["version"],
            "dienste": p["dienste"],
            "gesendet": p["gesendet"],
            "empfangen": p["empfangen"],
        })

    return json.dumps({
        "erzeugt": int(jetzt.timestamp()),
        "stempel": jetzt.strftime("%d.%m.%Y %H:%M:%S"),
        "titel": titel,
        "stufe": stufe,
        "veraltet": veraltet_seit is not None,
        "fortschritt": round(fortschritt, 3),
        "spalten": zonen["spalten"],
        "zonen": {
            "kopf": zonen["kopf"],
            "zustand": zonen["zustand"],
            "stoerung": zonen["stoerung"],
            "band": zonen["band"],
            "netz": zonen["netz"],
            "raster": zonen["raster"],
            "weit": zonen["weit"],
            "voll": zonen["voll"],
        },
        "peers": schlanke_peers,
    }, ensure_ascii=False, separators=(",", ":"))


# ================================================================== Schreiben
# Was zuletzt geschrieben wurde. Unveraendertes wird nicht noch einmal auf die
# SSD geschrieben — bei einem Takt von fuenf Sekunden spart das im Dauerbetrieb
# eine erhebliche Zahl sinnloser Schreibvorgaenge.
ZULETZT = {}


def schreibe_datei_atomar(zielordner, dateiname, inhalt):
    """Erst in eine temporaere Datei, dann umbenennen. So sieht der Webserver
    niemals eine halb geschriebene Seite."""
    if ZULETZT.get(dateiname) == inhalt:
        return False

    os.makedirs(zielordner, exist_ok=True)
    ziel = os.path.join(zielordner, dateiname)
    fd, temp = tempfile.mkstemp(dir=zielordner, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(inhalt)
        os.chmod(temp, 0o644)
        os.replace(temp, ziel)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    ZULETZT[dateiname] = inhalt
    return True


def schreibe_bytes_atomar(zielordner, dateiname, inhalt):
    """Dasselbe fuer Dateien, die kein Text sind — derzeit nur das Logo."""
    if ZULETZT.get(dateiname) == inhalt:
        return False
    os.makedirs(zielordner, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=zielordner, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(inhalt)
        os.chmod(temp, 0o644)
        os.replace(temp, os.path.join(zielordner, dateiname))
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    ZULETZT[dateiname] = inhalt
    return True


def schreibe_atomar(zielordner, inhalt):
    schreibe_datei_atomar(zielordner, "index.html", inhalt)


def schreibe_beiwerk(cfg):
    """Stil und Skript liegen als eigene Dateien neben der Seite.

    Sie aendern sich nur, wenn dieses Programm ausgetauscht wird — die
    Aenderungspruefung sorgt dafuer, dass sie danach genau einmal geschrieben
    werden und nicht bei jedem Durchlauf.
    """
    schreibe_datei_atomar(cfg["OUT_DIR"], "stil.css", STIL)
    schreibe_datei_atomar(cfg["OUT_DIR"], "dash.js", SKRIPT)
    schreibe_bytes_atomar(cfg["OUT_DIR"], "bitcoin.png", BITCOIN_PNG)

    # Rueckstand aus Fassung 2.x: Damals steckte das Protokoll als eigene
    # Seite in einem Rahmen. Sie wird nicht mehr erzeugt und soll auch nicht
    # als veraltete Datei liegenbleiben. Angefasst wird ausschliesslich eine
    # Datei, die dieses Programm frueher selbst geschrieben hat.
    alt = os.path.join(cfg["OUT_DIR"], "protokoll.html")
    if os.path.exists(alt):
        try:
            os.unlink(alt)
        except OSError:
            pass


def schreibe_protokolltext(cfg, protokolle=None):
    if protokolle is None:
        protokolle = sammle_protokoll(cfg)
    schreibe_datei_atomar(cfg["OUT_DIR"], "protokoll.txt",
                          protokolltext(protokolle))


# ====================================================================== Ablauf
def einmal(cfg):
    """Ein vollstaendiger Durchlauf: abfragen, bauen, schreiben.

    Der Sonderfall ist das Toleranzfenster. Antwortet der Node nicht, wird
    nicht sofort alles verworfen — der letzte gute Stand bleibt stehen, bis
    mehrere Versuche hintereinander gescheitert sind.
    """
    global FEHLER_IN_FOLGE

    gruppen = []
    fortschritt, synchron, fehler = 0.0, False, None
    kennzahlen, peers, veraltet_seit, anlauf = {}, [], None, False

    try:
        toleranz = max(1, int(cfg.get("TOLERANZ", 3)))
    except (TypeError, ValueError):
        toleranz = 3
    try:
        peers_max = max(1, int(cfg.get("PEERS_MAX", 64)))
    except (TypeError, ValueError):
        peers_max = 64

    try:
        fortschritt, synchron, node_gruppen, kennzahlen = sammle_node(cfg)
        gruppen.extend(node_gruppen)
        peers = sammle_peers(cfg, peers_max)
        FEHLER_IN_FOLGE = 0
        LETZTER_STAND.update({
            "zeit": time.time(),
            "fortschritt": fortschritt,
            "synchron": synchron,
            "gruppen": node_gruppen,
            "kennzahlen": kennzahlen,
            "peers": peers,
        })
    except RpcFehler as e:
        FEHLER_IN_FOLGE += 1
        if FEHLER_IN_FOLGE >= toleranz:
            fehler = str(e)
        elif LETZTER_STAND.get("gruppen"):
            # Noch im Toleranzfenster: alten Stand weiterzeigen, aber sagen,
            # dass er alt ist.
            fortschritt = LETZTER_STAND["fortschritt"]
            synchron = LETZTER_STAND["synchron"]
            gruppen.extend(LETZTER_STAND["gruppen"])
            kennzahlen = LETZTER_STAND["kennzahlen"]
            peers = LETZTER_STAND["peers"]
            veraltet_seit = time.time() - LETZTER_STAND["zeit"]
        else:
            # Gleich nach dem Start gibt es noch keinen alten Stand. Frueher
            # fiel das Toleranzfenster hier sofort auf die rote Fehlerkarte
            # zurueck — genau das war am 23.08.2026 nach jedem Neustart des
            # Dienstes zu sehen, waehrend der Node daneben weiterlief.
            anlauf = True

    electrum = sammle_electrum(cfg)
    if electrum:
        gruppen.append(electrum)

    # System vor den Aktualisierungen: erst der Zustand des Geraets,
    # dann der Hinweis, ob neue Ausgaben bereitliegen.
    gruppen.append(sammle_system(cfg))

    # Die Versionspruefung ist keine Karte mehr, sondern eine Zeile in der
    # Kopfzeile — sie sagte im Regelfall dreimal "aktuell" und belegte dafuer
    # den Platz einer vollen Karte.
    updates = sammle_updates(cfg)
    tor = sammle_tor(cfg)
    protokolle = sammle_protokoll(cfg)

    jetzt = datetime.now(timezone.utc).astimezone()
    zonen = baue_zonen(cfg, fortschritt, synchron, gruppen, fehler,
                       protokolle, kennzahlen, peers, veraltet_seit,
                       anlauf, updates, tor)

    schreibe_beiwerk(cfg)
    schreibe_atomar(
        cfg["OUT_DIR"],
        baue_seite(cfg, fortschritt, synchron, gruppen, fehler, protokolle,
                   kennzahlen, peers, veraltet_seit, zonen, anlauf,
                   updates, tor),
    )
    schreibe_datei_atomar(
        cfg["OUT_DIR"], "status.json",
        baue_status(cfg, zonen, peers, jetzt,
                    veraltet_seit if not anlauf else 0.0, fortschritt),
    )
    schreibe_protokolltext(cfg, protokolle)
    return fehler


def main():
    p = argparse.ArgumentParser(description="Erzeugt eine statische Statusseite fuer den Node.")
    p.add_argument("--config", default="/etc/node-dashboard.conf")
    p.add_argument("--once", action="store_true",
                   help="Nur einmal erzeugen statt dauerhaft zu laufen")
    args = p.parse_args()

    cfg = lies_konfiguration(args.config)

    if args.once:
        fehler = einmal(cfg)
        print("Seite geschrieben nach", os.path.join(cfg["OUT_DIR"], "index.html"))
        if fehler:
            print("Hinweis:", fehler, file=sys.stderr)
        return 0

    intervall = max(5, int(cfg["INTERVALL"]))
    log_takt = max(1, min(intervall, int(cfg.get("LOG_INTERVALL", 5))))

    # Zwei Takte in einer Schleife: die Abfrage des Nodes selten, das
    # Protokoll oft. Kein zweiter Prozess, keine Nebenlaeufigkeit.
    letzte_hauptseite = 0.0
    while True:
        try:
            if time.time() - letzte_hauptseite >= intervall:
                einmal(cfg)
                letzte_hauptseite = time.time()
            else:
                schreibe_protokolltext(cfg)
        except Exception as e:  # noqa: BLE001 — der Dienst darf nie sterben
            print(f"Fehler beim Erzeugen: {e}", file=sys.stderr)
        time.sleep(log_takt)


if __name__ == "__main__":
    sys.exit(main() or 0)
