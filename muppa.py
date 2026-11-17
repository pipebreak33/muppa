#!/usr/bin/env python3
"""muppa – migrera en Windows-dator från lokal AD-domän till Entra ID. Windows, macOS och Linux.

  muppa                    meny
  muppa ny [--mall NAMN]   guiden: frågor → anpassat PowerShell-skript + checklista
  muppa skapa ANVÄNDARE [--mall NAMN] [flaggor]   skapa skript + checklista utan frågor
  muppa mallar             lista sparade mallar
  muppa hej

Guiden frågar efter användare, målmapp, vilka mappar som ska kopieras, webbläsare, BitLocker,
lokalt admin-konto och domän/Entra ID. Den sparar Migrera-<användare>.ps1 och Checklista-<användare>.md
i ~/Documents/Muppa. Skriptet är för Windows PowerShell 5.1 och körs som administratör på datorn som
migreras, i faser: (förbered) · -Doman · -Entra · -Aterlas. -Torrkorning / -WhatIf visar bara vad som görs.

Flaggor: --no-anim stänger av animationer.
Data: ~/.muppa/mallar.json (val, inga hemligheter). Enbart standardbiblioteket, Python 3.8+.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
HOME = Path.home()
DIR = Path(os.environ.get("MUPPA_DIR") or HOME / ".muppa")
TEMPLATES = DIR / "mallar.json"
CONFIG = DIR / "config.json"
OUT_DIR = Path(os.environ.get("MUPPA_UT") or HOME / "Documents" / "Muppa")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def _win_console():
    """UTF-8 + ANSI-färger i Windows-konsolen (Windows Terminal, conhost, VS Code)."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        k.SetConsoleCP(65001)
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)       # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


if IS_WIN:
    _win_console()


def open_path(path):
    try:
        if IS_WIN:
            os.startfile(str(path))                              # noqa – finns bara på Windows
        elif IS_MAC:
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen([shutil.which("xdg-open") or "xdg-open", str(path)])
    except Exception:
        out(dim(f"  Öppna själv: {path}"))


# ───────────────────────────── terminal ─────────────────────────────
TTY = sys.stdout.isatty()
ITTY = sys.stdin.isatty()
COLOR = TTY and "NO_COLOR" not in os.environ
ANIM = TTY and COLOR and not os.environ.get("MUPPA_NO_ANIM") and not os.environ.get("CI")
DIRTY = False      # något har skrivits ut som användaren bör hinna läsa innan skärmen töms


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if COLOR else s


def green(s): return c("38;5;42", s)
def yellow(s): return c("38;5;214", s)
def red(s): return c("38;5;203", s)
def dim(s): return c("2", s)
def bold(s): return c("1", s)
def accent(s): return c("38;5;177", s)
def cyan(s): return c("38;5;80", s)


ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def vlen(s):
    return len(ANSI.sub("", s))


def pad(s, n):
    return s + " " * max(0, n - vlen(s))


def cut(s, n):
    s = ANSI.sub("", s).replace("\t", " ").rstrip()
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def width():
    return max(20, shutil.get_terminal_size((100, 30)).columns)


def height():
    return max(10, shutil.get_terminal_size((100, 30)).lines)


def fit(s, w=None):
    """Klipp en (färgad) rad så att den aldrig radbryts i terminalen."""
    w = max(8, (w or width()) - 1)
    if vlen(s) <= w:
        return s
    out, n, i = [], 0, 0
    while i < len(s) and n < w - 1:
        m = ANSI.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(s[i])
        n += 1
        i += 1
    return "".join(out) + ("\033[0m" if COLOR else "") + "…"


def out(s=""):
    print(fit(s) if s else "")


def wrap(msg, w, maxl=4):
    lines, cur = [], ""
    for word in ANSI.sub("", msg).split():
        if cur and len(cur) + 1 + len(word) > w:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        lines.append(cur)
    if len(lines) > maxl:
        lines = lines[:maxl]
        lines[-1] = cut(lines[-1] + " …", w)
    return [cut(l, w) for l in lines] or [""]


def hard_wrap(s, w):
    """Bryt en lång rad i bitar om w tecken (för kommandon som måste synas i sin helhet)."""
    s = s.replace("\t", "    ")
    if len(s) <= w:
        return [s]
    parts = [s[:w]]
    s = s[w:]
    while s:
        parts.append(s[: w - 2])
        s = s[w - 2:]
    return parts


