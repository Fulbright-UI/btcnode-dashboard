#!/usr/bin/env python3
"""Attrappe eines Bitcoin-Core-RPC-Servers.

Antwortet auf demselben Weg wie der echte Node — HTTP, JSON-RPC, Basic Auth —
damit node-dashboard.py im Test genau den Code durchlaeuft, der spaeter auf dem
Pi laeuft. Ein Ersatz fuer die Funktion rpc() wuerde die Fehlerbehandlung,
die Anmeldung und das Auspacken der Antwort ueberspringen.

Nur Standardbibliothek, keine Installation noetig.

Aufruf:
    python3 tests/attrappe.py [--port 18332] [--lage synchron|sync|leer]

Lagen:
    synchron   Kette vollstaendig, 24-Stunden-Daten vorhanden — der Normalfall
    sync       Erstsynchronisation bei 4,6 % — wie der Pi es gerade zeigt
    leer       Node antwortet, liefert aber nichts Verwertbares
"""

import argparse
import base64
import json
import math
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BENUTZER, PASSWORT = "dashboard", "probe"

SPITZE = 915312
LAGE = "synchron"

# Feste Streuung: derselbe Aufruf liefert dieselbe Kurve. Sonst waere nicht
# unterscheidbar, ob eine Aenderung am Bild vom Code kommt oder vom Zufall.
STREUUNG = random.Random(20260823)


def blockstats(hoehe):
    i = hoehe - (SPITZE - 143)
    return {
        "height": hoehe,
        "time": int(time.time()) - (143 - i) * 600,
        "total_out": int((900 + 700 * math.sin(i / 9)
                          + STREUUNG.uniform(-150, 150)) * 1e8),
        "txs": int(2600 + 900 * math.sin(i / 7)),
        "feerate_percentiles": [1, 2,
                                round(2.5 + 4 * abs(math.sin(i / 11)), 1), 9, 20],
    }


