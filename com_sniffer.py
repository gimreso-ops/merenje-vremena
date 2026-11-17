#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
com_sniffer.py - praćenje i snimanje saobraćaja na serijskom (COM) portu,
npr. između ALGE sistema za merenje i Splash Meet Manager-a.

Tri režima rada:

  list    - ispiše sve COM portove na računaru
  sniff   - pasivno sluša jedan ili dva porta (hardverski prisluškivač:
            dodatni USB-serial adapter(i) čiji je RX spojen na TX liniju)
  bridge  - "čovek u sredini": skripta otvara pravi port (ALGE) i jedan kraj
            virtuelnog para (com0com), prosleđuje podatke u oba smera i sve
            beleži. Splash se podesi na drugi kraj virtuelnog para.

Rezultat (u folderu --out):
  sniff_GGGGMMDD_HHMMSS.log           - čitljiv log: vreme, smer, HEX, tekst
  sniff_GGGGMMDD_HHMMSS_<smer>.bin    - sirovi bajtovi po smeru (za kasniju analizu)

Dok skripta radi, u konzolu možete upisati belešku i pritisnuti Enter
(npr. "start 100m slobodno grupa 2" ili "lažni start"). Beleška ulazi u log
sa vremenom, pa se kasnije zna koja poruka pripada kojoj situaciji.
Upišite  q  i Enter za ranije zaustavljanje.

