#!/usr/bin/env python3
"""Probelauf des Dashboards ohne Raspberry Pi.

Startet die Attrappe, laesst node-dashboard.py eine echte Seite erzeugen und
prueft sie. Das Ergebnis landet in tests/ausgabe/ und laesst sich im Browser
oeffnen — so ist eine Gestaltungsaenderung sichtbar, bevor sie auf den Pi geht.

Geprueft wird:
  * HTML ist wohlgeformt, Skript nur als eigene Datei, keine Inline-Handler
  * jede erwartete Karte ist da und steht in der richtigen Zone
  * die Netzkarte enthaelt einen Punkt je Gegenstelle
  * fremder Text (Kennung eines anderen Knotens) landet nirgends als Markup
  * status.json ist gueltig und deckt sich mit der Seite
  * das Toleranzfenster haelt den letzten Stand, statt sofort Alarm zu geben
  * Kopierfelder brechen nicht um
  * Zahlen benutzen durchgaengig das deutsche Komma
  * die 24-Stunden-Daten werden nur einmal geholt, nicht bei jedem Durchlauf

Aufruf:
    python3 tests/probelauf.py                # Lage 'synchron'
    python3 tests/probelauf.py --lage sync    # Erstsynchronisation
    python3 tests/probelauf.py --lage leer
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

HIER = Path(__file__).resolve().parent
PROJEKT = HIER.parent
AUSGABE = HIER / "ausgabe"
PORT = 18332

# Die Attrappe schickt diese Kennung als 'subver' eines fremden Knotens mit.
# Wenn sie irgendwo unmaskiert in der Seite auftaucht, kann ein fremder Node
# Markup in das Dashboard schreiben.
GIFT = "<b>Knoten</b>"

fehler_gesamt = []


def melde(bestanden, text, zusatz=""):
    zeichen = "ok  " if bestanden else "FEHL"
    print(f"  [{zeichen}] {text}{('  — ' + zusatz) if zusatz else ''}")
    if not bestanden:
        fehler_gesamt.append(text)


def lade_dashboard():
    spec = importlib.util.spec_from_file_location(
        "node_dashboard", PROJEKT / "node-dashboard.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def ersetze_systemteile(nd, lage):
    """Alles, was es nur auf dem Pi gibt, durch feste Werte ersetzen.

    Bewusst eng gehalten: Nur Dateien unter /sys, /proc und systemd-Aufrufe.
    Die RPC-Schicht bleibt unangetastet und spricht wirklich ueber HTTP.
    """
    echt_lesen = nd.lies_datei
    # Erfundene Adresse in der richtigen Laenge (56 Zeichen plus .onion).
    # Hier stand einmal die echte Adresse dieses Nodes — in einer Datei, die
    # in ein Repository gehoert. Eine Onion-Adresse ist kein Geheimnis im
    # kryptografischen Sinn, aber sie ist die Anschrift eines Dienstes, der
    # niemandem sonst offenstehen soll.
    onion = ("beispielbeispielbeispielbeispiel"
             "beispielbeispielbeispiel.onion")

    def lesen(pfad, standard=None):
        pfad = str(pfad)
        if "thermal" in pfad:
            return "69634"
        if "onion" in pfad or "hostname" in pfad:
            return onion
        return echt_lesen(pfad, standard)

    nd.lies_datei = lesen
    nd.dienst_laeuft = lambda name: True
    nd.port_offen = lambda host, port: True
    nd.eigene_ip = lambda: "192.168.1.50"

    echt_exists = os.path.exists
    nd.os.path.exists = lambda p: True if ".service" in str(p) else echt_exists(p)

    # Ein echtes Protokoll, nicht eine Zeile. Vorher lieferte die Attrappe
    # fuer jeden Aufruf "throttled=0x0" — auch fuer journalctl. Dadurch war
    # das Protokoll im Test 18 Pixel hoch und auf dem Pi 2673, und ein
    # Layoutfehler, der nur bei vollem Protokoll auftritt, blieb unsichtbar.
    muster = ("2026-08-23T14:04:16+02:00 btcnode bitcoind[62345]: "
              "2026-08-23T12:04:16Z UpdateTip: new best="
              "00000000000000000118e7c0614044d2846a57fc347fb2ae684415e8fdefb293 "
              "height={h} version=0x20000000 log2_work=85.493329 tx=167769911 "
              "date='2016-11-03T12:23:13Z' progress=0.112807 "
              "cache=204.1MiB(1483920txo)")
    protokoll = "\n".join(muster.format(h=437184 - i) for i in range(150))

    def lauf(befehl, *a, **k):
        class Ergebnis:
            returncode = 0
            stderr = ""
            stdout = (protokoll if "journalctl" in " ".join(map(str, befehl))
                      else "throttled=0x0")
        return Ergebnis()

    nd.subprocess.run = lauf

    # Eine Stunde Temperaturverlauf, damit die Kurve etwas zu zeichnen hat
    jetzt = time.time()
    for i in range(90):
        nd.TEMP_VERLAUF.append(
            (jetzt - (90 - i) * nd.TEMP_TAKT, 56 + 13 * (i / 89) + 1.6 * math.sin(i / 3))
        )


def schreibe_konfiguration():
    AUSGABE.mkdir(parents=True, exist_ok=True)
    pfad = HIER / "probe.conf"
    pfad.write_text(
        f"RPC_HOST=127.0.0.1\nRPC_PORT={PORT}\nRPC_USER=dashboard\n"
        f"RPC_PASSWORD=probe\nOUT_DIR={AUSGABE}\nDATA_DIR={PROJEKT}\n"
        "ELECTRS_PORT=50001\nINTERVALL=30\nLOG_DIENSTE=bitcoind\n"
        "LOG_ZEILEN=40\nLOG_INTERVALL=5\nTOLERANZ=3\nPEERS_MAX=64\n"
        f"UPDATE_DATEI={HIER / 'updates-probe.json'}\n",
        encoding="utf-8")
    (HIER / "updates-probe.json").write_text(
        '{"geprueft": %d, "eintraege": ['
        '{"name": "Bitcoin Core", "installiert": "31.1", "neueste": "31.1"},'
        '{"name": "electrs", "installiert": "0.11.1", "neueste": "0.11.1"}]}'
        % int(time.time() - 1260), encoding="utf-8")
    return str(pfad)


# ------------------------------------------------------------------ Pruefungen
class Formpruefer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stapel, self.maengel = [], []

    def handle_startendtag(self, tag, attrs):
        """<circle .../> und Verwandte schliessen sich selbst.

        Ohne diese Ueberschreibung ruft HTMLParser nacheinander Anfang und
        Ende auf, und das SVG der Netzkarte gilt faelschlich als kaputt.
        """
        return

    def handle_starttag(self, tag, attrs):
        if tag not in ("meta", "br", "hr", "img", "link", "input"):
            self.stapel.append(tag)

    def handle_endtag(self, tag):
        if self.stapel and self.stapel[-1] == tag:
            self.stapel.pop()
        else:
            self.maengel.append(f"</{tag}> ohne passenden Anfang")


def zonen_aus(seite):
    """Ordnet die Kartentitel den Zonen zu, in denen sie stehen."""
    zonen = {}
    stellen = [(m.start(), m.group(1))
               for m in re.finditer(
                   r'id=z-(kopf|zustand|stoerung|band|netz|raster|weit|voll)',
                   seite)]
    stellen.append((len(seite), "ende"))
    for i in range(len(stellen) - 1):
        anfang, name = stellen[i]
        stueck = seite[anfang:stellen[i + 1][0]]
        zonen[name] = re.findall(r"<h2>(.*?)</h2>", stueck)
    zonen["protokoll"] = re.findall(
        r'class="karte protokoll">.*?<h2>(.*?)</h2>', seite, re.S)
    return zonen


def pruefe_seite(seite, lage, nd=None):
    print("\n  Aufbau")
    pruefer = Formpruefer()
    pruefer.feed(seite)
    melde(not pruefer.maengel and not pruefer.stapel, "HTML ist wohlgeformt",
          ", ".join(pruefer.maengel + [f"<{t}> offen" for t in pruefer.stapel]))

    skripte = re.findall(r"<script([^>]*)>", seite)
    melde(len(skripte) == 1 and re.fullmatch(r' src="dash\.js\?v=[0-9a-f]{8}"',
                                             skripte[0]),
          "Skript nur als eigene Datei eingebunden", str(skripte))

    # Fingerabdruck an der Adresse: Ohne ihn liefert der Browser nach einem
    # Programmtausch bis zu zehn Minuten lang alte Regeln zu neuem Markup aus.
    # Auf dem Pi wurden daraus am 23.08.2026 gruene Kloetze in den Karten.
    melde(re.search(r'href="stil\.css\?v=[0-9a-f]{8}"', seite) is not None,
          "Stil traegt einen Fingerabdruck gegen den Zwischenspeicher")
    melde("onclick" not in seite and "onmouse" not in seite
          and "javascript:" not in seite,
          "keine Ereignisbehandlung im Markup")
    melde("Content-Security-Policy" in seite and "'unsafe-inline'" not in seite,
          "strenge Content-Security-Policy ohne unsafe-inline")

    # Die CSP verwirft style-Attribute im Markup, nicht nur <style>-Bloecke.
    # Am 23.08.2026 stand der Fortschrittsbalken deshalb auf dem Pi immer auf
    # voll: seine Breite war ein Inline-Stil. Geometrie gehoert in
    # SVG-Attribute, Farbe in eine Klasse.
    inline = re.findall(r'style="[^"]*"', seite)
    melde(not inline,
          "kein style-Attribut im Markup (die CSP wuerde es verwerfen)",
          " | ".join(sorted(set(inline))[:4]))
    melde("<style" not in seite, "kein Stilblock in der Seite")

    zonen = zonen_aus(seite)
    print("\n  Zonen")
    for name, karten in zonen.items():
        print(f"        {name:<10} {', '.join(karten) or '(leer)'}")

    # Reihenfolge, nicht nur Vorhandensein: Sie ist bewusst gewaehlt und
    # ergab sich vorher zufaellig aus der Aufrufreihenfolge der sammle_*.
    erwartet_raster = ["System", "Netzwerk-Eckdaten", "Mempool &amp; Gebühren"]
    melde(zonen.get("raster") == erwartet_raster,
          "Karten stehen in der festgelegten Reihenfolge",
          " | ".join(zonen.get("raster", [])))

    # Diese drei Karten sind aufgeloest worden. Ihre Angaben stehen jetzt im
    # Kennzahlenband, in 'Verbundene Knoten' und in der Kopfzeile.
    for weg in ("Blockchain", "Netzwerk", "Aktualisierungen"):
        melde(weg not in zonen.get("raster", []),
              f"Karte '{weg}' ist aufgeloest")

    melde("Verbundene Knoten" in " ".join(zonen.get("netz", [])),
          "Netzkarte steht vor dem Kartenraster")

    print("\n  Kopfzeile")
    kopf = re.search(r'<div id=z-kopf>(.*?)</div></div>', seite, re.S)
    inhalt = kopf.group(1) if kopf else ""
    melde("Core 31.1" in inhalt, "Fassung von Bitcoin Core in der Kopfzeile",
          re.sub(r"<[^>]+>", " ", inhalt).strip())
    melde("electrs 0.11.1" in inhalt, "Fassung von electrs in der Kopfzeile")
    melde("läuft seit" in inhalt, "Laufzeit des Nodes in der Kopfzeile")
    melde("kopfinfo gut" in inhalt,
          "alles aktuell wird gruen und ohne Pfeil gezeigt")
    melde(zonen.get("protokoll") == ["Protokoll"], "Protokollkarte vorhanden")

    # Zweiteilung: links alles Ausgewertete, rechts das rohe Protokoll. Die
    # Kopfzeile steht darueber und laeuft ueber beide Spalten.
    print("\n  Zweispaltiger Aufbau")
    melde(re.search(r"</header>\s*<div class=inhalt><div class=links>", seite)
          is not None,
          "Kopfzeile ueber beiden Spalten, danach die Aufteilung")
    # Genau an der Spaltengrenze trennen, nicht am naechstbesten </div> —
    # sonst reicht der vermeintlich linke Teil in die rechte Spalte hinein
    # und die Pruefungen darunter sind wertlos.
    grenze = seite.find("<div class=rechts>")
    melde(grenze > 0, "die rechte Spalte ist als eigener Block angelegt")
    anfang = seite.find("<div class=links>")
    inhalt_links = seite[anfang:grenze] if grenze > anfang > 0 else ""
    inhalt_rechts = seite[grenze:] if grenze > 0 else ""

    for zone in ("z-zustand", "z-band", "z-weit", "z-raster", "z-voll"):
        melde(zone in inhalt_links, f"'{zone}' steht in der linken Spalte")
    for zone in ("z-netz", "logtext"):
        melde(zone in inhalt_rechts, f"'{zone}' steht in der rechten Spalte")
    melde("z-netz" not in inhalt_links and "logtext" not in inhalt_links,
          "nichts davon steht doppelt in der linken Spalte")

    # Das Protokoll darf die Seitenhoehe nicht bestimmen. 150 Zeilen sind
    # rund 2670 px, die linke Spalte etwa 920 — ohne Begrenzung waere die
    # Seite dreimal so lang wie noetig. Am 23.08.2026 war sie das.
    print("\n  Hoehe des Protokolls")
    zeilen = (AUSGABE / "protokoll.txt").read_text(encoding="utf-8").count("\n") + 1
    natuerlich = zeilen * 11.5 * 1.55
    melde(zeilen >= 100,
          f"die Attrappe liefert ein volles Protokoll ({zeilen} Zeilen, "
          f"{natuerlich:.0f} px natuerliche Hoehe)")

    eng = (AUSGABE / "stil.css").read_text(encoding="utf-8")
    eng = eng.replace("\n", "").replace(" ", "")
    melde("<div class=logbox>" in seite,
          "das <pre> steckt in einem Kasten, der die Hoehe vorgibt")
    melde(".protokollpre{position:absolute;inset:0" in eng,
          "und liegt darin absolut, traegt also nichts zur Hoehe bei")
    melde(".logbox{flex:1;min-height:0" in eng,
          "der Kasten nimmt sich den uebrigen Platz der Spalte")

    stil = (AUSGABE / "stil.css").read_text(encoding="utf-8")
    knapp = stil.replace("\n", "").replace(" ", "")

    # Schaltflaechen duerfen ausschliesslich die Zwischenablage anfassen.
    # Alles, was den Node erreichen koennte, gehoert nicht auf diese Seite.
    knoepfe = re.findall(r"<button([^>]*)>", seite)
    melde(all("kopierknopf" in k for k in knoepfe),
          f"Schaltflaechen nur zum Kopieren ({len(knoepfe)} Stueck)",
          " | ".join(k for k in knoepfe if "kopierknopf" not in k))
    melde("<form" not in seite and "action=" not in seite,
          "kein Formular, keine Aktion im Markup")

    melde("grid-template-columns:repeat(4,minmax(0,1fr))" in knapp,
          "das Kennzahlenband hat feste vier Spalten")

    # 'auto-fill' legt so viele Spuren an, wie hineinpassen, und laesst die
    # ueberzaehligen leer — drei Karten in einer vierspurigen Spalte lassen
    # dann rechts ein Viertel frei. Genau das war am 23.08.2026 zu sehen.
    melde("repeat(auto-fit,minmax(19rem,1fr))" in knapp,
          "das Kartenraster benutzt auto-fit, nicht auto-fill")
    melde("repeat(auto-fill" not in knapp,
          "und nirgends mehr auto-fill")

    # Die Spaltenzahl muss sich nach der Breite der Spalte richten, nicht
    # nach der des Fensters — die linke Spalte ist nur halb so breit.
    melde("container-type:inline-size" in knapp,
          "die Spalten sind Groessenkontext fuer ihre Karten")
    melde(knapp.count("@container") >= 2,
          f"Raster und 24-Stunden-Karten fragen die Spaltenbreite ab "
          f"({knapp.count('@container')} Abfragen)")
    melde("@media(min-width:72rem)and(max-width:95.99rem)" not in knapp,
          "keine am Fenster bemessene Spaltenzahl mehr")

    # Eine leere Zone ist unsichtbar, zaehlt in der Flex-Spalte aber als
    # Element und erzeugt einen zweiten Abstand — das sah aus wie ein
    # ungleicher Rand. Ohne Inhalt muss sie ganz verschwinden.
    melde("<div id=z-stoerung></div>" in seite or "class=veraltet" in seite
          or "fehlerkarte" in seite,
          "die Stoerungszone ist entweder leer oder gefuellt, nie halb")
    melde(".links>*:empty,.rechts>*:empty{display:none}" in knapp,
          "leere Zonen erzeugen keinen Abstand")
    # Der Fehler, der am 23.08.2026 die ganze Seite breiter machte als das
    # Fenster: Rasterelemente haben min-width:auto, und das <pre> des
    # Protokolls hat mit 'white-space:pre' die Mindestbreite seiner laengsten
    # Zeile. Ohne diese Regel schiebt es die Spalte aus dem Bild.
    melde(".links>*,.rechts>*{min-width:0}" in knapp,
          "die Spalten koennen nicht breiter werden als ihr Anteil")
    melde("Electrum-Server" in zonen.get("voll", []),
          "Electrum-Karte in voller Breite")

    # Die 24-Stunden-Karten stehen immer da, damit das Layout vollstaendig
    # ist. Ohne Daten tragen sie ein Geruest.
    for karte in ("Volumen · 24 Stunden", "Gebührenverlauf · 24 Stunden"):
        melde(karte in zonen.get("weit", []), f"Karte '{karte}' in halber Breite")

    if lage != "synchron":
        print("\n  Platzhalter")
        gerueste = re.findall(r'class="minikurve geruest"', seite)
        melde(len(gerueste) >= 3,
              f"Geruest statt Grafik, wo Daten fehlen ({len(gerueste)} Stueck)")

        # Der wichtigste Punkt: keine erfundenen Zahlen. In den Karten ohne
        # Daten darf nichts stehen, was man fuer eine Messung halten koennte.
        for karte in ("Volumen · 24 Stunden", "Gebührenverlauf · 24 Stunden"):
            block = re.search(
                r"<h2>" + re.escape(karte) + r"</h2>(.*?)</section>", seite, re.S)
            werte = re.findall(r"<dd[^>]*>([^<]*)</dd>", block.group(1) if block else "")
            melde(all(w.strip() in ("—", "") for w in werte),
                  f"'{karte}' zeigt Striche statt erfundener Werte",
                  " | ".join(w for w in werte if w.strip() not in ("—", "")))
        melde("Erscheint, sobald die Kette steht" in seite,
              "die Karten sagen, worauf sie warten")

    print("\n  Kennzahlenband")
    kacheln = re.findall(r"<div class=klabel>(.*?)</div>", seite)
    melde(len(kacheln) >= 3, f"{len(kacheln)} Kacheln im Band", ", ".join(kacheln))

    print("\n  Netzkarte")
    if lage == "leer":
        # Ohne Peer-Daten muss die Karte die Verbindungswerte trotzdem
        # zeigen — die Karte 'Netzwerk' gibt es nicht mehr, die sie frueher
        # getragen hat.
        melde("netzersatz" in seite, "ohne Gegenstellen steht die Ersatzliste da")
        melde("Verbindungen" in seite, "die Verbindungszahl bleibt sichtbar")
        # Der Node hat geantwortet, nur mit einer leeren Liste. Dann darf
        # dort nicht stehen, die Abfrage sei nicht freigeschaltet.
        melde("06-tor.sh" not in seite,
              "keine Freischaltungs-Meldung, wenn der Node geantwortet hat")
        melde("werden abgefragt" in seite,
              "stattdessen der Hinweis auf die ausstehende Antwort")
    else:
        punkte = re.findall(r'<g class="peer [\w ]+" tabindex="0" data-nr="(\d+)"',
                            seite)
        melde(len(punkte) == 19, f"eine Zeile je Gegenstelle ({len(punkte)} von 19)")
        melde([int(n) for n in punkte] == list(range(len(punkte))),
              "die Zeilen sind fortlaufend nummeriert")
        melde("id=peerdetail" in seite, "Detailkasten ist angelegt")

        # Die Nabe traegt das Bitcoin-Zeichen als Pfad. Als Schriftzeichen
        # (U+20BF) waere es in vielen Schriften ein leeres Kaestchen.
        if nd is not None:
            pruefe_bitcoinzeichen(nd, seite)
        melde("Verbundene Knoten" in seite, "Karte traegt eine Ueberschrift")

        # Der Faecher haengt links und rechts an der Nabe. Bei ungerader
        # Anzahl bekommt die linke Seite die eine Zeile mehr.
        zeilen = re.findall(r'<text x="([\d.]+)"[^>]*text-anchor="(\w+)"'
                            r'[^>]*class="peerzeile"', seite)
        links = [x for x, a in zeilen if a == "end"]
        rechts = [x for x, a in zeilen if a == "start"]
        melde(len(links) == 10 and len(rechts) == 9,
              f"Aufteilung auf beide Seiten ({len(links)} links, {len(rechts)} rechts)")
        melde(all(float(x) < 600 for x in links)
              and all(float(x) > 600 for x in rechts),
              "linke Beschriftungen stehen links der Nabe, rechte rechts")

        # Beschriftung und Punkt muessen auf derselben Hoehe sitzen, sonst
        # wirkt der Faecher schief. Frueher stand der Text acht Pixel ueber
        # der Linie, weil dort noch eine Waagerechte verlief.
        paare = re.findall(
            r'<circle cx="[\d.]+" cy="([\d.]+)" r="4\.5"[^>]*/>'
            r'<text x="[\d.]+" y="([\d.]+)"[^>]*class="peerzeile"', seite)
        melde(len(paare) == 19, f"{len(paare)} Punkt-Text-Paare gefunden")
        schief = [(p, t) for p, t in paare if abs(float(p) - float(t)) > 0.01]
        melde(not schief, "Punkt und Beschriftung liegen auf gleicher Hoehe",
              " | ".join(f"{p} gegen {t}" for p, t in schief[:3]))
        melde('dominant-baseline="central"' in seite,
              "die Schrift ist dabei senkrecht zentriert")

        # Die Eckdaten sollen an der Linie stehen, nicht erst beim Zeigen.
        texte = re.findall(r'class="peerzeile">([^<]*)</text>', seite)
        melde(all("·" in t for t in texte),
              "jede Zeile traegt Adresse, Netzart und Kennzahlen",
              texte[0] if texte else "")

        # Ein SVG schneidet alles ab, was ueber sein viewBox hinausragt. Am
        # 23.08.2026 war die Breite fest auf 1200 gesetzt, und bei Latenzen
        # mit sechs Stellen verschwand das Ende jeder rechten Zeile.
        rahmen = re.search(r'class=netzkarte viewBox="0 0 (\d+) (\d+)"', seite)
        melde(rahmen is not None, "die Netzkarte hat ein viewBox")
        if rahmen:
            breite = int(rahmen.group(1))
            felder = re.findall(
                r'<text x="([\d.]+)" y="[\d.]+" text-anchor="(\w+)"'
                r' class="peerzeile">([^<]*)</text>', seite)
            zeichen = 12.5 * 0.63          # muss zu SCHRIFT_PEER passen
            heraus = []
            for x, anker, text in felder:
                x, spanne = float(x), len(text) * zeichen
                links, rechts = ((x - spanne, x) if anker == "end"
                                 else (x, x + spanne))
                if links < 0 or rechts > breite:
                    heraus.append(text[:28])
            melde(not heraus,
                  f"alle {len(felder)} Beschriftungen passen in die Zeichnung",
                  " | ".join(heraus[:3]))

    print("\n  Kopierfelder")
    felder = re.findall(
        r'<span class=kopierlabel>(.*?)</span>.*?<code class=kopier[^>]*>(.*?)</code>',
        seite, re.S)
    melde(len(felder) == 2, f"zwei Adressen zum Kopieren ({len(felder)} gefunden)")
    for bezeichnung, wert in felder:
        melde("\n" not in wert and "<" not in wert,
              f"'{bezeichnung}' steht als reiner Text da", f"{len(wert)} Zeichen")

    # Der Text muss umbrechen statt aus der Karte zu laufen: Eine
    # Onion-Adresse ist 70 Zeichen lang und passt in keine halbe Kartenbreite.
    css = (AUSGABE / "stil.css").read_text(encoding="utf-8")
    eng_css = css.replace("\n", "").replace(" ", "")
    melde("white-space:nowrap" not in eng_css.replace("white-space:nowrap;", "", 0)
          or "word-break:break-all" in eng_css,
          "Kopierfelder brechen um statt ueberzulaufen")
    melde("overflow-wrap:anywhere" in eng_css,
          "und brechen notfalls mitten im Wort")

    knoepfe = re.findall(r'class=kopierknopf data-wert="([^"]*)"', seite)
    melde(len(knoepfe) == len(felder),
          f"je Adresse ein Kopierknopf ({len(knoepfe)} zu {len(felder)})")
    melde(all(w for w in knoepfe), "jeder Knopf kennt seinen Wert")
    skript_roh = (AUSGABE / "dash.js").read_text(encoding="utf-8")
    # navigator.clipboard gibt es nur ueber HTTPS. Diese Seite laeuft im
    # Heimnetz ueber http:// — ohne Rueckfall waere der Knopf dort wirkungslos.
    melde("execCommand" in skript_roh and "isSecureContext" in skript_roh,
          "mit Rueckfall, weil es ueber http:// keine Zwischenablage-Schnittstelle gibt")

    print("\n  Darstellung der Zahlen")
    # Versionsnummern sind keine Dezimalzahlen — "31.1.0" bleibt so, wie es ist.
    keine_zahlen = ("version", "bitcoin core", "electrs", "stand der", "kennung")
    verdaechtig = []
    for bez, wert in re.findall(r"<dt[^>]*>([^<]*)</dt><dd[^>]*>([^<]*)</dd>", seite):
        if any(k in bez.lower() for k in keine_zahlen):
            continue
        # Punkt als Dezimaltrenner: Ziffer, Punkt, ein bis zwei Ziffern, danach
        # keine weitere Ziffer. Tausenderpunkte wie 963.634 bleiben unbehelligt.
        if re.search(r"\d\.\d{1,2}(?!\d)", wert):
            verdaechtig.append(f"{bez}: {wert}")
    melde(not verdaechtig, "durchgaengig deutsches Komma in den Karten",
          " | ".join(verdaechtig))

    # Die grossen Zahlen stehen nicht in einer <dl>. Genau dort ist am
    # 23.08.2026 ein "11.24 %" durchgerutscht, weil die Pruefung darueber
    # nur Karten angesehen hat.
    gross = re.findall(r"<div class=(?:zwort|zzahl|kwert)>([^<]*)</div>", seite)
    schlecht = [w for w in gross if re.search(r"\d\.\d{1,2}(?!\d)", w)]
    melde(not schlecht, f"deutsches Komma auch in den {len(gross)} grossen Zahlen",
          " | ".join(schlecht))

    # Shell-Skripte geben bewusst ASCII aus, die Seite nicht. Das ist hier
    # dreimal danebengegangen ("Bloecke", "Schaetzung", "waehrend"), deshalb
    # eine feste Liste der Woerter, die erfahrungsgemaess durchrutschen.
    umschriften = ("waehrend", "moeglich", "Bloecke", "bloecke", "Schaetzung",
                   "Eintraege", "Pruefung", "naechste", "Groesse", "koennen",
                   "muessen", "gehoert", "zurueck", "ueber ", "fuer ")
    gefunden = [w for w in umschriften if w in seite]
    melde(not gefunden, "keine ASCII-Umschrift im sichtbaren Text",
          " | ".join(gefunden))

    print("\n  Sicherheit")
    melde("rpcpassword" not in seite.lower() and "probe" not in seite,
          "kein Zugangsdatum in der Seite")
    melde(GIFT not in seite,
          "Kennung eines fremden Knotens landet nicht als Markup in der Seite")
    grafiken = re.findall(r"<dd class=grafik>(.*?)</dd>", seite, re.S)
    melde(all(g.lstrip().startswith("<svg") or g.lstrip().startswith("<span")
              for g in grafiken),
          f"alle {len(grafiken)} Grafikfelder enthalten nur erzeugtes SVG")


def pruefe_bitcoinzeichen(nd, seite):
    """Das Zeichen ist ein Bild, keine nachgebaute Geometrie.

    Es war dreimal von Hand aus Rechtecken und Boegen zusammengesetzt und
    dreimal falsch — und jeder Versuch kostete eine Runde ueber ein
    Bildschirmfoto, weil sich hier nichts ansehen laesst. Ein Logo ist keine
    Geometrieaufgabe. Geprueft wird jetzt, dass das Bild da ist, richtig
    sitzt und in den Kreis passt.
    """
    treffer = re.search(
        r'<image href="bitcoin\.png\?v=([0-9a-f]{8})" x="([\d.]+)" y="([\d.]+)" '
        r'width="(\d+)" height="(\d+)"/>', seite)
    melde(treffer is not None, "das Bitcoin-Zeichen steht als Bild in der Nabe")
    if not treffer:
        return

    fingerabdruck, x, y, breite, hoehe = treffer.groups()
    x, y, breite, hoehe = float(x), float(y), int(breite), int(hoehe)
    melde(fingerabdruck == nd.BITCOIN_V,
          "es traegt den Fingerabdruck gegen den Zwischenspeicher")
    melde(breite == hoehe == nd.MARKE_R * 2,
          f"es ist quadratisch und {nd.MARKE_R * 2} Einheiten gross "
          f"({breite} x {hoehe})")

    # Mittig auf der Nabe der Netzkarte
    rahmen = re.search(r'class=netzkarte viewBox="0 0 (\d+) (\d+)"', seite)
    if rahmen:
        kb, kh = int(rahmen.group(1)), int(rahmen.group(2))
        mitte_x, mitte_y = x + breite / 2, y + hoehe / 2
        melde(abs(mitte_x - kb / 2) < 1 and abs(mitte_y - kh / 2) < 1,
              "es sitzt genau auf der Nabe",
              f"{mitte_x:.1f}/{mitte_y:.1f} statt {kb / 2:.1f}/{kh / 2:.1f}")

    # Die Datei selbst
    bild = AUSGABE / "bitcoin.png"
    melde(bild.exists(), "bitcoin.png wurde geschrieben")
    if bild.exists():
        roh = bild.read_bytes()
        melde(roh[:8] == b"\x89PNG\r\n\x1a\n", "und ist ein gueltiges PNG")
        melde(roh == nd.BITCOIN_PNG, "und stimmt mit der eingebetteten Fassung ueberein")
        melde(len(roh) < 8000, f"und bleibt klein ({len(roh)} Bytes)")


def pruefe_status(lage):
    """status.json ist die Lese-API. Sie muss zur Seite passen."""
    print("\n  Lese-API")
    pfad = AUSGABE / "status.json"
    melde(pfad.exists(), "status.json wurde geschrieben")
    if not pfad.exists():
        return
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        melde(False, "status.json ist gueltiges JSON", str(e))
        return
    melde(True, "status.json ist gueltiges JSON", f"{pfad.stat().st_size} Bytes")

    for schluessel in ("erzeugt", "stempel", "titel", "stufe", "zonen", "peers"):
        melde(schluessel in daten, f"Feld '{schluessel}' vorhanden")

    zonen = daten.get("zonen", {})
    melde(set(zonen) == {"kopf", "zustand", "stoerung", "band", "netz",
                          "raster", "weit", "voll"},
          "alle Zonen enthalten", ", ".join(sorted(zonen)))

    peers = daten.get("peers", [])
    if lage == "leer":
        melde(peers == [], "ohne Gegenstellen bleibt die Liste leer")
    else:
        melde(len(peers) == 19, f"{len(peers)} Gegenstellen in der Liste")
        melde(any(GIFT in p.get("version", "") for p in peers),
              "fremde Kennung steht als reiner Wert in der Liste")
        markup = [z for z in zonen.values() if GIFT in z]
        melde(not markup,
              "fremde Kennung steht in keiner der HTML-Zonen")

    text = (AUSGABE / "protokoll.txt")
    melde(text.exists(), "protokoll.txt wurde geschrieben")
    if text.exists():
        inhalt = text.read_text(encoding="utf-8")
        melde("<html" not in inhalt and "<pre" not in inhalt,
              "protokoll.txt ist reiner Text ohne Markup")

    for name in ("stil.css", "dash.js"):
        melde((AUSGABE / name).exists(), f"{name} wurde geschrieben")

    # Ein Tippfehler in dash.js wuerde die gesamte bewegliche Schicht still
    # abschalten — die Seite saehe richtig aus und wuerde nur nie aktueller.
    # Kein anderer Test hier faende das. Wenn node da ist, pruefen wir es.
    try:
        r = subprocess.run(["node", "--check", str(AUSGABE / "dash.js")],
                           capture_output=True, text=True, timeout=20)
        melde(r.returncode == 0, "dash.js ist syntaktisch gueltig",
              (r.stderr or "").strip().split("\n")[0])
    except (OSError, subprocess.SubprocessError):
        print("  [ --  ] dash.js nicht geprueft (node nicht vorhanden)")


def pruefe_tormeldung(nd, cfg):
    """Der Waechter meldet ueber eine Datei, das Dashboard zeigt sie an.

    Wichtig ist vor allem der Fehlerfall: Wenn 06-tor.sh scheitert, muss das
    unuebersehbar sein — dann steht der Node moeglicherweise halb umgestellt da.
    """
    print("\n  Tor-Automatik")
    # Bewusst ausserhalb des Projektordners: Der liegt je nach Umgebung auf
    # einem Mount, auf dem sich Dateien nicht wieder loeschen lassen.
    ordner = tempfile.mkdtemp(prefix="torprobe-")
    pfad = Path(ordner) / "tor.json"
    cfg["TOR_DATEI"] = str(pfad)

    faelle = [
        ("wartet", "", False, "waehrend der Synchronisation still"),
        ("bereit", "meldung warn", True, "kuendigt die Umstellung an"),
        ("laeuft", "meldung warn", True, "meldet die laufende Umstellung"),
        ("fehler", "fehlerkarte", True, "zeigt das Scheitern deutlich"),
        ("fertig", "", False, "nach getaner Arbeit wieder still"),
    ]
    try:
        for zustand, marke, sichtbar, text in faelle:
            pfad.write_text(json.dumps({
                "zustand": zustand, "meldung": "Probe",
                "treffer": 3, "noetig": 6, "zeit": int(time.time()),
            }), encoding="utf-8")
            nd.einmal(cfg)
            seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
            block = re.search(r"<div id=z-stoerung>(.*?)</div>\s*<div class=band",
                              seite, re.S)
            inhalt = block.group(1) if block else ""
            melde((marke in inhalt) if sichtbar else (inhalt.strip() == ""),
                  f"Zustand '{zustand}': {text}")

        # Der Abbruchbefehl muss dastehen, solange noch etwas abzubrechen ist.
        pfad.write_text(json.dumps({"zustand": "bereit", "meldung": "",
                                    "treffer": 3, "noetig": 6}), encoding="utf-8")
        nd.einmal(cfg)
        seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
        melde("08-tor-automatik.sh --aus" in seite,
              "der Abbruchbefehl steht in der Ankuendigung")
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
        cfg.pop("TOR_DATEI", None)
        nd.einmal(cfg)


def pruefe_peers_bei_aussetzer(nd, cfg):
    """Ein Aussetzer bei getpeerinfo darf die Peer-Liste nicht loeschen.

    Am 23.08.2026 tat er genau das: Bei zwoelf Sekunden Antwortzeit lief die
    Abfrage gelegentlich ins Zeitlimit, das Dashboard warf alle Gegenstellen
    weg und zeigte stattdessen "wird von 06-tor.sh freigeschaltet" — obwohl
    die Methode laengst frei war.
    """
    print("\n  Gegenstellen bei Aussetzern")
    nd.einmal(cfg)
    vorher = len(json.loads(
        (AUSGABE / "status.json").read_text(encoding="utf-8"))["peers"])
    melde(vorher > 0, f"im Normalfall stehen {vorher} Gegenstellen da")

    echtes_rpc = nd.rpc

    def stolpernd(c, methode, parameter=None):
        if methode == "getpeerinfo":
            raise nd.RpcFehler("Node nicht erreichbar: timed out")
        return echtes_rpc(c, methode, parameter)

    nd.rpc = stolpernd
    try:
        nd.einmal(cfg)
        daten = json.loads((AUSGABE / "status.json").read_text(encoding="utf-8"))
        melde(len(daten["peers"]) == vorher,
              f"nach einem Aussetzer stehen sie weiterhin da ({len(daten['peers'])})")
        seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
        melde("06-tor.sh" not in seite,
              "und es erscheint keine Freischaltungs-Meldung")
    finally:
        nd.rpc = echtes_rpc
        nd.einmal(cfg)


def pruefe_verbotene_methoden(nd, cfg):
    """Was der Node ablehnt, darf nicht alle 30 Sekunden neu gefragt werden.

    Jede abgelehnte Abfrage erzeugt in bitcoind eine Protokollzeile
    "RPC User dashboard not allowed to call method …" — und die landet direkt
    in der Protokollanzeige des Dashboards. Auf dem Pi standen dort am
    23.08.2026 im Minutentakt zwei davon.
    """
    print("\n  Abgelehnte Methoden")
    nd.VERBOTEN.clear()

    # Gezaehlt wird auf der Leitung, nicht am Funktionsaufruf: Die Sperre sitzt
    # innerhalb von rpc(), ein Zaehler davor wuerde jeden Aufruf mitzaehlen und
    # nie etwas beweisen.
    versuche = {"n": 0}
    echt_oeffnen = nd.urllib.request.urlopen

    def zaehlend(*a, **k):
        versuche["n"] += 1
        return echt_oeffnen(*a, **k)

    nd.urllib.request.urlopen = zaehlend
    try:
        for _ in range(5):
            try:
                nd.rpc(cfg, "gibtesnicht")
            except nd.RpcFehler:
                pass
        melde(versuche["n"] == 1,
              f"fuenf Aufrufe erreichen den Node genau einmal ({versuche['n']}x)")
        melde("gibtesnicht" in nd.VERBOTEN, "die Ablehnung ist gemerkt")

        # Nach Ablauf der Frist wird wieder gefragt — sonst bliebe eine
        # nachtraeglich freigeschaltete Methode fuer immer aus.
        nd.VERBOTEN["gibtesnicht"] = time.time() - nd.VERBOTEN_ERNEUT - 1
        try:
            nd.rpc(cfg, "gibtesnicht")
        except nd.RpcFehler:
            pass
        melde(versuche["n"] == 2,
              f"nach Ablauf der Frist wird erneut probiert ({versuche['n']}x)")
    finally:
        nd.urllib.request.urlopen = echt_oeffnen
        nd.VERBOTEN.clear()


def pruefe_toleranz(nd, cfg):
    """Ein einzelner Aussetzer darf nicht als Ausfall gelten.

    Der Node haelt seinen RPC-Thread an, waehrend er den dbcache auf die SSD
    schreibt. Genau das hat frueher 'Nicht erreichbar' und Blockhoehe null
    ausgeloest, obwohl nebenan im Protokoll die Synchronisation weiterlief.
    """
    print("\n  Toleranzfenster")

    def blockhoehe():
        seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
        treffer = re.search(r"<div class=zzahl>(.*?)</div>", seite)
        return treffer.group(1) if treffer else None

    vorher = blockhoehe()
    echtes_rpc = nd.rpc
    nd.rpc = lambda *a, **k: (_ for _ in ()).throw(
        nd.RpcFehler("Node nicht erreichbar: timed out"))

    try:
        for versuch in (1, 2):
            nd.einmal(cfg)
            seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
            melde("fehlerkarte" not in seite,
                  f"Aussetzer {versuch}: keine rote Fehlerkarte")
            melde("class=veraltet" in seite,
                  f"Aussetzer {versuch}: leiser Hinweis auf alte Werte")
            melde(blockhoehe() == vorher,
                  f"Aussetzer {versuch}: Blockhoehe bleibt stehen",
                  f"{vorher} -> {blockhoehe()}")

        nd.einmal(cfg)
        seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
        melde("fehlerkarte" in seite,
              "nach drei Aussetzern in Folge steht die Fehlerkarte da")

        # Der Fall direkt nach einem Neustart des Dienstes: kein alter Stand,
        # an dem sich das Fenster festhalten koennte. Frueher schlug es hier
        # sofort Alarm, obwohl der Node lief.
        nd.LETZTER_STAND.clear()
        nd.FEHLER_IN_FOLGE = 0
        nd.einmal(cfg)
        seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
        melde("fehlerkarte" not in seite,
              "frisch gestartet: keine Fehlerkarte beim ersten Aussetzer")
        melde('data-stufe="anlauf"' in seite,
              "frisch gestartet: die Seite sagt, dass sie noch wartet")
    finally:
        nd.rpc = echtes_rpc
        nd.FEHLER_IN_FOLGE = 0
        nd.einmal(cfg)          # sauberen Stand wiederherstellen


def pruefe_zwischenspeicher(nd, cfg):
    """Die 24-Stunden-Daten duerfen nur einmal geholt werden.

    144 Bloecke bei jedem Durchlauf neu abzufragen wuerde den Node alle
    30 Sekunden unnoetig beschaeftigen.
    """
    print("\n  Zwischenspeicher")
    zaehler = {"n": 0}
    echtes_rpc = nd.rpc

    def zaehlend(c, methode, parameter=None):
        if methode in ("getblockstats", "getblockheader"):
            zaehler["n"] += 1
        return echtes_rpc(c, methode, parameter)

    # Der Durchlauf davor hat die Speicher schon gefuellt — fuer die Messung
    # muessen sie leer sein, sonst misst man nichts.
    nd.BLOCKDATEN.clear()
    nd.SCHWIERIGKEIT.clear()

    nd.rpc = zaehlend
    nd.hole_schwierigkeit(cfg, 915312)
    nd.hole_blockdaten(cfg, 915312)
    erste = zaehler["n"]
    zaehler["n"] = 0
    nd.hole_schwierigkeit(cfg, 915312)
    nd.hole_blockdaten(cfg, 915312)
    nd.rpc = echtes_rpc
    melde(erste > 100, f"Erstbefuellung holt {erste} Bloecke")
    melde(zaehler["n"] == 0,
          f"zweiter Durchlauf holt nichts nach ({zaehler['n']} Abfragen)")


def pruefe_schreibsparsamkeit(nd, cfg):
    """Unveraendertes darf nicht erneut auf die SSD geschrieben werden."""
    print("\n  Schreibvorgaenge")
    geschrieben = []
    echt = nd.schreibe_datei_atomar

    def mitzaehlend(ordner, name, inhalt):
        ergebnis = echt(ordner, name, inhalt)
        if ergebnis:
            geschrieben.append(name)
        return ergebnis

    nd.schreibe_datei_atomar = mitzaehlend
    try:
        nd.schreibe_beiwerk(cfg)
        melde(not geschrieben,
              "Stil und Skript werden nicht bei jedem Durchlauf neu geschrieben",
              ", ".join(geschrieben))
    finally:
        nd.schreibe_datei_atomar = echt


def main():
    zerleger = argparse.ArgumentParser()
    zerleger.add_argument("--lage", default="synchron",
                          choices=["synchron", "sync", "leer"])
    argumente = zerleger.parse_args()

    print(f"\n=== Probelauf, Lage '{argumente.lage}' ===")
    attrappe = subprocess.Popen(
        [sys.executable, str(HIER / "attrappe.py"),
         "--port", str(PORT), "--lage", argumente.lage],
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
            print("  Attrappe kam nicht hoch.")
            return 1

        nd = lade_dashboard()
        ersetze_systemteile(nd, argumente.lage)
        cfg = nd.lies_konfiguration(schreibe_konfiguration())
        nd.einmal(cfg)

        seite = (AUSGABE / "index.html").read_text(encoding="utf-8")
        print(f"\n  {len(seite)} Bytes nach {AUSGABE / 'index.html'}")
        pruefe_seite(seite, argumente.lage, nd)
        pruefe_status(argumente.lage)
        pruefe_schreibsparsamkeit(nd, cfg)
        pruefe_tormeldung(nd, cfg)
        if argumente.lage != "leer":
            pruefe_peers_bei_aussetzer(nd, cfg)
        pruefe_verbotene_methoden(nd, cfg)
        pruefe_toleranz(nd, cfg)
        if argumente.lage == "synchron":
            pruefe_zwischenspeicher(nd, cfg)
    finally:
        attrappe.terminate()
        attrappe.wait(timeout=5)

    print()
    if fehler_gesamt:
        print(f"=== {len(fehler_gesamt)} Pruefung(en) gescheitert ===")
        for f in fehler_gesamt:
            print(f"    {f}")
        return 1
    print("=== alles bestanden ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