def antwort(methode, parameter):
    """Liefert das, was der echte Node liefern wuerde — Feldnamen inklusive."""
    p = parameter or []

    if methode == "getblockchaininfo":
        if LAGE == "sync":
            # 'time' ist die Zeit des zuletzt geprueften Blocks — waehrend der
            # Erstsynchronisation ein Datum von 2015, nicht die Kettenspitze.
            # Das Dashboard zeigt es als "geprueft bis" an.
            return {"blocks": 350328, "headers": 963634,
                    "verificationprogress": 0.0459,
                    "initialblockdownload": True, "pruned": False,
                    "size_on_disk": 38_200_000_000,
                    "chain": "main", "difficulty": 1.263e14,
                    "time": 1428021287, "mediantime": 1428021287}
        if LAGE == "leer":
            return {}
        return {"blocks": SPITZE, "headers": SPITZE,
                "verificationprogress": 0.9999987,
                "initialblockdownload": False, "pruned": False,
                "size_on_disk": 812_000_000_000,
                "chain": "main", "difficulty": 1.263e14,
                "mediantime": int(time.time()) - 900}

    if methode == "getnetworkinfo":
        return {"connections": 10, "connections_in": 0, "connections_out": 10,
                "subversion": "/Satoshi:31.1.0/", "localaddresses": [],
                "networks": []}

    if methode == "getmempoolinfo":
        if LAGE == "sync":
            return {"size": 0, "usage": 0, "bytes": 0, "mempoolminfee": 0.00001}
        return {"size": 41233, "usage": 198_800_000, "bytes": 61_000_000,
                "mempoolminfee": 0.0000122}

    if methode == "getconnectioncount":
        return 10

    if methode == "uptime":
        return 2280

    if methode == "estimatesmartfee":
        if LAGE == "sync":
            return {"errors": ["Insufficient data or no feerate found"]}
        ziel = p[0] if p else 6
        return {"feerate": {1: 0.000041, 6: 0.000023, 24: 0.000015}.get(ziel, 0.00002),
                "blocks": ziel}

    if methode == "getpeerinfo":
        if LAGE == "leer":
            return []
        # Bewusst gemischt: verschiedene Netzarten, ein eingehender Knoten,
        # ein Peer ohne Latenzmessung und eine Kennung mit spitzen Klammern.
        # Letztere prueft, dass fremder Text nirgends als Markup landet.
        arten = ["onion"] * 6 + ["ipv4"] * 9 + ["ipv6"] * 3 + ["i2p"]
        knoten = []
        for i, art in enumerate(arten):
            if art == "onion":
                adresse = f"{'abcdefghij' * 5}{i:02d}xyzw.onion:8333"
            elif art == "ipv6":
                adresse = f"[2a01:4f8:{i:04x}::{i:x}]:8333"
            elif art == "i2p":
                adresse = f"{'q' * 52}.b32.i2p:0"
            else:
                adresse = f"185.{20 + i}.{100 + i}.{i + 3}:8333"
            knoten.append({
                "addr": adresse,
                "network": art,
                "inbound": i == 4,
                # Beide in Dezimalsekunden, so wie Bitcoin Core sie liefert.
                # 'pingtime' liegt waehrend der Synchronisation um Zehnerpotenzen
                # ueber 'minping', weil Ping und Pong im selben Strang laufen
                # wie das Anhaengen der Bloecke.
                "minping": None if i == 7 else round(
                    0.02 + 0.36 * abs(math.sin(i / 3.7)), 4),
                "pingtime": None if i == 7 else round(
                    9.8 + 1.7 * abs(math.sin(i / 2.1)), 4),
                "conntime": int(time.time()) - (600 + i * 917),
                "subver": "/Satoshi:31.1.0/" if i % 3 else "/<b>Knoten</b>:0.1/",
                "servicesnames": ["NETWORK", "WITNESS"]
                                 + (["NETWORK_LIMITED"] if i % 4 == 0 else []),
                "bytessent": 40_000 + i * 31_000,
                "bytesrecv": 900_000 + int(4.2e7 * abs(math.sin(i / 2.3))),
            })
        return knoten

    if methode == "getblockstats":
        if LAGE != "synchron":
            raise ValueError("getblockstats braucht eine synchrone Kette")
        return blockstats(p[0])

    if methode == "getblockhash":
        return f"attrappe-{p[0]:08d}"

    if methode == "getblockheader":
        hoehe = int(p[0].split("-")[1])
        # Schwierigkeit waechst rueckwaerts betrachtet leicht ab
        k = ((SPITZE // 2016) * 2016 - hoehe) // 2016
        return {"height": hoehe, "difficulty": 1.263e14 / (1 + 0.028 * k),
                "time": int(time.time()) - k * 2016 * 600}

    raise KeyError(methode)


class Griff(BaseHTTPRequestHandler):
    def _sende(self, code, nutzlast):
        roh = json.dumps(nutzlast).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def do_POST(self):
        kopf = self.headers.get("Authorization", "")
        erwartet = base64.b64encode(f"{BENUTZER}:{PASSWORT}".encode()).decode()
        if kopf != f"Basic {erwartet}":
            self._sende(401, {"error": {"code": -1, "message": "unauthorized"}})
            return

        laenge = int(self.headers.get("Content-Length", 0))
        try:
            anfrage = json.loads(self.rfile.read(laenge).decode())
        except json.JSONDecodeError:
            self._sende(400, {"error": {"code": -32700, "message": "parse error"}})
            return

        methode = anfrage.get("method", "")
        try:
            ergebnis = antwort(methode, anfrage.get("params"))
        except KeyError:
            # Genau so meldet sich eine Methode, die nicht in der
            # rpcwhitelist steht — der Fall muss im Dashboard sauber ankommen.
            self._sende(403, {"error": {"code": -32601,
                                        "message": f"Method not found: {methode}"}})
            return
        except ValueError as e:
            self._sende(500, {"error": {"code": -1, "message": str(e)}})
            return

        self._sende(200, {"result": ergebnis, "error": None, "id": anfrage.get("id")})

    def log_message(self, *_):
        pass  # keine Zugriffszeilen im Testlauf


def main():
    global LAGE
    zerleger = argparse.ArgumentParser()
    zerleger.add_argument("--port", type=int, default=18332)
    zerleger.add_argument("--lage", default="synchron",
                          choices=["synchron", "sync", "leer"])
    argumente = zerleger.parse_args()
    LAGE = argumente.lage

    server = ThreadingHTTPServer(("127.0.0.1", argumente.port), Griff)
    print(f"Attrappe laeuft auf 127.0.0.1:{argumente.port}, Lage '{LAGE}'")
    server.serve_forever()


if __name__ == "__main__":
    main()
