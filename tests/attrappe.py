#!/usr/bin/env python3
"""A stand-in for a Bitcoin Core RPC server.

Answers over the same route as the real node — HTTP, JSON-RPC, basic auth —
so the test runs node-dashboard.py through exactly the code that will later
run on the Pi. Replacing the rpc() function instead would skip the error
handling, the authentication and the unwrapping of the answer.

Standard library only, nothing to install.

Usage:
    python3 tests/attrappe.py [--port 18332] [--case synchron|sync|leer]

Cases:
    synchron   chain complete, 24 hour data present — the normal case
    sync       initial sync at 4.6 % — what the Pi shows right now
    leer       node answers but delivers nothing usable

The case names stay German because they are identifiers shared with
probelauf.py, not text anyone reads on a page.
"""

import argparse
import base64
import json
import math
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER, PASSWORD = "dashboard", "probe"

TIP = 915312
CASE = "synchron"

# Fixed spread: the same call yields the same curve. Otherwise there would be
# no telling whether a change in the picture came from the code or from
# chance.
SPREAD = random.Random(20260823)


def blockstats(height):
    i = height - (TIP - 143)
    return {
        "height": height,
        "time": int(time.time()) - (143 - i) * 600,
        "total_out": int((900 + 700 * math.sin(i / 9)
                          + SPREAD.uniform(-150, 150)) * 1e8),
        "txs": int(2600 + 900 * math.sin(i / 7)),
        "feerate_percentiles": [1, 2,
                                round(2.5 + 4 * abs(math.sin(i / 11)), 1), 9, 20],
    }


def answer(method, params):
    """Return what the real node would return — field names included."""
    p = params or []

    if method == "getblockchaininfo":
        if CASE == "sync":
            # 'time' is the time of the last verified block — during the
            # initial sync a date from 2015, not the chain tip. The dashboard
            # shows it as "verified through".
            return {"blocks": 350328, "headers": 963634,
                    "verificationprogress": 0.0459,
                    "initialblockdownload": True, "pruned": False,
                    "size_on_disk": 38_200_000_000,
                    "chain": "main", "difficulty": 1.263e14,
                    "time": 1428021287, "mediantime": 1428021287}
        if CASE == "leer":
            return {}
        return {"blocks": TIP, "headers": TIP,
                "verificationprogress": 0.9999987,
                "initialblockdownload": False, "pruned": False,
                "size_on_disk": 812_000_000_000,
                "chain": "main", "difficulty": 1.263e14,
                "mediantime": int(time.time()) - 900}

    if method == "getnetworkinfo":
        return {"connections": 10, "connections_in": 0, "connections_out": 10,
                "subversion": "/Satoshi:31.1.0/", "localaddresses": [],
                "networks": []}

    if method == "getmempoolinfo":
        if CASE == "sync":
            return {"size": 0, "usage": 0, "bytes": 0, "mempoolminfee": 0.00001}
        return {"size": 41233, "usage": 198_800_000, "bytes": 61_000_000,
                "mempoolminfee": 0.0000122}

    if method == "getconnectioncount":
        return 10

    if method == "uptime":
        return 2280

    if method == "estimatesmartfee":
        if CASE == "sync":
            return {"errors": ["Insufficient data or no feerate found"]}
        target = p[0] if p else 6
        return {"feerate": {1: 0.000041, 6: 0.000023, 24: 0.000015}.get(target, 0.00002),
                "blocks": target}

    if method == "getpeerinfo":
        if CASE == "leer":
            return []
        # Deliberately mixed: several network types, one inbound node, one
        # peer without a latency measurement and an identifier with angle
        # brackets. The last one checks that foreign text never lands as
        # markup.
        kinds = ["onion"] * 6 + ["ipv4"] * 9 + ["ipv6"] * 3 + ["i2p"]
        nodes = []
        for i, kind in enumerate(kinds):
            if kind == "onion":
                addr = f"{'abcdefghij' * 5}{i:02d}xyzw.onion:8333"
            elif kind == "ipv6":
                addr = f"[2a01:4f8:{i:04x}::{i:x}]:8333"
            elif kind == "i2p":
                addr = f"{'q' * 52}.b32.i2p:0"
            else:
                addr = f"185.{20 + i}.{100 + i}.{i + 3}:8333"
            nodes.append({
                "addr": addr,
                "network": kind,
                "inbound": i == 4,
                # Both in decimal seconds, exactly as Bitcoin Core delivers
                # them. During the sync 'pingtime' is orders of magnitude
                # above 'minping' because ping and pong run in the same
                # thread as connecting blocks.
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
        return nodes

    if method == "getblockstats":
        if CASE != "synchron":
            raise ValueError("getblockstats needs a synced chain")
        return blockstats(p[0])

    if method == "getblockhash":
        return f"attrappe-{p[0]:08d}"

    if method == "getblockheader":
        height = int(p[0].split("-")[1])
        # Looking backwards, difficulty decreases slightly
        k = ((TIP // 2016) * 2016 - height) // 2016
        return {"height": height, "difficulty": 1.263e14 / (1 + 0.028 * k),
                "time": int(time.time()) - k * 2016 * 600}

    raise KeyError(method)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        head = self.headers.get("Authorization", "")
        expected = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if head != f"Basic {expected}":
            self._send(401, {"error": {"code": -1, "message": "unauthorized"}})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            request = json.loads(self.rfile.read(length).decode())
        except json.JSONDecodeError:
            self._send(400, {"error": {"code": -32700, "message": "parse error"}})
            return

        method = request.get("method", "")
        try:
            result = answer(method, request.get("params"))
        except KeyError:
            # This is exactly how a method missing from rpcwhitelist reports
            # itself — the case has to arrive cleanly in the dashboard.
            self._send(403, {"error": {"code": -32601,
                                        "message": f"Method not found: {method}"}})
            return
        except ValueError as e:
            self._send(500, {"error": {"code": -1, "message": str(e)}})
            return

        self._send(200, {"result": result, "error": None, "id": request.get("id")})

    def log_message(self, *_):
        pass  # keine Zugriffszeilen im Testlauf


def main():
    global CASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18332)
    parser.add_argument("--case", default="synchron",
                          choices=["synchron", "sync", "leer"])
    args = parser.parse_args()
    CASE = args.case

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Attrappe laeuft auf 127.0.0.1:{args.port}, Lage '{CASE}'")
    server.serve_forever()


if __name__ == "__main__":
    main()
