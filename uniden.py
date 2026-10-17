#!/usr/bin/env python3
"""Talk to the BCD536HP over its mini-USB serial port (remote commands), from a Mac or Linux (e.g. a Raspberry Pi).

    ./uniden.py MDL VER ...        send raw commands, print each reply
    ./uniden.py status             what it's on now: list / system / department / channel
    ./uniden.py watch [seconds]    print a line each time it stops on a channel with signal (Ctrl-C ends)
    ./uniden.py qk                 favorites list quick keys and whether each is on
    ./uniden.py qk 2 on|off        turn a favorites list quick key on or off

The scanner is a standard USB CDC device (no driver), at /dev/cu.usbmodem* on the Mac or /dev/ttyACM* on
Linux; set SCANNER_PORT to pick one.
Commands end in CR. GSI replies with XML that runs over several lines. FQK uses the same values for
get and set: 0 = no list on that key, 1 = off, 2 = on. Don't press a number key on the scanner to
toggle a quick key: in scan mode that opens a number-entry menu (Menu backs out), and FQK answers NG
until it's back to scanning.
"""
import glob, os, re, select, sys, termios, time
import xml.etree.ElementTree as ET

ENCODING = "latin-1"  # the scanner's text isn't UTF-8; latin-1 round-trips any byte


def find_port():
    if os.environ.get("SCANNER_PORT"):
        return os.environ["SCANNER_PORT"]
    ports = glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyACM*")
    if len(ports) != 1:
        sys.exit(f"expected one /dev/cu.usbmodem* (Mac) or /dev/ttyACM* (Linux) port, found {ports or 'none'}; set SCANNER_PORT")
    return ports[0]


class Scanner:
    def __init__(self, port=None):
        self.fd = os.open(port or find_port(), os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0  # raw: no input, output or line processing
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def close(self):
        os.close(self.fd)

    def send(self, cmd, timeout=2.0):
        """Send one command and return its reply, without the CRs around it.

        The scanner sometimes sends a stray CR after a reply, and a reply nobody read stays queued even
        after the port is closed and flushed. So anything already waiting is dropped first, and leading
        CRs don't count as a reply."""
        while select.select([self.fd], [], [], 0.05)[0] and os.read(self.fd, 65536):
            pass
        os.write(self.fd, (cmd + "\r").encode(ENCODING))
        xml = (cmd.split(",")[0] + ",<XML>,").encode(ENCODING)
        buf, end = b"", time.time() + timeout
        while time.time() < end:
            if select.select([self.fd], [], [], 0.1)[0]:
                buf = (buf + os.read(self.fd, 65536)).lstrip(b"\r")
                if buf.startswith(xml):
                    if b"</ScannerInfo>" in buf:
                        break
                elif buf.endswith(b"\r"):
                    break
        return buf.decode(ENCODING).strip("\r")

    def status(self):
        """GSI as {"mode", "list", "system", "department", "channel", "freq", "signal", "encrypted", "service"}.

        "encrypted" is the scanner's P25 status: on an encrypted call it still stops (and watch logs it),
        but there's no audio. The status can linger after the call, so it only means something with signal.
        "service" is the channel's service type as the scanner names it (e.g. "Law Dispatch"), if it says."""
        reply = self.send("GSI")
        if "<ScannerInfo" not in reply:
            raise RuntimeError(f"GSI: {reply!r}")
        root = ET.fromstring(reply[reply.index("<?xml"):].replace("\r", "\n").encode("utf-8"))
        get = lambda tag, attr="Name": root.find(tag).get(attr) if root.find(tag) is not None else None
        prop = root.find("Property")
        return {
            "mode": root.get("Mode"),
            "list": get("MonitorList"),
            "system": get("System"),
            "department": get("Department"),
            "channel": get("ConvFrequency") or get("TGID"),
            "freq": (get("ConvFrequency", "Freq") or "").strip() or None,
            "signal": int(prop.get("Sig")) if prop is not None else 0,
            "encrypted": prop is not None and prop.get("P25Status") == "Enc",
            "service": get("ConvFrequency", "SvcType") or get("TGID", "SvcType"),
        }

    def quick_keys(self):
        """FQK as a list of 100 values: 0 = no list, 1 = off, 2 = on."""
        reply = self.send("FQK")
        if not reply.startswith("FQK,") or reply == "FQK,NG":
            raise RuntimeError(f"FQK: {reply!r} (is the scanner in a menu?)")
        return [int(v) for v in reply.split(",")[1:]]

    def set_quick_key(self, key, on):
        keys = self.quick_keys()
        if keys[key] == 0:
            raise RuntimeError(f"no favorites list on quick key {key}")
        keys[key] = 2 if on else 1
        reply = self.send("FQK," + ",".join(map(str, keys)))
        if reply != "FQK,OK" or self.quick_keys()[key] != keys[key]:
            raise RuntimeError(f"FQK set: {reply!r}")


def strength(s):
    return f"signal {s['signal']}" + (", encrypted" if s["signal"] and s["encrypted"] else "")


def line(s):
    return " / ".join(str(s[k]) for k in ("list", "system", "department", "channel", "freq") if s[k])


def main(args):
    if not args:
        sys.exit(__doc__)
    sc = Scanner()
    try:
        if args[0] == "status":
            s = sc.status()
            print(f"{s['mode']}: {line(s)} ({strength(s)})")
        elif args[0] == "watch":
            end = time.time() + float(args[1]) if len(args) > 1 else float("inf")
            last = None
            while time.time() < end:
                s = sc.status()
                # On a trunked system's control channel there's signal but no talkgroup: not a call.
                hit = (s["list"], s["channel"], s["freq"]) if s["signal"] and (s["channel"] or s["freq"]) else None
                if hit and hit != last:
                    print(f"{time.strftime('%H:%M:%S')}  {line(s)}  ({strength(s)})", flush=True)
                last = hit
                time.sleep(0.5)
        elif args[0] == "qk":
            if len(args) == 3 and args[2] in ("on", "off"):
                sc.set_quick_key(int(args[1]), args[2] == "on")
            for key, v in enumerate(sc.quick_keys()):
                if v:
                    print(f"{key:2}  {'on' if v == 2 else 'off'}")
        else:
            for cmd in args:
                print(sc.send(cmd).replace("\r", "\n"))
    except KeyboardInterrupt:
        pass
    finally:
        sc.close()


if __name__ == "__main__":
    main(sys.argv[1:])
