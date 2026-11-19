#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stoperica.py - merenje vremena plivača tasterom sa tastature.

Režim 1 staza (podrazumevano):
    START taster (podrazumevano RAZMAK) - prvi pritisak pokreće vreme,
                                          drugi pritisak ga zaustavlja.
Režim 2-8 staza (priprema za 9 tastera: start + 8 staza):
    START taster pokreće vreme, tasteri 1-8 zaustavljaju odgovarajuću stazu.
    Trka se završava kad sve staze stignu ili pritiskom na Esc.

Esc (ili dugme "Reset") - završava/poništava trku i priprema novu.
Dugme "Promeni START taster" - posle klika pritisnite bilo koji taster.

Vremena se odmah upisuju u rezultati/rezultati_GGGGMMDD.txt (jedan fajl po danu),
svaki cilj čim se dogodi, pa se ništa ne gubi ni ako se program zatvori.

Po završetku trke pravi se i fajl za Splash Meet Manager u formatu
"Generic Txt Heat Files" (folder splash/, ime EventB-HeatC.txt):
    LANE;TIME50
    1;35.22
    2;1:02.10
U Splash-u izabrati sistem za merenje "Generic Txt Heat Files" i usmeriti ga
na taj folder. Polja Disciplina/Grupa/Dužina u prozoru određuju ime fajla i
kolonu TIMExx; grupa se posle svake trke sama poveća za 1.
Vremena su na stotinke, odsečena (ne zaokružena), kako je pravilo u plivanju.

Prazne staze: pored svake staze je dugme "isključi" (radi i dok trka traje).
Isključena staza se ne čeka i ne ulazi u Splash fajl.

Program takmičenja: fajl program.txt pored programa (ili dugme "Program..."),
jedan red po disciplini:  disciplina;dužina;broj grupa;naziv
    1;50;3;50m slobodno žene
    7F;100;1;100m leđno finale
Posle svake trke program sam prelazi na sledeću grupu, odnosno disciplinu.

Lenex startna lista iz Splash-a (.lxf ili .lef, dugme "Program..."): program,
broj staza, imena plivača i klubovi po stazama; prazne staze se same isključe.

Semafor: dugme "Semafor" otvara drugi prozor preko celog projektora (drugi
ekran, Win+P -> Extend). F11 ili dupli klik uključuje/isključuje ceo ekran.