Potrebno: Python 3.8+ i  pip install pyserial
"""

import argparse
import datetime
import os
import re
import sys
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Nedostaje biblioteka pyserial. Instalirajte je komandom:")
    print("    pip install pyserial")
    sys.exit(1)


CTRL = {
    0x00: "NUL", 0x01: "SOH", 0x02: "STX", 0x03: "ETX", 0x04: "EOT",
    0x05: "ENQ", 0x06: "ACK", 0x07: "BEL", 0x08: "BS", 0x09: "TAB",
    0x0A: "LF", 0x0B: "VT", 0x0C: "FF", 0x0D: "CR", 0x0E: "SO",
    0x0F: "SI", 0x10: "DLE", 0x11: "DC1", 0x12: "DC2", 0x13: "DC3",
    0x14: "DC4", 0x15: "NAK", 0x16: "SYN", 0x17: "ETB", 0x18: "CAN",
    0x19: "EM", 0x1A: "SUB", 0x1B: "ESC", 0x1C: "FS", 0x1D: "GS",
    0x1E: "RS", 0x1F: "US", 0x7F: "DEL",
}

PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
          "O": serial.PARITY_ODD, "M": serial.PARITY_MARK,
          "S": serial.PARITY_SPACE}
STOPBITS = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
            "2": serial.STOPBITS_TWO}


def readable(data: bytes) -> str:
    """Tekstualni prikaz: obični znakovi kakvi jesu, kontrolni kao <STX>, <CR>..."""
    out = []
    for b in data:
        if 0x20 <= b < 0x7F:
            out.append(chr(b))
        elif b in CTRL:
            out.append("<%s>" % CTRL[b])
        else:
            out.append("<%02X>" % b)
    return "".join(out)


def hexdump(data: bytes) -> str:
    return " ".join("%02X" % b for b in data)


def safe_name(s: str) -> str:
    s = s.replace("->", "_to_")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_") or "port"


class Logger:
    def __init__(self, folder: str, echo: bool):
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base = os.path.join(folder, "sniff_" + stamp)
        self.path = self.base + ".log"
        self.f = open(self.path, "w", encoding="utf-8", newline="\n")
        self.raw = {}
        self.stats = {}
        self.lock = threading.Lock()
        self.echo = echo
        self.t0 = time.perf_counter()

    def stamp(self, wall=None, perf=None) -> str:
        wall = wall or datetime.datetime.now()
        perf = perf if perf is not None else time.perf_counter()
        return "%s.%03d  +%9.3fs" % (wall.strftime("%H:%M:%S"),
                                      wall.microsecond // 1000, perf - self.t0)

    def line(self, text: str):
        with self.lock:
            self.f.write(text + "\n")
            self.f.flush()
            if self.echo:
                try:
                    print(text, flush=True)
                except UnicodeEncodeError:
                    print(text.encode("ascii", "replace").decode(), flush=True)

    def note(self, text: str):
        self.line("%s  ##### BELEŠKA: %s" % (self.stamp(), text))

    def data(self, direction: str, data: bytes, wall, perf):
        with self.lock:
            if direction not in self.raw:
                self.raw[direction] = open(
                    "%s_%s.bin" % (self.base, safe_name(direction)), "wb")
                self.stats[direction] = [0, 0]
            self.raw[direction].write(data)
            self.raw[direction].flush()
            self.stats[direction][0] += 1
            self.stats[direction][1] += len(data)
        self.line("%s  %-14s %4d B\n    HEX: %s\n    TXT: %s" % (
            self.stamp(wall, perf), direction, len(data),
            hexdump(data), readable(data)))

    def close(self):
        with self.lock:
            for f in self.raw.values():
                f.close()
            self.f.close()


def open_port(name: str, args) -> serial.Serial:
    return serial.Serial(
        port=name,
        baudrate=args.baud,
        bytesize=args.bytesize,
        parity=PARITY[args.parity.upper()],
        stopbits=STOPBITS[args.stopbits],
        timeout=0.005,
        write_timeout=1,
        xonxoff=False, rtscts=False, dsrdtr=False,
    )


def reader(port, direction, logger, stop, gap_s, forward=None):
    """Čita port. Bajtove koji stignu bez pauze duže od gap_s spaja u jednu
    poruku. Ako je zadat forward, svaki bajt se odmah prosleđuje dalje."""
    buf = bytearray()
    first_wall = first_perf = None
    last = 0.0
    while not stop.is_set():
        try:
            data = port.read(port.in_waiting or 1)
        except Exception as e:  # port nestao, adapter izvučen...
            logger.line("!!! Greška pri čitanju %s: %s" % (port.port, e))
            stop.set()
            break
        now = time.perf_counter()
        if data:
            if forward is not None:
                try:
                    forward.write(data)
                except Exception as e:
                    logger.line("!!! Greška pri prosleđivanju na %s: %s"
                                % (forward.port, e))
            if not buf:
                first_wall, first_perf = datetime.datetime.now(), now
            buf += data
            last = now
            if len(buf) >= 4096:
                logger.data(direction, bytes(buf), first_wall, first_perf)
                buf.clear()
        elif buf and now - last >= gap_s:
            logger.data(direction, bytes(buf), first_wall, first_perf)
            buf.clear()
    if buf:
        logger.data(direction, bytes(buf), first_wall, first_perf)


def notes_thread(logger, stop):
    while not stop.is_set():
        try:
            text = input()
        except (EOFError, OSError):
            return
        text = text.strip()
        if not text:
            continue
        if text.lower() in ("q", "quit", "kraj", "stop"):
            logger.note("ručno zaustavljanje")
            stop.set()
            return
        logger.note(text)


def cmd_list(_args):
    ports = list(list_ports.comports())
    if not ports:
        print("Nije pronađen nijedan serijski port.")
    for p in ports:
        print("%-8s %s" % (p.device, p.description))


def run(args, pairs):
    """pairs: lista (port, ime_smera, port_za_prosleđivanje_ili_None)"""
    logger = Logger(args.out, echo=not args.quiet)
    stop = threading.Event()
    settings = "%d baud, %d%s%s" % (args.baud, args.bytesize,
                                     args.parity.upper(), args.stopbits)
    logger.line("=" * 72)
    logger.line("Početak snimanja: %s" % datetime.datetime.now().isoformat(" ", "seconds"))
    logger.line("Režim: %s   Podešavanja: %s   Pauza za kraj poruke: %d ms"
                % (args.mode, settings, args.gap_ms))
    for port, direction, fwd in pairs:
        logger.line("  %s  ->  smer '%s'%s" % (
            port.port, direction, ("  (prosleđuje na %s)" % fwd.port) if fwd else ""))
    logger.line("=" * 72)

    threads = [threading.Thread(target=reader, daemon=True,
                                args=(p, d, logger, stop, args.gap_ms / 1000.0, f))
               for p, d, f in pairs]
    for t in threads:
        t.start()
    threading.Thread(target=notes_thread, args=(logger, stop), daemon=True).start()

    print("\nSnimam %d min u %s" % (args.minutes, logger.path))
    print("Upišite belešku + Enter da označite situaciju, 'q' + Enter za kraj.\n")

    end = time.time() + args.minutes * 60
    next_info = time.time() + 60
    try:
        while not stop.is_set() and time.time() < end:
            time.sleep(0.2)
            if time.time() >= next_info:
                left = int((end - time.time()) / 60 + 0.5)
                print("... još oko %d min" % left, flush=True)
                next_info += 60
    except KeyboardInterrupt:
        logger.note("prekinuto sa Ctrl+C")
    stop.set()
    for t in threads:
        t.join(timeout=2)
    for port, _, _ in pairs:
        try:
            port.close()
        except Exception:
            pass

    logger.line("=" * 72)
    logger.line("Kraj snimanja: %s" % datetime.datetime.now().isoformat(" ", "seconds"))
    if not logger.stats:
        logger.line("Nije primljen nijedan bajt. Proverite port, brzinu i ožičenje.")
    for d, (n, b) in logger.stats.items():
        logger.line("  %-14s %6d poruka, %8d bajtova" % (d, n, b))
    logger.line("=" * 72)
    path = logger.path
    logger.close()
    print("\nGotovo. Log: %s" % path)


def cmd_sniff(args):
    pairs = []
    try:
        pairs.append((open_port(args.port, args), args.name1, None))
        if args.port2:
            pairs.append((open_port(args.port2, args), args.name2, None))
    except serial.SerialException as e:
        print("Ne mogu da otvorim port: %s" % e)
        print("Ako port drži drugi program (npr. Splash), koristite režim 'bridge'.")
        sys.exit(2)
    run(args, pairs)


def cmd_bridge(args):
    try:
        real = open_port(args.real, args)
        virt = open_port(args.virtual, args)
    except serial.SerialException as e:
        print("Ne mogu da otvorim port: %s" % e)
        sys.exit(2)
    run(args, [(real, "ALGE->SPLASH", virt),
               (virt, "SPLASH->ALGE", real)])


def main():
    ap = argparse.ArgumentParser(
        description="Praćenje saobraćaja na COM portu (ALGE <-> Splash Meet Manager)")
    sub = ap.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--baud", type=int, default=9600, help="brzina (podrazumevano 9600)")
        p.add_argument("--bytesize", type=int, default=8, choices=[5, 6, 7, 8])
        p.add_argument("--parity", default="N", choices=list("NEOMSneoms"))
        p.add_argument("--stopbits", default="1", choices=list(STOPBITS))
        p.add_argument("--minutes", type=float, default=30, help="trajanje (podrazumevano 30)")
        p.add_argument("--gap-ms", type=int, default=30,
                       help="pauza u ms posle koje se poruka smatra završenom (podrazumevano 30)")
        p.add_argument("--out", default="logovi", help="folder za logove (podrazumevano 'logovi')")
        p.add_argument("--quiet", action="store_true", help="ne ispisuj poruke u konzolu")

    sub.add_parser("list", help="ispiši COM portove")

    s = sub.add_parser("sniff", help="pasivno slušanje (hardverski prisluškivač)")
    s.add_argument("--port", required=True, help="npr. COM5")
    s.add_argument("--port2", help="drugi adapter, za drugi smer (opciono)")
    s.add_argument("--name1", default="ALGE->SPLASH")
    s.add_argument("--name2", default="SPLASH->ALGE")
    common(s)

    b = sub.add_parser("bridge", help="most preko virtuelnog para (com0com)")
    b.add_argument("--real", required=True, help="pravi port na koji je ALGE, npr. COM3")
    b.add_argument("--virtual", required=True, help="kraj virtuelnog para za skriptu, npr. COM10")
    common(b)

    args = ap.parse_args()
    {"list": cmd_list, "sniff": cmd_sniff, "bridge": cmd_bridge}[args.mode](args)


if __name__ == "__main__":
    main()
