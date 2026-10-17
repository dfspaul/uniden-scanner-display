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
import glob, json, math, os, random, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import uniden

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_SIZE = 200
POLL = 0.5
# Favorites lists, systems or departments to show green as flights, e.g. SCANNER_FLIGHT="Air Medical,Med Flight".
FLIGHT = [n.strip() for n in os.environ.get("SCANNER_FLIGHT", "Air Medical").split(",") if n.strip()]

lock = threading.Lock()
state = {"connected": False, "error": "starting", "now": None, "log": []}

# A touchscreen on the Pi: normal brightness during and just after calls, lower once it has been quiet a while.
# A call or a tap on the screen (the page POSTs /wake) brings it back. Levels are 0-31 on the Touch Display 2.
BRIGHT = int(os.environ.get("SCANNER_BRIGHT", 15))
DIM = int(os.environ.get("SCANNER_DIM", 2))
DIM_AFTER = int(os.environ.get("SCANNER_DIM_AFTER", 180))  # seconds with no call and no tap
# With SCANNER_LAT and SCANNER_LON set (to 0.1 degree is plenty), both levels are lower from sunset to
# sunrise, so a call in a dark room isn't startling.
NIGHT_BRIGHT = int(os.environ.get("SCANNER_NIGHT_BRIGHT", 5))
NIGHT_DIM = int(os.environ.get("SCANNER_NIGHT_DIM", 2))
LAT = float(os.environ["SCANNER_LAT"]) if os.environ.get("SCANNER_LAT") else None
LON = float(os.environ["SCANNER_LON"]) if os.environ.get("SCANNER_LON") else None
woke = time.time()


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


def daylight(n, lat, lon):
    """(sunrise, sunset) as Unix times for Julian day number n (days since 2000-01-01 12:00 UTC), by the
    standard sunrise equation; good to a minute or two. None, None if the sun doesn't rise or set that day."""
    j = n - lon / 360
    m = (357.5291 + 0.98560028 * j) % 360
    rm = math.radians(m)
    lam = math.radians((m + 1.9148 * math.sin(rm) + 0.02 * math.sin(2 * rm) + 0.0003 * math.sin(3 * rm) + 282.9372) % 360)
    transit = 2451545.0 + j + 0.0053 * math.sin(rm) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    if not -1 <= cos_w <= 1:
        return None, None
    w = math.degrees(math.acos(cos_w)) / 360
    unix = lambda jd: (jd - 2440587.5) * 86400
    return unix(transit - w), unix(transit + w)


def is_night(t=None):
    """True from sunset to sunrise; always False without SCANNER_LAT and SCANNER_LON."""
    if LAT is None or LON is None:
        return False
    t = time.time() if t is None else t
    n = round(t / 86400 + 2440587.5 - 2451545.0 + 0.0008)
    for d in (n - 1, n, n + 1):
        rise, set_ = daylight(d, LAT, LON)
        if rise is not None and rise <= t < set_:
            return False
    return True


def backlight():
    """Set the screen's backlight from the last call or tap. Does nothing on a machine without one."""
    paths = glob.glob("/sys/class/backlight/*/brightness")
    if not paths:
        return
    current = None
    while True:
        with lock:
            live = state["now"] is not None
            last = max(woke, state["log"][0]["last"]) if state["log"] else woke
        bright, dim = (NIGHT_BRIGHT, NIGHT_DIM) if is_night() else (BRIGHT, DIM)
        level = bright if live or time.time() - last < DIM_AFTER else dim
        if level != current:
            try:
                with open(paths[0], "w") as f:
                    f.write(str(level))
                current = level
                print(f"backlight {level}", flush=True)  # in journalctl -u scanner-display
            except OSError as e:  # not in the video group, say; keep serving the page
                print(f"backlight: {e}", file=sys.stderr, flush=True)
                return
        time.sleep(POLL)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global woke
        # A tap on the Pi's own screen: wake the backlight. Only the Pi itself may.
        if self.path == "/wake" and self.client_address[0] in ("127.0.0.1", "::1"):
            with lock:
                woke = time.time()
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/state.json":
            with lock:
                body = json.dumps({**state, "time": time.time()}).encode()
            ctype = "application/json"
        elif self.path in ("/", "/index.html", "/history"):
            with open(os.path.join(HERE, "history.html" if self.path == "/history" else "display.html"), "rb") as f:
                body = f.read()
            # A request from the Pi itself is its touchscreen: the main card gets the full height, no recent-calls list.
            if self.client_address[0] in ("127.0.0.1", "::1"):
                body = body.replace(b"<body>", b'<body class="kiosk">', 1)
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
    if not demo:
        threading.Thread(target=backlight, daemon=True).start()
    print(f"serving on http://0.0.0.0:{port}/" + (" (demo)" if demo else ""), flush=True)
    try:
        ThreadingHTTPServer(("", port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv[1:])