Logika trke (klasa Race) ne zavisi od tastature: kasnije se isti metodi
start() / finish(staza) / reset() pozivaju sa Arduina preko serijskog porta.
"""

import datetime
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Kod .exe napravljenog PyInstaller-om __file__ pokazuje na privremeni folder,
# pa se rezultati čuvaju pored samog .exe fajla.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(APP_DIR, "rezultati")
SPLASH_DIR_DEFAULT = os.path.join(APP_DIR, "splash")
PROGRAM_FILE = os.path.join(APP_DIR, "program.txt")
DISTANCES = ["25", "50", "100", "200", "400", "800", "1500"]

START_KEY_DEFAULT = "space"
RESET_KEY = "Escape"
LANE_KEYS = [str(i) for i in range(1, 9)]   # 1..8 na tastaturi
MIN_RACE_S = 1.0     # cilj pre 1 s od starta se ignoriše (dupli pritisak)
REFRESH_MS = 37      # osvežavanje prikaza


# ---------------------------------------------------------------- pomoćne

def to_hundredths(sec: float) -> int:
    """Odsecanje na stotinke (59.839 -> 5983)."""
    return int(sec * 100 + 1e-9)


def fmt(hundredths: int) -> str:
    """5983 -> '00:59.83', 365432 -> '1:00:54.32'."""
    h, rest = divmod(hundredths, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    if h:
        return "%d:%02d:%02d.%02d" % (h, m, s, cs)
    return "%02d:%02d.%02d" % (m, s, cs)


def splash_time(hundredths: int) -> str:
    """Format za Splash: 35.22 ili 1:11.22 (minuti bez vodeće nule)."""
    m, rest = divmod(hundredths, 6000)
    s, cs = divmod(rest, 100)
    if m:
        return "%d:%02d.%02d" % (m, s, cs)
    return "%d.%02d" % (s, cs)


def write_splash_heat(folder: str, event: str, heat: int, distance: int,
                      finishes: dict) -> str:
    """Upisuje EventB-HeatC.txt (Generic Txt Heat Files). Fajl se prvo piše
    pod privremenim imenom pa preimenuje, da Splash nikad ne pročita pola fajla."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "Event%s-Heat%d.txt" % (event, heat))
    tmp = path + ".tmp"
    lines = ["LANE;TIME%d" % distance]
    for lane in sorted(finishes):
        lines.append("%d;%s" % (lane, splash_time(finishes[lane])))
    with open(tmp, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


def load_program(path: str) -> list:
    """Čita program takmičenja: disciplina;dužina;grupe;naziv (naziv opciono).
    Prazni redovi i redovi koji počinju sa # se preskaču."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1250"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    prog, errors = [], []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split(";")]
        try:
            ev = parts[0].upper()
            if not re.fullmatch(r"\d+[A-Z]?", ev):
                raise ValueError
            dist, heats = int(parts[1]), int(parts[2])
            if dist <= 0 or heats <= 0:
                raise ValueError
        except (IndexError, ValueError):
            errors.append("red %d: %s" % (n, line))
            continue
        name = ";".join(parts[3:]) if len(parts) > 3 else ""
        prog.append({"event": ev, "distance": dist, "heats": heats, "name": name})
    if errors:
        raise ValueError("Neispravni redovi u programu:\n" + "\n".join(errors[:10]))
    return prog


STROKES = {"FREE": "slobodno", "BACK": "leđno", "BREAST": "prsno", "FLY": "delfin",
           "MEDLEY": "mešovito", "IMRELAY": "mešovito"}
GENDERS = {"M": "muški", "F": "ženski", "X": "mešovito"}
CLUB_MAX = 22        # najviše znakova za naziv kluba na semaforu


def short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def load_lenex(path: str) -> dict:
    """Čita Lenex startnu listu (.lxf = zip, .lef = xml).
    Vraća program, startnu listu {(disciplina, grupa): {staza: (ime, klub)}},
    broj staza bazena i naziv takmičenja."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".lef", ".xml"))]
            if not names:
                raise ValueError("U .lxf arhivi nema .lef fajla.")
            data = z.read(names[0])
    else:
        with open(path, "rb") as f:
            data = f.read()
    try:
        root = ET.fromstring(data)
    except ET.ParseError as ex:
        raise ValueError("Fajl nije ispravan Lenex (XML): %s" % ex)
    meet = root.find(".//MEET")
    if meet is None:
        raise ValueError("U fajlu nema takmičenja (MEET).")

    def num(x, default=0):
        try:
            return int(x)
        except (TypeError, ValueError):
            return default

    program, heat_of = [], {}
    sessions = sorted(meet.findall("SESSIONS/SESSION"), key=lambda e: num(e.get("number")))
    for ses in sessions:
        events = sorted(ses.findall("EVENTS/EVENT"),
                        key=lambda e: (num(e.get("order"), 9999), num(e.get("number"))))
        for ev in events:
            heats = ev.findall("HEATS/HEAT")
            if not heats:
                continue                  # disciplina bez prijavljenih
            evno = (ev.get("number") or "").strip().upper()
            st = ev.find("SWIMSTYLE")
            st = st.attrib if st is not None else {}
            dist = num(st.get("distance"), 50)
            relay = num(st.get("relaycount"), 1)
            name = st.get("name") or "%s%dm %s %s" % (
                "%dx" % relay if relay > 1 else "", dist,
                STROKES.get(st.get("stroke", ""), ""), GENDERS.get(ev.get("gender", ""), ""))
            for h in heats:
                heat_of[h.get("heatid")] = (evno, num(h.get("number"), 1))
            program.append({"event": evno, "distance": dist * relay,
                            "heats": max(num(h.get("number"), 1) for h in heats),
                            "name": " ".join(name.split())})

    startlist, max_lane = {}, 0
    for club in meet.iter("CLUB"):
        cname = club.get("shortname") or club.get("name") or club.get("code") or ""
        for ath in club.findall("ATHLETES/ATHLETE"):
            person = "%s %s" % (ath.get("firstname", ""), ath.get("lastname", ""))
            for en in ath.findall("ENTRIES/ENTRY"):
                key, lane = heat_of.get(en.get("heatid")), num(en.get("lane"))
                if key and lane > 0:
                    startlist.setdefault(key, {})[lane] = (person.strip(), cname)
                    max_lane = max(max_lane, lane)
        for rel in club.findall("RELAYS/RELAY"):
            for en in rel.findall("ENTRIES/ENTRY"):
                key, lane = heat_of.get(en.get("heatid")), num(en.get("lane"))
                if key and lane > 0:
                    startlist.setdefault(key, {})[lane] = (cname, "štafeta")
                    max_lane = max(max_lane, lane)
    pool = meet.find("POOL")
    lanes = num(pool.get("lanemax")) if pool is not None else 0
    lanes = min(8, max(lanes or max_lane, 1))
    return {"program": program, "startlist": startlist, "lanes": lanes,
            "name": meet.get("name", "")}


def monitors() -> list:
    """Ekrani na Windows-u: [(x, y, širina, visina, glavni)]. Drugde prazna lista."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        found = []
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                    ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def cb(hmon, _hdc, _rc, _lp):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
            r = mi.rcMonitor
            found.append((r.left, r.top, r.right - r.left, r.bottom - r.top,
                          bool(mi.dwFlags & 1)))
            return 1

        user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
        return found
    except Exception:
        return []


def places(finishes: dict) -> list:
    """[(mesto, staza, stotinke)], isto vreme = isto mesto."""
    order = sorted(finishes.items(), key=lambda kv: (kv[1], kv[0]))
    out, last_t, last_p = [], None, 0
    for i, (lane, t) in enumerate(order, 1):
        p = last_p if t == last_t else i
        out.append((p, lane, t))
        last_t, last_p = t, p
    return out


# ---------------------------------------------------------------- upis

class ResultFile:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    @property
    def path(self) -> str:
        return os.path.join(self.folder, "rezultati_%s.txt"
                            % datetime.date.today().strftime("%Y%m%d"))

    def last_race_no(self) -> int:
        n = 0
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("=== Trka "):
                        try:
                            n = max(n, int(line.split()[2]))
                        except (IndexError, ValueError):
                            pass
        except FileNotFoundError:
            pass
        return n

    def write(self, *lines: str):
        with open(self.path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())


# ---------------------------------------------------------------- logika trke

class Race:
    READY, RUNNING, DONE = "spreman", "u toku", "završena"

    def __init__(self, out: ResultFile, lanes: int = 1):
        self.out = out
        self.lanes = lanes
        self.race_no = out.last_race_no()
        self.event = "1"         # broj discipline u Splash-u, npr. "7" ili "7F"
        self.heat = 1            # grupa
        self.distance = 50       # dužina u metrima -> kolona TIMExx
        self.splash_dir = SPLASH_DIR_DEFAULT   # None = ne pravi Splash fajl
        self.last_splash = None
        self.program = []        # lista disciplina (load_program / load_lenex)
        self.program_end = False
        self.startlist = {}      # {(disciplina, grupa): {staza: (ime, klub)}}
        self._applied = None
        self._clear()

    def _clear(self):
        self.state = Race.READY
        self.t0 = None
        self.start_wall = None
        self.finishes = {}       # staza -> stotinke
        self.disabled = set()    # isključene (prazne) staze
        self._applied = None
        self.apply_startlist()

    def heat_entries(self) -> dict:
        return self.startlist.get((self.event, self.heat), {})

    def lane_info(self, lane: int):
        return self.heat_entries().get(lane)

    def apply_startlist(self):
        """Pre trke isključi staze u kojima po startnoj listi niko ne pliva.
        Radi se samo kad se promeni disciplina/grupa, pa ručna podešavanja ostaju."""
        key = (self.event, self.heat)
        if self.state != Race.READY or not self.startlist or key == self._applied:
            return
        self._applied = key
        if key in self.startlist:
            used = self.startlist[key]
            self.disabled = {l for l in range(1, self.lanes + 1) if l not in used}

    def who(self, lane: int) -> str:
        info = self.lane_info(lane)
        return "   %s (%s)" % info if info else ""

    def elapsed(self) -> int:
        if self.t0 is None:
            return 0
        return to_hundredths(time.perf_counter() - self.t0)

    def start(self, t=None) -> bool:
        if self.state != Race.READY:
            return False
        self.t0 = t if t is not None else time.perf_counter()
        self.start_wall = datetime.datetime.now()
        self.state = Race.RUNNING
        self.race_no += 1
        self.out.write("", "=== Trka %d | %s | staza: %d | disc. %s, grupa %d, %dm ===" % (
            self.race_no,
            self.start_wall.strftime("%Y-%m-%d %H:%M:%S.") +
            "%03d" % (self.start_wall.microsecond // 1000),
            self.lanes, self.event, self.heat, self.distance))
        return True

    def active_lanes(self) -> list:
        return [l for l in range(1, self.lanes + 1) if l not in self.disabled]

    def _all_finished(self) -> bool:
        act = self.active_lanes()
        return bool(act) and all(l in self.finishes for l in act)

    def toggle_lane(self, lane: int) -> bool:
        """Uključuje/isključuje stazu; dozvoljeno pre i tokom trke."""
        if self.state == Race.DONE or lane in self.finishes or not 1 <= lane <= self.lanes:
            return False
        if lane in self.disabled:
            self.disabled.discard(lane)
            what = "uključena"
        else:
            self.disabled.add(lane)
            what = "isključena"
        if self.state == Race.RUNNING:
            self.out.write("(staza %d %s)" % (lane, what))
            if self._all_finished():
                self._end("kraj trke")
        return True

    def program_entry(self):
        for i, e in enumerate(self.program):
            if e["event"] == self.event:
                return i, e
        return None, None

    def set_program_event(self, idx: int):
        e = self.program[idx]
        self.event, self.distance, self.heat = e["event"], e["distance"], 1
        self.program_end = False
        self.apply_startlist()

    def _advance(self):
        """Sledeća grupa; posle poslednje grupe sledeća disciplina iz programa."""
        i, e = self.program_entry()
        if e is None or self.heat < e["heats"]:
            self.heat += 1
        elif i + 1 < len(self.program):
            self.set_program_event(i + 1)
        else:
            self.heat += 1
            self.program_end = True

    def finish(self, lane: int, t=None) -> bool:
        if self.state != Race.RUNNING or not 1 <= lane <= self.lanes:
            return False
        if lane in self.finishes or lane in self.disabled:
            return False
        t = t if t is not None else time.perf_counter()
        if t - self.t0 < MIN_RACE_S:
            return False
        self.finishes[lane] = to_hundredths(t - self.t0)
        self.out.write("Staza %d    %s%s" % (lane, fmt(self.finishes[lane]), self.who(lane)))
        if self._all_finished():
            self._end("kraj trke")
        return True

    def _end(self, note: str):
        if self.lanes > 1 and self.finishes:
            self.out.write("-- Plasman --")
            for p, lane, t in places(self.finishes):
                self.out.write("%d. mesto   Staza %d    %s%s" % (p, lane, fmt(t), self.who(lane)))
        if self.disabled:
            self.out.write("Isključene staze: %s" % ", ".join(
                str(l) for l in sorted(self.disabled)))
        self.out.write("--- %s ---" % note)
        self.state = Race.DONE
        if self.finishes and self.splash_dir:
            path = write_splash_heat(self.splash_dir, self.event, self.heat,
                                     self.distance, self.finishes)
            self.last_splash = path
            self.out.write("Splash fajl: %s" % os.path.basename(path))
            self._advance()

    def reset(self):
        if self.state == Race.RUNNING:
            note = "RESET - trka poništena" if not self.finishes \
                else "RESET - trka završena ručno"
            self._end(note)
        self._clear()

    def set_lanes(self, n: int) -> bool:
        if self.state == Race.RUNNING:
            return False
        self.lanes = n
        self._clear()
        return True


# ---------------------------------------------------------------- semafor

class Scoreboard(tk.Toplevel):
    BG, FG, DIM = "#000000", "#ffffff", "#555555"
    HEAD, TIME, PLACE = "#ffd34d", "#7fe3ff", "#ffd34d"

    def __init__(self, app):
        super().__init__(app.root, bg=self.BG)
        self.app = app
        self.title("Semafor")
        self.n = 0
        self.rows = {}
        self.px = 40
        self.head_l = tk.Label(self, bg=self.BG, fg=self.HEAD, anchor="w")
        self.head_r = tk.Label(self, bg=self.BG, fg=self.HEAD, anchor="e")
        self.clock = tk.Label(self, bg=self.BG, fg=self.FG)
        self.head_l.grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(10, 0))
        self.head_r.grid(row=0, column=3, columnspan=2, sticky="e", padx=20, pady=(10, 0))
        self.clock.grid(row=1, column=0, columnspan=5, pady=(0, 10))
        self.columnconfigure(1, weight=3)
        self.columnconfigure(2, weight=2)
        self.bind("<Configure>", self.on_resize)
        self.bind("<Double-Button-1>", lambda e: self.toggle_fullscreen())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.geometry("1024x576")
        self.place_on_projector()

    def place_on_projector(self):
        mons = monitors()
        other = [m for m in mons if not m[4]]
        if other:
            x, y, w, h, _ = other[0]
            self.geometry("%dx%d+%d+%d" % (w, h, x, y))
            self.update_idletasks()
            self.attributes("-fullscreen", True)

    def toggle_fullscreen(self):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def close(self):
        self.app.board = None
        self.destroy()
        self.app.root.focus_force()

    def on_resize(self, e):
        if e.widget is self:
            px = max(14, int(e.height / (self.n + 3.6) * 0.62))
            if px != self.px:
                self.px = px
                self.apply_fonts()

    def apply_fonts(self):
        px = self.px
        self.head_l.config(font=("Arial", -int(px * 0.8), "bold"))
        self.head_r.config(font=("Arial", -int(px * 0.8), "bold"))
        self.clock.config(font=("Consolas", -int(px * 1.5), "bold"))
        for lane_l, name_l, club_l, time_l, place_l in self.rows.values():
            lane_l.config(font=("Arial", -px, "bold"))
            name_l.config(font=("Arial", -px, "bold"))
            club_l.config(font=("Arial", -int(px * 0.75)))
            time_l.config(font=("Consolas", -px, "bold"))
            place_l.config(font=("Arial", -px, "bold"))

    def build(self, n: int):
        for ws in self.rows.values():
            for w in ws:
                w.destroy()
        self.rows = {}
        self.n = n
        for lane in range(1, n + 1):
            r = lane + 1
            ws = (tk.Label(self, bg=self.BG, fg=self.HEAD, text=str(lane), width=2),
                  tk.Label(self, bg=self.BG, fg=self.FG, anchor="w"),
                  tk.Label(self, bg=self.BG, fg=self.FG, anchor="w"),
                  tk.Label(self, bg=self.BG, fg=self.TIME, anchor="e"),
                  tk.Label(self, bg=self.BG, fg=self.PLACE, anchor="e", width=3))
            for c, w in enumerate(ws):
                w.grid(row=r, column=c, sticky="ew", padx=(20 if c == 0 else 8, 20 if c == 4 else 8))
            self.rowconfigure(r, weight=1)
            self.rows[lane] = ws
        self.apply_fonts()

    def refresh(self):
        r = self.app.race
        if r.lanes != self.n:
            self.build(r.lanes)
        i, e = r.program_entry()
        title = ("Disc. %s  %s" % (r.event, e["name"])) if e else "Disciplina %s  %dm" % (
            r.event, r.distance)
        heats = ("/%d" % e["heats"]) if e else ""
        self.head_l.config(text=title)
        self.head_r.config(text="Grupa %d%s" % (r.heat, heats))
        if r.state == Race.RUNNING:
            self.clock.config(text=fmt(r.elapsed()))
        elif r.state == Race.READY:
            self.clock.config(text=fmt(0))
        else:
            self.clock.config(text=fmt(max(r.finishes.values())) if r.finishes else fmt(0))
        pl = {lane: p for p, lane, _ in places(r.finishes)}
        for lane, (lane_l, name_l, club_l, time_l, place_l) in self.rows.items():
            off = lane in r.disabled
            info = r.lane_info(lane)
            name, club = (info[0], short(info[1], CLUB_MAX)) if info else ("", "")
            color = self.DIM if off else self.FG
            lane_l.config(fg=self.DIM if off else self.HEAD)
            name_l.config(text=name, fg=color)
            club_l.config(text=club, fg=self.DIM if off else "#bbbbbb")
            if lane in r.finishes:
                time_l.config(text=fmt(r.finishes[lane]))
                place_l.config(text="%d." % pl[lane])
            else:
                time_l.config(text="")
                place_l.config(text="")


# ---------------------------------------------------------------- prozor

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.out = ResultFile(OUT_DIR)
        self.race = Race(self.out, lanes=1)
        self.start_key = START_KEY_DEFAULT
        self.waiting_key = False
        self.down = set()          # tasteri koji su trenutno pritisnuti
        self.board = None          # prozor semafora
        self.program_path = None

        root.title("Stoperica - plivanje")
        root.geometry("860x700")
        root.minsize(480, 420)

        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Broj staza:").pack(side="left")
        self.lanes_var = tk.StringVar(value="1")
        cb = ttk.Combobox(top, width=3, state="readonly", textvariable=self.lanes_var,
                          values=[str(i) for i in range(1, 9)], takefocus=0)
        cb.pack(side="left", padx=(4, 16))
        cb.bind("<<ComboboxSelected>>", self.on_lanes)
        self.cb = cb
        ttk.Button(top, text="Promeni START taster", takefocus=0,
                   command=self.ask_key).pack(side="left")
        ttk.Button(top, text="Otvori rezultate", takefocus=0,
                   command=self.open_results).pack(side="right")
        ttk.Button(top, text="Semafor", takefocus=0,
                   command=self.toggle_board).pack(side="right", padx=(0, 8))

        prog = ttk.Frame(root, padding=(10, 0, 10, 6))
        prog.pack(fill="x")
        ttk.Button(prog, text="Program...", takefocus=0,
                   command=self.choose_program).pack(side="left")
        ttk.Button(prog, text="Ponovo učitaj", takefocus=0,
                   command=self.reload_program).pack(side="left", padx=(6, 0))
        self.prog_var = tk.StringVar()
        self.cb_prog = ttk.Combobox(prog, state="readonly", textvariable=self.prog_var,
                                    takefocus=0, width=40)
        self.cb_prog.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.cb_prog.bind("<<ComboboxSelected>>", self.on_prog_select)

        meta = ttk.Frame(root, padding=(10, 0))
        meta.pack(fill="x")
        ttk.Label(meta, text="Disciplina:").pack(side="left")
        self.event_var = tk.StringVar(value=self.race.event)
        self.e_event = ttk.Entry(meta, width=5, textvariable=self.event_var)
        self.e_event.pack(side="left", padx=(4, 12))
        ttk.Label(meta, text="Grupa:").pack(side="left")
        self.heat_var = tk.StringVar(value=str(self.race.heat))
        self.e_heat = ttk.Spinbox(meta, from_=1, to=99, width=4, textvariable=self.heat_var)
        self.e_heat.pack(side="left", padx=(4, 12))
        ttk.Label(meta, text="Dužina:").pack(side="left")
        self.dist_var = tk.StringVar(value=str(self.race.distance))
        self.cb_dist = ttk.Combobox(meta, width=5, state="readonly", values=DISTANCES,
                                    textvariable=self.dist_var, takefocus=0)
        self.cb_dist.pack(side="left", padx=(4, 12))
        self.cb_dist.bind("<<ComboboxSelected>>", lambda e: self.root.focus_set())
        ttk.Button(meta, text="Splash folder...", takefocus=0,
                   command=self.choose_splash).pack(side="left")
        self._synced = (self.race.event, self.race.heat, self.race.distance)
        self.prog_lbl = tk.Label(root, font=("Segoe UI", 12), fg="#1f4f8f")
        self.prog_lbl.pack(pady=(6, 0))
        for v in (self.event_var, self.heat_var):
            v.trace_add("write", lambda *_: self.on_fields_changed())

        self.time_lbl = tk.Label(root, text="00:00.00",
                                 font=("Consolas", 64, "bold"))
        self.time_lbl.pack(pady=(10, 0))
        self.state_lbl = tk.Label(root, font=("Segoe UI", 14))
        self.state_lbl.pack()
        self.order_lbl = tk.Label(root, font=("Segoe UI", 16, "bold"))
        self.order_lbl.pack()

        self.lanes_frame = ttk.Frame(root, padding=(20, 10))
        self.lanes_frame.pack(fill="both", expand=True)
        self.lane_lbls = {}

        bottom = ttk.Frame(root, padding=10)
        bottom.pack(fill="x", side="bottom")
        ttk.Button(bottom, text="Reset (Esc)", takefocus=0,
                   command=self.do_reset).pack(side="left")
        self.info_lbl = ttk.Label(bottom, foreground="gray")
        self.info_lbl.pack(side="right")

        root.bind_all("<KeyPress>", self.on_press)
        root.bind_all("<KeyRelease>", self.on_release)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        if os.path.exists(PROGRAM_FILE):
            self.load_program_file(PROGRAM_FILE, quiet=True)
        self.build_lanes()
        self.refresh_texts()
        self.root.focus_force()
        self.tick()

    # --- prikaz
    def build_lanes(self):
        for w in self.lanes_frame.winfo_children():
            w.destroy()
        self.lane_lbls = {}
        if self.race.lanes == 1:
            return
        for lane in range(1, self.race.lanes + 1):
            row = tk.Frame(self.lanes_frame)
            row.pack(fill="x", pady=1)
            b = tk.Button(row, width=8, takefocus=0,
                          command=lambda l=lane: self.toggle_lane(l))
            b.pack(side="left", padx=(0, 10))
            n = tk.Label(row, text="Staza %d" % lane, width=8, anchor="w",
                         font=("Segoe UI", 13))
            n.pack(side="left")
            t = tk.Label(row, text="--:--.--", font=("Consolas", 16))
            t.pack(side="left", padx=10)
            p = tk.Label(row, text="", font=("Segoe UI", 13, "bold"), width=3)
            p.pack(side="left")
            w = tk.Label(row, text="", font=("Segoe UI", 12), anchor="w", fg="#1f4f8f")
            w.pack(side="left", padx=(10, 0), fill="x", expand=True)
            self.lane_lbls[lane] = (b, n, t, p, w)

    def key_name(self, k):
        return {"space": "RAZMAK", "Return": "ENTER"}.get(k, k.upper() if len(k) == 1 else k)

    def refresh_texts(self):
        r = self.race
        sk = self.key_name(self.start_key)
        if self.waiting_key:
            msg, color = "Pritisnite taster koji će biti START...", "#b36b00"
        elif r.state == Race.READY:
            msg, color = "Spremno - pritisnite %s za start" % sk, "#1f6f1f"
        elif r.state == Race.RUNNING:
            msg = ("U toku - %s zaustavlja" % sk) if r.lanes == 1 \
                else "U toku - tasteri 1-%d zaustavljaju staze" % r.lanes
            color = "#b00000"
        elif r.program_end:
            msg, color = "Završeno - KRAJ PROGRAMA", "#333333"
        else:
            msg, color = "Završeno - Esc za novu trku", "#333333"
        self.state_lbl.config(text="Trka %d: %s" % (
            r.race_no + (1 if r.state == Race.READY else 0), msg), fg=color)
        self.info_lbl.config(text="%s  |  Splash: %s" % (
            os.path.basename(self.out.path), r.splash_dir or "isključeno"))
        running = r.state == Race.RUNNING
        self.cb.config(state="disabled" if running else "readonly")
        self.cb_dist.config(state="disabled" if running else "readonly")
        self.e_event.config(state="disabled" if running else "normal")
        self.e_heat.config(state="disabled" if running else "normal")
        self.cb_prog.config(state="disabled" if running or not r.program else "readonly")
        cur = (r.event, r.heat, r.distance)
        if cur != self._synced:                  # program je prešao na sledeću trku
            self._synced = cur
            self.event_var.set(r.event)
            self.heat_var.set(str(r.heat))
            self.dist_var.set(str(r.distance))
        if r.state == Race.DONE and r.lanes > 1 and r.finishes:
            self.order_lbl.config(text="Ulasci u cilj:  " + " ".join(
                str(lane) for _, lane, _ in places(r.finishes)))
        else:
            self.order_lbl.config(text="")
        self.update_prog_label()

    def update_prog_label(self):
        r = self.race
        if not r.program:
            self.prog_lbl.config(text="")
            self.prog_var.set("")
            return
        try:
            ev = self.event_var.get().strip().upper()
            heat = int(self.heat_var.get())
        except ValueError:
            ev, heat = r.event, r.heat
        i = next((k for k, e in enumerate(r.program) if e["event"] == ev), None)
        if i is None:
            self.prog_lbl.config(text="Disciplina %s nije u programu" % ev)
            self.prog_var.set("")
            return
        e = r.program[i]
        self.cb_prog.current(i)
        extra = "  (van programa!)" if heat > e["heats"] else ""
        self.prog_lbl.config(text="Disc. %s  -  grupa %d od %d  -  %dm  %s%s" % (
            e["event"], heat, e["heats"], e["distance"], e["name"], extra))

    def tick(self):
        r = self.race
        if r.state == Race.RUNNING:
            self.time_lbl.config(text=fmt(r.elapsed()))
        elif r.state == Race.READY:
            self.time_lbl.config(text=fmt(0))
        elif r.lanes == 1 and 1 in r.finishes:
            self.time_lbl.config(text=fmt(r.finishes[1]))
        pl = {lane: p for p, lane, _ in places(r.finishes)}
        for lane, (b, n, t, p, w) in self.lane_lbls.items():
            off = lane in r.disabled
            gray = "#aaaaaa" if off else "black"
            n.config(fg=gray)
            t.config(fg=gray)
            info = r.lane_info(lane)
            w.config(text=("%s  -  %s" % info) if info else "",
                     fg="#aaaaaa" if off else "#1f4f8f")
            if lane in r.finishes:
                t.config(text=fmt(r.finishes[lane]))
                p.config(text="%d." % pl[lane])
            else:
                t.config(text="prazna" if off else "--:--.--")
                p.config(text="")
            can = r.state != Race.DONE and lane not in r.finishes
            b.config(text="uključi" if off else "isključi",
                     state="normal" if can else "disabled",
                     bg="#f3d6d6" if off else "#dfeedd")
        if self.board is not None:
            self.board.refresh()
        self.root.after(REFRESH_MS, self.tick)

    # --- tastatura
    def on_press(self, e):
        t = time.perf_counter()          # vreme uzimamo odmah, pre bilo čega
        k = e.keysym
        if e.widget in (self.e_event, self.e_heat):
            # kucanje u polja Disciplina/Grupa; Enter ili Esc vraća na merenje
            if k in ("Return", "KP_Enter", RESET_KEY):
                self.root.focus_set()
                return "break"
            return None
        if k in self.down:               # automatsko ponavljanje držanog tastera
            return "break"
        if k == "F11":
            if self.board is not None:
                self.board.toggle_fullscreen()
            return "break"
        self.down.add(k)

        if self.waiting_key:
            if k == RESET_KEY:
                self.waiting_key = False
            elif self.race.lanes > 1 and k in LANE_KEYS:
                messagebox.showwarning("Taster", "Tasteri 1-8 su rezervisani za staze.")
            else:
                self.start_key = k
                self.waiting_key = False
            self.refresh_texts()
            return "break"

        try:
            if k == RESET_KEY:
                self.do_reset()
            elif k == self.start_key:
                if self.race.state == Race.READY:
                    if self.apply_meta():
                        self.race.start(t)
                elif self.race.lanes == 1:
                    self.race.finish(1, t)
            elif self.race.lanes > 1 and k in LANE_KEYS:
                self.race.finish(int(k), t)
        except OSError as ex:
            messagebox.showerror("Greška pri upisu", str(ex))
        self.refresh_texts()
        return "break"

    def on_release(self, e):
        self.down.discard(e.keysym)

    # --- komande
    def do_reset(self):
        try:
            self.race.reset()
        except OSError as ex:
            messagebox.showerror("Greška pri upisu", str(ex))
        self.refresh_texts()
        self.root.focus_set()

    def apply_meta(self) -> bool:
        ev = self.event_var.get().strip().upper()
        if not re.fullmatch(r"\d+[A-Z]?", ev):
            messagebox.showwarning("Disciplina", "Disciplina mora biti broj, npr. 7 ili 7F.")
            return False
        try:
            heat = int(self.heat_var.get())
            if heat < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Grupa", "Grupa mora biti ceo broj veći od 0.")
            return False
        self.event_var.set(ev)
        self.race.event, self.race.heat = ev, heat
        self.race.distance = int(self.dist_var.get())
        self._synced = (ev, heat, self.race.distance)
        self.race.program_end = False
        return True

    def on_fields_changed(self):
        """Ručna promena discipline/grupe pre trke: odmah prikaži imena i
        isključi prazne staze iz startne liste."""
        r = self.race
        if r.state == Race.READY:
            ev = self.event_var.get().strip().upper()
            try:
                heat = int(self.heat_var.get())
            except ValueError:
                heat = 0
            if re.fullmatch(r"\d+[A-Z]?", ev) and heat >= 1:
                r.event, r.heat = ev, heat
                e = next((x for x in r.program if x["event"] == ev), None)
                if e is not None and e["distance"] != r.distance:
                    r.distance = e["distance"]
                    self.dist_var.set(str(r.distance))
                self._synced = (ev, heat, r.distance)
                r.apply_startlist()
        self.update_prog_label()

    def toggle_board(self):
        if self.board is None:
            self.board = Scoreboard(self)
            self.board.refresh()
        else:
            self.board.close()
        self.root.focus_force()

    def toggle_lane(self, lane: int):
        try:
            self.race.toggle_lane(lane)
        except OSError as ex:
            messagebox.showerror("Greška pri upisu", str(ex))
        self.refresh_texts()
        self.root.focus_set()

    def load_program_file(self, path: str, quiet: bool = False, keep: bool = False):
        is_lenex = path.lower().endswith((".lxf", ".lef", ".xml"))
        try:
            if is_lenex:
                lx = load_lenex(path)
                prog, startlist, lanes = lx["program"], lx["startlist"], lx["lanes"]
            else:
                prog, startlist, lanes = load_program(path), {}, None
        except (OSError, ValueError, zipfile.BadZipFile) as ex:
            messagebox.showerror("Program", str(ex))
            return
        if not prog:
            if not quiet:
                messagebox.showwarning("Program", "Fajl ne sadrži nijednu disciplinu.")
            return
        r = self.race
        if r.state == Race.RUNNING:
            messagebox.showinfo("Program", "Sačekajte kraj trke pa učitajte ponovo.")
            return
        self.program_path = path
        r.program, r.startlist = prog, startlist
        self.cb_prog.config(values=["%s  -  %dm  -  %d gr.  %s" % (
            e["event"], e["distance"], e["heats"], e["name"]) for e in prog])
        if lanes and lanes != r.lanes:
            r.set_lanes(lanes)
            self.lanes_var.set(str(lanes))
            self.build_lanes()
        pos = (r.event, r.heat)
        if keep and any(e["event"] == pos[0] for e in prog):
            r._applied = None
            if r.state == Race.READY:
                r.disabled = set()
            r.apply_startlist()
        else:
            r.set_program_event(0)
            if r.state == Race.DONE:
                r.reset()
        self.refresh_texts()
        if is_lenex and not quiet:
            messagebox.showinfo("Startna lista", "Učitano: %d disciplina, %d grupa, %d staza." % (
                len(prog), sum(e["heats"] for e in prog), r.lanes))

    def reload_program(self):
        if self.program_path:
            self.load_program_file(self.program_path, quiet=True, keep=True)
        self.root.focus_set()

    def choose_program(self):
        if self.race.state == Race.RUNNING:
            return
        path = filedialog.askopenfilename(
            title="Program takmičenja", initialdir=APP_DIR,
            filetypes=[("Lenex ili program", "*.lxf *.lef *.txt *.csv"),
                       ("Lenex startna lista", "*.lxf *.lef"), ("Tekst", "*.txt *.csv"),
                       ("Svi fajlovi", "*.*")])
        if path:
            self.load_program_file(path)
        self.root.focus_set()

    def on_prog_select(self, _e=None):
        if self.race.state == Race.RUNNING:
            return
        self.race.set_program_event(self.cb_prog.current())
        self.refresh_texts()
        self.root.focus_set()

    def choose_splash(self):
        if self.race.state == Race.RUNNING:
            return
        d = filedialog.askdirectory(title="Folder za Splash fajlove",
                                    initialdir=self.race.splash_dir or APP_DIR)
        if d:
            self.race.splash_dir = d
        self.refresh_texts()
        self.root.focus_set()

    def on_lanes(self, _e=None):
        if self.race.state == Race.RUNNING:
            return
        self.race.set_lanes(int(self.lanes_var.get()))
        self.build_lanes()
        self.refresh_texts()
        self.root.focus_set()

    def ask_key(self):
        if self.race.state == Race.RUNNING:
            return
        self.waiting_key = True
        self.refresh_texts()
        self.root.focus_set()

    def open_results(self):
        path = self.out.path
        if not os.path.exists(path):
            messagebox.showinfo("Rezultati", "Danas još nema upisanih trka.")
            return
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        self.root.focus_set()

    def on_close(self):
        if self.race.state == Race.RUNNING and not messagebox.askyesno(
                "Izlaz", "Trka je u toku. Zatvoriti program (trka se poništava)?"):
            return
        self.race.reset()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
