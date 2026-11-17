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
Vremena su na stotinke, odsečena (ne zaokružena), kako je pravilo u plivanju.

Logika trke (klasa Race) ne zavisi od tastature: kasnije se isti metodi
start() / finish(staza) / reset() pozivaju sa Arduina preko serijskog porta.
"""

import datetime
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(APP_DIR, "rezultati")

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
        self._clear()

    def _clear(self):
        self.state = Race.READY
        self.t0 = None
        self.start_wall = None
        self.finishes = {}       # staza -> stotinke

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
        self.out.write("", "=== Trka %d | %s | staza: %d ===" % (
            self.race_no,
            self.start_wall.strftime("%Y-%m-%d %H:%M:%S.") +
            "%03d" % (self.start_wall.microsecond // 1000),
            self.lanes))
        return True

    def finish(self, lane: int, t=None) -> bool:
        if self.state != Race.RUNNING or not 1 <= lane <= self.lanes:
            return False
        if lane in self.finishes:
            return False
        t = t if t is not None else time.perf_counter()
        if t - self.t0 < MIN_RACE_S:
            return False
        self.finishes[lane] = to_hundredths(t - self.t0)
        self.out.write("Staza %d    %s" % (lane, fmt(self.finishes[lane])))
        if len(self.finishes) == self.lanes:
            self._end("kraj trke")
        return True

    def _end(self, note: str):
        if self.lanes > 1 and self.finishes:
            self.out.write("-- Plasman --")
            for p, lane, t in places(self.finishes):
                self.out.write("%d. mesto   Staza %d    %s" % (p, lane, fmt(t)))
        self.out.write("--- %s ---" % note)
        self.state = Race.DONE

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


# ---------------------------------------------------------------- prozor

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.out = ResultFile(OUT_DIR)
        self.race = Race(self.out, lanes=1)
        self.start_key = START_KEY_DEFAULT
        self.waiting_key = False
        self.down = set()          # tasteri koji su trenutno pritisnuti

        root.title("Stoperica - plivanje")
        root.geometry("620x560")
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

        self.time_lbl = tk.Label(root, text="00:00.00",
                                 font=("Consolas", 64, "bold"))
        self.time_lbl.pack(pady=(10, 0))
        self.state_lbl = tk.Label(root, font=("Segoe UI", 14))
        self.state_lbl.pack()

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
            row = ttk.Frame(self.lanes_frame)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text="Staza %d" % lane, width=9,
                      font=("Segoe UI", 13)).pack(side="left")
            t = ttk.Label(row, text="--:--.--", font=("Consolas", 16))
            t.pack(side="left", padx=10)
            p = ttk.Label(row, text="", font=("Segoe UI", 13, "bold"))
            p.pack(side="left")
            self.lane_lbls[lane] = (t, p)

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
        else:
            msg, color = "Završeno - Esc za novu trku", "#333333"
        self.state_lbl.config(text="Trka %d: %s" % (
            r.race_no + (1 if r.state == Race.READY else 0), msg), fg=color)
        self.info_lbl.config(text=os.path.basename(self.out.path))
        self.cb.config(state="disabled" if r.state == Race.RUNNING else "readonly")

    def tick(self):
        r = self.race
        if r.state == Race.RUNNING:
            self.time_lbl.config(text=fmt(r.elapsed()))
        elif r.state == Race.READY:
            self.time_lbl.config(text=fmt(0))
        elif r.lanes == 1 and 1 in r.finishes:
            self.time_lbl.config(text=fmt(r.finishes[1]))
        pl = {lane: p for p, lane, _ in places(r.finishes)}
        for lane, (t, p) in self.lane_lbls.items():
            if lane in r.finishes:
                t.config(text=fmt(r.finishes[lane]))
                p.config(text="%d." % pl[lane])
            else:
                t.config(text="--:--.--")
                p.config(text="")
        self.root.after(REFRESH_MS, self.tick)

    # --- tastatura
    def on_press(self, e):
        t = time.perf_counter()          # vreme uzimamo odmah, pre bilo čega
        k = e.keysym
        if k in self.down:               # automatsko ponavljanje držanog tastera
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

    def on_lanes(self, _e=None):
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