class Live:
    """Ritar om ett block rader på plats, även efter att fönstret ändrat storlek."""

    def __init__(self):
        self.prev = []

    def _up(self):
        w = width()
        return sum(max(1, -(-n // w)) for n in self.prev)

    def draw(self, lines):
        w = width()
        lines = [fit(l, w) for l in lines]
        sys.stdout.write("\r" + "\033[F" * self._up() + "\033[J" + "".join(l + "\n" for l in lines))
        sys.stdout.flush()
        self.prev = [vlen(l) for l in lines]

    def clear(self):
        sys.stdout.write("\r" + "\033[F" * self._up() + "\033[J")
        sys.stdout.flush()
        self.prev = []



# ───────────────────────────── Muppa, maskoten ─────────────────────────────
# Muppa är ett tjockt M, syskon till Puppa (P:et) och Uppa (U:et). Ansiktet bor mellan M:ets ben.
EYES = {"happy": " ◕ ◕ ", "blink": " ─ ─ ", "left": "◕ ◕  ", "right": "  ◕ ◕", "wait": " ◕ ◕ ",
        "sad": " ◕ ◕ ", "proud": " ^ ^ ", "wow": " ◕ ◕ ", "worry": " ◔ ◔ ", "sleep": " ─ ─ "}
MOUTH = {"happy": "  ‿  ", "blink": "  ‿  ", "left": " ‿   ", "right": "   ‿ ", "wait": "  ─  ",
         "sad": "  ⁀  ", "proud": "  ◡  ", "wow": "  o  ", "worry": "  ~  ", "sleep": "  ᶻ  "}
ART = ["██▀▄ ▄▀██", "██{e}██", "██{m}██", "██     ██", "██     ██", "▀▀     ▀▀"]
SHADE = ["38;5;219", "38;5;213", "38;5;177", "38;5;141", "38;5;135", "38;5;99"]   # ljus topp → mörkare botten
SAD_SHADE = ["38;5;189", "38;5;183", "38;5;146", "38;5;103", "38;5;97", "38;5;60"]
ART_W = 9
LOGO = "M›"
JOKES = [
    "Jag heter Muppa. Det står för Migrera Utan Panik. Typ.",
    "Jag är ett M. Som i Migrera. Eller Molnet. Eller Måndag.",
    "BitLocker-nyckeln? Spara den. Två gånger. På två ställen.",
    "Robocopy-kod 1 betyder att det gick bra. Ja, på riktigt.",
    "Windows Hello? Inte än. Hoppa över.",
    "Files On-Demand: filer som finns, men ändå inte. Schrödingers Dokument.",
    "Remove-Computer: det enda kommandot som får en domänkontrollant att känna sig övergiven.",
    "Det finns två sorters admins: de som tar backup, och de som kommer att göra det.",
]


def muppa_art(mood="happy"):
    e, m = EYES.get(mood, EYES["happy"]), MOUTH.get(mood, MOUTH["happy"])
    sh = SHADE if mood != "sad" else SAD_SHADE

    def mid(k, inner):
        return c(sh[k], "██") + bold(inner) + c(sh[k], "██")
    return [c(sh[0], ART[0]), mid(1, e), mid(2, m)] + [c(sh[k], ART[k]) for k in range(3, 6)]


def frame(msg="", mood="happy", lift=0, top="", sub=""):
    """Maskot + pratbubbla. Under 44 kolumner döljs maskoten."""
    w = width()
    if w < 44:
        lines = []
        if msg:
            for k, l in enumerate(wrap(msg, w - 6, 4)):
                lines.append((f"  {accent(bold(LOGO))} " if k == 0 else "     ") + l)
        if sub:
            lines.append("     " + dim(cut(sub, w - 7)))
        return lines
    rows = [""] * 7
    for k, r in enumerate(muppa_art(mood)):
        rows[k + 1 - lift] = r
    if top and not rows[0]:
        rows[0] = top
    bub = []
    if msg:
        lines = wrap(msg, max(14, min(60, w - 20)), 4)
        iw = max(len(l) for l in lines)
        bub = [dim("╭" + "─" * (iw + 2) + "╮")] + \
              [dim("│ ") + l + " " * (iw - len(l)) + dim(" │") for l in lines] + \
              [dim("╰" + "─" * (iw + 2) + "╯")]
    res = []
    for k in range(7):
        line = "  " + pad(rows[k], ART_W + 1)
        if 0 <= k - 1 < len(bub):
            line += " " + (accent("◂ ") if k == 2 else "  ") + bub[k - 1]
        elif sub and k - 1 == len(bub):
            line += "    " + dim(cut(sub, w - ART_W - 9))
        res.append(fit(line, w))
    while res and not ANSI.sub("", res[-1]).strip():
        res.pop()
    while res and not ANSI.sub("", res[0]).strip():
        res.pop(0)
    return res


def say(msg, mood="happy", sub=""):
    global DIRTY
    DIRTY = True
    if TTY:
        print("\n".join(frame(msg, mood, sub=sub)))
    else:
        print(f"  {LOGO} {msg}")


def idle_mood(t):
    """Blinkar då och då, tittar runt ibland."""
    if t % 4.1 < 0.16:
        return "blink"
    phase = int(t / 2.3) % 7
    return {2: "left", 5: "right"}.get(phase, "happy")


def celebrate(msg):
    global DIRTY
    DIRTY = True
    if not ANIM:
        return say(msg, "proud")
    cols = ["38;5;219", "38;5;214", "38;5;80", "38;5;177", "38;5;227"]
    live = Live()
    sys.stdout.write("\033[?25l")
    try:
        for f in range(14):
            top = "".join(c(random.choice(cols), random.choice("✦✧⋆·*")) if random.random() < .35 else " "
                          for _ in range(ART_W + 1))
            live.draw(frame(msg, "proud" if f % 5 else "blink", lift=f % 2, top=top))
            time.sleep(0.09)
        live.draw(frame(msg, "proud"))
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


# ───────────────────────────── input ─────────────────────────────
class Cancel(Exception):
    pass


class Keys:
    """Rå tangentläsning. Windows: msvcrt. macOS/Linux: termios + select. read() ger None vid timeout,
    så menyerna kan animera och rita om när fönstret ändrat storlek (även där SIGWINCH saknas)."""

    def __enter__(self):
        if IS_WIN:
            import msvcrt
            self.m = msvcrt
        else:
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        return self

    def __exit__(self, *a):
        if not IS_WIN:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    def read(self, timeout=0.12):
        if getattr(self, "buf", ""):
            return self._take()
        if IS_WIN:
            end = time.time() + timeout
            while time.time() < end:
                if self.m.kbhit():
                    ch = self.m.getwch()
                    if ch in ("\x00", "\xe0"):
                        return {"H": "\x1b[A", "P": "\x1b[B", "K": "\x1b[D", "M": "\x1b[C", "I": "\x1b[5~",
                                "Q": "\x1b[6~", "G": "\x1b[H", "O": "\x1b[F"}.get(self.m.getwch(), "")
                    return ch
                time.sleep(0.02)
            return None
        import select
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        self.buf = os.read(self.fd, 64).decode(errors="ignore")
        return self._take()

    def _take(self):
        """En tangent i taget: flera piltryck i samma läsning (tangent nedhållen) delas upp."""
        b = self.buf
        n = 1
        if b.startswith("\x1b") and len(b) >= 3 and b[1] in "[O":
            n = 2
            while n < len(b) and not ("@" <= b[n] <= "~"):
                n += 1
            n += 1
        self.buf = b[n:]
        return b[:n]


def new_screen():
    """Töm skärmen inför en meny. Har något just skrivits ut får man läsa klart först."""
    global DIRTY
    if DIRTY:
        pause()
    DIRTY = False
    if TTY:
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()


def fuzzy_score(query, text, loose=True):
    """Fuzzy-match: alla ord i frågan måste finnas i texten, som delsträng (bäst) eller som bokstäver i ordning.
    Högre = bättre. None = ingen träff."""
    text = ANSI.sub("", text).lower()
    total = 0
    for word in query.lower().split():
        i = text.find(word)
        if i >= 0:
            start = i == 0 or not text[i - 1].isalnum()
            total += 100 + (40 if start else 0) - min(i, 30)
            continue
        if len(word) < 3 or not loose:       # korta sökord / långa texter: bara som delsträng
            return None
        pos, gaps, last = 0, 0, -1
        for ch in word:
            j = text.find(ch, pos)
            if j < 0:
                return None
            if last >= 0:
                gaps += j - last - 1
            last, pos = j, j + 1
        if gaps > 2 * len(word):             # för utspritt för att vara det man menade
            return None
        total += max(1, 60 - gaps * 3)
    return total


def fuzzy_rank(query, items, key):
    if not query.strip():
        return list(items)
    scored = [(fuzzy_score(query, key(x)), n, x) for n, x in enumerate(items)]
    return [x for s, n, x in sorted([y for y in scored if y[0] is not None], key=lambda y: (-y[0], y[1]))]


def choose(rows, title="", msg="", mood="happy", sub="", status=None, start=None, footer=None, screen=False,
           search_rows=None):
    """Piltangent-meny. rows: ("head", text) eller dict(label, hint, value, badge, key).
    Siffra väljer direkt, q/esc avbryter (→ None). Börja skriva för att fuzzy-söka (search_rows = sök i en
    större lista, t.ex. alla recept). Maskoten blinkar medan den väntar.
    screen=True: menyn ritas på en tom skärm (navigeringsmenyer), annars där markören står (frågor i ett flöde)."""
    if not ITTY:
        raise Cancel()
    if screen:
        new_screen()
    hot = {r["key"] for r in rows if not isinstance(r, tuple) and r.get("key")}
    st = {"q": "", "pos": 0, "top": 0, "mood": None}

    def view():
        if st["q"]:
            pool = [r for r in (search_rows if search_rows is not None else rows) if not isinstance(r, tuple)]
            pool = [r for r in pool if r.get("value") is not None]
            q = st["q"]
            scored = []
            for n, r in enumerate(pool):
                a = fuzzy_score(q, r["label"])
                b = fuzzy_score(q, r["label"] + " " + ANSI.sub("", r.get("hint") or "") + " " + (r.get("search") or ""),
                                loose=False)
                if a is None and r.get("search"):
                    a = fuzzy_score(q, r["search"])
                sc = max(a * 2 if a is not None else -1, b if b is not None else -1)
                if sc >= 0:
                    scored.append((-sc, n, r))
            return [r for _, _, r in sorted(scored, key=lambda x: (x[0], x[1]))]
        return rows

    def numbering(vr):
        sel = [k for k, r in enumerate(vr) if not isinstance(r, tuple)]
        keys, n = {}, 0
        for k in sel:
            r = vr[k]
            if r.get("key") and not st["q"]:
                keys[r["key"]] = k
            else:
                n += 1
                if n <= 9:
                    keys[str(n)] = k
        return sel, keys

    vr = view()
    sel, keys = numbering(vr)
    if not sel:
        return None
    st["pos"] = sel[0]
    if start is not None:
        for k in sel:
            if vr[k]["value"] == start:
                st["pos"] = k
    live = Live()
    winch = {"flag": False}
    t0 = time.time()

    def draw(mood_now):
        w = width()
        lines = []
        if msg and not st["q"]:
            lines += frame(msg, mood_now, sub=sub)
        if status:
            lines += [""] + status()
        lines.append("")
        if st["q"]:
            lines.append("  " + accent("⌕ ") + bold(st["q"]) + accent("▏") + dim(f"  {len(sel)} träffar"))
        elif title:
            lines.append("  " + accent("▍") + bold(title))
        narrow = w < 58
        if not sel:
            lines.append("     " + dim("Inga träffar. Backsteg ändrar, esc rensar."))
        labels = [vr[k]["label"] for k in sel] or [""]
        lw = min(max(len(x) for x in labels) + 2, max(10, w - 14 if narrow else min(34, w // 2)))
        room = max(3, height() - len(lines) - 3)
        pos = st["pos"]
        top = min(max(st["top"], pos - room + 1), pos)
        top = max(0, min(top, max(0, len(vr) - room)))
        st["top"] = top
        if top > 0:
            lines.append(dim("     ↑ fler"))
        num = {k: n for n, k in keys.items()}
        anybadge = any(vr[j].get("badge") for j in sel)
        for k in range(top, min(len(vr), top + room)):
            r = vr[k]
            if isinstance(r, tuple):
                lines.append("   " + dim(cut(r[1].upper(), w - 5)))
                continue
            on = k == pos
            key = num.get(k, " ")
            label = cut(r["label"], lw - 1)
            label = bold(accent(label)) if on else label
            badge = r.get("badge") or ""
            if narrow:
                line = f"  {accent('❯') if on else ' '}{dim(key)} " + (pad(label, lw) + badge if anybadge else label)
            else:
                line = f"   {accent('❯') if on else ' '} {dim(key)}  " + pad(label, lw) + (pad(badge, 8) if anybadge else "")
                if r.get("hint") and w >= 64:
                    line += dim(ANSI.sub("", r["hint"]))
            lines.append(line)
        if top + room < len(vr):
            lines.append(dim("     ↓ fler"))
        lines.append("")
        if st["q"]:
            help_ = "↑/↓ · enter väljer · backsteg · esc rensar" if w >= 50 else "↑↓ enter · esc"
        else:
            help_ = ((footer or "↑/↓ · enter · siffra väljer · q tillbaka") + " · skriv för att söka") if w >= 72 else \
                (footer or "↑/↓ · enter · q tillbaka") if w >= 50 else "↑↓ enter · q"
        lines.append(dim("   " + help_))
        live.draw(lines)

    def refilter():
        nonlocal vr, sel, keys
        vr = view()
        sel, keys = numbering(vr)
        st["pos"] = sel[0] if sel else 0
        st["top"] = 0

    def on_winch(*_):
        winch["flag"] = True

    old_winch = signal.signal(signal.SIGWINCH, on_winch) if hasattr(signal, "SIGWINCH") else None
    size = shutil.get_terminal_size((100, 30))
    choice = None
    picked = None
    kb = Keys()
    try:
        kb.__enter__()
        draw(mood)
        while True:
            ch = kb.read(0.12)
            if ch is None:
                now = shutil.get_terminal_size((100, 30))
                if now != size:              # fönstret ändrat storlek (Windows har ingen SIGWINCH)
                    size = now
                    winch["flag"] = True
                m = mood
                if ANIM and not st["q"]:
                    im = idle_mood(time.time() - t0)
                    m = im if mood == "happy" else ("blink" if im == "blink" else mood)
                if winch["flag"] or m != st["mood"]:
                    winch["flag"] = False
                    st["mood"] = m
                    draw(m)
                continue
            idx = sel.index(st["pos"]) if st["pos"] in sel else 0
            if ch in ("\x1b[A", "\x1bOA"):
                if sel:
                    st["pos"] = sel[(idx - 1) % len(sel)]
            elif ch in ("\x1b[B", "\x1bOB"):
                if sel:
                    st["pos"] = sel[(idx + 1) % len(sel)]
            elif ch in ("\r", "\n"):
                if sel:
                    picked = vr[st["pos"]]
                    choice = picked["value"]
                    break
            elif ch in ("\x7f", "\x08"):
                if st["q"]:
                    st["q"] = st["q"][:-1]
                    refilter()
            elif ch == "\x1b":
                if st["q"]:
                    st["q"] = ""
                    refilter()
                else:
                    break
            elif ch in ("\x03", "\x04"):
                break
            elif not st["q"] and ch.isdigit() and ch in keys:
                picked = vr[keys[ch]]
                choice = picked["value"]
                break
            elif not st["q"] and ch in hot:
                picked = vr[keys[ch]]
                choice = picked["value"]
                break
            elif not st["q"] and ch == "q":
                break
            elif ch and not ch.startswith("\x1b") and ch.isprintable() and (st["q"] or not ch.startswith(" ")):
                st["q"] += ch                    # även inklistrat / snabbt skrivet (flera tecken på en gång)
                refilter()
            draw(st["mood"] or mood)
    except KeyboardInterrupt:
        choice = None
    finally:
        if old_winch is not None:
            signal.signal(signal.SIGWINCH, old_winch)
        kb.__exit__()
        live.clear()
        sys.stdout.flush()
    if choice is not None and not screen:
        out(f"   {accent('❯')} {bold(picked['label'])}")
    return choice


def ask_text(question, default="", required=False, check=None, secret=False):
    """Fråga efter en text. Enter = standardvärdet. Ctrl+C avbryter flödet."""
    if not ITTY:
        if required and not default:
            raise Cancel()
        return default
    w = width()
    for k, l in enumerate(wrap(question, w - 6, 6)):
        out(("  " + accent("?") + " " if k == 0 else "    ") + bold(l))
    if default:
        out("    " + dim("standard: " + cut(default, w - 16) + "  (enter behåller)"))
    while True:
        try:
            if secret:
                import getpass
                v = getpass.getpass("    › ")
            else:
                v = input("    " + accent("›") + " ")
        except (EOFError, KeyboardInterrupt):
            print()
            raise Cancel()
        v = v.strip()
        if not v:
            v = default
        if not v and required:
            out("    " + yellow("Det här behövs. Ctrl+C avbryter."))
            continue
        if check and v:
            err = check(v)
            if err:
                out("    " + yellow(err))
                continue
        return v


def confirm(question, default=False):
    if not ITTY:
        return default
    opts = "[J/n]" if default else "[j/N]"
    try:
        a = input(fit(f"  {accent('?')} {bold(question)} {dim(opts)}", width() - 5) + " ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not a:
        return default
    return a in ("j", "ja", "y", "yes")


def pause():
    """Vänta på enter (eller esc/q) innan skärmen töms. Läser tangenter rått så att piltangenter inte syns som ^[[A."""
    if ITTY:
        print()
        sys.stdout.write(dim("   enter = fortsätt "))
        sys.stdout.flush()
        kb = Keys()
        try:
            kb.__enter__()
            while True:
                ch = kb.read(0.5)
                if ch is None:
                    continue
                if ch in ("\r", "\n", "\x1b", "q", "\x03", "\x04", " "):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            kb.__exit__()
            print()


# ───────────────────────────── PowerShell-delen som är lika i alla skript ─────────────────────────────
PS_BODY = r'''
# ─────────────── Nedanför är samma i alla skript från Muppa. Ändra inställningarna ovanför. ───────────────
$ErrorActionPreference = 'Continue'
$Torr = [bool]$Torrkorning -or [bool]$WhatIfPreference
$SkriptNamn = if ($PSCommandPath) { Split-Path -Leaf $PSCommandPath } else { 'Migrera.ps1' }
$Faser = @()
if ($Doman) { $Faser += 'doman' }
if ($Entra) { $Faser += 'entra' }
if ($Aterlas) { $Faser += 'aterlas' }
if ($Faser.Count -gt 1) { Write-Host "  Välj en fas i taget: -Doman, -Entra eller -Aterlas." -ForegroundColor Red; exit 2 }
$Fas = if ($Faser.Count) { $Faser[0] } else { 'forbered' }

# ── utskrift och frågor ──
function Rubrik([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Info([string]$Text) { Write-Host "   $Text" }
function Ok([string]$Text) { Write-Host "   [OK] $Text" -ForegroundColor Green }
function Varning([string]$Text) { Write-Host "   [!]  $Text" -ForegroundColor Yellow }
function Fel([string]$Text) { Write-Host "   [X]  $Text" -ForegroundColor Red }

function Atgard([string]$Text, [scriptblock]$Block) {
    # Kör en ändring, eller visar bara vad som skulle göras vid -Torrkorning / -WhatIf.
    if ($Torr) { Write-Host "   [torrkörning] $Text" -ForegroundColor DarkGray; return }
    Write-Host "   -> $Text"
    & $Block
}

function Fraga([string]$Text, [bool]$Standard = $true) {
    $opt = if ($Standard) { '[J/n]' } else { '[j/N]' }
    if ($Torr) {
        $svar = if ($Standard) { 'ja' } else { 'nej' }
        Write-Host "   $Text $opt  (torrkörning: $svar)" -ForegroundColor DarkGray
        return $Standard
    }
    $s = Read-Host "   $Text $opt"
    if ([string]::IsNullOrWhiteSpace($s)) { return $Standard }
    return @('j', 'ja', 'y', 'yes') -contains $s.Trim().ToLower()
}

function Vanta([string]$Text) {
    if ($Torr) { return }
    Read-Host "   $Text" | Out-Null
}

function AdminVem {
    if ($Cfg.AdminNamn) { return ".\$($Cfg.AdminNamn)" }
    return 'ett lokalt admin-konto'
}

function Format-Storlek([double]$Byte) {
    if ($Byte -ge 1GB) { return ('{0:N1} GB' -f ($Byte / 1GB)) }
    if ($Byte -ge 1MB) { return ('{0:N0} MB' -f ($Byte / 1MB)) }
    return ('{0:N0} kB' -f ($Byte / 1KB))
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Anslutning {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    $ds = @(& dsregcmd.exe /status 2>$null)
    $tenant = ''
    $m = $ds | Select-String -Pattern '^\s*TenantName\s*:\s*(.+)$' | Select-Object -First 1
    if ($m) { $tenant = $m.Matches[0].Groups[1].Value.Trim() }
    return [pscustomobject]@{
        Dator     = $cs.Name
        Doman     = [bool]$cs.PartOfDomain
        DomanNamn = $cs.Domain
        Entra     = [bool]($ds | Select-String -Pattern '^\s*AzureAdJoined\s*:\s*YES' -Quiet)
        Tenant    = $tenant
    }
}

function Get-LokalaAdmins {
    # Get-LocalGroupMember kan krascha på föräldralösa SID:er i PS 5.1, därför net localgroup som reserv.
    try {
        return @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop |
            Where-Object { $_.PrincipalSource -eq 'Local' -and $_.ObjectClass -eq 'User' } |
            ForEach-Object { ($_.Name -split '\\')[-1] })
    } catch {
        $grupp = (Get-LocalGroup -SID 'S-1-5-32-544').Name
        return @(& net.exe localgroup $grupp 2>$null | Select-Object -Skip 6 | Where-Object { $_ -and $_ -notmatch '^-+$|\\' } |
            Select-Object -SkipLast 1)
    }
}

# ── användarens register (User Shell Folders) ──
$script:Hive = $null
$script:HiveLaddad = $false

function Open-AnvandarHive {
    if ($script:Hive) { return $script:Hive }
    $p = Get-CimInstance -ClassName Win32_UserProfile | Where-Object { $_.LocalPath -eq $Cfg.Profil } | Select-Object -First 1
    if ($p -and (Test-Path -LiteralPath "Registry::HKEY_USERS\$($p.SID)")) {
        $script:Hive = "HKEY_USERS\$($p.SID)"            # användaren är inloggad: registret är redan laddat
        return $script:Hive
    }
    $dat = Join-Path $Cfg.Profil 'NTUSER.DAT'
    if (-not (Test-Path -LiteralPath $dat)) { Varning "Hittar inte $dat."; return $null }
    & reg.exe load 'HKU\MuppaHive' $dat 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $script:Hive = 'HKEY_USERS\MuppaHive'
        $script:HiveLaddad = $true
        return $script:Hive
    }
    Varning "Kunde inte läsa registret i $dat. Mapparna antas ligga på standardplatsen."
    return $null
}

function Close-AnvandarHive {
    if (-not $script:HiveLaddad) { return }
    [gc]::Collect()
    [gc]::WaitForPendingFinalizers()
    Start-Sleep -Milliseconds 300
    & reg.exe unload 'HKU\MuppaHive' 2>$null | Out-Null
    $script:HiveLaddad = $false
    $script:Hive = $null
}

function Resolve-Mapp($Mapp, [string]$HiveRot, [string]$ProfilSokvag) {
    # Var mappen faktiskt ligger. Skrivbord/Dokument/Bilder kan vara omdirigerade till OneDrive.
    if ($Mapp.Typ -eq 'relativ') { return (Join-Path $ProfilSokvag $Mapp.Varde) }
    if ($Mapp.Typ -eq 'absolut') { return $Mapp.Varde }
    $varde = $null
    if ($HiveRot) {
        try {
            $k = Get-Item -LiteralPath "Registry::$HiveRot\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" -ErrorAction Stop
            $varde = $k.GetValue($Mapp.Varde, $null, 'DoNotExpandEnvironmentNames')
        } catch { }
    }
    if (-not $varde) { return (Join-Path $ProfilSokvag $Mapp.Standard) }
    $varde = $varde -ireplace '%USERPROFILE%', $ProfilSokvag.Replace('$', '$$')
    return [Environment]::ExpandEnvironmentVariables($varde).TrimEnd('\')
}

function Measure-Mapp([string]$Sokvag) {
    # Lokala filer och filer som bara finns i OneDrive-molnet (Files On-Demand).
    $r = [pscustomobject]@{ Filer = 0; Byte = [int64]0; MolnFiler = 0; MolnByte = [int64]0 }
    if (-not (Test-Path -LiteralPath $Sokvag)) { return $r }
    Get-ChildItem -LiteralPath $Sokvag -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $a = [int64]$_.Attributes
        # 0x400000 RECALL_ON_DATA_ACCESS · 0x40000 RECALL_ON_OPEN · 0x1000 OFFLINE
        if (($a -band 0x400000) -or ($a -band 0x40000) -or ($a -band 0x1000)) {
            $r.MolnFiler++
            $r.MolnByte += $_.Length
        } else {
            $r.Filer++
            $r.Byte += $_.Length
        }
    }
    return $r
}

function Get-Ledigt([string]$Sokvag) {
    $rot = [IO.Path]::GetPathRoot($Sokvag).TrimEnd('\')
    $d = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$rot'"
    if ($d) { return [int64]$d.FreeSpace }
    return [int64]-1
}

function Get-RobocopyText([int]$Kod) {
    if ($Kod -ge 16) { return "allvarligt fel ($Kod): ingenting kopierades. Kontrollera sökvägar och behörighet." }
    $d = @()
    if ($Kod -band 8) { $d += 'vissa filer kunde inte kopieras (se robocopy.log)' }
    if ($Kod -band 4) { $d += 'avvikande filer finns i målet' }
    if ($Kod -band 2) { $d += 'målet har extra filer (de rörs inte)' }
    if ($Kod -band 1) { $d += 'filer kopierades' }
    if ($Kod -eq 0) { $d += 'inget att kopiera, målet är redan aktuellt' }
    return ($d -join ', ')
}

function Invoke-Robocopy([string]$Fran, [string]$Till, [switch]$Backup, [switch]$BehallNyare) {
    $arg = @($Fran.TrimEnd('\'), $Till.TrimEnd('\'), '/E', '/XJ', '/R:1', '/W:1', '/MT:8', '/NP', '/NFL', '/NDL',
             '/XF', 'desktop.ini', "/UNILOG+:$script:RoboLogg", '/TEE')
    if ($Backup) { $arg += '/B' }                      # backup-läge: läser filer även utan egen behörighet
    if ($BehallNyare) { $arg += '/XO' }                # skriv inte över nyare filer i målet
    if ($Cfg.Molnfiler -eq 'hoppa') { $arg += '/XA:O' } # hoppa över filer med Offline-attributet
    if ($Torr) { $arg += '/L' }                        # /L = lista bara, kopiera inget
    & robocopy.exe @arg | Out-Host
    $kod = $LASTEXITCODE
    $text = Get-RobocopyText $kod
    if ($kod -lt 8) { Ok "robocopy: $text" } else { Fel "robocopy: $text" }
    return $kod
}

# ── webbläsare ──
$Webblasare = @{
    edge   = @{ Namn = 'Edge'; Data = 'AppData\Local\Microsoft\Edge\User Data'; Exe = 'msedge.exe'
                Losen = 'edge://settings/passwords'; Import = 'edge://settings/importData' }
    chrome = @{ Namn = 'Chrome'; Data = 'AppData\Local\Google\Chrome\User Data'; Exe = 'chrome.exe'
                Losen = 'chrome://password-manager/settings'; Import = 'chrome://settings/importData' }
}

function Add-BokmarkNod($Sb, $Nod, [int]$Niva, [bool]$Verktygsfalt) {
    $ind = '    ' * $Niva
    if ($Nod.type -eq 'url') {
        $u = [System.Net.WebUtility]::HtmlEncode([string]$Nod.url)
        $n = [System.Net.WebUtility]::HtmlEncode([string]$Nod.name)
        [void]$Sb.AppendLine("$ind<DT><A HREF=""$u"">$n</A>")
        $script:BmAntal++
        return
    }
    $barn = @($Nod.children | Where-Object { $_ })
    if (-not $barn.Count) { return }
    $extra = if ($Verktygsfalt) { ' PERSONAL_TOOLBAR_FOLDER="true"' } else { '' }
    [void]$Sb.AppendLine("$ind<DT><H3$extra>$([System.Net.WebUtility]::HtmlEncode([string]$Nod.name))</H3>")
    [void]$Sb.AppendLine("$ind<DL><p>")
    foreach ($b in $barn) { Add-BokmarkNod $Sb $b ($Niva + 1) $false }
    [void]$Sb.AppendLine("$ind</DL><p>")
}

function ConvertTo-BokmarkHtml([string]$Json, [string]$Html) {
    # Chromium-bokmärken (JSON) → HTML i Netscape-format, som både Edge och Chrome kan importera.
    $data = Get-Content -LiteralPath $Json -Raw -Encoding UTF8 | ConvertFrom-Json
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('<!DOCTYPE NETSCAPE-Bookmark-file-1>')
    [void]$sb.AppendLine('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">')
    [void]$sb.AppendLine('<TITLE>Bookmarks</TITLE>')
    [void]$sb.AppendLine('<H1>Bookmarks</H1>')
    [void]$sb.AppendLine('<DL><p>')
    $script:BmAntal = 0
    foreach ($rot in 'bookmark_bar', 'other', 'synced') {
        $nod = $data.roots.$rot
        if ($nod) { Add-BokmarkNod $sb $nod 1 ($rot -eq 'bookmark_bar') }
    }
    [void]$sb.AppendLine('</DL><p>')
    [IO.File]::WriteAllText($Html, $sb.ToString(), (New-Object System.Text.UTF8Encoding $false))
    return $script:BmAntal
}

function Get-WebbProfiler([string]$Rot) {
    if (-not (Test-Path -LiteralPath $Rot)) { return @() }
    return @(Get-ChildItem -LiteralPath $Rot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'Default' -or $_.Name -like 'Profile *' })
}

function Test-SammaLosen($A, $B) {
    $pa = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($A)
    $pb = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($B)
    try {
        $sa = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pa)
        $sb = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pb)
        return ($sa.Length -gt 0 -and $sa -ceq $sb)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pa)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pb)
    }
}

# ═══════════════════════════════ steg: förbered ═══════════════════════════════
function Steg-info {
    $a = Get-Anslutning
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    Info "Dator:       $($a.Dator)  ($($os.Caption) $($os.Version))"
    Info "Domän:       $(if ($a.Doman) { $a.DomanNamn } else { 'ingen (arbetsgrupp)' })"
    Info "Entra ID:    $(if ($a.Entra) { "ansluten ($($a.Tenant))" } else { 'inte ansluten' })"
    Info "Körs som:    $env:USERDOMAIN\$env:USERNAME"
    Info "Användare:   $($Cfg.Anvandare)  ($($Cfg.Profil))"
    if (-not (Test-Path -LiteralPath $Cfg.Profil)) { Varning "Profilmappen $($Cfg.Profil) finns inte på den här datorn." }
    try {
        $bl = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
        Info "BitLocker:   $($bl.VolumeStatus), skydd $($bl.ProtectionStatus)"
    } catch { Info 'BitLocker:   kunde inte läsas' }
    $admins = Get-LokalaAdmins
    Info "Lokala admins: $(if ($admins) { $admins -join ', ' } else { 'inga lokala konton' })"
    if (-not $Torr) {
        $fil = Join-Path $Cfg.Malmapp 'Datorinfo.txt'
        @("Dator: $($a.Dator)", "OS: $($os.Caption) $($os.Version)", "Domän: $($a.DomanNamn) (med i domän: $($a.Doman))",
          "Entra ID: $($a.Entra) $($a.Tenant)", "Användare: $($Cfg.Anvandare) ($($Cfg.Profil))",
          "Lokala admins: $($admins -join ', ')", "Datum: $(Get-Date -Format 'yyyy-MM-dd HH:mm')") |
            Set-Content -LiteralPath $fil -Encoding UTF8
        Ok "Sparat i $fil"
    }
}

function Steg-kopiera {
    if (-not $Mappar.Count) { Info 'Inga mappar valda.'; return }
    $hive = Open-AnvandarHive
    $lista = @()
    foreach ($m in $Mappar) {
        $lista += [pscustomobject]@{ Mapp = $m; Kalla = (Resolve-Mapp $m $hive $Cfg.Profil); Mal = (Join-Path $Cfg.Malmapp $m.Mal) }
    }
    Close-AnvandarHive

    Info 'Mäter mapparna …'
    $summa = [int64]0; $moln = 0; $molnByte = [int64]0
    foreach ($x in $lista) {
        if (-not (Test-Path -LiteralPath $x.Kalla)) {
            Varning ("{0,-16} finns inte ({1})" -f $x.Mapp.Namn, $x.Kalla)
            $x.Kalla = $null
            continue
        }
        $s = Measure-Mapp $x.Kalla
        $summa += $s.Byte; $moln += $s.MolnFiler; $molnByte += $s.MolnByte
        $od = if ($x.Kalla -match '\\OneDrive') { '  [OneDrive]' } else { '' }
        $mo = if ($s.MolnFiler) { "  + $($s.MolnFiler) bara i molnet" } else { '' }
        Info ("{0,-16} {1,10}  {2,7} filer{3}  {4}{5}" -f $x.Mapp.Namn, (Format-Storlek $s.Byte), $s.Filer, $mo, $x.Kalla, $od)
    }
    if ($Cfg.Molnfiler -ne 'hoppa') { $summa += $molnByte }
    $ledigt = Get-Ledigt $Cfg.Malmapp
    Info ("Totalt {0}, ledigt på {1}: {2}" -f (Format-Storlek $summa), [IO.Path]::GetPathRoot($Cfg.Malmapp), (Format-Storlek $ledigt))
    if ($moln) {
        Varning ("{0} filer ({1}) finns bara i OneDrive-molnet (Files On-Demand)." -f $moln, (Format-Storlek $molnByte))
        if ($Cfg.Molnfiler -eq 'hoppa') {
            Info '     De hoppas över (/XA:O) – de finns redan i OneDrive och kommer tillbaka när användaren loggar in.'
            Info '     Kontrollera att OneDrive är helt synkat innan datorn lämnar domänen.'
        } else {
            Info '     robocopy försöker läsa dem. Det laddar ner dem om OneDrive körs för användaren, annars blir det fel.'
        }
    }
    if ($ledigt -ge 0 -and $summa * 1.05 -gt $ledigt) {
        Fel 'Det får inte plats i målmappen.'
        if (-not (Fraga 'Kopiera ändå?' $false)) { return }
    }
    if (-not $Torr) { New-Item -ItemType Directory -Path $Cfg.Malmapp -Force | Out-Null }
    $fel = 0
    foreach ($x in $lista) {
        if (-not $x.Kalla) { continue }
        Info ''
        Info "$($x.Mapp.Namn): $($x.Kalla) -> $($x.Mal)"
        $kod = Invoke-Robocopy $x.Kalla $x.Mal -Backup
        if ($kod -ge 8) { $fel++ }
    }
    if (-not $Torr) {
        $lista | Where-Object { $_.Kalla } | ForEach-Object { [pscustomobject]@{ Mapp = $_.Mapp.Namn; Fran = $_.Kalla; Till = $_.Mal } } |
            Export-Csv -LiteralPath (Join-Path $Cfg.Malmapp 'Mappar.csv') -NoTypeInformation -Encoding UTF8
    }
    if ($fel) { Varning "$fel mapp(ar) kopierades inte helt. Se $script:RoboLogg." } else { Ok 'Mapparna är kopierade.' }
}

function Steg-bitlocker_spara {
    $vol = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
    if ($vol.VolumeStatus -eq 'FullyDecrypted') { Info "BitLocker är inte på för $env:SystemDrive."; return }
    $rp = @($vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' })
    if (-not $rp.Count) {
        Varning 'Det finns ingen återställningsnyckel (RecoveryPassword).'
        if (Fraga 'Skapa en återställningsnyckel nu?' $true) {
            Atgard 'Add-BitLockerKeyProtector -RecoveryPasswordProtector' {
                Add-BitLockerKeyProtector -MountPoint $env:SystemDrive -RecoveryPasswordProtector -ErrorAction Stop | Out-Null
            }
            $vol = Get-BitLockerVolume -MountPoint $env:SystemDrive
            $rp = @($vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' })
        }
    }
    $fil = Join-Path $Cfg.Malmapp ("BitLocker-$env:COMPUTERNAME.txt")
    Atgard "manage-bde -protectors -get $env:SystemDrive > $fil" {
        & manage-bde.exe -protectors -get $env:SystemDrive | Out-File -LiteralPath $fil -Encoding UTF8
    }
    foreach ($p in $rp) { Info "Nyckel-ID $($p.KeyProtectorId)"; Info "   Nyckel: $($p.RecoveryPassword)" }
    Varning 'Spara nyckeln utanför datorn också (dokumentation/ärende). Målmappen ligger på samma disk.'
    $a = Get-Anslutning
    foreach ($p in $rp) {
        if ($a.Entra) {
            # Till Entra ID: BackupToAAD-BitLockerKeyProtector (Backup-BitLockerKeyProtector skickar till AD DS).
            try {
                Atgard "Säkerhetskopiera nyckeln till Entra ID ($($a.Tenant))" {
                    BackupToAAD-BitLockerKeyProtector -MountPoint $env:SystemDrive -KeyProtectorId $p.KeyProtectorId -ErrorAction Stop | Out-Null
                }
            } catch { Varning "Kunde inte spara nyckeln i Entra ID: $($_.Exception.Message)" }
        }
        if ($a.Doman) {
            try {
                Atgard "Säkerhetskopiera nyckeln till AD ($($a.DomanNamn))" {
                    Backup-BitLockerKeyProtector -MountPoint $env:SystemDrive -KeyProtectorId $p.KeyProtectorId -ErrorAction Stop | Out-Null
                }
            } catch { Varning "Kunde inte spara nyckeln i AD: $($_.Exception.Message)" }
        }
    }
}

function Steg-bitlocker_av {
    $vol = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
    if ($vol.VolumeStatus -eq 'FullyDecrypted') { Info 'BitLocker är redan avstängt.'; return }
    if ($Cfg.BitLocker -eq 'stang-av') {
        Varning 'Disable-BitLocker dekrypterar hela disken. Det kan ta lång tid, men datorn går att använda under tiden.'
        if (-not (Fraga 'Stänga av BitLocker?' $true)) { return }
        Atgard "Disable-BitLocker -MountPoint $env:SystemDrive" {
            Disable-BitLocker -MountPoint $env:SystemDrive -ErrorAction Stop | Out-Null
            Ok 'Dekrypteringen har startat. Följ den med: manage-bde -status'
        }
    } else {
        if ($vol.ProtectionStatus -eq 'Off') { Info 'BitLocker är redan pausat.'; return }
        Atgard "Suspend-BitLocker -MountPoint $env:SystemDrive -RebootCount 0 (pausat tills det slås på igen)" {
            Suspend-BitLocker -MountPoint $env:SystemDrive -RebootCount 0 -ErrorAction Stop | Out-Null
            Ok 'BitLocker är pausat. Slå på igen efter migreringen (görs i -Entra, eller: Resume-BitLocker).'
        }
    }
}

function Steg-bokmarken {
    $ut = Join-Path $Cfg.Malmapp 'Webblasare'
    $antal = 0
    foreach ($b in $Cfg.Webblasare) {
        $w = $Webblasare[$b]
        $profiler = Get-WebbProfiler (Join-Path $Cfg.Profil $w.Data)
        if (-not $profiler.Count) { Info "$($w.Namn): ingen profil hos $($Cfg.Anvandare)."; continue }
        foreach ($p in $profiler) {
            $bm = Join-Path $p.FullName 'Bookmarks'
            if (-not (Test-Path -LiteralPath $bm)) { Info "$($w.Namn) ($($p.Name)): inga bokmärken."; continue }
            $namn = "$($w.Namn)-$($p.Name -replace '\s', '')"
            $antal++
            Atgard "$($w.Namn) ($($p.Name)): kopiera Bookmarks och exportera till $namn-bokmarken.html" {
                New-Item -ItemType Directory -Path $ut -Force | Out-Null
                Copy-Item -LiteralPath $bm -Destination (Join-Path $ut "$namn-Bookmarks.json") -Force
                try {
                    $n = ConvertTo-BokmarkHtml $bm (Join-Path $ut "$namn-bokmarken.html")
                    Ok "$n bokmärken exporterade."
                } catch { Varning "HTML-exporten misslyckades ($($_.Exception.Message)). JSON-filen är kopierad." }
            }
        }
    }
    if (-not $antal) { Info 'Inga bokmärken hittades.' }
}

function Steg-losenord {
    $ut = Join-Path $Cfg.Malmapp 'Webblasare'
    if (-not $Torr) { New-Item -ItemType Directory -Path $ut -Force | Out-Null }
    $sammaAnvandare = ($env:USERPROFILE -eq $Cfg.Profil)
    Info 'Lösenord kan inte exporteras automatiskt. Du sparar dem som CSV själv:'
    foreach ($b in $Cfg.Webblasare) {
        $w = $Webblasare[$b]
        $csv = Join-Path $ut "$($w.Namn)-losenord.csv"
        Info ''
        Info "$($w.Namn):"
        Info "   1. Öppna $($w.Losen)"
        Info '   2. Välj Exportera lösenord (… / Ladda ned fil) och bekräfta med Windows-inloggningen'
        Info "   3. Spara som $csv"
        if ($sammaAnvandare) {
            Atgard "Öppna $($w.Losen)" { try { Start-Process $w.Exe $w.Losen } catch { Varning "Kunde inte starta $($w.Exe)." } }
        } else {
            Varning "Skriptet körs som $env:USERNAME, inte som $($Cfg.Anvandare). Öppna $($w.Namn) i användarens session."
            if (-not $Torr) { try { Set-Clipboard -Value $w.Losen; Info "   (adressen är kopierad)" } catch { } }
        }
        Vanta 'Tryck Enter när CSV-filen är sparad (eller för att hoppa över)'
        if (-not $Torr) {
            if (Test-Path -LiteralPath $csv) { Ok "$csv finns." } else { Varning "Hittar inte $csv. Har den ett annat namn, är det också okej." }
        }
    }
    Varning 'CSV-filerna innehåller lösenord i klartext. Radera dem när de är importerade (-Aterlas frågar).'
}

function Steg-aktivitetsfalt {
    $ut = Join-Path $Cfg.Malmapp 'Aktivitetsfalt'
    $fast = Join-Path $Cfg.Profil 'AppData\Roaming\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'
    if (Test-Path -LiteralPath $fast) {
        $lnk = @(Get-ChildItem -LiteralPath $fast -Force -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer })
        Info "Fästa program ($($lnk.Count)): $(($lnk | ForEach-Object { $_.BaseName }) -join ', ')"
        Atgard "Kopiera genvägarna till $ut\Genvagar" {
            New-Item -ItemType Directory -Path (Join-Path $ut 'Genvagar') -Force | Out-Null
            Copy-Item -Path (Join-Path $fast '*') -Destination (Join-Path $ut 'Genvagar') -Recurse -Force
            $lnk | ForEach-Object { $_.BaseName } | Set-Content -LiteralPath (Join-Path $ut 'Fasta-program.txt') -Encoding UTF8
        }
    } else {
        Info 'Hittar inga fästa genvägar.'
    }
    $hive = Open-AnvandarHive
    if ($hive) {
        $nyckel = "$hive\Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband"
        Atgard "Exportera registret Taskband till $ut\Taskband.reg" {
            New-Item -ItemType Directory -Path $ut -Force | Out-Null
            & reg.exe export $nyckel (Join-Path $ut 'Taskband.reg') /y 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { Ok 'Taskband.reg sparad (som referens, importera inte rakt av).' } else { Varning 'Taskband-nyckeln saknas.' }
        }
    }
    Close-AnvandarHive
    Info 'Windows 11 sparar inte hela aktivitetsfältet där. Skärmdumpen är facit.'
}

function Steg-skarmdump {
    if (-not (Fraga 'Ta en skärmdump av skärmen nu (visa aktivitetsfältet)?' $true)) { return }
    Atgard "Skärmdump till $($Cfg.Malmapp)" {
        Info 'Skärmdumpen tas om 5 sekunder …'
        Start-Sleep -Seconds 5
        try {
            Add-Type -Namespace Muppa -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();' -ErrorAction SilentlyContinue
            [void][Muppa.Dpi]::SetProcessDPIAware()
        } catch { }
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $yta = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bild = New-Object System.Drawing.Bitmap $yta.Width, $yta.Height
        $g = [System.Drawing.Graphics]::FromImage($bild)
        $g.CopyFromScreen($yta.Left, $yta.Top, 0, 0, $bild.Size)
        New-Item -ItemType Directory -Path $Cfg.Malmapp -Force | Out-Null
        $fil = Join-Path $Cfg.Malmapp ('Skarmdump-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.png')
        $bild.Save($fil, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bild.Dispose()
        Ok "Sparad: $fil"
    }
}

function Steg-admin {
    $namn = $Cfg.AdminNamn
    $finns = Get-LocalUser -Name $namn -ErrorAction SilentlyContinue
    if ($finns) {
        Info "Kontot $namn finns redan$(if (-not $finns.Enabled) { ' (inaktiverat)' })."
        $nyttLosen = Fraga "Sätta nytt lösenord för $namn?" $false
    } else {
        $nyttLosen = $true
    }
    if ($Torr) {
        Atgard "$(if ($finns) { 'Uppdatera' } else { 'Skapa' }) lokalt konto $namn och lägg det i Administratörer" { }
        return
    }
    if ($nyttLosen) {
        while ($true) {
            # Lösenordet frågas här och sparas ingenstans – det står aldrig i skriptet.
            $p1 = Read-Host "   Lösenord för $namn" -AsSecureString
            $p2 = Read-Host '   Samma lösenord igen' -AsSecureString
            if (-not (Test-SammaLosen $p1 $p2)) { Varning 'Lösenorden var tomma eller olika. Försök igen.'; continue }
            try {
                if ($finns) {
                    Set-LocalUser -Name $namn -Password $p1 -ErrorAction Stop
                } else {
                    New-LocalUser -Name $namn -Password $p1 -FullName $namn -Description 'Lokalt admin-konto (migrering)' `
                        -PasswordNeverExpires -AccountNeverExpires -ErrorAction Stop | Out-Null
                }
                break
            } catch {
                Fel $_.Exception.Message
                if (-not (Fraga 'Försöka med ett annat lösenord?' $true)) { return }
            }
        }
    }
    if ($finns -and -not $finns.Enabled) { Enable-LocalUser -Name $namn }
    try {
        Add-LocalGroupMember -SID 'S-1-5-32-544' -Member $namn -ErrorAction Stop
    } catch {
        if ($_.Exception.Message -notmatch 'already|redan') { Fel "Kunde inte lägga till i Administratörer: $($_.Exception.Message)"; return }
    }
    Ok "$namn är lokal administratör. Logga in som .\$namn."
}

# ═══════════════════════════════ steg: domän, Entra, återläsning ═══════════════════════════════
function Steg-doman {
    $a = Get-Anslutning
    if (-not $a.Doman) { Ok 'Datorn är inte med i någon domän.'; return }
    Info "Datorn $($a.Dator) är med i domänen $($a.DomanNamn)."
    $admins = Get-LokalaAdmins
    if ($Cfg.AdminNamn -and $admins -notcontains $Cfg.AdminNamn) {
        Varning "Det lokala admin-kontot $($Cfg.AdminNamn) finns inte, eller är inte administratör."
    }
    Info "Lokala admins: $(if ($admins) { $admins -join ', ' } else { 'INGA' })"
    if ($env:USERDOMAIN -ne $env:COMPUTERNAME) { Varning "Du är inloggad med domänkontot $env:USERDOMAIN\$env:USERNAME." }
    Varning 'Efter omstarten kan inga domänkonton logga in. Du behöver lösenordet till ett lokalt admin-konto.'
    if ($Torr) {
        Atgard 'Remove-Computer -WorkgroupName WORKGROUP, sedan omstart' { }
        return
    }
    $svar = Read-Host "   Skriv LÄMNA för att ta bort datorn ur $($a.DomanNamn)"
    if (@('lämna', 'lamna') -notcontains $svar.Trim().ToLower()) { Info 'Avbrutet. Datorn är kvar i domänen.'; return }
    $p = @{ WorkgroupName = 'WORKGROUP'; Force = $true; ErrorAction = 'Stop' }
    if (Fraga 'Ange ett domänkonto som får ta bort datorkontot i AD? (nej = nuvarande konto)' $false) {
        $p.UnjoinDomainCredential = Get-Credential -Message 'Domänkonto (DOMÄN\namn) som får ta bort datorn'
    }
    try {
        Remove-Computer @p
    } catch {
        Fel "Remove-Computer misslyckades: $($_.Exception.Message)"
        Info 'Når datorn ingen domänkontrollant? Prova i stället: Systemegenskaper (sysdm.cpl) → Ändra → Arbetsgrupp.'
        return
    }
    Ok 'Datorn har lämnat domänen. Den måste startas om.'
    Info "Efter omstarten: logga in som $(AdminVem) och kör: .\$SkriptNamn -Entra"
    if (Fraga 'Starta om nu?' $true) {
        try { Stop-Transcript | Out-Null } catch { }
        Restart-Computer -Force
    }
}

function Steg-entra {
    $a = Get-Anslutning
    if ($a.Entra) {
        Ok "Datorn är redan ansluten till Entra ID ($($a.Tenant))."
    } else {
        if ($a.Doman) { Varning "Datorn är fortfarande med i domänen $($a.DomanNamn). Kör -Doman först." }
        Info 'Anslutningen till Entra ID görs i Inställningar:'
        Info '   1. Konton → Åtkomst till arbete eller skola → Anslut'
        Info '   2. Klicka på länken längst ner: "Anslut den här enheten till Microsoft Entra ID"'
        Info '      (inte e-postfältet – det registrerar bara ett konto)'
        Info "   3. Logga in med användarens konto$(if ($Cfg.Upn) { ": $($Cfg.Upn)" })"
        Info '   4. Kontrollera organisationen, klicka på Anslut och sedan Klar'
        Atgard 'Öppna ms-settings:workplace' { Start-Process 'ms-settings:workplace' }
        while (-not $Torr) {
            Vanta 'Tryck Enter när anslutningen är klar'
            $a = Get-Anslutning
            if ($a.Entra) { Ok "Ansluten till Entra ID ($($a.Tenant))."; break }
            Varning 'dsregcmd /status visar inte AzureAdJoined : YES än.'
            if (-not (Fraga 'Kontrollera igen?' $true)) { break }
        }
    }
    try { $vol = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop } catch { $vol = $null }
    if ($vol -and $vol.VolumeStatus -ne 'FullyDecrypted') {
        if ($a.Entra -and $Steg.bitlocker_spara) {
            foreach ($p in @($vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' })) {
                try {
                    Atgard 'Säkerhetskopiera BitLocker-nyckeln till Entra ID' {
                        BackupToAAD-BitLockerKeyProtector -MountPoint $env:SystemDrive -KeyProtectorId $p.KeyProtectorId -ErrorAction Stop | Out-Null
                        Ok 'Nyckeln finns nu på enheten i Entra ID.'
                    }
                } catch { Varning "Kunde inte spara nyckeln i Entra ID: $($_.Exception.Message)" }
            }
        }
        if ($vol.ProtectionStatus -eq 'Off' -and $vol.VolumeStatus -eq 'FullyEncrypted') {
            if (Fraga 'BitLocker är pausat. Slå på det igen (Resume-BitLocker)?' $true) {
                Atgard 'Resume-BitLocker' { Resume-BitLocker -MountPoint $env:SystemDrive -ErrorAction Stop | Out-Null; Ok 'BitLocker är på igen.' }
            }
        }
    }
    Info ''
    Info 'Starta om och logga in som användaren (Annan användare → e-postadressen).'
    Info 'Windows Hello: hoppa över vid första inloggningen.'
    Info "Kör sedan som användaren (inte som administratör): .\$SkriptNamn -Aterlas"
}

function Steg-aterlas {
    if ($env:USERPROFILE -eq $Cfg.Profil) {
        Varning "Du är inloggad med den gamla profilen ($($Cfg.Profil)). Återläsningen ska göras i den nya profilen."
        if (-not (Fraga 'Fortsätta ändå?' $false)) { return }
    }
    if (Test-Admin) { Varning "Skriptet körs som administratör. Kopiorna hamnar i profilen för $env:USERNAME ($env:USERPROFILE)." }
    Info "Ny profil: $env:USERPROFILE"
    if ($Steg.kopiera -and $Mappar.Count) {
        if (Get-Process -Name 'Microsoft.Notes' -ErrorAction SilentlyContinue) { Varning 'Stäng Sticky Notes innan återläsningen.' }
        foreach ($m in $Mappar) {
            $fran = Join-Path $Cfg.Malmapp $m.Mal
            if (-not (Test-Path -LiteralPath $fran)) { Info "$($m.Namn): ingen kopia i $fran."; continue }
            $till = Resolve-Mapp $m 'HKEY_CURRENT_USER' $env:USERPROFILE
            Info ''
            Info "$($m.Namn): $fran -> $till"
            Invoke-Robocopy $fran $till -BehallNyare | Out-Null
        }
    }

    Rubrik 'OneDrive'
    $od = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\OneDrive\Accounts\Business1' -ErrorAction SilentlyContinue
    if ($od -and $od.UserEmail) { Ok "OneDrive är inloggat som $($od.UserEmail)." } else { Varning 'OneDrive (jobb/skola) är inte inloggat än.' }
    if (Get-Process -Name 'OneDrive' -ErrorAction SilentlyContinue) { Ok 'OneDrive körs.' } else {
        Varning 'OneDrive körs inte.'
        $exe = @("$env:LOCALAPPDATA\Microsoft\OneDrive\OneDrive.exe", "$env:ProgramFiles\Microsoft OneDrive\OneDrive.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($exe) { Atgard 'Starta OneDrive' { Start-Process $exe } }
    }
    foreach ($n in 'Desktop', 'Personal', 'My Pictures') {
        $v = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue).$n
        $v = [Environment]::ExpandEnvironmentVariables([string]$v)
        $t = if ($v -match '\\OneDrive') { 'säkerhetskopieras av OneDrive' } else { 'inte i OneDrive' }
        Info ("{0,-12} {1}  ({2})" -f $n, $v, $t)
    }

    $wmapp = Join-Path $Cfg.Malmapp 'Webblasare'
    if ($Cfg.Webblasare.Count -and (Test-Path -LiteralPath $wmapp)) {
        Rubrik 'Webbläsare'
        Get-ChildItem -LiteralPath $wmapp -File | ForEach-Object { Info $_.Name }
        foreach ($b in $Cfg.Webblasare) {
            $w = $Webblasare[$b]
            Info "$($w.Namn): logga in, importera bokmärken (*-bokmarken.html) via $($w.Import)"
            Info "         och lösenord (CSV) via $($w.Losen) → Importera."
            Atgard "Öppna $($w.Import)" { try { Start-Process $w.Exe $w.Import } catch { Varning "Kunde inte starta $($w.Exe)." } }
        }
        Atgard "Öppna $wmapp" { Invoke-Item -LiteralPath $wmapp }
        Vanta 'Tryck Enter när bokmärken och lösenord är importerade'
        $csv = @(Get-ChildItem -LiteralPath $wmapp -Filter '*.csv' -File -ErrorAction SilentlyContinue)
        if ($csv.Count -and (Fraga "Radera $($csv.Count) CSV-fil(er) med lösenord nu?" $false)) {
            Atgard 'Radera lösenordsfilerna' { $csv | Remove-Item -Force; Ok 'Raderade.' }
        }
    }

    $amapp = Join-Path $Cfg.Malmapp 'Aktivitetsfalt'
    $bild = Get-ChildItem -LiteralPath $Cfg.Malmapp -Filter 'Skarmdump-*.png' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($bild -or (Test-Path -LiteralPath $amapp)) {
        Rubrik 'Aktivitetsfältet'
        Info 'Fäst programmen enligt skärmdumpen (högerklicka → Fäst i aktivitetsfältet).'
        if ($bild) { Atgard "Öppna $($bild.Name)" { Invoke-Item -LiteralPath $bild.FullName } }
        if (Test-Path -LiteralPath (Join-Path $amapp 'Fasta-program.txt')) {
            Info "Tidigare fästa: $((Get-Content -LiteralPath (Join-Path $amapp 'Fasta-program.txt')) -join ', ')"
        }
    }

    Rubrik 'Outlook och Teams'
    if (Fraga 'Starta Outlook (skapar profilen)?' $true) {
        Atgard 'Starta Outlook' { try { Start-Process 'outlook.exe' } catch { Varning 'Hittar inte Outlook.' } }
    }
    if (Fraga 'Starta Teams?' $true) {
        Atgard 'Starta Teams' {
            try { Start-Process 'ms-teams.exe' -ErrorAction Stop } catch { try { Start-Process 'msteams:' } catch { Varning 'Hittar inte Teams.' } }
        }
    }
    Info 'Kontrollera att Teams och Outlook använder rätt konto, och synka Teams-/SharePoint-bibliotek (Synkronisera i webben).'
}

# ═══════════════════════════════ start ═══════════════════════════════
$StegTitel = @{
    info = 'Datorinformation'; kopiera = 'Kopiera mappar'; bitlocker_spara = 'Spara BitLocker-nyckeln'
    bitlocker_av = $(if ($Cfg.BitLocker -eq 'stang-av') { 'Stäng av BitLocker' } else { 'Pausa BitLocker' })
    bokmarken = 'Bokmärken'; losenord = 'Lösenord (manuell export)'; aktivitetsfalt = 'Aktivitetsfältet'
    skarmdump = 'Skärmdump'; admin = "Lokalt admin-konto ($($Cfg.AdminNamn))"
    doman = 'Lämna domänen'; entra = 'Anslut till Entra ID'; aterlas = 'Återläsning'
}

function Kor-Steg([string[]]$Lista) {
    $valda = @($Lista | Where-Object { $Steg[$_] })
    if (-not $valda.Count) { Info 'Inga steg är valda för den här fasen.'; return }
    $n = 0
    foreach ($id in $valda) {
        $n++
        Rubrik ('Steg {0}/{1}: {2}' -f $n, $valda.Count, $StegTitel[$id])
        try {
            & ('Steg-' + $id)
        } catch {
            Fel ('{0} misslyckades: {1}' -f $StegTitel[$id], $_.Exception.Message)
            if (-not (Fraga 'Fortsätta med nästa steg?' $true)) { throw 'Avbrutet.' }
        }
    }
}

if ($PSVersionTable.PSVersion.Major -gt 5) {
    Write-Host '  Tips: skriptet är gjort för Windows PowerShell 5.1 (powershell.exe). BitLocker- och LocalAccounts-cmdletarna finns inte alltid i pwsh.' -ForegroundColor Yellow
}
if ($env:OS -ne 'Windows_NT') { Write-Host '  Skriptet körs bara på Windows.' -ForegroundColor Red; exit 1 }
if ($Fas -ne 'aterlas' -and -not (Test-Admin)) {
    Write-Host '  Kör skriptet som administratör (högerklicka PowerShell → Kör som administratör).' -ForegroundColor Red
    exit 1
}
if ($Fas -eq 'aterlas' -and -not (Test-Path -LiteralPath $Cfg.Malmapp)) {
    Write-Host "  Hittar inte målmappen $($Cfg.Malmapp). Ändra Malmapp högst upp i skriptet." -ForegroundColor Red
    exit 1
}

$loggMapp = $Cfg.Malmapp
if ($Torr -and -not (Test-Path -LiteralPath $loggMapp)) { $loggMapp = $env:TEMP }
try { New-Item -ItemType Directory -Path $loggMapp -Force -WhatIf:$false -ErrorAction Stop | Out-Null } catch { $loggMapp = $env:TEMP }
$script:RoboLogg = Join-Path $loggMapp 'robocopy.log'
$logg = Join-Path $loggMapp ('Muppa-{0}-{1}.log' -f $Fas, (Get-Date -Format 'yyyyMMdd-HHmmss'))
$transkript = $false
try { Start-Transcript -Path $logg -Append -WhatIf:$false | Out-Null; $transkript = $true } catch { Write-Host "  Kunde inte starta loggen: $($_.Exception.Message)" -ForegroundColor Yellow }

$fasNamn = @{ forbered = 'förbered'; doman = 'lämna domänen'; entra = 'anslut till Entra ID'; aterlas = 'återläsning' }[$Fas]
Write-Host ''
Write-Host "  Muppa · migrering av $($Cfg.Anvandare) · fas: $fasNamn$(if ($Torr) { ' · TORRKÖRNING, inget ändras' })" -ForegroundColor Magenta
if ($Cfg.Kund) { Write-Host "  Kund: $($Cfg.Kund)" }
Write-Host "  Målmapp: $($Cfg.Malmapp) · logg: $logg"

try {
    switch ($Fas) {
        'forbered' {
            if ($PSCommandPath -and -not $Torr) {
                $kopia = Join-Path $Cfg.Malmapp $SkriptNamn
                if ($PSCommandPath -ne $kopia) {
                    Copy-Item -LiteralPath $PSCommandPath -Destination $kopia -Force
                    Write-Host "  Skriptet finns nu också i $kopia (kör de andra faserna därifrån)."
                }
            }
            $plan = @('info', 'kopiera', 'bitlocker_spara', 'bitlocker_av', 'bokmarken', 'losenord', 'aktivitetsfalt', 'skarmdump', 'admin')
            Write-Host "  Steg: $((@($plan | Where-Object { $Steg[$_] }) | ForEach-Object { $StegTitel[$_] }) -join ' · ')"
            if (-not (Fraga 'Starta?' $true)) { throw 'Avbrutet.' }
            Kor-Steg $plan
            Rubrik 'Nästa steg'
            $skript = Join-Path $Cfg.Malmapp $SkriptNamn
            if ($Steg.doman) {
                Info "1. Logga ut och logga in som $(AdminVem)"
                Info "2. Kör som administratör: powershell -ExecutionPolicy Bypass -File `"$skript`" -Doman"
            } elseif ($Steg.entra) {
                Info "Kör som administratör: powershell -ExecutionPolicy Bypass -File `"$skript`" -Entra"
            } elseif ($Steg.aterlas) {
                Info "Logga in som användaren och kör: powershell -ExecutionPolicy Bypass -File `"$skript`" -Aterlas"
            }
            Info 'Resten står i checklistan.'
        }
        'doman' { Kor-Steg @('doman') }
        'entra' { Kor-Steg @('entra') }
        'aterlas' { Kor-Steg @('aterlas') }
    }
    Write-Host ''
    Write-Host "  Klart: $fasNamn." -ForegroundColor Green
} catch {
    Fel "$($_.Exception.Message)"
} finally {
    Close-AnvandarHive
    if ($transkript) { try { Stop-Transcript | Out-Null } catch { } }
}
'''


def choose_many(rows, chosen, title="", msg="", mood="happy", sub="", footer=None):
    """Flerval med kryssrutor. rows: ("head", text) eller dict(label, hint, value[, action=True]).
    Mellanslag eller siffra kryssar, + markerar alla/inga, enter är klar (på en action-rad: kör den).
    Börja skriva för att söka. Returnerar (valda värden i radordning, action-värde eller None), None vid avbrott."""
    if not ITTY:
        raise Cancel()
    chosen = set(chosen)
    st = {"q": "", "pos": 0, "top": 0, "mood": None}
    items = [r for r in rows if not isinstance(r, tuple)]
    toggles = [r["value"] for r in items if not r.get("action")]

    def view():
        if not st["q"]:
            return rows
        scored = []
        for n, r in enumerate(items):
            s = fuzzy_score(st["q"], r["label"] + " " + ANSI.sub("", r.get("hint") or ""))
            if s is not None:
                scored.append((-s, n, r))
        return [r for _, _, r in sorted(scored, key=lambda x: (x[0], x[1]))]

    def numbering(vr):
        sel = [k for k, r in enumerate(vr) if not isinstance(r, tuple)]
        return sel, {str(n + 1): k for n, k in enumerate(sel[:9])}

    vr = view()
    sel, keys = numbering(vr)
    st["pos"] = sel[0] if sel else 0
    live = Live()
    winch = {"flag": False}
    t0 = time.time()

    def draw(mood_now):
        w = width()
        lines = []
        if msg and not st["q"]:
            lines += frame(msg, mood_now, sub=sub)
        lines.append("")
        n_on = len([v for v in toggles if v in chosen])
        if st["q"]:
            lines.append("  " + accent("⌕ ") + bold(st["q"]) + accent("▏") + dim(f"  {len(sel)} träffar"))
        elif title:
            lines.append("  " + accent("▍") + bold(title) + dim(f"  {n_on} valda"))
        narrow = w < 58
        if not sel:
            lines.append("     " + dim("Inga träffar. Backsteg ändrar, esc rensar."))
        labels = [vr[k]["label"] for k in sel] or [""]
        lw = min(max(len(x) for x in labels) + 2, max(10, w - 16 if narrow else min(40, w // 2)))
        room = max(3, height() - len(lines) - 3)
        pos = st["pos"]
        top = min(max(st["top"], pos - room + 1), pos)
        top = max(0, min(top, max(0, len(vr) - room)))
        st["top"] = top
        if top > 0:
            lines.append(dim("     ↑ fler"))
        num = {k: n for n, k in keys.items()}
        for k in range(top, min(len(vr), top + room)):
            r = vr[k]
            if isinstance(r, tuple):
                lines.append("   " + dim(cut(r[1].upper(), w - 5)))
                continue
            on = k == pos
            key = num.get(k, " ")
            if r.get("action"):
                box = accent(" + ")
            else:
                box = "[" + (green("✔") if r["value"] in chosen else " ") + "]"
            label = cut(r["label"], lw - 1)
            label = bold(accent(label)) if on else label
            if narrow:
                line = f"  {accent('❯') if on else ' '}{dim(key)} {box} {label}"
            else:
                line = f"   {accent('❯') if on else ' '} {dim(key)} {box} " + pad(label, lw)
                if r.get("hint") and w >= 64:
                    line += dim(ANSI.sub("", r["hint"]))
            lines.append(line)
        if top + room < len(vr):
            lines.append(dim("     ↓ fler"))
        lines.append("")
        if st["q"]:
            help_ = "mellanslag kryssar · enter klar · esc rensar" if w >= 50 else "␣ kryss · enter · esc"
        else:
            help_ = (footer or "↑/↓ · mellanslag/siffra kryssar · + alla/inga · enter klar · q avbryt") if w >= 80 else \
                "␣/siffra kryssar · + alla · enter klar" if w >= 50 else "␣ kryss · enter klar"
        lines.append(dim("   " + help_))
        live.draw(lines)

    def refilter():
        nonlocal vr, sel, keys
        vr = view()
        sel, keys = numbering(vr)
        st["pos"] = sel[0] if sel else 0
        st["top"] = 0

    def toggle(k):
        r = vr[k]
        if r.get("action"):
            return r["value"]
        chosen.symmetric_difference_update({r["value"]})
        return None

    def on_winch(*_):
        winch["flag"] = True

    old_winch = signal.signal(signal.SIGWINCH, on_winch) if hasattr(signal, "SIGWINCH") else None
    size = shutil.get_terminal_size((100, 30))
    result = None
    kb = Keys()
    try:
        kb.__enter__()
        draw(mood)
        while True:
            ch = kb.read(0.12)
            if ch is None:
                now = shutil.get_terminal_size((100, 30))
                if now != size:
                    size = now
                    winch["flag"] = True
                m = mood
                if ANIM and not st["q"]:
                    im = idle_mood(time.time() - t0)
                    m = im if mood == "happy" else ("blink" if im == "blink" else mood)
                if winch["flag"] or m != st["mood"]:
                    winch["flag"] = False
                    st["mood"] = m
                    draw(m)
                continue
            idx = sel.index(st["pos"]) if st["pos"] in sel else 0
            if ch in ("\x1b[A", "\x1bOA"):
                if sel:
                    st["pos"] = sel[(idx - 1) % len(sel)]
            elif ch in ("\x1b[B", "\x1bOB"):
                if sel:
                    st["pos"] = sel[(idx + 1) % len(sel)]
            elif ch == " ":
                if sel:
                    act = toggle(st["pos"])
                    if act is not None:
                        result = act
                        break
            elif ch in ("\r", "\n"):
                if sel and vr[st["pos"]].get("action"):
                    result = vr[st["pos"]]["value"]
                else:
                    result = ""
                break
            elif ch in ("\x7f", "\x08"):
                if st["q"]:
                    st["q"] = st["q"][:-1]
                    refilter()
            elif ch == "\x1b":
                if st["q"]:
                    st["q"] = ""
                    refilter()
                else:
                    break
            elif ch in ("\x03", "\x04"):
                break
            elif not st["q"] and ch.isdigit() and ch in keys:
                st["pos"] = keys[ch]
                act = toggle(keys[ch])
                if act is not None:
                    result = act
                    break
            elif not st["q"] and ch == "+":
                if all(v in chosen for v in toggles):
                    chosen.difference_update(toggles)
                else:
                    chosen.update(toggles)
            elif not st["q"] and ch == "q":
                break
            elif ch and not ch.startswith("\x1b") and ch.isprintable():
                st["q"] += ch
                refilter()
            draw(st["mood"] or mood)
    except KeyboardInterrupt:
        result = None
    finally:
        if old_winch is not None:
            signal.signal(signal.SIGWINCH, old_winch)
        kb.__exit__()
        live.clear()
        sys.stdout.flush()
    if result is None:
        return None
    picked = [r["value"] for r in items if not r.get("action") and r["value"] in chosen]
    if result == "":
        names = [r["label"] for r in items if not r.get("action") and r["value"] in chosen]
        out(f"   {accent('❯')} " + (bold(", ".join(names)) if names else dim("inget valt")))
    return picked, (result or None)


def view_text(text, title):
    """Bläddra i en text (skriptet, checklistan). ↑/↓, PgUp/PgDn, mellanslag, g/G, q/enter/esc stänger."""
    lines = text.splitlines()
    if not ITTY or not TTY:
        print(text)
        return
    st = {"top": 0}
    size = shutil.get_terminal_size((100, 30))
    winch = {"flag": False}

    def color(l):
        s = l.strip()
        if s.startswith("#") or s.startswith("<#") or s.startswith("#>"):
            return dim(l)
        if s.startswith("function ") or s.startswith("## ") or s.startswith("# "):
            return accent(l)
        if s.startswith("- [ ]"):
            return l.replace("- [ ]", accent("☐"), 1)
        return l

    def draw():
        w, h = width(), height()
        room = max(3, h - 3)
        st["top"] = max(0, min(st["top"], max(0, len(lines) - room)))
        nw = len(str(len(lines)))
        buf = ["\033[H\033[2J" if True else "", fit("  " + accent("▍") + bold(title) +
               dim(f"  rad {st['top'] + 1}–{min(len(lines), st['top'] + room)} av {len(lines)}"), w)]
        for k in range(st["top"], min(len(lines), st["top"] + room)):
            num = dim(str(k + 1).rjust(nw)) + " " if w >= 58 else ""
            buf.append(fit(" " + num + color(lines[k].replace("\t", "    ")), w))
        buf.append(fit(dim("   ↑/↓ · PgUp/PgDn · mellanslag · g/G · q stänger" if w >= 50 else "   ↑↓ ␣ · q"), w))
        sys.stdout.write(buf[0] + "\n".join(buf[1:]))
        sys.stdout.flush()

    def on_winch(*_):
        winch["flag"] = True

    old_winch = signal.signal(signal.SIGWINCH, on_winch) if hasattr(signal, "SIGWINCH") else None
    kb = Keys()
    try:
        kb.__enter__()
        draw()
        while True:
            ch = kb.read(0.15)
            room = max(3, height() - 3)
            if ch is None:
                now = shutil.get_terminal_size((100, 30))
                if now != size or winch["flag"]:
                    size = now
                    winch["flag"] = False
                    draw()
                continue
            if ch in ("q", "\x1b", "\r", "\n", "\x03", "\x04"):
                break
            if ch in ("\x1b[A", "\x1bOA", "k"):
                st["top"] -= 1
            elif ch in ("\x1b[B", "\x1bOB", "j"):
                st["top"] += 1
            elif ch in ("\x1b[5~", "b"):
                st["top"] -= room
            elif ch in ("\x1b[6~", " "):
                st["top"] += room
            elif ch in ("g", "\x1b[H"):
                st["top"] = 0
            elif ch in ("G", "\x1b[F"):
                st["top"] = len(lines)
            draw()
    except KeyboardInterrupt:
        pass
    finally:
        if old_winch is not None:
            signal.signal(signal.SIGWINCH, old_winch)
        kb.__exit__()
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()


# ───────────────────────────── lagring ─────────────────────────────
def private(path):
    try:
        os.chmod(path, 0o700 if Path(path).is_dir() else 0o600)
    except OSError:
        pass


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    DIR.mkdir(parents=True, exist_ok=True)
    private(DIR)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


# ───────────────────────────── vad som kan migreras ─────────────────────────────
# id, namn, typ, värde (User Shell Folders-namn eller sökväg i profilen), standardmapp, mapp i målmappen, förvald
FOLDERS = [
    ("skrivbord", "Skrivbord", "shell", "Desktop", "Desktop", "Skrivbord", True),
    ("dokument", "Dokument", "shell", "Personal", "Documents", "Dokument", True),
    ("bilder", "Bilder", "shell", "My Pictures", "Pictures", "Bilder", True),
    ("hamtade", "Hämtade filer", "shell", "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads", "Hamtade", True),
    ("musik", "Musik", "shell", "My Music", "Music", "Musik", False),
    ("videor", "Videor", "shell", "My Video", "Videos", "Videor", False),
    ("favoriter", "Favoriter", "shell", "Favorites", "Favorites", "Favoriter", False),
    ("kontakter", "Kontakter", "shell", "{56784854-C6CB-462B-8169-88E350ACB882}", "Contacts", "Kontakter", False),
    ("sticky", "Sticky Notes", "relativ",
     r"AppData\Local\Packages\Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe\LocalState", "", "StickyNotes", False),
    ("signaturer", "Outlook-signaturer", "relativ", r"AppData\Roaming\Microsoft\Signatures", "", "Signaturer", False),
]
FOLDER_HINT = {
    "skrivbord": "kan vara omdirigerad till OneDrive", "dokument": "kan vara omdirigerad till OneDrive",
    "bilder": "kan vara omdirigerad till OneDrive", "hamtade": "Downloads",
    "favoriter": "Internet Explorer-favoriter (inte Edge)", "kontakter": "Windows-kontakter (.contact)",
    "sticky": r"AppData\Local\Packages\…StickyNotes…\LocalState",
    "signaturer": r"AppData\Roaming\Microsoft\Signatures",
}
FOLDER_BY_ID = {f[0]: f for f in FOLDERS}
BROWSERS = [("edge", "Microsoft Edge"), ("chrome", "Google Chrome")]
BITLOCKER = [("pausa", "Spara nyckeln och pausa BitLocker", "rekommenderas: Suspend-BitLocker, slås på igen i -Entra"),
             ("stang-av", "Spara nyckeln och stäng av BitLocker", "Disable-BitLocker: dekrypterar hela disken"),
             ("spara", "Bara spara nyckeln", "rör inte skyddet"),
             ("ingen", "Rör inte BitLocker", "")]
DOMAIN = [("bada", "Lämna domänen och anslut till Entra ID", "-Doman, sedan -Entra"),
          ("doman", "Bara lämna domänen", "-Doman"),
          ("entra", "Bara anslut till Entra ID", "datorn är redan utanför domänen"),
          ("inget", "Inget av dem", "bara kopiering och förberedelser")]
CLOUD = [("hoppa", "Hoppa över filer som bara finns i molnet", "rekommenderas: de finns redan i OneDrive (/XA:O)"),
         ("kopiera", "Försök kopiera dem också", "laddar ner dem om OneDrive körs, annars fel")]

# Steg: id, rubrik, grupp. Alla kan väljas bort.
STEPS = [
    ("m_losenord", "Återställ lösenordet i Entra ID", "före migreringen"),
    ("m_onedrive_fore", "Kontrollera OneDrive-backup", "före migreringen"),
    ("info", "Datorinformation (domän, Entra, BitLocker)", "före migreringen"),
    ("kopiera", "Kopiera valda mappar till målmappen", "före migreringen"),
    ("bitlocker_spara", "Spara BitLocker-nyckeln", "före migreringen"),
    ("bitlocker_av", "Pausa / stäng av BitLocker", "före migreringen"),
    ("bokmarken", "Exportera bokmärken", "före migreringen"),
    ("losenord", "Lösenordsexport (du sparar CSV)", "före migreringen"),
    ("aktivitetsfalt", "Spara fästa ikoner i aktivitetsfältet", "före migreringen"),
    ("skarmdump", "Skärmdump av skärmen", "före migreringen"),
    ("admin", "Skapa lokalt admin-konto", "före migreringen"),
    ("m_bitlocker_extern", "Spara BitLocker-nyckeln utanför datorn", "före migreringen"),
    ("doman", "Lämna domänen (-Doman)", "från domän till Entra ID"),
    ("entra", "Anslut till Entra ID (-Entra)", "från domän till Entra ID"),
    ("m_hello", "Windows Hello: hoppa över första gången", "som användaren efteråt"),
    ("aterlas", "Återläsning i nya profilen (-Aterlas)", "som användaren efteråt"),
    ("m_onedrive", "OneDrive-synk kontrollerad", "som användaren efteråt"),
    ("m_edge", "Importera lösenord, logga in i webbläsaren", "som användaren efteråt"),
    ("m_outlook", "Outlook-profil", "som användaren efteråt"),
    ("m_ikoner", "Fäst program enligt skärmdumpen", "som användaren efteråt"),
    ("m_teams", "Teams med rätt konto", "som användaren efteråt"),
    ("m_bibliotek", "Synka Teams- och SharePoint-bibliotek", "som användaren efteråt"),
    ("m_radera", "Radera lösenordsfilerna (CSV)", "som användaren efteråt"),
    ("m_meddela", "Meddela ansvarig att sätta nytt lösenord", "klart"),
]
STEP_IDS = [s[0] for s in STEPS]
STEP_LABEL = {s[0]: s[1] for s in STEPS}
SCRIPT_STEPS = [s for s in STEP_IDS if not s.startswith("m_")]
OPTIONAL_FIELDS = [("kund", "Kund"), ("ansvarig", "Ansvarig (får meddelandet när det är klart)"),
                   ("dator", "Datorns namn"), ("upn", "Användarens e-post (UPN) i Entra ID"),
                   ("tekniker", "Tekniker"), ("arende", "Ärendenummer")]
SHORT_LABEL = {"kund": "Kund", "ansvarig": "Ansvarig", "dator": "Dator", "upn": "E-post (UPN)",
               "tekniker": "Tekniker", "arende": "Ärende"}
TEMPLATE_KEYS = ["folders", "custom", "cloud", "browsers", "bitlocker", "admin", "admin_name", "domain",
                 "on", "off", "malmapp", "kund", "ansvarig", "tekniker"]
DEFAULT_ANS = {"user": "", "profile": "", "malmapp": r"C:\temp\{användare}",
               "folders": [f[0] for f in FOLDERS if f[6]], "custom": [], "cloud": "hoppa",
               "browsers": ["edge"], "bitlocker": "pausa", "admin": True, "admin_name": "lokaladmin",
               "domain": "bada", "on": [], "off": [],
               "kund": "", "ansvarig": "", "dator": "", "upn": "", "tekniker": "", "arende": ""}


def derive_steps(a):
    """Stegen som följer av svaren (innan man kryssat bort/till något själv)."""
    s = {"m_losenord", "m_onedrive_fore", "info", "aktivitetsfalt", "skarmdump", "m_ikoner", "aterlas",
         "m_onedrive", "m_outlook", "m_teams", "m_bibliotek", "m_meddela"}
    if a["folders"] or a["custom"]:
        s.add("kopiera")
    if a["bitlocker"] != "ingen":
        s |= {"bitlocker_spara", "m_bitlocker_extern"}
    if a["bitlocker"] in ("pausa", "stang-av"):
        s.add("bitlocker_av")
    if a["browsers"]:
        s |= {"bokmarken", "losenord", "m_edge", "m_radera"}
    if a["admin"]:
        s.add("admin")
    if a["domain"] in ("bada", "doman"):
        s.add("doman")
    if a["domain"] in ("bada", "entra"):
        s |= {"entra", "m_hello"}
    return s


def final_steps(a):
    return (derive_steps(a) | set(a.get("on") or [])) - set(a.get("off") or [])


def malmapp_for(a):
    return (a.get("malmapp") or DEFAULT_ANS["malmapp"]).replace("{användare}", a["user"] or "anvandare")


def user_pattern(path, user):
    """C:\\temp\\anna → C:\\temp\\{användare}, så att mallen fungerar för nästa användare."""
    if user and path.lower().endswith("\\" + user.lower()):
        return path[: -len(user)] + "{användare}"
    return path


def file_slug(s):
    s = re.sub(r"[^\w.\-]+", "-", s.strip(), flags=re.UNICODE).strip("-.")
    return s or "anvandare"


def win_path_ok(p):
    return bool(re.match(r"^[A-Za-z]:\\", p))


def split_user(v):
    """'anna', 'C:\\Users\\anna' eller 'CONTOSO\\anna' → (användare, profilmapp)."""
    v = v.strip().strip('"').rstrip("\\")
    if win_path_ok(v):
        return v.split("\\")[-1], v
    if "\\" in v:                                    # DOMÄN\namn
        v = v.split("\\")[-1]
    if "@" in v:
        v = v.split("@")[0]
    return v, "C:\\Users\\" + v


def custom_entry(path, profile, n, used):
    """Egen sökväg → mappdefinition. Under profilen = relativ (återläses till nya profilen), annars absolut."""
    p = path.strip().strip('"').rstrip("\\")
    low, prof = p.lower(), profile.lower().rstrip("\\")
    if low.startswith("%userprofile%\\"):
        typ, val = "relativ", p[len("%userprofile%\\"):]
    elif win_path_ok(p) and (low + "\\").startswith(prof + "\\"):
        typ, val = "relativ", p[len(prof) + 1:]
    elif win_path_ok(p):
        typ, val = "absolut", p
    else:
        typ, val = "relativ", p.lstrip("\\")
    leaf = re.sub(r"[^\w.\-]+", "-", (val.split("\\")[-1] or f"Egen{n}"), flags=re.UNICODE).strip("-") or f"Egen{n}"
    name = leaf
    k = 2
    while name.lower() in used:
        name = f"{leaf}-{k}"
        k += 1
    used.add(name.lower())
    return dict(Id=f"egen{n}", Namn=p, Typ=typ, Varde=val, Standard="", Mal="Egna\\" + name)


def folder_entries(a):
    res = []
    for fid in a["folders"]:
        f = FOLDER_BY_ID.get(fid)
        if f:
            res.append(dict(Id=f[0], Namn=f[1], Typ=f[2], Varde=f[3], Standard=f[4], Mal=f[5]))
    used = set()
    for n, p in enumerate(a["custom"], 1):
        res.append(custom_entry(p, a["profile"], n, used))
    return res


# ───────────────────────────── skriptet ─────────────────────────────
def psq(v):
    """PowerShell-sträng med enkla citattecken. PowerShell räknar även ‘ ’ ‚ ‛ som citattecken."""
    return "'" + re.sub(r"(['‘’‚‛])", r"\1\1", str(v)) + "'"


def safe_comment(s):
    return re.sub(r"[<>#\r\n]", "", str(s))


def build_script(a):
    steps = final_steps(a)
    user = a["user"]
    mal = malmapp_for(a)
    name = f"Migrera-{file_slug(user)}.ps1"
    bl = a["bitlocker"]
    if "bitlocker_av" in steps and bl not in ("pausa", "stang-av"):
        bl = "pausa"
    admin = (a.get("admin_name") or "lokaladmin") if "admin" in steps else ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = [("Kund", a.get("kund")), ("Dator", a.get("dator")), ("Ansvarig", a.get("ansvarig")),
            ("Ärende", a.get("arende")), ("Tekniker", a.get("tekniker"))]
    L = []
    L.append("<#")
    L.append(".SYNOPSIS")
    L.append(f"    Migrering från lokal AD-domän till Entra ID: {safe_comment(user)}. Skapat av Muppa {VERSION}, {now}.")
    L.append(".DESCRIPTION")
    L.append("    Körs i Windows PowerShell 5.1 på datorn som migreras, i den här ordningen:")
    L.append(f"      .\\{name}              förbered (som administratör): kopiera, BitLocker, webbläsare, admin-konto")
    if "doman" in steps:
        L.append(f"      .\\{name} -Doman       lämna domänen (efter bekräftelse) och starta om")
    if "entra" in steps:
        L.append(f"      .\\{name} -Entra       anslut till Entra ID (öppnar Inställningar), BitLocker-nyckel till Entra")
    if "aterlas" in steps:
        L.append(f"      .\\{name} -Aterlas     som användaren: kopiera tillbaka, OneDrive, Outlook och Teams")
    L.append("    Provkör först med -Torrkorning (eller -WhatIf): då visas bara vad som skulle göras.")
    L.append("    Inga lösenord står i skriptet. Admin-kontots lösenord frågas efter när skriptet körs.")
    L.append(f"    Starta: powershell.exe -ExecutionPolicy Bypass -File .\\{name} -Torrkorning")
    L.append(".PARAMETER Doman")
    L.append("    Lämna domänen (Remove-Computer) efter bekräftelse.")
    L.append(".PARAMETER Entra")
    L.append("    Öppna Inställningar för anslutning till Entra ID och säkerhetskopiera BitLocker-nyckeln dit.")
    L.append(".PARAMETER Aterlas")
    L.append("    Körs som användaren i den nya profilen: kopiera tillbaka från målmappen.")
    L.append(".PARAMETER Torrkorning")
    L.append("    Visa vad som skulle göras, ändra ingenting (samma som -WhatIf).")
    L.append("#>")
    L.append("[CmdletBinding(SupportsShouldProcess = $true)]")
    L.append("param(")
    L.append("    [switch]$Doman,")
    L.append("    [switch]$Entra,")
    L.append("    [switch]$Aterlas,")
    L.append("    [switch]$Torrkorning")
    L.append(")")
    L.append("")
    L.append("# ─────────────── Inställningar (skapade av Muppa) ───────────────")
    L.append("$Cfg = @{")
    rows = [("Anvandare", user), ("Profil", a["profile"]), ("Malmapp", mal), ("AdminNamn", admin),
            ("BitLocker", bl), ("Molnfiler", a["cloud"]), ("Upn", a.get("upn") or "")] + \
           [(k.replace("Ä", "A"), v or "") for k, v in meta]
    kw = max(len(k) for k, _ in rows + [("Webblasare", "")]) + 1
    for k, v in rows:
        L.append(f"    {k.ljust(kw)}= {psq(v)}")
    L.append("    " + "Webblasare".ljust(kw) + "= @(" + ", ".join(psq(b) for b in a["browsers"]) + ")")
    L.append("}")
    L.append("")
    L.append("# Stegen. $false = hoppas över.")
    L.append("$Steg = @{")
    for sid in SCRIPT_STEPS:
        L.append(f"    {sid.ljust(16)}= " + ("$true" if sid in steps else "$false").ljust(7) + f"  # {STEP_LABEL[sid]}")
    L.append("}")
    L.append("")
    L.append("# Mapparna som kopieras. Typ shell = läses ur användarens User Shell Folders (följer OneDrive-omdirigering).")
    L.append("$Mappar = @(")
    for e in folder_entries(a):
        L.append("    @{ " + "; ".join(f"{k} = {psq(e[k])}" for k in ("Id", "Namn", "Typ", "Varde", "Standard", "Mal")) + " }")
    L.append(")")
    L.append("")
    return name, "\r\n".join(L + PS_BODY.strip("\n").splitlines()) + "\r\n"


# ───────────────────────────── checklistan ─────────────────────────────
def build_checklist(a, script_name):
    steps = final_steps(a)
    user = a["user"]
    mal = malmapp_for(a)
    admin = a.get("admin_name") or "lokaladmin"
    login = f"**.\\{admin}**" if "admin" in steps else "ett lokalt admin-konto (du måste kunna lösenordet)"
    run = f"powershell.exe -ExecutionPolicy Bypass -File {mal}\\{script_name}"
    names = [FOLDER_BY_ID[f][1] for f in a["folders"] if f in FOLDER_BY_ID] + list(a["custom"])
    browsers = " och ".join(dict(BROWSERS)[b].split()[-1] for b in a["browsers"]) or "webbläsaren"
    who = a.get("upn") or user
    L = [f"# Migrering till Entra ID: {user}", ""]
    meta = [("Användare", f"{user} (`{a['profile']}`)")]
    for k, _ in OPTIONAL_FIELDS:
        if a.get(k):
            meta.append((SHORT_LABEL[k], a[k]))
    meta += [("Målmapp", f"`{mal}`"), ("Skapad", f"{datetime.now():%Y-%m-%d %H:%M} med Muppa {VERSION}")]
    L += ["| | |", "|---|---|"] + [f"| {k} | {str(v).replace('|', '/')} |" for k, v in meta] + [""]
    L += [f"Skript: `{script_name}`. Kopiera det till datorn (t.ex. `C:\\temp`) och kör i Windows PowerShell "
          f"som administratör. Första körningen kopierar skriptet till målmappen, så att resten kan köras därifrån.",
          "", "```powershell", f"powershell.exe -ExecutionPolicy Bypass -File .\\{script_name} -Torrkorning   # provkör först",
          f"powershell.exe -ExecutionPolicy Bypass -File .\\{script_name}", "```", ""]

    def box(sid, text, sub=None):
        if sid is None or sid in steps:
            L.append(f"- [ ] {text}")
            for s in sub or []:
                L.append(f"  - [ ] {s}")

    L.append("## 1. Före migreringen")
    L.append("")
    box("m_losenord", "Återställ användarens lösenord i Entra ID (har användaren ett lokalt konto: använd det lokala lösenordet).")
    box("m_onedrive_fore", "Kontrollera att OneDrive säkerhetskopierar Skrivbord, Dokument och Bilder och är helt synkat "
                           "(OneDrive → Inställningar → Synkronisera och säkerhetskopiera).")
    fore = []
    if "info" in steps:
        fore.append(f"Datorinformation sparad (`{mal}\\Datorinfo.txt`)")
    if "kopiera" in steps:
        fore.append(f"Mappar kopierade till `{mal}`: {', '.join(names) or '–'} (läs robocopy-sammanfattningen)")
    if "bitlocker_spara" in steps:
        fore.append(f"BitLocker-nyckeln sparad i `{mal}\\BitLocker-<dator>.txt`")
    if "bitlocker_av" in steps:
        fore.append("BitLocker avstängt (vänta tills `manage-bde -status` visar Fully Decrypted)" if a["bitlocker"] == "stang-av"
                    else "BitLocker pausat")
    if "bokmarken" in steps:
        fore.append(f"Bokmärken exporterade från {browsers} (`Webblasare\\*-bokmarken.html`)")
    if "losenord" in steps:
        fore.append(f"Lösenord exporterade till CSV från {browsers} (`Webblasare\\*-losenord.csv`), i användarens session")
    if "aktivitetsfalt" in steps:
        fore.append("Fästa ikoner sparade (`Aktivitetsfalt\\`)")
    if "skarmdump" in steps:
        fore.append("Skärmdump av aktivitetsfältet sparad")
    if "admin" in steps:
        fore.append(f"Lokalt admin-konto **{admin}** skapat. Lösenordet förvarat säkert (det står inte i skriptet)")
    if fore:
        box(None, f"Kör `{script_name}` som administratör:", fore)
    box("m_bitlocker_extern", "Spara BitLocker-nyckeln utanför datorn (dokumentation/ärende). Målmappen ligger på samma disk.")
    L.append("")

    if "doman" in steps or "entra" in steps:
        L.append("## 2. Byt från domän till Entra ID")
        L.append("")
        box(None, f"Logga ut och logga in som {login}.")
        box("doman", f"Kör `{run} -Doman` och skriv LÄMNA. Starta om.")
        if "entra" in steps:
            box(None, f"Logga in som {login} och kör `{run} -Entra`.")
            box(None, "Inställningar → Konton → Åtkomst till arbete eller skola → Anslut → "
                      "**Anslut den här enheten till Microsoft Entra ID**. Logga in med användarens uppgifter"
                      + (f" ({a['upn']})" if a.get("upn") else "") + ".")
            box(None, "Kontrollera att `dsregcmd /status` visar `AzureAdJoined : YES`. Starta om.")
        L.append("")

    L.append("## 3. Första inloggningen som användaren")
    L.append("")
    box(None, f"Logga in som användaren (Annan användare → {who}).")
    box("m_hello", "Windows Hello: hoppa över vid första inloggningen.")
    if "aterlas" in steps:
        box(None, f"Kör `{run} -Aterlas` som användaren (inte som administratör): kopierar tillbaka, kontrollerar "
                  "OneDrive och öppnar Outlook och Teams.")
    box("m_onedrive", "OneDrive är inloggat och synkar. Skrivbord, Dokument och Bilder finns och säkerhetskopieras.")
    box("m_edge", f"Logga in i {browsers}. Importera bokmärken (HTML) och lösenord (CSV).")
    box("m_outlook", "Starta Outlook så att profilen skapas. Kontrollera signaturen.")
    box("m_ikoner", "Fäst programmen i aktivitetsfältet enligt skärmdumpen.")
    box("m_teams", "Teams är inloggat med rätt konto.")
    box("m_bibliotek", "Synka Teams- och SharePoint-bibliotek (Synkronisera i webbläsaren).")
    box("m_radera", f"Radera lösenordsfilerna (`{mal}\\Webblasare\\*.csv`) när de är importerade.")
    L.append("")
    if "m_meddela" in steps:
        L.append("## Klart")
        L.append("")
        box("m_meddela", "Meddela ansvarig" + (f" ({a['ansvarig']})" if a.get("ansvarig") else "") +
            " att användaren ska sätta ett nytt lösenord.")
        L.append("")
    L += ["## Anteckningar", "", ""]
    return "\n".join(L)


def save_files(a):
    name, ps = build_script(a)
    md = build_checklist(a, name)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p1 = OUT_DIR / name
    p2 = OUT_DIR / f"Checklista-{file_slug(a['user'])}.md"
    with open(p1, "w", encoding="utf-8-sig", newline="") as f:      # BOM: annars läser PS 5.1 åäö fel
        f.write(ps)
    with open(p2, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    return p1, p2


# ───────────────────────────── mallar ─────────────────────────────
def templates():
    t = load_json(TEMPLATES, {})
    return t if isinstance(t, dict) else {}


def template_summary(t):
    parts = ["+".join(FOLDER_BY_ID[f][1].split()[0] for f in t.get("folders", []) if f in FOLDER_BY_ID)
             + (f" +{len(t['custom'])} egna" if t.get("custom") else "")]
    parts.append("+".join(dict(BROWSERS)[b].split()[-1] for b in t.get("browsers", [])) or "ingen webbläsare")
    parts.append({"pausa": "BitLocker paus", "stang-av": "BitLocker av", "spara": "BitLocker-nyckel",
                  "ingen": "utan BitLocker"}.get(t.get("bitlocker"), ""))
    if t.get("admin"):
        parts.append("admin " + (t.get("admin_name") or "lokaladmin"))
    if t.get("kund"):
        parts.append(t["kund"])
    return " · ".join(p for p in parts if p)


def apply_template(a, t):
    for k in TEMPLATE_KEYS:
        if k in t:
            a[k] = list(t[k]) if isinstance(t[k], list) else t[k]
    return a


def save_template(a, name):
    t = {k: a.get(k) for k in TEMPLATE_KEYS}
    t["malmapp"] = user_pattern(a.get("malmapp") or DEFAULT_ANS["malmapp"], a.get("user"))
    t["sparad"] = datetime.now().isoformat(timespec="seconds")
    ts = templates()
    ts[name] = t
    save_json(TEMPLATES, ts)


def new_answers(template=None):
    a = json.loads(json.dumps(DEFAULT_ANS))
    if template:
        apply_template(a, templates().get(template, {}))
    return a


# ───────────────────────────── guiden ─────────────────────────────
def windows_profiles():
    """Profilmappar i C:\\Users (bara när Muppa körs på Windows)."""
    if not IS_WIN:
        return []
    root = Path(os.environ.get("SystemDrive", "C:") + "\\Users")
    skip = {"public", "default", "default user", "all users", "defaultapppool", "wdagutilityaccount"}
    res = []
    try:
        for p in root.iterdir():
            if not p.is_dir() or p.name.lower() in skip:
                continue
            nt = p / "NTUSER.DAT"
            try:
                when = datetime.fromtimestamp(nt.stat().st_mtime).strftime("%Y-%m-%d")
            except OSError:
                when = ""
            res.append((p.name, str(p), when))
    except OSError:
        pass
    return sorted(res, key=lambda x: x[2], reverse=True)


def check_user(v):
    u, prof = split_user(v)
    if not u or re.search(r'[/\[\]:;|=,+*?<>"]', u):
        return "Det ser inte ut som ett användarnamn eller en profilmapp."
    return None


def check_admin(v):
    if len(v) > 20 or re.search(r'[/\\\[\]:;|=,+*?<>@"]', v) or v.strip(". ") == "":
        return "Högst 20 tecken, inga / \\ [ ] : ; | = , + * ? < > @ \"."
    return None


def ask_user(a):
    profs = windows_profiles()
    if profs:
        rows = [dict(label=n, hint=f"{p}" + (f" · senast {w}" if w else ""), value=p) for n, p, w in profs]
        rows.append(dict(label="Skriv själv…", hint="annat namn eller sökväg", value="__egen__"))
        v = choose(rows, title="Användare", start=a["profile"] or None)
        if v is None:
            raise Cancel()
        if v != "__egen__":
            a["user"], a["profile"] = split_user(v)
            return
    v = ask_text("Användarnamn eller profilmapp (t.ex. anna eller C:\\Users\\anna)", default=a["profile"] or "",
                 required=True, check=check_user)
    a["user"], a["profile"] = split_user(v)
    out("    " + dim(f"profil: {a['profile']}"))


def ask_malmapp(a):
    default = malmapp_for(a)

    def check(v):
        if not win_path_ok(v):
            return "Ange en Windows-sökväg, t.ex. C:\\temp\\" + a["user"]
        if (v.lower().rstrip("\\") + "\\").startswith(a["profile"].lower() + "\\"):
            return "Målmappen får inte ligga i profilen som kopieras."
        return None
    v = ask_text("Målmapp på datorn (där allt sparas)", default=default, required=True, check=check).rstrip("\\")
    a["malmapp"] = user_pattern(v, a["user"])


def ask_folders(a):
    chosen = set(a["folders"]) | {"c:" + p for p in a["custom"]}
    customs = list(a["custom"])
    while True:
        rows = [("head", "Profilmappar")]
        rows += [dict(label=f[1], hint=FOLDER_HINT.get(f[0], ""), value=f[0]) for f in FOLDERS]
        if customs:
            rows.append(("head", "Egna sökvägar"))
            rows += [dict(label=p, hint="egen", value="c:" + p) for p in customs]
        rows.append(dict(label="Lägg till egen sökväg…", hint="i profilen eller absolut, t.ex. D:\\Projekt", value="__add__",
                         action=True))
        res = choose_many(rows, chosen, title="Vilka mappar ska kopieras?")
        if res is None:
            raise Cancel()
        picked, act = res
        chosen = set(picked)
        if act == "__add__":
            p = ask_text("Sökväg (relativ till profilen, %USERPROFILE%\\… eller t.ex. D:\\Projekt)")
            if p:
                p = p.strip().strip('"')
                if p not in customs:
                    customs.append(p)
                chosen.add("c:" + p)
            continue
        a["folders"] = [f[0] for f in FOLDERS if f[0] in chosen]
        a["custom"] = [p for p in customs if "c:" + p in chosen]
        return


def ask_choice(title, options, current):
    rows = [dict(label=l, hint=h, value=v) for v, l, h in options]
    v = choose(rows, title=title, start=current)
    if v is None:
        raise Cancel()
    return v


def ask_steps(a):
    derived = derive_steps(a)
    chosen = final_steps(a)
    rows, group = [], None
    for sid, label, g in STEPS:
        if g != group:
            rows.append(("head", g))
            group = g
        kind = "manuellt, i checklistan" if sid.startswith("m_") else "skriptet"
        rows.append(dict(label=label, hint=kind + ("" if sid in derived else " · inte förvalt"), value=sid))
    res = choose_many(rows, chosen, title="Vilka steg ska ingå?")
    if res is None:
        raise Cancel()
    sel = set(res[0])
    a["on"] = sorted(sel - derived)
    a["off"] = sorted(derived - sel)


def wizard(a, intro=True):
    """Alla frågor i ordning. Svaren ligger kvar i a, så att "Ändra svaren" börjar från dem."""
    new_screen()
    if intro:
        say("Vi bygger ett migreringsskript och en checklista. Enter behåller förslaget, Ctrl+C avbryter.", "happy",
            sub=f"muppa {VERSION}")
        print()
    ask_user(a)
    ask_malmapp(a)
    ask_folders(a)
    if any(FOLDER_BY_ID[f][2] == "shell" for f in a["folders"]):
        a["cloud"] = ask_choice("Filer som bara finns i OneDrive-molnet (Files On-Demand)", CLOUD, a["cloud"])
    res = choose_many([dict(label=l, value=v) for v, l in BROWSERS], set(a["browsers"]),
                      title="Webbläsare (bokmärken och lösenord)")
    if res is None:
        raise Cancel()
    a["browsers"] = res[0]
    a["bitlocker"] = ask_choice("BitLocker", BITLOCKER, a["bitlocker"])
    a["admin"] = confirm("Skapa ett lokalt admin-konto?", a["admin"])
    if a["admin"]:
        a["admin_name"] = ask_text("Namn på admin-kontot (lösenordet frågas efter när skriptet körs)",
                                   default=a.get("admin_name") or "lokaladmin", required=True, check=check_admin)
    a["domain"] = ask_choice("Domän och Entra ID", DOMAIN, a["domain"])
    if confirm("Fylla i kund, ansvarig, dator m.m.? (valfritt, hamnar i checklistan)",
               any(a.get(k) for k, _ in OPTIONAL_FIELDS)):
        for k, label in OPTIONAL_FIELDS:
            a[k] = ask_text(label + dim(" (valfritt, enter hoppar över)"), default=a.get(k) or "")
    ask_steps(a)
    global DIRTY
    DIRTY = False                    # granskningen visar allt igen, ingen extra paus
    return a


def summary_lines(a):
    steps = final_steps(a)
    w = width()
    names = [FOLDER_BY_ID[f][1] for f in a["folders"]] + a["custom"]
    rows = [("Användare", f"{a['user']}  {dim(a['profile'])}"), ("Målmapp", malmapp_for(a)),
            ("Mappar", ", ".join(names) or "inga"),
            ("Webbläsare", ", ".join(dict(BROWSERS)[b] for b in a["browsers"]) or "inga"),
            ("BitLocker", dict((v, l) for v, l, _ in BITLOCKER)[a["bitlocker"]]),
            ("Admin-konto", (a.get("admin_name") or "lokaladmin") if "admin" in steps else "nej"),
            ("Domän/Entra", dict((v, l) for v, l, _ in DOMAIN)[a["domain"]])]
    rows += [(SHORT_LABEL[k], a[k]) for k, _ in OPTIONAL_FIELDS if a.get(k)]
    rows.append(("Steg", f"{len([s for s in steps if s in SCRIPT_STEPS])} i skriptet, "
                         f"{len([s for s in steps if s.startswith('m_')])} manuella"))
    lw = 13 if w >= 58 else 0
    res = []
    for k, v in rows:
        if lw:
            res.append("   " + dim(k.ljust(lw)) + v)
        else:
            res.append("   " + dim(k + ": ") + v)
    return [fit(l, w) for l in res]


def review(a, template_name=None):
    """Förhandsvisning: visa skript/checklista, spara, ändra eller spara som mall."""
    last = "save"
    while True:
        name, ps = build_script(a)
        md = build_checklist(a, name)
        rows = [dict(label="Spara skript och checklista", hint=f"~/Documents/Muppa/{name}", value="save", key="s"),
                dict(label="Visa skriptet", hint=f"{len(ps.splitlines())} rader PowerShell", value="ps", key="v"),
                dict(label="Visa checklistan", hint=f"{md.count('- [ ]')} punkter", value="md", key="c"),
                dict(label="Ändra svaren…", hint="guiden igen, med dina svar ifyllda", value="edit", key="a"),
                dict(label="Spara som mall…", hint="valen utan användaren, inga hemligheter", value="tpl", key="m"),
                dict(label="Avbryt", value="quit", key="q")]
        v = choose(rows, msg=f"Klart för {a['user']}. Titta gärna på skriptet innan du sparar.", status=lambda: summary_lines(a),
                   start=last, screen=True, footer="↑/↓ · enter · siffra/bokstav väljer · q avbryter",
                   sub=f"muppa {VERSION}")
        last = v
        if v in (None, "quit"):
            return False
        if v == "ps":
            view_text(ps, name)
        elif v == "md":
            view_text(md, f"Checklista-{file_slug(a['user'])}.md")
        elif v == "edit":
            try:
                wizard(a, intro=False)
            except Cancel:
                pass
        elif v == "tpl":
            ask_save_template(a, template_name)
        elif v == "save":
            p1, p2 = save_files(a)
            new_screen()
            celebrate(f"Sparat! Skript och checklista för {a['user']} ligger i {OUT_DIR.name}.")
            print()
            out("   " + green("✔") + " " + str(p1))
            out("   " + green("✔") + " " + str(p2))
            print()
            out("   " + bold("Kör på datorn som migreras") + dim(" (Windows PowerShell som administratör):"))
            out("     " + cyan(f"powershell -ExecutionPolicy Bypass -File .\\{p1.name} -Torrkorning"))
            out("     " + cyan(f"powershell -ExecutionPolicy Bypass -File .\\{p1.name}"))
            out("   " + dim("Sedan -Doman, -Entra och -Aterlas, som det står i checklistan."))
            print()
            if not template_name and ITTY:
                if confirm("Spara valen som mall till nästa gång?", False):
                    ask_save_template(a, None)
            return True


def ask_save_template(a, current):
    ts = templates()
    try:
        name = ask_text("Mallens namn", default=current or ("Standard" if "Standard" not in ts else ""), required=True)
    except Cancel:
        return
    if name in ts and name != current and not confirm(f"Mallen {name} finns. Skriva över?", False):
        return
    save_template(a, name)
    out("   " + green("✔") + f" Mallen {bold(name)} sparad: " + dim(template_summary(templates()[name])))


def run_wizard(template=None):
    a = new_answers(template)
    try:
        wizard(a)
    except Cancel:
        out(dim("  Avbrutet."))
        return False
    return review(a, template)


# ───────────────────────────── menyer ─────────────────────────────
def templates_menu():
    while True:
        ts = templates()
        if not ts:
            say("Inga mallar än. Gör en migrering och välj Spara som mall, så hamnar valen här.", "happy")
            return
        rows = [dict(label=n, hint=template_summary(t), value=n) for n, t in sorted(ts.items())]
        rows.append(dict(label="Tillbaka", value="__back__", key="q"))
        v = choose(rows, msg="Välj en mall för att använda eller ta bort den.", screen=True, sub=f"muppa {VERSION}")
        if v in (None, "__back__"):
            return
        act = choose([dict(label="Använd", hint="starta guiden med mallens val", value="use"),
                      dict(label="Ta bort", value="del"),
                      dict(label="Tillbaka", value="back", key="q")], title=f"Mall: {v}")
        if act == "use":
            run_wizard(v)
            return
        if act == "del" and confirm(f"Ta bort mallen {v}?", False):
            ts.pop(v, None)
            save_json(TEMPLATES, ts)


def greeting():
    if not templates():
        return ("Hej! Jag är Muppa. Jag bygger ett PowerShell-skript och en checklista för att flytta en dator "
                "från lokal AD-domän till Entra ID."), "happy"
    return "Välkommen tillbaka! Välj en mall så går det snabbt, eller börja från början.", "happy"


def main_menu():
    last = None
    while True:
        msg, mood = greeting()
        ts = templates()
        rows = [dict(label="Ny migrering…", hint="guiden: frågor → skript + checklista", value="new", key="n")]
        if ts:
            rows.append(("head", "Från mall"))
            rows += [dict(label=n, hint=template_summary(t), value="t:" + n) for n, t in sorted(ts.items())[:6]]
            rows.append(("head", ""))
        rows += [dict(label="Mallar…", hint=f"{len(ts)} sparade" if ts else "inga än", value="tpl", key="m"),
                 dict(label="Öppna mappen med skript", hint=str(OUT_DIR), value="open", key="o"),
                 dict(label="Avsluta", value="quit", key="q")]
        v = choose(rows, msg=msg, mood=mood, start=last or "new", screen=True,
                   footer="↑/↓ · enter · siffra/bokstav väljer · q avslutar", sub=f"muppa {VERSION}")
        last = v
        if v in (None, "quit"):
            print()
            say(random.choice(["Hej då!", "Vi ses!", "Lycka till med migreringen!"]), "happy")
            print()
            return
        try:
            if v == "new":
                run_wizard()
            elif v.startswith("t:"):
                run_wizard(v[2:])
            elif v == "tpl":
                templates_menu()
            elif v == "open":
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                open_path(OUT_DIR)
        except Cancel:
            out(dim("  Avbrutet."))


# ───────────────────────────── CLI ─────────────────────────────
def cmd_create(args):
    if args.mall and args.mall not in templates():
        print(f"  Hittar ingen mall som heter {args.mall}. Se muppa mallar.", file=sys.stderr)
        return 1
    a = new_answers(args.mall)
    a["user"], a["profile"] = split_user(args.anvandare)
    if args.malmapp:
        a["malmapp"] = args.malmapp
    if args.mappar is not None:
        ids = [x.strip() for x in args.mappar.split(",") if x.strip()]
        bad = [x for x in ids if x not in FOLDER_BY_ID]
        if bad:
            print(f"  Okända mappar: {', '.join(bad)}. Välj bland: {', '.join(FOLDER_BY_ID)}", file=sys.stderr)
            return 1
        a["folders"] = ids
    if args.egen:
        a["custom"] = list(args.egen)
    if args.webblasare is not None:
        a["browsers"] = [b for b in args.webblasare.split(",") if b in dict(BROWSERS)]
    for k in ("bitlocker", "domain", "cloud"):
        if getattr(args, k):
            a[k] = getattr(args, k)
    if args.admin:
        a["admin"], a["admin_name"] = True, args.admin
    if args.ingen_admin:
        a["admin"] = False
    for k, _ in OPTIONAL_FIELDS:
        if getattr(args, k, None):
            a[k] = getattr(args, k)
    p1, p2 = save_files(a)
    print(p1)
    print(p2)
    return 0


def list_templates():
    ts = templates()
    if not ts:
        out("  Inga mallar. Spara en från guiden (muppa ny).")
        return
    w = width()
    nw = min(24, max(len(n) for n in ts) + 2)
    for n, t in sorted(ts.items()):
        out(f"    {pad(cut(n, nw), nw)}" + (dim(template_summary(t)) if w >= 58 else ""))


def main(argv=None):
    import argparse
    global ANIM
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--no-anim" in argv:
        ANIM = False
        argv = [x for x in argv if x != "--no-anim"]
    ap = argparse.ArgumentParser(prog="muppa", description="Muppa – migrera en dator från lokal AD till Entra ID.")
    ap.add_argument("--version", action="version", version="muppa " + VERSION)
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("ny", help="guiden")
    n.add_argument("--mall")
    s = sub.add_parser("skapa", help="skapa skript + checklista utan frågor")
    s.add_argument("anvandare", help="användarnamn eller profilmapp")
    s.add_argument("--mall")
    s.add_argument("--malmapp", help=r"standard C:\temp\{användare}")
    s.add_argument("--mappar", help="t.ex. skrivbord,dokument,bilder,hamtade (" + ",".join(FOLDER_BY_ID) + ")")
    s.add_argument("--egen", action="append", help="egen sökväg (kan upprepas)")
    s.add_argument("--webblasare", help="edge,chrome eller tomt")
    s.add_argument("--bitlocker", choices=[x[0] for x in BITLOCKER])
    s.add_argument("--doman", dest="domain", choices=[x[0] for x in DOMAIN])
    s.add_argument("--molnfiler", dest="cloud", choices=[x[0] for x in CLOUD])
    s.add_argument("--admin", help="namn på lokalt admin-konto")
    s.add_argument("--ingen-admin", action="store_true")
    for k, label in OPTIONAL_FIELDS:
        s.add_argument("--" + k, help=label)
    sub.add_parser("mallar", help="lista sparade mallar")
    sub.add_parser("hej")
    a = ap.parse_args(argv)
    try:
        if a.cmd is None:
            if not ITTY or not TTY:
                print(__doc__)
                return 0
            signal.signal(signal.SIGINT, signal.default_int_handler)
            main_menu()
            return 0
        if a.cmd == "ny":
            if not ITTY:
                sys.exit("  guiden behöver en riktig terminal (eller: muppa skapa ANVÄNDARE)")
            if a.mall and a.mall not in templates():
                say(f"Hittar ingen mall som heter {a.mall}. Se muppa mallar.", "sad")
                return 1
            return 0 if run_wizard(a.mall) else 1
        if a.cmd == "skapa":
            return cmd_create(a)
        if a.cmd == "mallar":
            list_templates()
            return 0
        if a.cmd == "hej":
            print()
            say(random.choice(JOKES), random.choice(["happy", "proud", "wow", "left"]))
            print()
            return 0
    except Cancel:
        out(dim("  Avbrutet."))
        return 1
    except KeyboardInterrupt:
        sys.stdout.write("\033[?25h\n")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
