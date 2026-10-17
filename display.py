#!/usr/bin/env python3
"""Serve what the scanner is receiving as a web page, for a phone, tablet, laptop or a screen on the Pi itself.

    ./display.py [port]          poll the scanner and serve http://<host>:<port>/ (default 8000);
                                 /history lists the last 200 stops
    ./display.py --demo [port]   made-up calls, no scanner needed, to work on the page's look

One process owns the serial port: don't run uniden.py watch at the same time, the two would garble each
other's replies. The page polls /state.json; every stop with signal goes in the log, the same rule as
watch, including silent ones (a key-up or data burst with no voice). If the scanner is off, unplugged,
or still showing its USB mode prompt (it picks serial on its own after ~15 s), this keeps retrying.
"""
import json, os, random, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import uniden

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_SIZE = 200
POLL = 0.5
# Favorites lists, systems or departments to show green as flights, e.g. SCANNER_FLIGHT="Air Medical,Med Flight".
FLIGHT = [n.strip() for n in os.environ.get("SCANNER_FLIGHT", "Air Medical").split(",") if n.strip()]

lock = threading.Lock()
state = {"connected": False, "error": "starting", "now": None, "log": []}


def kind(s):
    """Color group for the page: flight (a list, system or department named in FLIGHT, or an aircraft
    service type), law, fire (fire/EMS/hospital), or None."""
    if any(n in FLIGHT for n in (s.get("list"), s.get("system"), s.get("department"))):
        return "flight"
    svc = (s.get("service") or "").lower()
    if any(w in svc for w in ("law", "corrections")):
        return "law"
    if any(w in svc for w in ("fire", "ems", "hospital")):
        return "fire"
    if "aircraft" in svc:
        return "flight"
    return None


def record(s):
    """Update state from one GSI reading: the current stop, and the log the way watch decides it."""
    with lock:
        state["connected"], state["error"] = True, None
        if not (s["signal"] and (s["channel"] or s["freq"])):  # on a trunked control channel there's signal but no call
            state["now"] = None
            return
        key = [s["list"], s["channel"], s["freq"]]
        log = state["log"]
        if state["now"] and state["now"]["key"] == key:
            # Same call still going: the encrypted flag can show up after the first poll, so keep it once seen.
            entry = log[0]
            entry["encrypted"] = entry["encrypted"] or s["encrypted"]
            entry["signal"] = max(entry["signal"], s["signal"])
            entry["last"] = time.time()
        else:
            entry = {k: s.get(k) for k in ("list", "system", "department", "channel", "freq", "signal", "encrypted", "service")}
            entry["kind"] = kind(s)
            entry["start"] = entry["last"] = time.time()
            log.insert(0, entry)
            del log[LOG_SIZE:]
        state["now"] = {"key": key, "signal": s["signal"]}


def disconnected(err):
    with lock:
        state["connected"], state["error"], state["now"] = False, str(err), None


def poll_scanner():
    while True:
        try:
            sc = uniden.Scanner()
        except (OSError, SystemExit) as e:
            disconnected(e if isinstance(e, OSError) else "scanner not found")
            time.sleep(3)
            continue
        try:
            while True:
                record(sc.status())
                time.sleep(POLL)
        except Exception as e:  # unplugged, powered off, or a garbled reply: reopen and carry on
            disconnected(e)
            time.sleep(2)
        finally:
            try:
                sc.close()
            except OSError:
                pass


def poll_demo():
    calls = [
        ("Lakeview County", "Lakeview", "Lakeview County", "Sheriff Dispatch", "155.5050MHz", False, "Law Dispatch"),
        ("Lakeview County", "Lakeview", "Lakeview County", "Sheriff Tac", "155.5050MHz", True, "Law Tac"),
        ("Lakeview County", "Lakeview", "Lakeview County - Fire", "Fire Dispatch", "154.4300MHz", False, "Fire Dispatch"),
        ("Lakeview County", "Lakeview", "Lakeview County - EMS", "Ambulance Ops", "155.2050MHz", False, "EMS-Talk"),
        ("Lakeview County", "Lakeview", "Lakeview County", "Highway Department", "151.0700MHz", False, "Public Works"),
        ("Air Medical", "Air Medical", "Air Medical", "Air Ambulance 1", "155.3400MHz", False, "EMS Dispatch"),
        ("FAVORITES 0", "Aviation", "Regional Airport", "Tower", "118.5000MHz", False, "Aircraft"),
        ("FAVORITES 0", "Riverton", "Riverton City", "Police", "460.1250MHz", False, "Law Talk"),
    ]
    while True:
        lst, system, dept, chan, freq, enc, svc = random.choice(calls)
        for _ in range(random.randint(2, 16)):
            record({"list": lst, "system": system, "department": dept, "channel": chan, "freq": freq,
                    "signal": random.randint(2, 5), "encrypted": enc, "service": svc})
            time.sleep(POLL)
        for _ in range(random.randint(4, 30)):
            record({"list": lst, "system": system, "department": dept, "channel": chan, "freq": freq,
                    "signal": 0, "encrypted": False, "service": svc})
            time.sleep(POLL)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/state.json":
            with lock:
                body = json.dumps({**state, "time": time.time()}).encode()
            ctype = "application/json"
        elif self.path in ("/", "/index.html", "/history"):
            with open(os.path.join(HERE, "history.html" if self.path == "/history" else "display.html"), "rb") as f:
                body = f.read()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main(args):
    demo = "--demo" in args
    args = [a for a in args if a != "--demo"]
    port = int(args[0]) if args else 8000
    threading.Thread(target=poll_demo if demo else poll_scanner, daemon=True).start()
    print(f"serving on http://0.0.0.0:{port}/" + (" (demo)" if demo else ""), flush=True)
    try:
        ThreadingHTTPServer(("", port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv[1:])
