#!/usr/bin/env python3
"""muppa – migrate a Windows computer from an on-premises AD domain to Entra ID. Windows, macOS and Linux.

  muppa                    menu
  muppa new [--template NAME]   the wizard: questions → tailored PowerShell script + checklist
  muppa create USER [--template NAME] [flags]   create script + checklist without questions
  muppa templates          list saved templates
  muppa lang [en|sv]       language for the whole troupe (English or Swedish)
  muppa hello

The wizard asks for the user, target folder, which folders to copy, browsers, BitLocker, a local admin
account and domain/Entra ID. It saves Migrera-<user>.ps1 and Checklista-<user>.md in ~/Documents/Muppa.
The script is for Windows PowerShell 5.1 and runs as administrator on the computer being migrated, in phases:
(prepare) · -Doman · -Entra · -Aterlas. -Torrkorning / -WhatIf only shows what would be done.

Flags: --no-anim turns animations off.
Data: ~/.muppa/mallar.json (choices, no secrets). Standard library only, Python 3.8+.
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

DOC_SV = """muppa – migrera en Windows-dator från lokal AD-domän till Entra ID. Windows, macOS och Linux.

  muppa                    meny
  muppa ny [--mall NAMN]   guiden: frågor → anpassat PowerShell-skript + checklista
  muppa skapa ANVÄNDARE [--mall NAMN] [flaggor]   skapa skript + checklista utan frågor
  muppa mallar             lista sparade mallar
  muppa lang [en|sv]       språk för hela truppen (engelska eller svenska)
  muppa hej

Guiden frågar efter användare, målmapp, vilka mappar som ska kopieras, webbläsare, BitLocker,
lokalt admin-konto och domän/Entra ID. Den sparar Migrera-<användare>.ps1 och Checklista-<användare>.md
i ~/Documents/Muppa. Skriptet är för Windows PowerShell 5.1 och körs som administratör på datorn som
migreras, i faser: (förbered) · -Doman · -Entra · -Aterlas. -Torrkorning / -WhatIf visar bara vad som görs.

Flaggor: --no-anim stänger av animationer.
Data: ~/.muppa/mallar.json (val, inga hemligheter). Enbart standardbiblioteket, Python 3.8+.
"""

VERSION = "1.2.0"
HOME = Path.home()
DIR = Path(os.environ.get("MUPPA_DIR") or HOME / ".muppa")
TEMPLATES = DIR / "mallar.json"
CONFIG = DIR / "config.json"
OUT_DIR = Path(os.environ.get("MUPPA_UT") or HOME / "Documents" / "Muppa")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# ───────────────────────────── language ─────────────────────────────
# One setting for the whole troupe: ~/.truppa/config.json {"lang": "en"|"sv"}. TRUPPA_LANG overrides it. Default: English.
TRUPPA = Path.home() / ".truppa" / "config.json"


def _lang():
    v = os.environ.get("TRUPPA_LANG")
    if not v:
        try:
            v = json.loads(TRUPPA.read_text(encoding="utf-8")).get("lang")
        except Exception:
            v = None
    return "sv" if str(v or "en").lower().startswith("sv") else "en"


LANG = _lang()
os.environ["TRUPPA_LANG"] = LANG          # PowerShell and the other tools read it too


def L(en, sv):
    """The text in the chosen language (English first)."""
    return sv if LANG == "sv" else en


def set_lang(lang):
    global LANG
    TRUPPA.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(TRUPPA.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["lang"] = lang
    TRUPPA.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    LANG = lang
    os.environ["TRUPPA_LANG"] = lang


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
        out(dim(L(f"  Open it yourself: {path}", f"  Öppna själv: {path}")))


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
JOKES_EN = [
    "My name is Muppa. It stands for Migrate Under Pressure, Allegedly.",
    "I'm an M. As in Migrate. Or Microsoft. Or Monday.",
    "The BitLocker key? Save it. Twice. In two places.",
    "Robocopy exit code 1 means it went well. Yes, really.",
    "Windows Hello? Not yet. Skip it.",
    "Files On-Demand: files that exist, and yet they don't. Schrödinger's Documents.",
    "Remove-Computer: the only command that makes a domain controller feel abandoned.",
    "There are two kinds of admins: those who take backups, and those who will.",
]
JOKES_SV = [
    "Jag heter Muppa. Det står för Migrera Utan Panik. Typ.",
    "Jag är ett M. Som i Migrera. Eller Molnet. Eller Måndag.",
    "BitLocker-nyckeln? Spara den. Två gånger. På två ställen.",
    "Robocopy-kod 1 betyder att det gick bra. Ja, på riktigt.",
    "Windows Hello? Inte än. Hoppa över.",
    "Files On-Demand: filer som finns, men ändå inte. Schrödingers Dokument.",
    "Remove-Computer: det enda kommandot som får en domänkontrollant att känna sig övergiven.",
    "Det finns två sorters admins: de som tar backup, och de som kommer att göra det.",
]
JOKES = JOKES_SV if LANG == "sv" else JOKES_EN


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
            lines.append("  " + accent("⌕ ") + bold(st["q"]) + accent("▏") + dim(L(f"  {len(sel)} matches", f"  {len(sel)} träffar")))
        elif title:
            lines.append("  " + accent("▍") + bold(title))
        narrow = w < 58
        if not sel:
            lines.append("     " + dim(L("No matches. Backspace edits, esc clears.", "Inga träffar. Backsteg ändrar, esc rensar.")))
        labels = [vr[k]["label"] for k in sel] or [""]
        lw = min(max(len(x) for x in labels) + 2, max(10, w - 14 if narrow else min(34, w // 2)))
        room = max(3, height() - len(lines) - 3)
        pos = st["pos"]
        top = min(max(st["top"], pos - room + 1), pos)
        top = max(0, min(top, max(0, len(vr) - room)))
        st["top"] = top
        if top > 0:
            lines.append(dim(L("     ↑ more", "     ↑ fler")))
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
            lines.append(dim(L("     ↓ more", "     ↓ fler")))
        lines.append("")
        if st["q"]:
            help_ = L("↑/↓ · enter picks · backspace · esc clears", "↑/↓ · enter väljer · backsteg · esc rensar") if w >= 50 else "↑↓ enter · esc"
        else:
            help_ = ((footer or L("↑/↓ · enter · a digit picks · q back", "↑/↓ · enter · siffra väljer · q tillbaka")) + L(" · type to search", " · skriv för att söka")) if w >= 72 else \
                (footer or L("↑/↓ · enter · q back", "↑/↓ · enter · q tillbaka")) if w >= 50 else "↑↓ enter · q"
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
        out("    " + dim(L("default: ", "standard: ") + cut(default, w - 16) + L("  (enter keeps it)", "  (enter behåller)")))
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
            out("    " + yellow(L("This is needed. Ctrl+C cancels.", "Det här behövs. Ctrl+C avbryter.")))
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
        sys.stdout.write(dim(L("   enter = continue ", "   enter = fortsätt ")))
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
# ─────────────── Below is the same in every script from Muppa. Change the settings above. ───────────────
# ─────────────── Nedanför är samma i alla skript från Muppa. Ändra inställningarna ovanför. ───────────────
$ErrorActionPreference = 'Continue'
function T([string]$en, [string]$sv) { if ($Sprak -eq 'sv') { $sv } else { $en } }
$Torr = [bool]$Torrkorning -or [bool]$WhatIfPreference
$SkriptNamn = if ($PSCommandPath) { Split-Path -Leaf $PSCommandPath } else { 'Migrera.ps1' }
$Faser = @()
if ($Doman) { $Faser += 'doman' }
if ($Entra) { $Faser += 'entra' }
if ($Aterlas) { $Faser += 'aterlas' }
if ($Faser.Count -gt 1) { Write-Host (T "  Choose one phase at a time: -Doman, -Entra or -Aterlas." "  Välj en fas i taget: -Doman, -Entra eller -Aterlas.") -ForegroundColor Red; exit 2 }
$Fas = if ($Faser.Count) { $Faser[0] } else { 'forbered' }

# ── utskrift och frågor ──
function Rubrik([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Info([string]$Text) { Write-Host "   $Text" }
function Ok([string]$Text) { Write-Host "   [OK] $Text" -ForegroundColor Green }
function Varning([string]$Text) { Write-Host "   [!]  $Text" -ForegroundColor Yellow }
function Fel([string]$Text) { Write-Host "   [X]  $Text" -ForegroundColor Red }

function Atgard([string]$Text, [scriptblock]$Block) {
    # Kör en ändring, eller visar bara vad som skulle göras vid -Torrkorning / -WhatIf.
    if ($Torr) { Write-Host (T "   [dry run] $Text" "   [torrkörning] $Text") -ForegroundColor DarkGray; return }
    Write-Host "   -> $Text"
    & $Block
}

function Fraga([string]$Text, [bool]$Standard = $true) {
    $opt = if ($Standard) { (T '[Y/n]' '[J/n]') } else { (T '[y/N]' '[j/N]') }
    if ($Torr) {
        $svar = if ($Standard) { (T 'yes' 'ja') } else { (T 'no' 'nej') }
        Write-Host (T "   $Text $opt  (dry run: $svar)" "   $Text $opt  (torrkörning: $svar)") -ForegroundColor DarkGray
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
    return (T 'a local admin account' 'ett lokalt admin-konto')
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
    if (-not (Test-Path -LiteralPath $dat)) { Varning (T "Cannot find $dat." "Hittar inte $dat."); return $null }
    & reg.exe load 'HKU\MuppaHive' $dat 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $script:Hive = 'HKEY_USERS\MuppaHive'
        $script:HiveLaddad = $true
        return $script:Hive
    }
    Varning (T "Could not read the registry in $dat. The folders are assumed to be in their default location." "Kunde inte läsa registret i $dat. Mapparna antas ligga på standardplatsen.")
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
    if ($Kod -ge 16) { return (T "serious error ($Kod): nothing was copied. Check paths and permissions." "allvarligt fel ($Kod): ingenting kopierades. Kontrollera sökvägar och behörighet.") }
    $d = @()
    if ($Kod -band 8) { $d += (T 'some files could not be copied (see robocopy.log)' 'vissa filer kunde inte kopieras (se robocopy.log)') }
    if ($Kod -band 4) { $d += (T 'mismatched files exist in the target' 'avvikande filer finns i målet') }
    if ($Kod -band 2) { $d += (T 'the target has extra files (they are left alone)' 'målet har extra filer (de rörs inte)') }
    if ($Kod -band 1) { $d += (T 'files were copied' 'filer kopierades') }
    if ($Kod -eq 0) { $d += (T 'nothing to copy, the target is already up to date' 'inget att kopiera, målet är redan aktuellt') }
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
    Info (T "Computer:    $($a.Dator)  ($($os.Caption) $($os.Version))" "Dator:       $($a.Dator)  ($($os.Caption) $($os.Version))")
    Info (T "Domain:      $(if ($a.Doman) { $a.DomanNamn } else { 'none (workgroup)' })" "Domän:       $(if ($a.Doman) { $a.DomanNamn } else { 'ingen (arbetsgrupp)' })")
    Info (T "Entra ID:    $(if ($a.Entra) { "joined ($($a.Tenant))" } else { 'not joined' })" "Entra ID:    $(if ($a.Entra) { "ansluten ($($a.Tenant))" } else { 'inte ansluten' })")
    Info (T "Running as:  $env:USERDOMAIN\$env:USERNAME" "Körs som:    $env:USERDOMAIN\$env:USERNAME")
    Info (T "User:        $($Cfg.Anvandare)  ($($Cfg.Profil))" "Användare:   $($Cfg.Anvandare)  ($($Cfg.Profil))")
    if (-not (Test-Path -LiteralPath $Cfg.Profil)) { Varning (T "The profile folder $($Cfg.Profil) does not exist on this computer." "Profilmappen $($Cfg.Profil) finns inte på den här datorn.") }
    try {
        $bl = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
        Info (T "BitLocker:   $($bl.VolumeStatus), protection $($bl.ProtectionStatus)" "BitLocker:   $($bl.VolumeStatus), skydd $($bl.ProtectionStatus)")
    } catch { Info (T 'BitLocker:   could not be read' 'BitLocker:   kunde inte läsas') }
    $admins = Get-LokalaAdmins
    Info (T "Local admins: $(if ($admins) { $admins -join ', ' } else { 'no local accounts' })" "Lokala admins: $(if ($admins) { $admins -join ', ' } else { 'inga lokala konton' })")
    if (-not $Torr) {
        $fil = Join-Path $Cfg.Malmapp 'Datorinfo.txt'
        @((T "Computer: $($a.Dator)" "Dator: $($a.Dator)"), "OS: $($os.Caption) $($os.Version)", (T "Domain: $($a.DomanNamn) (domain joined: $($a.Doman))" "Domän: $($a.DomanNamn) (med i domän: $($a.Doman))"),
          "Entra ID: $($a.Entra) $($a.Tenant)", (T "User: $($Cfg.Anvandare) ($($Cfg.Profil))" "Användare: $($Cfg.Anvandare) ($($Cfg.Profil))"),
          (T "Local admins: $($admins -join ', ')" "Lokala admins: $($admins -join ', ')"), (T "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm')" "Datum: $(Get-Date -Format 'yyyy-MM-dd HH:mm')")) |
            Set-Content -LiteralPath $fil -Encoding UTF8
        Ok (T "Saved to $fil" "Sparat i $fil")
    }
}

function Steg-kopiera {
    if (-not $Mappar.Count) { Info (T 'No folders selected.' 'Inga mappar valda.'); return }
    $hive = Open-AnvandarHive
    $lista = @()
    foreach ($m in $Mappar) {
        $lista += [pscustomobject]@{ Mapp = $m; Kalla = (Resolve-Mapp $m $hive $Cfg.Profil); Mal = (Join-Path $Cfg.Malmapp $m.Mal) }
    }
    Close-AnvandarHive

    Info (T 'Measuring the folders …' 'Mäter mapparna …')
    $summa = [int64]0; $moln = 0; $molnByte = [int64]0
    foreach ($x in $lista) {
        if (-not (Test-Path -LiteralPath $x.Kalla)) {
            Varning ((T "{0,-16} does not exist ({1})" "{0,-16} finns inte ({1})") -f $x.Mapp.Namn, $x.Kalla)
            $x.Kalla = $null
            continue
        }
        $s = Measure-Mapp $x.Kalla
        $summa += $s.Byte; $moln += $s.MolnFiler; $molnByte += $s.MolnByte
        $od = if ($x.Kalla -match '\\OneDrive') { '  [OneDrive]' } else { '' }
        $mo = if ($s.MolnFiler) { (T "  + $($s.MolnFiler) only in the cloud" "  + $($s.MolnFiler) bara i molnet") } else { '' }
        Info ("{0,-16} {1,10}  {2,7} filer{3}  {4}{5}" -f $x.Mapp.Namn, (Format-Storlek $s.Byte), $s.Filer, $mo, $x.Kalla, $od)
    }
    if ($Cfg.Molnfiler -ne 'hoppa') { $summa += $molnByte }
    $ledigt = Get-Ledigt $Cfg.Malmapp
    Info ((T "Total {0}, free on {1}: {2}" "Totalt {0}, ledigt på {1}: {2}") -f (Format-Storlek $summa), [IO.Path]::GetPathRoot($Cfg.Malmapp), (Format-Storlek $ledigt))
    if ($moln) {
        Varning ((T "{0} files ({1}) only exist in the OneDrive cloud (Files On-Demand)." "{0} filer ({1}) finns bara i OneDrive-molnet (Files On-Demand).") -f $moln, (Format-Storlek $molnByte))
        if ($Cfg.Molnfiler -eq 'hoppa') {
            Info (T '     They are skipped (/XA:O): they are already in OneDrive and come back when the user signs in.' '     De hoppas över (/XA:O) – de finns redan i OneDrive och kommer tillbaka när användaren loggar in.')
            Info (T '     Make sure OneDrive is fully synced before the computer leaves the domain.' '     Kontrollera att OneDrive är helt synkat innan datorn lämnar domänen.')
        } else {
            Info (T '     robocopy tries to read them. That downloads them if OneDrive is running for the user, otherwise it fails.' '     robocopy försöker läsa dem. Det laddar ner dem om OneDrive körs för användaren, annars blir det fel.')
        }
    }
    if ($ledigt -ge 0 -and $summa * 1.05 -gt $ledigt) {
        Fel (T 'It does not fit in the target folder.' 'Det får inte plats i målmappen.')
        if (-not (Fraga (T 'Copy anyway?' 'Kopiera ändå?') $false)) { return }
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
    if ($fel) { Varning (T "$fel folder(s) were not copied completely. See $script:RoboLogg." "$fel mapp(ar) kopierades inte helt. Se $script:RoboLogg.") } else { Ok (T 'The folders are copied.' 'Mapparna är kopierade.') }
}

function Steg-bitlocker_spara {
    $vol = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
    if ($vol.VolumeStatus -eq 'FullyDecrypted') { Info (T "BitLocker is not on for $env:SystemDrive." "BitLocker är inte på för $env:SystemDrive."); return }
    $rp = @($vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' })
    if (-not $rp.Count) {
        Varning (T 'There is no recovery key (RecoveryPassword).' 'Det finns ingen återställningsnyckel (RecoveryPassword).')
        if (Fraga (T 'Create a recovery key now?' 'Skapa en återställningsnyckel nu?') $true) {
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
    foreach ($p in $rp) { Info (T "Key ID $($p.KeyProtectorId)" "Nyckel-ID $($p.KeyProtectorId)"); Info (T "   Key: $($p.RecoveryPassword)" "   Nyckel: $($p.RecoveryPassword)") }
    Varning (T 'Save the key outside the computer too (documentation/ticket). The target folder is on the same disk.' 'Spara nyckeln utanför datorn också (dokumentation/ärende). Målmappen ligger på samma disk.')
    $a = Get-Anslutning
    foreach ($p in $rp) {
        if ($a.Entra) {
            # Till Entra ID: BackupToAAD-BitLockerKeyProtector (Backup-BitLockerKeyProtector skickar till AD DS).
            try {
                Atgard (T "Back up the key to Entra ID ($($a.Tenant))" "Säkerhetskopiera nyckeln till Entra ID ($($a.Tenant))") {
                    BackupToAAD-BitLockerKeyProtector -MountPoint $env:SystemDrive -KeyProtectorId $p.KeyProtectorId -ErrorAction Stop | Out-Null
                }
            } catch { Varning (T "Could not save the key in Entra ID: $($_.Exception.Message)" "Kunde inte spara nyckeln i Entra ID: $($_.Exception.Message)") }
        }
        if ($a.Doman) {
            try {
                Atgard (T "Back up the key to AD ($($a.DomanNamn))" "Säkerhetskopiera nyckeln till AD ($($a.DomanNamn))") {
                    Backup-BitLockerKeyProtector -MountPoint $env:SystemDrive -KeyProtectorId $p.KeyProtectorId -ErrorAction Stop | Out-Null
                }
            } catch { Varning (T "Could not save the key in AD: $($_.Exception.Message)" "Kunde inte spara nyckeln i AD: $($_.Exception.Message)") }
        }
    }
}

function Steg-bitlocker_av {
    $vol = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
    if ($vol.VolumeStatus -eq 'FullyDecrypted') { Info (T 'BitLocker is already off.' 'BitLocker är redan avstängt.'); return }
    if ($Cfg.BitLocker -eq 'stang-av') {
        Varning (T 'Disable-BitLocker decrypts the whole disk. It can take a long time, but the computer can be used meanwhile.' 'Disable-BitLocker dekrypterar hela disken. Det kan ta lång tid, men datorn går att använda under tiden.')
        if (-not (Fraga (T 'Turn off BitLocker?' 'Stänga av BitLocker?') $true)) { return }
        Atgard "Disable-BitLocker -MountPoint $env:SystemDrive" {
            Disable-BitLocker -MountPoint $env:SystemDrive -ErrorAction Stop | Out-Null
            Ok (T 'Decryption has started. Follow it with: manage-bde -status' 'Dekrypteringen har startat. Följ den med: manage-bde -status')
        }
    } else {
        if ($vol.ProtectionStatus -eq 'Off') { Info (T 'BitLocker is already suspended.' 'BitLocker är redan pausat.'); return }
        Atgard (T "Suspend-BitLocker -MountPoint $env:SystemDrive -RebootCount 0 (suspended until it is turned back on)" "Suspend-BitLocker -MountPoint $env:SystemDrive -RebootCount 0 (pausat tills det slås på igen)") {
            Suspend-BitLocker -MountPoint $env:SystemDrive -RebootCount 0 -ErrorAction Stop | Out-Null
            Ok (T 'BitLocker is suspended. Turn it back on after the migration (done in -Entra, or: Resume-BitLocker).' 'BitLocker är pausat. Slå på igen efter migreringen (görs i -Entra, eller: Resume-BitLocker).')
        }
    }
}

function Steg-bokmarken {
    $ut = Join-Path $Cfg.Malmapp 'Webblasare'
    $antal = 0
    foreach ($b in $Cfg.Webblasare) {
        $w = $Webblasare[$b]
        $profiler = Get-WebbProfiler (Join-Path $Cfg.Profil $w.Data)
        if (-not $profiler.Count) { Info (T "$($w.Namn): no profile for $($Cfg.Anvandare)." "$($w.Namn): ingen profil hos $($Cfg.Anvandare)."); continue }
        foreach ($p in $profiler) {
            $bm = Join-Path $p.FullName 'Bookmarks'
            if (-not (Test-Path -LiteralPath $bm)) { Info (T "$($w.Namn) ($($p.Name)): no bookmarks." "$($w.Namn) ($($p.Name)): inga bokmärken."); continue }
            $namn = "$($w.Namn)-$($p.Name -replace '\s', '')"
            $antal++
            Atgard (T "$($w.Namn) ($($p.Name)): copy Bookmarks and export to $namn-bokmarken.html" "$($w.Namn) ($($p.Name)): kopiera Bookmarks och exportera till $namn-bokmarken.html") {
                New-Item -ItemType Directory -Path $ut -Force | Out-Null
                Copy-Item -LiteralPath $bm -Destination (Join-Path $ut "$namn-Bookmarks.json") -Force
                try {
                    $n = ConvertTo-BokmarkHtml $bm (Join-Path $ut "$namn-bokmarken.html")
                    Ok (T "$n bookmarks exported." "$n bokmärken exporterade.")
                } catch { Varning (T "The HTML export failed ($($_.Exception.Message)). The JSON file is copied." "HTML-exporten misslyckades ($($_.Exception.Message)). JSON-filen är kopierad.") }
            }
        }
    }
    if (-not $antal) { Info (T 'No bookmarks found.' 'Inga bokmärken hittades.') }
}

function Steg-losenord {
    $ut = Join-Path $Cfg.Malmapp 'Webblasare'
    if (-not $Torr) { New-Item -ItemType Directory -Path $ut -Force | Out-Null }
    $sammaAnvandare = ($env:USERPROFILE -eq $Cfg.Profil)
    Info (T 'Passwords cannot be exported automatically. You save them as CSV yourself:' 'Lösenord kan inte exporteras automatiskt. Du sparar dem som CSV själv:')
    foreach ($b in $Cfg.Webblasare) {
        $w = $Webblasare[$b]
        $csv = Join-Path $ut "$($w.Namn)-losenord.csv"
        Info ''
        Info "$($w.Namn):"
        Info (T "   1. Open $($w.Losen)" "   1. Öppna $($w.Losen)")
        Info (T '   2. Choose Export passwords (… / Download file) and confirm with the Windows sign-in' '   2. Välj Exportera lösenord (… / Ladda ned fil) och bekräfta med Windows-inloggningen')
        Info (T "   3. Save as $csv" "   3. Spara som $csv")
        if ($sammaAnvandare) {
            Atgard (T "Open $($w.Losen)" "Öppna $($w.Losen)") { try { Start-Process $w.Exe $w.Losen } catch { Varning (T "Could not start $($w.Exe)." "Kunde inte starta $($w.Exe).") } }
        } else {
            Varning (T "The script runs as $env:USERNAME, not as $($Cfg.Anvandare). Open $($w.Namn) in the user's session." "Skriptet körs som $env:USERNAME, inte som $($Cfg.Anvandare). Öppna $($w.Namn) i användarens session.")
            if (-not $Torr) { try { Set-Clipboard -Value $w.Losen; Info (T "   (the address is copied)" "   (adressen är kopierad)") } catch { } }
        }
        Vanta (T 'Press Enter when the CSV file is saved (or to skip)' 'Tryck Enter när CSV-filen är sparad (eller för att hoppa över)')
        if (-not $Torr) {
            if (Test-Path -LiteralPath $csv) { Ok (T "$csv exists." "$csv finns.") } else { Varning (T "Cannot find $csv. If it has another name, that is fine too." "Hittar inte $csv. Har den ett annat namn, är det också okej.") }
        }
    }
    Varning (T 'The CSV files contain passwords in plain text. Delete them once they are imported (-Aterlas asks).' 'CSV-filerna innehåller lösenord i klartext. Radera dem när de är importerade (-Aterlas frågar).')
}

function Steg-aktivitetsfalt {
    $ut = Join-Path $Cfg.Malmapp 'Aktivitetsfalt'
    $fast = Join-Path $Cfg.Profil 'AppData\Roaming\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'
    if (Test-Path -LiteralPath $fast) {
        $lnk = @(Get-ChildItem -LiteralPath $fast -Force -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer })
        Info (T "Pinned apps ($($lnk.Count)): $(($lnk | ForEach-Object { $_.BaseName }) -join ', ')" "Fästa program ($($lnk.Count)): $(($lnk | ForEach-Object { $_.BaseName }) -join ', ')")
        Atgard (T "Copy the shortcuts to $ut\Genvagar" "Kopiera genvägarna till $ut\Genvagar") {
            New-Item -ItemType Directory -Path (Join-Path $ut 'Genvagar') -Force | Out-Null
            Copy-Item -Path (Join-Path $fast '*') -Destination (Join-Path $ut 'Genvagar') -Recurse -Force
            $lnk | ForEach-Object { $_.BaseName } | Set-Content -LiteralPath (Join-Path $ut 'Fasta-program.txt') -Encoding UTF8
        }
    } else {
        Info (T 'No pinned shortcuts found.' 'Hittar inga fästa genvägar.')
    }
    $hive = Open-AnvandarHive
    if ($hive) {
        $nyckel = "$hive\Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband"
        Atgard (T "Export the Taskband registry key to $ut\Taskband.reg" "Exportera registret Taskband till $ut\Taskband.reg") {
            New-Item -ItemType Directory -Path $ut -Force | Out-Null
            & reg.exe export $nyckel (Join-Path $ut 'Taskband.reg') /y 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { Ok (T 'Taskband.reg saved (for reference, do not import it as is).' 'Taskband.reg sparad (som referens, importera inte rakt av).') } else { Varning (T 'The Taskband key is missing.' 'Taskband-nyckeln saknas.') }
        }
    }
    Close-AnvandarHive
    Info (T 'Windows 11 does not keep the whole taskbar there. The screenshot is the reference.' 'Windows 11 sparar inte hela aktivitetsfältet där. Skärmdumpen är facit.')
}

function Steg-skarmdump {
    if (-not (Fraga (T 'Take a screenshot now (show the taskbar)?' 'Ta en skärmdump av skärmen nu (visa aktivitetsfältet)?') $true)) { return }
    Atgard (T "Screenshot to $($Cfg.Malmapp)" "Skärmdump till $($Cfg.Malmapp)") {
        Info (T 'The screenshot is taken in 5 seconds …' 'Skärmdumpen tas om 5 sekunder …')
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
        Ok (T "Saved: $fil" "Sparad: $fil")
    }
}

function Steg-admin {
    $namn = $Cfg.AdminNamn
    $finns = Get-LocalUser -Name $namn -ErrorAction SilentlyContinue
    if ($finns) {
        Info (T "The account $namn already exists$(if (-not $finns.Enabled) { ' (disabled)' })." "Kontot $namn finns redan$(if (-not $finns.Enabled) { ' (inaktiverat)' }).")
        $nyttLosen = Fraga (T "Set a new password for $namn?" "Sätta nytt lösenord för $namn?") $false
    } else {
        $nyttLosen = $true
    }
    if ($Torr) {
        Atgard (T "$(if ($finns) { 'Update' } else { 'Create' }) local account $namn and add it to Administrators" "$(if ($finns) { 'Uppdatera' } else { 'Skapa' }) lokalt konto $namn och lägg det i Administratörer") { }
        return
    }
    if ($nyttLosen) {
        while ($true) {
            # Lösenordet frågas här och sparas ingenstans – det står aldrig i skriptet.
            $p1 = Read-Host (T "   Password for $namn" "   Lösenord för $namn") -AsSecureString
            $p2 = Read-Host (T '   The same password again' '   Samma lösenord igen') -AsSecureString
            if (-not (Test-SammaLosen $p1 $p2)) { Varning (T 'The passwords were empty or different. Try again.' 'Lösenorden var tomma eller olika. Försök igen.'); continue }
            try {
                if ($finns) {
                    Set-LocalUser -Name $namn -Password $p1 -ErrorAction Stop
                } else {
                    New-LocalUser -Name $namn -Password $p1 -FullName $namn -Description (T 'Local admin account (migration)' 'Lokalt admin-konto (migrering)') `
                        -PasswordNeverExpires -AccountNeverExpires -ErrorAction Stop | Out-Null
                }
                break
            } catch {
                Fel $_.Exception.Message
                if (-not (Fraga (T 'Try another password?' 'Försöka med ett annat lösenord?') $true)) { return }
            }
        }
    }
    if ($finns -and -not $finns.Enabled) { Enable-LocalUser -Name $namn }
    try {
        Add-LocalGroupMember -SID 'S-1-5-32-544' -Member $namn -ErrorAction Stop
    } catch {
        if ($_.Exception.Message -notmatch 'already|redan') { Fel (T "Could not add to Administrators: $($_.Exception.Message)" "Kunde inte lägga till i Administratörer: $($_.Exception.Message)"); return }
    }
    Ok (T "$namn is a local administrator. Sign in as .\$namn." "$namn är lokal administratör. Logga in som .\$namn.")
}

# ═══════════════════════════════ steg: domän, Entra, återläsning ═══════════════════════════════
function Steg-doman {
    $a = Get-Anslutning
    if (-not $a.Doman) { Ok (T 'The computer is not in a domain.' 'Datorn är inte med i någon domän.'); return }
    Info (T "The computer $($a.Dator) is in the domain $($a.DomanNamn)." "Datorn $($a.Dator) är med i domänen $($a.DomanNamn).")
    $admins = Get-LokalaAdmins
    if ($Cfg.AdminNamn -and $admins -notcontains $Cfg.AdminNamn) {
        Varning (T "The local admin account $($Cfg.AdminNamn) does not exist, or is not an administrator." "Det lokala admin-kontot $($Cfg.AdminNamn) finns inte, eller är inte administratör.")
    }
    Info (T "Local admins: $(if ($admins) { $admins -join ', ' } else { 'NONE' })" "Lokala admins: $(if ($admins) { $admins -join ', ' } else { 'INGA' })")
    if ($env:USERDOMAIN -ne $env:COMPUTERNAME) { Varning (T "You are signed in with the domain account $env:USERDOMAIN\$env:USERNAME." "Du är inloggad med domänkontot $env:USERDOMAIN\$env:USERNAME.") }
    Varning (T 'After the restart no domain accounts can sign in. You need the password of a local admin account.' 'Efter omstarten kan inga domänkonton logga in. Du behöver lösenordet till ett lokalt admin-konto.')
    if ($Torr) {
        Atgard (T 'Remove-Computer -WorkgroupName WORKGROUP, then restart' 'Remove-Computer -WorkgroupName WORKGROUP, sedan omstart') { }
        return
    }
    $svar = Read-Host (T "   Type LEAVE to remove the computer from $($a.DomanNamn)" "   Skriv LÄMNA för att ta bort datorn ur $($a.DomanNamn)")
    if (@('lämna', 'lamna', 'leave') -notcontains $svar.Trim().ToLower()) { Info (T 'Cancelled. The computer stays in the domain.' 'Avbrutet. Datorn är kvar i domänen.'); return }
    $p = @{ WorkgroupName = 'WORKGROUP'; Force = $true; ErrorAction = 'Stop' }
    if (Fraga (T 'Use a domain account that may remove the computer account in AD? (no = current account)' 'Ange ett domänkonto som får ta bort datorkontot i AD? (nej = nuvarande konto)') $false) {
        $p.UnjoinDomainCredential = Get-Credential -Message (T 'Domain account (DOMAIN\name) that may remove the computer' 'Domänkonto (DOMÄN\namn) som får ta bort datorn')
    }
    try {
        Remove-Computer @p
    } catch {
        Fel (T "Remove-Computer failed: $($_.Exception.Message)" "Remove-Computer misslyckades: $($_.Exception.Message)")
        Info (T 'Can the computer not reach a domain controller? Try instead: System Properties (sysdm.cpl) → Change → Workgroup.' 'Når datorn ingen domänkontrollant? Prova i stället: Systemegenskaper (sysdm.cpl) → Ändra → Arbetsgrupp.')
        return
    }
    Ok (T 'The computer has left the domain. It must be restarted.' 'Datorn har lämnat domänen. Den måste startas om.')
    Info (T "After the restart: sign in as $(AdminVem) and run: .\$SkriptNamn -Entra" "Efter omstarten: logga in som $(AdminVem) och kör: .\$SkriptNamn -Entra")
    if (Fraga (T 'Restart now?' 'Starta om nu?') $true) {
        try { Stop-Transcript | Out-Null } catch { }
        Restart-Computer -Force
    }
}

function Steg-entra {
    $a = Get-Anslutning
    if ($a.Entra) {
        Ok (T "The computer is already joined to Entra ID ($($a.Tenant))." "Datorn är redan ansluten till Entra ID ($($a.Tenant)).")
    } else {
        if ($a.Doman) { Varning (T "The computer is still in the domain $($a.DomanNamn). Run -Doman first." "Datorn är fortfarande med i domänen $($a.DomanNamn). Kör -Doman först.") }
        Info (T 'The Entra ID join is done in Settings:' 'Anslutningen till Entra ID görs i Inställningar:')
        Info (T '   1. Accounts → Access work or school → Connect' '   1. Konton → Åtkomst till arbete eller skola → Anslut')
        Info (T '   2. Click the link at the bottom: "Join this device to Microsoft Entra ID"' '   2. Klicka på länken längst ner: "Anslut den här enheten till Microsoft Entra ID"')
        Info (T '      (not the email field, which only registers an account)' '      (inte e-postfältet – det registrerar bara ett konto)')
        Info (T "   3. Sign in with the user's account$(if ($Cfg.Upn) { ": $($Cfg.Upn)" })" "   3. Logga in med användarens konto$(if ($Cfg.Upn) { ": $($Cfg.Upn)" })")
        Info (T '   4. Check the organisation, click Join and then Done' '   4. Kontrollera organisationen, klicka på Anslut och sedan Klar')
        Atgard (T 'Open ms-settings:workplace' 'Öppna ms-settings:workplace') { Start-Process 'ms-settings:workplace' }
        while (-not $Torr) {
            Vanta (T 'Press Enter when the join is done' 'Tryck Enter när anslutningen är klar')
            $a = Get-Anslutning
            if ($a.Entra) { Ok (T "Joined to Entra ID ($($a.Tenant))." "Ansluten till Entra ID ($($a.Tenant))."); break }
            Varning (T 'dsregcmd /status does not show AzureAdJoined : YES yet.' 'dsregcmd /status visar inte AzureAdJoined : YES än.')
            if (-not (Fraga (T 'Check again?' 'Kontrollera igen?') $true)) { break }
        }
    }
    try { $vol = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop } catch { $vol = $null }
    if ($vol -and $vol.VolumeStatus -ne 'FullyDecrypted') {
        if ($a.Entra -and $Steg.bitlocker_spara) {
            foreach ($p in @($vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' })) {
                try {
                    Atgard (T 'Back up the BitLocker key to Entra ID' 'Säkerhetskopiera BitLocker-nyckeln till Entra ID') {
                        BackupToAAD-BitLockerKeyProtector -MountPoint $env:SystemDrive -KeyProtectorId $p.KeyProtectorId -ErrorAction Stop | Out-Null
                        Ok (T 'The key is now on the device in Entra ID.' 'Nyckeln finns nu på enheten i Entra ID.')
                    }
                } catch { Varning (T "Could not save the key in Entra ID: $($_.Exception.Message)" "Kunde inte spara nyckeln i Entra ID: $($_.Exception.Message)") }
            }
        }
        if ($vol.ProtectionStatus -eq 'Off' -and $vol.VolumeStatus -eq 'FullyEncrypted') {
            if (Fraga (T 'BitLocker is suspended. Turn it back on (Resume-BitLocker)?' 'BitLocker är pausat. Slå på det igen (Resume-BitLocker)?') $true) {
                Atgard 'Resume-BitLocker' { Resume-BitLocker -MountPoint $env:SystemDrive -ErrorAction Stop | Out-Null; Ok (T 'BitLocker is on again.' 'BitLocker är på igen.') }
            }
        }
    }
    Info ''
    Info (T 'Restart and sign in as the user (Other user → the email address).' 'Starta om och logga in som användaren (Annan användare → e-postadressen).')
    Info (T 'Windows Hello: skip it at the first sign-in.' 'Windows Hello: hoppa över vid första inloggningen.')
    Info (T "Then run as the user (not as administrator): .\$SkriptNamn -Aterlas" "Kör sedan som användaren (inte som administratör): .\$SkriptNamn -Aterlas")
}

function Steg-aterlas {
    if ($env:USERPROFILE -eq $Cfg.Profil) {
        Varning (T "You are signed in with the old profile ($($Cfg.Profil)). The restore must be done in the new profile." "Du är inloggad med den gamla profilen ($($Cfg.Profil)). Återläsningen ska göras i den nya profilen.")
        if (-not (Fraga (T 'Continue anyway?' 'Fortsätta ändå?') $false)) { return }
    }
    if (Test-Admin) { Varning (T "The script runs as administrator. The copies end up in the profile of $env:USERNAME ($env:USERPROFILE)." "Skriptet körs som administratör. Kopiorna hamnar i profilen för $env:USERNAME ($env:USERPROFILE).") }
    Info (T "New profile: $env:USERPROFILE" "Ny profil: $env:USERPROFILE")
    if ($Steg.kopiera -and $Mappar.Count) {
        if (Get-Process -Name 'Microsoft.Notes' -ErrorAction SilentlyContinue) { Varning (T 'Close Sticky Notes before the restore.' 'Stäng Sticky Notes innan återläsningen.') }
        foreach ($m in $Mappar) {
            $fran = Join-Path $Cfg.Malmapp $m.Mal
            if (-not (Test-Path -LiteralPath $fran)) { Info (T "$($m.Namn): no copy in $fran." "$($m.Namn): ingen kopia i $fran."); continue }
            $till = Resolve-Mapp $m 'HKEY_CURRENT_USER' $env:USERPROFILE
            Info ''
            Info "$($m.Namn): $fran -> $till"
            Invoke-Robocopy $fran $till -BehallNyare | Out-Null
        }
    }

    Rubrik 'OneDrive'
    $od = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\OneDrive\Accounts\Business1' -ErrorAction SilentlyContinue
    if ($od -and $od.UserEmail) { Ok (T "OneDrive is signed in as $($od.UserEmail)." "OneDrive är inloggat som $($od.UserEmail).") } else { Varning (T 'OneDrive (work/school) is not signed in yet.' 'OneDrive (jobb/skola) är inte inloggat än.') }
    if (Get-Process -Name 'OneDrive' -ErrorAction SilentlyContinue) { Ok (T 'OneDrive is running.' 'OneDrive körs.') } else {
        Varning (T 'OneDrive is not running.' 'OneDrive körs inte.')
        $exe = @("$env:LOCALAPPDATA\Microsoft\OneDrive\OneDrive.exe", "$env:ProgramFiles\Microsoft OneDrive\OneDrive.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($exe) { Atgard (T 'Start OneDrive' 'Starta OneDrive') { Start-Process $exe } }
    }
    foreach ($n in 'Desktop', 'Personal', 'My Pictures') {
        $v = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue).$n
        $v = [Environment]::ExpandEnvironmentVariables([string]$v)
        $t = if ($v -match '\\OneDrive') { (T 'backed up by OneDrive' 'säkerhetskopieras av OneDrive') } else { (T 'not in OneDrive' 'inte i OneDrive') }
        Info ("{0,-12} {1}  ({2})" -f $n, $v, $t)
    }

    $wmapp = Join-Path $Cfg.Malmapp 'Webblasare'
    if ($Cfg.Webblasare.Count -and (Test-Path -LiteralPath $wmapp)) {
        Rubrik (T 'Browsers' 'Webbläsare')
        Get-ChildItem -LiteralPath $wmapp -File | ForEach-Object { Info $_.Name }
        foreach ($b in $Cfg.Webblasare) {
            $w = $Webblasare[$b]
            Info (T "$($w.Namn): sign in, import bookmarks (*-bokmarken.html) via $($w.Import)" "$($w.Namn): logga in, importera bokmärken (*-bokmarken.html) via $($w.Import)")
            Info (T "         and passwords (CSV) via $($w.Losen) → Import." "         och lösenord (CSV) via $($w.Losen) → Importera.")
            Atgard (T "Open $($w.Import)" "Öppna $($w.Import)") { try { Start-Process $w.Exe $w.Import } catch { Varning (T "Could not start $($w.Exe)." "Kunde inte starta $($w.Exe).") } }
        }
        Atgard (T "Open $wmapp" "Öppna $wmapp") { Invoke-Item -LiteralPath $wmapp }
        Vanta (T 'Press Enter when bookmarks and passwords are imported' 'Tryck Enter när bokmärken och lösenord är importerade')
        $csv = @(Get-ChildItem -LiteralPath $wmapp -Filter '*.csv' -File -ErrorAction SilentlyContinue)
        if ($csv.Count -and (Fraga (T "Delete $($csv.Count) CSV file(s) with passwords now?" "Radera $($csv.Count) CSV-fil(er) med lösenord nu?") $false)) {
            Atgard (T 'Delete the password files' 'Radera lösenordsfilerna') { $csv | Remove-Item -Force; Ok 'Raderade.' }
        }
    }

    $amapp = Join-Path $Cfg.Malmapp 'Aktivitetsfalt'
    $bild = Get-ChildItem -LiteralPath $Cfg.Malmapp -Filter 'Skarmdump-*.png' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($bild -or (Test-Path -LiteralPath $amapp)) {
        Rubrik (T 'Taskbar' 'Aktivitetsfältet')
        Info (T 'Pin the apps as in the screenshot (right-click → Pin to taskbar).' 'Fäst programmen enligt skärmdumpen (högerklicka → Fäst i aktivitetsfältet).')
        if ($bild) { Atgard (T "Open $($bild.Name)" "Öppna $($bild.Name)") { Invoke-Item -LiteralPath $bild.FullName } }
        if (Test-Path -LiteralPath (Join-Path $amapp 'Fasta-program.txt')) {
            Info (T "Previously pinned: $((Get-Content -LiteralPath (Join-Path $amapp 'Fasta-program.txt')) -join ', ')" "Tidigare fästa: $((Get-Content -LiteralPath (Join-Path $amapp 'Fasta-program.txt')) -join ', ')")
        }
    }

    Rubrik (T 'Outlook and Teams' 'Outlook och Teams')
    if (Fraga (T 'Start Outlook (creates the profile)?' 'Starta Outlook (skapar profilen)?') $true) {
        Atgard (T 'Start Outlook' 'Starta Outlook') { try { Start-Process 'outlook.exe' } catch { Varning (T 'Cannot find Outlook.' 'Hittar inte Outlook.') } }
    }
    if (Fraga (T 'Start Teams?' 'Starta Teams?') $true) {
        Atgard (T 'Start Teams' 'Starta Teams') {
            try { Start-Process 'ms-teams.exe' -ErrorAction Stop } catch { try { Start-Process 'msteams:' } catch { Varning (T 'Cannot find Teams.' 'Hittar inte Teams.') } }
        }
    }
    Info (T 'Check that Teams and Outlook use the right account, and sync Teams/SharePoint libraries (Sync in the browser).' 'Kontrollera att Teams och Outlook använder rätt konto, och synka Teams-/SharePoint-bibliotek (Synkronisera i webben).')
}

# ═══════════════════════════════ start ═══════════════════════════════
$StegTitel = @{
    info = (T 'Computer information' 'Datorinformation'); kopiera = (T 'Copy folders' 'Kopiera mappar'); bitlocker_spara = (T 'Save the BitLocker key' 'Spara BitLocker-nyckeln')
    bitlocker_av = $(if ($Cfg.BitLocker -eq 'stang-av') { (T 'Turn off BitLocker' 'Stäng av BitLocker') } else { (T 'Suspend BitLocker' 'Pausa BitLocker') })
    bokmarken = (T 'Bookmarks' 'Bokmärken'); losenord = (T 'Passwords (manual export)' 'Lösenord (manuell export)'); aktivitetsfalt = (T 'Taskbar' 'Aktivitetsfältet')
    skarmdump = (T 'Screenshot' 'Skärmdump'); admin = (T "Local admin account ($($Cfg.AdminNamn))" "Lokalt admin-konto ($($Cfg.AdminNamn))")
    doman = (T 'Leave the domain' 'Lämna domänen'); entra = (T 'Join Entra ID' 'Anslut till Entra ID'); aterlas = (T 'Restore' 'Återläsning')
}

function Kor-Steg([string[]]$Lista) {
    $valda = @($Lista | Where-Object { $Steg[$_] })
    if (-not $valda.Count) { Info (T 'No steps are selected for this phase.' 'Inga steg är valda för den här fasen.'); return }
    $n = 0
    foreach ($id in $valda) {
        $n++
        Rubrik ((T 'Step {0}/{1}: {2}' 'Steg {0}/{1}: {2}') -f $n, $valda.Count, $StegTitel[$id])
        try {
            & ('Steg-' + $id)
        } catch {
            Fel ((T '{0} failed: {1}' '{0} misslyckades: {1}') -f $StegTitel[$id], $_.Exception.Message)
            if (-not (Fraga (T 'Continue with the next step?' 'Fortsätta med nästa steg?') $true)) { throw (T 'Cancelled.' 'Avbrutet.') }
        }
    }
}

if ($PSVersionTable.PSVersion.Major -gt 5) {
    Write-Host (T '  Tip: the script is made for Windows PowerShell 5.1 (powershell.exe). The BitLocker and LocalAccounts cmdlets are not always in pwsh.' '  Tips: skriptet är gjort för Windows PowerShell 5.1 (powershell.exe). BitLocker- och LocalAccounts-cmdletarna finns inte alltid i pwsh.') -ForegroundColor Yellow
}
if ($env:OS -ne 'Windows_NT') { Write-Host (T '  The script only runs on Windows.' '  Skriptet körs bara på Windows.') -ForegroundColor Red; exit 1 }
if ($Fas -ne 'aterlas' -and -not (Test-Admin)) {
    Write-Host (T '  Run the script as administrator (right-click PowerShell → Run as administrator).' '  Kör skriptet som administratör (högerklicka PowerShell → Kör som administratör).') -ForegroundColor Red
    exit 1
}
if ($Fas -eq 'aterlas' -and -not (Test-Path -LiteralPath $Cfg.Malmapp)) {
    Write-Host (T "  Cannot find the target folder $($Cfg.Malmapp). Change Malmapp at the top of the script." "  Hittar inte målmappen $($Cfg.Malmapp). Ändra Malmapp högst upp i skriptet.") -ForegroundColor Red
    exit 1
}

$loggMapp = $Cfg.Malmapp
if ($Torr -and -not (Test-Path -LiteralPath $loggMapp)) { $loggMapp = $env:TEMP }
try { New-Item -ItemType Directory -Path $loggMapp -Force -WhatIf:$false -ErrorAction Stop | Out-Null } catch { $loggMapp = $env:TEMP }
$script:RoboLogg = Join-Path $loggMapp 'robocopy.log'
$logg = Join-Path $loggMapp ('Muppa-{0}-{1}.log' -f $Fas, (Get-Date -Format 'yyyyMMdd-HHmmss'))
$transkript = $false
try { Start-Transcript -Path $logg -Append -WhatIf:$false | Out-Null; $transkript = $true } catch { Write-Host (T "  Could not start the log: $($_.Exception.Message)" "  Kunde inte starta loggen: $($_.Exception.Message)") -ForegroundColor Yellow }

$fasNamn = @{ forbered = (T 'prepare' 'förbered'); doman = (T 'leave the domain' 'lämna domänen'); entra = (T 'join Entra ID' 'anslut till Entra ID'); aterlas = (T 'restore' 'återläsning') }[$Fas]
Write-Host ''
Write-Host (T "  Muppa · migration of $($Cfg.Anvandare) · phase: $fasNamn$(if ($Torr) { ' · DRY RUN, nothing changes' })" "  Muppa · migrering av $($Cfg.Anvandare) · fas: $fasNamn$(if ($Torr) { ' · TORRKÖRNING, inget ändras' })") -ForegroundColor Magenta
if ($Cfg.Kund) { Write-Host (T "  Customer: $($Cfg.Kund)" "  Kund: $($Cfg.Kund)") }
Write-Host (T "  Target folder: $($Cfg.Malmapp) · log: $logg" "  Målmapp: $($Cfg.Malmapp) · logg: $logg")

try {
    switch ($Fas) {
        'forbered' {
            if ($PSCommandPath -and -not $Torr) {
                $kopia = Join-Path $Cfg.Malmapp $SkriptNamn
                if ($PSCommandPath -ne $kopia) {
                    Copy-Item -LiteralPath $PSCommandPath -Destination $kopia -Force
                    Write-Host (T "  The script is now also in $kopia (run the other phases from there)." "  Skriptet finns nu också i $kopia (kör de andra faserna därifrån).")
                }
            }
            $plan = @('info', 'kopiera', 'bitlocker_spara', 'bitlocker_av', 'bokmarken', 'losenord', 'aktivitetsfalt', 'skarmdump', 'admin')
            Write-Host (T "  Steps: $((@($plan | Where-Object { $Steg[$_] }) | ForEach-Object { $StegTitel[$_] }) -join ' · ')" "  Steg: $((@($plan | Where-Object { $Steg[$_] }) | ForEach-Object { $StegTitel[$_] }) -join ' · ')")
            if (-not (Fraga (T 'Start?' 'Starta?') $true)) { throw (T 'Cancelled.' 'Avbrutet.') }
            Kor-Steg $plan
            Rubrik (T 'Next steps' 'Nästa steg')
            $skript = Join-Path $Cfg.Malmapp $SkriptNamn
            if ($Steg.doman) {
                Info (T "1. Sign out and sign in as $(AdminVem)" "1. Logga ut och logga in som $(AdminVem)")
                Info (T "2. Run as administrator: powershell -ExecutionPolicy Bypass -File `"$skript`" -Doman" "2. Kör som administratör: powershell -ExecutionPolicy Bypass -File `"$skript`" -Doman")
            } elseif ($Steg.entra) {
                Info (T "Run as administrator: powershell -ExecutionPolicy Bypass -File `"$skript`" -Entra" "Kör som administratör: powershell -ExecutionPolicy Bypass -File `"$skript`" -Entra")
            } elseif ($Steg.aterlas) {
                Info (T "Sign in as the user and run: powershell -ExecutionPolicy Bypass -File `"$skript`" -Aterlas" "Logga in som användaren och kör: powershell -ExecutionPolicy Bypass -File `"$skript`" -Aterlas")
            }
            Info (T 'The rest is in the checklist.' 'Resten står i checklistan.')
        }
        'doman' { Kor-Steg @('doman') }
        'entra' { Kor-Steg @('entra') }
        'aterlas' { Kor-Steg @('aterlas') }
    }
    Write-Host ''
    Write-Host (T "  Done: $fasNamn." "  Klart: $fasNamn.") -ForegroundColor Green
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
            lines.append("  " + accent("⌕ ") + bold(st["q"]) + accent("▏") + dim(L(f"  {len(sel)} matches", f"  {len(sel)} träffar")))
        elif title:
            lines.append("  " + accent("▍") + bold(title) + dim(L(f"  {n_on} selected", f"  {n_on} valda")))
        narrow = w < 58
        if not sel:
            lines.append("     " + dim(L("No matches. Backspace edits, esc clears.", "Inga träffar. Backsteg ändrar, esc rensar.")))
        labels = [vr[k]["label"] for k in sel] or [""]
        lw = min(max(len(x) for x in labels) + 2, max(10, w - 16 if narrow else min(40, w // 2)))
        room = max(3, height() - len(lines) - 3)
        pos = st["pos"]
        top = min(max(st["top"], pos - room + 1), pos)
        top = max(0, min(top, max(0, len(vr) - room)))
        st["top"] = top
        if top > 0:
            lines.append(dim(L("     ↑ more", "     ↑ fler")))
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
            lines.append(dim(L("     ↓ more", "     ↓ fler")))
        lines.append("")
        if st["q"]:
            help_ = L("space ticks · enter done · esc clears", "mellanslag kryssar · enter klar · esc rensar") if w >= 50 else L("␣ tick · enter · esc", "␣ kryss · enter · esc")
        else:
            help_ = (footer or L("↑/↓ · space/digit ticks · + all/none · enter done · q cancel", "↑/↓ · mellanslag/siffra kryssar · + alla/inga · enter klar · q avbryt")) if w >= 80 else \
                L("␣/digit ticks · + all · enter done", "␣/siffra kryssar · + alla · enter klar") if w >= 50 else L("␣ tick · enter done", "␣ kryss · enter klar")
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
        out(f"   {accent('❯')} " + (bold(", ".join(names)) if names else dim(L("nothing selected", "inget valt"))))
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
        buf.append(fit(dim(L("   ↑/↓ · PgUp/PgDn · space · g/G · q closes", "   ↑/↓ · PgUp/PgDn · mellanslag · g/G · q stänger") if w >= 50 else "   ↑↓ ␣ · q"), w))
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
    ("skrivbord", L("Desktop", "Skrivbord"), "shell", "Desktop", "Desktop", "Skrivbord", True),
    ("dokument", L("Documents", "Dokument"), "shell", "Personal", "Documents", "Dokument", True),
    ("bilder", L("Pictures", "Bilder"), "shell", "My Pictures", "Pictures", "Bilder", True),
    ("hamtade", L("Downloads", "Hämtade filer"), "shell", "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads", "Hamtade", True),
    ("musik", L("Music", "Musik"), "shell", "My Music", "Music", "Musik", False),
    ("videor", L("Videos", "Videor"), "shell", "My Video", "Videos", "Videor", False),
    ("favoriter", L("Favorites", "Favoriter"), "shell", "Favorites", "Favorites", "Favoriter", False),
    ("kontakter", L("Contacts", "Kontakter"), "shell", "{56784854-C6CB-462B-8169-88E350ACB882}", "Contacts", "Kontakter", False),
    ("sticky", "Sticky Notes", "relativ",
     r"AppData\Local\Packages\Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe\LocalState", "", "StickyNotes", False),
    ("signaturer", L("Outlook signatures", "Outlook-signaturer"), "relativ", r"AppData\Roaming\Microsoft\Signatures", "", "Signaturer", False),
]
FOLDER_HINT = {
    "skrivbord": L("may be redirected to OneDrive", "kan vara omdirigerad till OneDrive"), "dokument": L("may be redirected to OneDrive", "kan vara omdirigerad till OneDrive"),
    "bilder": L("may be redirected to OneDrive", "kan vara omdirigerad till OneDrive"), "hamtade": "Downloads",
    "favoriter": L("Internet Explorer favorites (not Edge)", "Internet Explorer-favoriter (inte Edge)"), "kontakter": L("Windows contacts (.contact)", "Windows-kontakter (.contact)"),
    "sticky": r"AppData\Local\Packages\…StickyNotes…\LocalState",
    "signaturer": r"AppData\Roaming\Microsoft\Signatures",
}
FOLDER_BY_ID = {f[0]: f for f in FOLDERS}
BROWSERS = [("edge", "Microsoft Edge"), ("chrome", "Google Chrome")]
BITLOCKER = [("pausa", L("Save the key and suspend BitLocker", "Spara nyckeln och pausa BitLocker"), L("recommended: Suspend-BitLocker, turned back on in -Entra", "rekommenderas: Suspend-BitLocker, slås på igen i -Entra")),
             ("stang-av", L("Save the key and turn off BitLocker", "Spara nyckeln och stäng av BitLocker"), L("Disable-BitLocker: decrypts the whole disk", "Disable-BitLocker: dekrypterar hela disken")),
             ("spara", L("Only save the key", "Bara spara nyckeln"), L("leaves the protection alone", "rör inte skyddet")),
             ("ingen", L("Leave BitLocker alone", "Rör inte BitLocker"), "")]
DOMAIN = [("bada", L("Leave the domain and join Entra ID", "Lämna domänen och anslut till Entra ID"), L("-Doman, then -Entra", "-Doman, sedan -Entra")),
          ("doman", L("Only leave the domain", "Bara lämna domänen"), "-Doman"),
          ("entra", L("Only join Entra ID", "Bara anslut till Entra ID"), L("the computer is already out of the domain", "datorn är redan utanför domänen")),
          ("inget", L("Neither", "Inget av dem"), L("only copying and preparation", "bara kopiering och förberedelser"))]
CLOUD = [("hoppa", L("Skip files that only exist in the cloud", "Hoppa över filer som bara finns i molnet"), L("recommended: they are already in OneDrive (/XA:O)", "rekommenderas: de finns redan i OneDrive (/XA:O)")),
         ("kopiera", L("Try to copy them too", "Försök kopiera dem också"), L("downloads them if OneDrive is running, otherwise errors", "laddar ner dem om OneDrive körs, annars fel"))]

# Steg: id, rubrik, grupp. Alla kan väljas bort.
STEPS = [
    ("m_losenord", L("Reset the password in Entra ID", "Återställ lösenordet i Entra ID"), L("before the migration", "före migreringen")),
    ("m_onedrive_fore", L("Check the OneDrive backup", "Kontrollera OneDrive-backup"), L("before the migration", "före migreringen")),
    ("info", L("Computer information (domain, Entra, BitLocker)", "Datorinformation (domän, Entra, BitLocker)"), L("before the migration", "före migreringen")),
    ("kopiera", L("Copy the chosen folders to the target folder", "Kopiera valda mappar till målmappen"), L("before the migration", "före migreringen")),
    ("bitlocker_spara", L("Save the BitLocker key", "Spara BitLocker-nyckeln"), L("before the migration", "före migreringen")),
    ("bitlocker_av", L("Suspend / turn off BitLocker", "Pausa / stäng av BitLocker"), L("before the migration", "före migreringen")),
    ("bokmarken", L("Export bookmarks", "Exportera bokmärken"), L("before the migration", "före migreringen")),
    ("losenord", L("Password export (you save the CSV)", "Lösenordsexport (du sparar CSV)"), L("before the migration", "före migreringen")),
    ("aktivitetsfalt", L("Save the pinned taskbar icons", "Spara fästa ikoner i aktivitetsfältet"), L("before the migration", "före migreringen")),
    ("skarmdump", L("Screenshot of the screen", "Skärmdump av skärmen"), L("before the migration", "före migreringen")),
    ("admin", L("Create a local admin account", "Skapa lokalt admin-konto"), L("before the migration", "före migreringen")),
    ("m_bitlocker_extern", L("Save the BitLocker key outside the computer", "Spara BitLocker-nyckeln utanför datorn"), L("before the migration", "före migreringen")),
    ("doman", L("Leave the domain (-Doman)", "Lämna domänen (-Doman)"), L("from domain to Entra ID", "från domän till Entra ID")),
    ("entra", L("Join Entra ID (-Entra)", "Anslut till Entra ID (-Entra)"), L("from domain to Entra ID", "från domän till Entra ID")),
    ("m_hello", L("Windows Hello: skip the first time", "Windows Hello: hoppa över första gången"), L("as the user afterwards", "som användaren efteråt")),
    ("aterlas", L("Restore in the new profile (-Aterlas)", "Återläsning i nya profilen (-Aterlas)"), L("as the user afterwards", "som användaren efteråt")),
    ("m_onedrive", L("OneDrive sync checked", "OneDrive-synk kontrollerad"), L("as the user afterwards", "som användaren efteråt")),
    ("m_edge", L("Import passwords, sign in to the browser", "Importera lösenord, logga in i webbläsaren"), L("as the user afterwards", "som användaren efteråt")),
    ("m_outlook", L("Outlook profile", "Outlook-profil"), L("as the user afterwards", "som användaren efteråt")),
    ("m_ikoner", L("Pin apps as in the screenshot", "Fäst program enligt skärmdumpen"), L("as the user afterwards", "som användaren efteråt")),
    ("m_teams", L("Teams with the right account", "Teams med rätt konto"), L("as the user afterwards", "som användaren efteråt")),
    ("m_bibliotek", L("Sync Teams and SharePoint libraries", "Synka Teams- och SharePoint-bibliotek"), L("as the user afterwards", "som användaren efteråt")),
    ("m_radera", L("Delete the password files (CSV)", "Radera lösenordsfilerna (CSV)"), L("as the user afterwards", "som användaren efteråt")),
    ("m_meddela", L("Tell the person responsible to set a new password", "Meddela ansvarig att sätta nytt lösenord"), L("done", "klart")),
]
STEP_IDS = [s[0] for s in STEPS]
STEP_LABEL = {s[0]: s[1] for s in STEPS}
SCRIPT_STEPS = [s for s in STEP_IDS if not s.startswith("m_")]
OPTIONAL_FIELDS = [("kund", L("Customer", "Kund")), ("ansvarig", L("Responsible (gets the message when it is done)", "Ansvarig (får meddelandet när det är klart)")),
                   ("dator", L("Computer name", "Datorns namn")), ("upn", L("The user's email (UPN) in Entra ID", "Användarens e-post (UPN) i Entra ID")),
                   ("tekniker", L("Technician", "Tekniker")), ("arende", L("Ticket number", "Ärendenummer"))]
SHORT_LABEL = {"kund": L("Customer", "Kund"), "ansvarig": L("Responsible", "Ansvarig"), "dator": L("Computer", "Dator"), "upn": L("Email (UPN)", "E-post (UPN)"),
               "tekniker": L("Technician", "Tekniker"), "arende": L("Ticket", "Ärende")}
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
    S = []
    S.append("<#")
    S.append(".SYNOPSIS")
    S.append(L(f"    Migration from on-premises AD domain to Entra ID: {safe_comment(user)}. Created by Muppa {VERSION}, {now}.",
               f"    Migrering från lokal AD-domän till Entra ID: {safe_comment(user)}. Skapat av Muppa {VERSION}, {now}."))
    S.append(".DESCRIPTION")
    S.append(L("    Runs in Windows PowerShell 5.1 on the computer being migrated, in this order:",
               "    Körs i Windows PowerShell 5.1 på datorn som migreras, i den här ordningen:"))
    S.append(L(f"      .\\{name}              prepare (as administrator): copy, BitLocker, browsers, admin account",
               f"      .\\{name}              förbered (som administratör): kopiera, BitLocker, webbläsare, admin-konto"))
    if "doman" in steps:
        S.append(L(f"      .\\{name} -Doman       leave the domain (after confirmation) and restart",
                   f"      .\\{name} -Doman       lämna domänen (efter bekräftelse) och starta om"))
    if "entra" in steps:
        S.append(L(f"      .\\{name} -Entra       join Entra ID (opens Settings), BitLocker key to Entra",
                   f"      .\\{name} -Entra       anslut till Entra ID (öppnar Inställningar), BitLocker-nyckel till Entra"))
    if "aterlas" in steps:
        S.append(L(f"      .\\{name} -Aterlas     as the user: copy back, OneDrive, Outlook and Teams",
                   f"      .\\{name} -Aterlas     som användaren: kopiera tillbaka, OneDrive, Outlook och Teams"))
    S.append(L("    Try it first with -Torrkorning (or -WhatIf): it only shows what would be done.",
               "    Provkör först med -Torrkorning (eller -WhatIf): då visas bara vad som skulle göras."))
    S.append(L("    No passwords are in the script. The admin account password is asked for when the script runs.",
               "    Inga lösenord står i skriptet. Admin-kontots lösenord frågas efter när skriptet körs."))
    S.append(L("    Start: ", "    Starta: ") + f"powershell.exe -ExecutionPolicy Bypass -File .\\{name} -Torrkorning")
    S.append(".PARAMETER Doman")
    S.append(L("    Leave the domain (Remove-Computer) after confirmation.", "    Lämna domänen (Remove-Computer) efter bekräftelse."))
    S.append(".PARAMETER Entra")
    S.append(L("    Open Settings to join Entra ID and back up the BitLocker key there.",
               "    Öppna Inställningar för anslutning till Entra ID och säkerhetskopiera BitLocker-nyckeln dit."))
    S.append(".PARAMETER Aterlas")
    S.append(L("    Runs as the user in the new profile: copy back from the target folder.",
               "    Körs som användaren i den nya profilen: kopiera tillbaka från målmappen."))
    S.append(".PARAMETER Torrkorning")
    S.append(L("    Show what would be done, change nothing (same as -WhatIf).", "    Visa vad som skulle göras, ändra ingenting (samma som -WhatIf)."))
    S.append("#>")
    S.append("[CmdletBinding(SupportsShouldProcess = $true)]")
    S.append("param(")
    S.append("    [switch]$Doman,")
    S.append("    [switch]$Entra,")
    S.append("    [switch]$Aterlas,")
    S.append("    [switch]$Torrkorning")
    S.append(")")
    S.append("")
    S.append(L("# ─────────────── Settings (created by Muppa) ───────────────", "# ─────────────── Inställningar (skapade av Muppa) ───────────────"))
    S.append(L("# Language of the messages: 'en' or 'sv'.", "# Språk för meddelandena: 'en' eller 'sv'.") )
    S.append(f"$Sprak = '{LANG}'")
    S.append("$Cfg = @{")
    rows = [("Anvandare", user), ("Profil", a["profile"]), ("Malmapp", mal), ("AdminNamn", admin),
            ("BitLocker", bl), ("Molnfiler", a["cloud"]), ("Upn", a.get("upn") or "")] + \
           [(k.replace("Ä", "A"), v or "") for k, v in meta]
    kw = max(len(k) for k, _ in rows + [("Webblasare", "")]) + 1
    for k, v in rows:
        S.append(f"    {k.ljust(kw)}= {psq(v)}")
    S.append("    " + "Webblasare".ljust(kw) + "= @(" + ", ".join(psq(b) for b in a["browsers"]) + ")")
    S.append("}")
    S.append("")
    S.append(L("# The steps. $false = skipped.", "# Stegen. $false = hoppas över."))
    S.append("$Steg = @{")
    for sid in SCRIPT_STEPS:
        S.append(f"    {sid.ljust(16)}= " + ("$true" if sid in steps else "$false").ljust(7) + f"  # {STEP_LABEL[sid]}")
    S.append("}")
    S.append("")
    S.append(L("# The folders that are copied. Typ shell = read from the user's User Shell Folders (follows OneDrive redirection).",
               "# Mapparna som kopieras. Typ shell = läses ur användarens User Shell Folders (följer OneDrive-omdirigering)."))
    S.append("$Mappar = @(")
    for e in folder_entries(a):
        S.append("    @{ " + "; ".join(f"{k} = {psq(e[k])}" for k in ("Id", "Namn", "Typ", "Varde", "Standard", "Mal")) + " }")
    S.append(")")
    S.append("")
    return name, "\r\n".join(S + PS_BODY.strip("\n").splitlines()) + "\r\n"


# ───────────────────────────── checklistan ─────────────────────────────
def build_checklist(a, script_name):
    steps = final_steps(a)
    user = a["user"]
    mal = malmapp_for(a)
    admin = a.get("admin_name") or "lokaladmin"
    login = f"**.\\{admin}**" if "admin" in steps else L("a local admin account (you must know the password)", "ett lokalt admin-konto (du måste kunna lösenordet)")
    run = f"powershell.exe -ExecutionPolicy Bypass -File {mal}\\{script_name}"
    names = [FOLDER_BY_ID[f][1] for f in a["folders"] if f in FOLDER_BY_ID] + list(a["custom"])
    browsers = L(" and ", " och ").join(dict(BROWSERS)[b].split()[-1] for b in a["browsers"]) or L("the browser", "webbläsaren")
    who = a.get("upn") or user
    S = [L(f"# Migration to Entra ID: {user}", f"# Migrering till Entra ID: {user}"), ""]
    meta = [(L("User", "Användare"), f"{user} (`{a['profile']}`)")]
    for k, _ in OPTIONAL_FIELDS:
        if a.get(k):
            meta.append((SHORT_LABEL[k], a[k]))
    meta += [(L("Target folder", "Målmapp"), f"`{mal}`"), (L("Created", "Skapad"), L(f"{datetime.now():%Y-%m-%d %H:%M} with Muppa {VERSION}", f"{datetime.now():%Y-%m-%d %H:%M} med Muppa {VERSION}"))]
    S += ["| | |", "|---|---|"] + [f"| {k} | {str(v).replace('|', '/')} |" for k, v in meta] + [""]
    S += [L(f"Script: `{script_name}`. Copy it to the computer (e.g. `C:\\temp`) and run it in Windows PowerShell "
            f"as administrator. The first run copies the script to the target folder, so the rest can be run from there.",
            f"Skript: `{script_name}`. Kopiera det till datorn (t.ex. `C:\\temp`) och kör i Windows PowerShell "
            f"som administratör. Första körningen kopierar skriptet till målmappen, så att resten kan köras därifrån."),
          "", "```powershell", f"powershell.exe -ExecutionPolicy Bypass -File .\\{script_name} -Torrkorning   # " + L("try it first", "provkör först"),
          f"powershell.exe -ExecutionPolicy Bypass -File .\\{script_name}", "```", ""]

    def box(sid, text, sub=None):
        if sid is None or sid in steps:
            S.append(f"- [ ] {text}")
            for s in sub or []:
                S.append(f"  - [ ] {s}")

    S.append(L("## 1. Before the migration", "## 1. Före migreringen"))
    S.append("")
    box("m_losenord", L("Reset the user's password in Entra ID (if the user has a local account: use the local password).",
                        "Återställ användarens lösenord i Entra ID (har användaren ett lokalt konto: använd det lokala lösenordet)."))
    box("m_onedrive_fore", L("Check that OneDrive backs up Desktop, Documents and Pictures and is fully synced "
                             "(OneDrive → Settings → Sync and back up).",
                             "Kontrollera att OneDrive säkerhetskopierar Skrivbord, Dokument och Bilder och är helt synkat "
                             "(OneDrive → Inställningar → Synkronisera och säkerhetskopiera)."))
    fore = []
    if "info" in steps:
        fore.append(L(f"Computer information saved (`{mal}\\Datorinfo.txt`)", f"Datorinformation sparad (`{mal}\\Datorinfo.txt`)"))
    if "kopiera" in steps:
        fore.append(L(f"Folders copied to `{mal}`: {', '.join(names) or '–'} (read the robocopy summary)", f"Mappar kopierade till `{mal}`: {', '.join(names) or '–'} (läs robocopy-sammanfattningen)"))
    if "bitlocker_spara" in steps:
        fore.append(L(f"BitLocker key saved in `{mal}\\BitLocker-<computer>.txt`", f"BitLocker-nyckeln sparad i `{mal}\\BitLocker-<dator>.txt`"))
    if "bitlocker_av" in steps:
        fore.append(L("BitLocker turned off (wait until `manage-bde -status` shows Fully Decrypted)", "BitLocker avstängt (vänta tills `manage-bde -status` visar Fully Decrypted)")
                    if a["bitlocker"] == "stang-av" else L("BitLocker suspended", "BitLocker pausat"))
    if "bokmarken" in steps:
        fore.append(L(f"Bookmarks exported from {browsers} (`Webblasare\\*-bokmarken.html`)", f"Bokmärken exporterade från {browsers} (`Webblasare\\*-bokmarken.html`)"))
    if "losenord" in steps:
        fore.append(L(f"Passwords exported to CSV from {browsers} (`Webblasare\\*-losenord.csv`), in the user's session", f"Lösenord exporterade till CSV från {browsers} (`Webblasare\\*-losenord.csv`), i användarens session"))
    if "aktivitetsfalt" in steps:
        fore.append(L("Pinned icons saved (`Aktivitetsfalt\\`)", "Fästa ikoner sparade (`Aktivitetsfalt\\`)"))
    if "skarmdump" in steps:
        fore.append(L("Screenshot of the taskbar saved", "Skärmdump av aktivitetsfältet sparad"))
    if "admin" in steps:
        fore.append(L(f"Local admin account **{admin}** created. The password kept safe (it is not in the script)", f"Lokalt admin-konto **{admin}** skapat. Lösenordet förvarat säkert (det står inte i skriptet)"))
    if fore:
        box(None, L(f"Run `{script_name}` as administrator:", f"Kör `{script_name}` som administratör:"), fore)
    box("m_bitlocker_extern", L("Save the BitLocker key outside the computer (documentation/ticket). The target folder is on the same disk.",
                                "Spara BitLocker-nyckeln utanför datorn (dokumentation/ärende). Målmappen ligger på samma disk."))
    S.append("")

    if "doman" in steps or "entra" in steps:
        S.append(L("## 2. Switch from domain to Entra ID", "## 2. Byt från domän till Entra ID"))
        S.append("")
        box(None, L(f"Sign out and sign in as {login}.", f"Logga ut och logga in som {login}."))
        box("doman", L(f"Run `{run} -Doman` and type LEAVE. Restart.", f"Kör `{run} -Doman` och skriv LÄMNA. Starta om."))
        if "entra" in steps:
            box(None, L(f"Sign in as {login} and run `{run} -Entra`.", f"Logga in som {login} och kör `{run} -Entra`."))
            box(None, L("Settings → Accounts → Access work or school → Connect → "
                        "**Join this device to Microsoft Entra ID**. Sign in with the user's credentials",
                        "Inställningar → Konton → Åtkomst till arbete eller skola → Anslut → "
                        "**Anslut den här enheten till Microsoft Entra ID**. Logga in med användarens uppgifter")
                      + (f" ({a['upn']})" if a.get("upn") else "") + ".")
            box(None, L("Check that `dsregcmd /status` shows `AzureAdJoined : YES`. Restart.", "Kontrollera att `dsregcmd /status` visar `AzureAdJoined : YES`. Starta om."))
        S.append("")

    S.append(L("## 3. The first sign-in as the user", "## 3. Första inloggningen som användaren"))
    S.append("")
    box(None, L(f"Sign in as the user (Other user → {who}).", f"Logga in som användaren (Annan användare → {who})."))
    box("m_hello", L("Windows Hello: skip it at the first sign-in.", "Windows Hello: hoppa över vid första inloggningen."))
    if "aterlas" in steps:
        box(None, L(f"Run `{run} -Aterlas` as the user (not as administrator): copies back, checks "
                    "OneDrive and opens Outlook and Teams.",
                    f"Kör `{run} -Aterlas` som användaren (inte som administratör): kopierar tillbaka, kontrollerar "
                    "OneDrive och öppnar Outlook och Teams."))
    box("m_onedrive", L("OneDrive is signed in and syncing. Desktop, Documents and Pictures are there and backed up.", "OneDrive är inloggat och synkar. Skrivbord, Dokument och Bilder finns och säkerhetskopieras."))
    box("m_edge", L(f"Sign in to {browsers}. Import bookmarks (HTML) and passwords (CSV).", f"Logga in i {browsers}. Importera bokmärken (HTML) och lösenord (CSV)."))
    box("m_outlook", L("Start Outlook so the profile is created. Check the signature.", "Starta Outlook så att profilen skapas. Kontrollera signaturen."))
    box("m_ikoner", L("Pin the apps to the taskbar as in the screenshot.", "Fäst programmen i aktivitetsfältet enligt skärmdumpen."))
    box("m_teams", L("Teams is signed in with the right account.", "Teams är inloggat med rätt konto."))
    box("m_bibliotek", L("Sync Teams and SharePoint libraries (Sync in the browser).", "Synka Teams- och SharePoint-bibliotek (Synkronisera i webbläsaren)."))
    box("m_radera", L(f"Delete the password files (`{mal}\\Webblasare\\*.csv`) once they are imported.", f"Radera lösenordsfilerna (`{mal}\\Webblasare\\*.csv`) när de är importerade."))
    S.append("")
    if "m_meddela" in steps:
        S.append(L("## Done", "## Klart"))
        S.append("")
        box("m_meddela", L("Tell the person responsible", "Meddela ansvarig") + (f" ({a['ansvarig']})" if a.get("ansvarig") else "") +
            L(" that the user should set a new password.", " att användaren ska sätta ett nytt lösenord."))
        S.append("")
    S += [L("## Notes", "## Anteckningar"), "", ""]
    return "\n".join(S)


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
             + (L(f" +{len(t['custom'])} own", f" +{len(t['custom'])} egna") if t.get("custom") else "")]
    parts.append("+".join(dict(BROWSERS)[b].split()[-1] for b in t.get("browsers", [])) or L("no browser", "ingen webbläsare"))
    parts.append({"pausa": L("BitLocker suspended", "BitLocker paus"), "stang-av": L("BitLocker off", "BitLocker av"), "spara": "BitLocker-nyckel",
                  "ingen": L("no BitLocker", "utan BitLocker")}.get(t.get("bitlocker"), ""))
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
        return L("That doesn't look like a user name or a profile folder.", "Det ser inte ut som ett användarnamn eller en profilmapp.")
    return None


def check_admin(v):
    if len(v) > 20 or re.search(r'[/\\\[\]:;|=,+*?<>@"]', v) or v.strip(". ") == "":
        return L("At most 20 characters, no / \\ [ ] : ; | = , + * ? < > @ \".", "Högst 20 tecken, inga / \\ [ ] : ; | = , + * ? < > @ \".")
    return None


def ask_user(a):
    profs = windows_profiles()
    if profs:
        rows = [dict(label=n, hint=f"{p}" + (L(f" · last {w}", f" · senast {w}") if w else ""), value=p) for n, p, w in profs]
        rows.append(dict(label=L("Type it yourself…", "Skriv själv…"), hint=L("another name or path", "annat namn eller sökväg"), value="__egen__"))
        v = choose(rows, title=L("User", "Användare"), start=a["profile"] or None)
        if v is None:
            raise Cancel()
        if v != "__egen__":
            a["user"], a["profile"] = split_user(v)
            return
    v = ask_text(L("User name or profile folder (e.g. anna or C:\\Users\\anna)", "Användarnamn eller profilmapp (t.ex. anna eller C:\\Users\\anna)"), default=a["profile"] or "",
                 required=True, check=check_user)
    a["user"], a["profile"] = split_user(v)
    out("    " + dim(L(f"profile: {a['profile']}", f"profil: {a['profile']}")))


def find_puppa():
    """Kusinen Puppa, om den är installerad (i PATH, i ~/.ruppa/bin eller ~/.local/bin)."""
    p = shutil.which("puppa")
    if p:
        return p
    for d in (HOME / ".ruppa" / "bin", HOME / ".local" / "bin", HOME / ".puppa" / "bin"):
        for n in ("puppa.cmd", "puppa") if IS_WIN else ("puppa",):
            if (d / n).exists():
                return str(d / n)
    return None


def tenant_users():
    """Alla användare i tenanten via Puppa: (tenant, [(upn, namn, detalj)]) eller (None, None).
    Puppa loggar in i Graph om det behövs; det syns i terminalen (stderr), svaret kommer som JSON på stdout."""
    pp = find_puppa()
    if not pp:
        return None, None
    cmd = [pp, "uppslag", "user", "--alla", "--json"]
    if IS_WIN and pp.lower().endswith(".cmd"):
        cmd = ["cmd", "/c"] + cmd
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=900)
        data = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except Exception:
        return None, None
    if not data.get("ok"):
        return None, None
    return data.get("tenant", ""), [(i["value"], i["name"], i.get("detail", "")) for i in data.get("items", [])]


def ask_upn(a):
    """Användarens e-post i Entra ID: välj i tenanten via Puppa (fuzzy-sök, användarnamnet som förslag) eller skriv."""
    if find_puppa() and confirm(L("Look up the user in the tenant via Puppa?", "Söka upp användaren i tenanten via Puppa?"), True):
        out("    " + dim(L("Fetching the users in the tenant (Puppa signs in to Graph if needed)…", "Hämtar användarna i tenanten (Puppa loggar in i Graph om det behövs)…")))
        tenant, users = tenant_users()
        if users:
            first = fuzzy_rank(a.get("user") or "", users, lambda u: f"{u[1]} {u[0]}") if a.get("user") else []
            rest = [u for u in users if u not in first]
            rows = [dict(label=n, hint=d or v, value=v) for v, n, d in first + rest]
            rows.append(dict(label=L("Type it yourself…", "Skriv själv…"), hint=L("another address", "annan adress"), value="__egen__"))
            v = choose(rows, title=L(f"The user in Entra ID{f' · {tenant}' if tenant else ''} – type to search", f"Användaren i Entra ID{f' · {tenant}' if tenant else ''} – skriv för att söka"),
                       start=rows[0]["value"] if first else None)
            if v is None:
                raise Cancel()
            if v != "__egen__":
                return v
        else:
            out("    " + yellow(L("Could not fetch the users. Type the address yourself.", "Kunde inte hämta användarna. Skriv adressen själv.")))
    return ask_text(L("The user's email (UPN) in Entra ID", "Användarens e-post (UPN) i Entra ID") + dim(L(" (optional, enter skips)", " (valfritt, enter hoppar över)")),
                    default=a.get("upn") or "")


def ask_malmapp(a):
    default = malmapp_for(a)

    def check(v):
        if not win_path_ok(v):
            return L("Enter a Windows path, e.g. C:\\temp\\", "Ange en Windows-sökväg, t.ex. C:\\temp\\") + a["user"]
        if (v.lower().rstrip("\\") + "\\").startswith(a["profile"].lower() + "\\"):
            return L("The target folder must not be inside the profile being copied.", "Målmappen får inte ligga i profilen som kopieras.")
        return None
    v = ask_text(L("Target folder on the computer (where everything is saved)", "Målmapp på datorn (där allt sparas)"), default=default, required=True, check=check).rstrip("\\")
    a["malmapp"] = user_pattern(v, a["user"])


def ask_folders(a):
    chosen = set(a["folders"]) | {"c:" + p for p in a["custom"]}
    customs = list(a["custom"])
    while True:
        rows = [("head", "Profilmappar")]
        rows += [dict(label=f[1], hint=FOLDER_HINT.get(f[0], ""), value=f[0]) for f in FOLDERS]
        if customs:
            rows.append(("head", L("Own paths", "Egna sökvägar")))
            rows += [dict(label=p, hint="egen", value="c:" + p) for p in customs]
        rows.append(dict(label=L("Add your own path…", "Lägg till egen sökväg…"), hint=L("in the profile or absolute, e.g. D:\\Projects", "i profilen eller absolut, t.ex. D:\\Projekt"), value="__add__",
                         action=True))
        res = choose_many(rows, chosen, title=L("Which folders should be copied?", "Vilka mappar ska kopieras?"))
        if res is None:
            raise Cancel()
        picked, act = res
        chosen = set(picked)
        if act == "__add__":
            p = ask_text(L("Path (relative to the profile, %USERPROFILE%\\… or e.g. D:\\Projects)", "Sökväg (relativ till profilen, %USERPROFILE%\\… eller t.ex. D:\\Projekt)"))
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
        kind = L("manual, in the checklist", "manuellt, i checklistan") if sid.startswith("m_") else L("the script", "skriptet")
        rows.append(dict(label=label, hint=kind + ("" if sid in derived else L(" · not preselected", " · inte förvalt")), value=sid))
    res = choose_many(rows, chosen, title=L("Which steps should be included?", "Vilka steg ska ingå?"))
    if res is None:
        raise Cancel()
    sel = set(res[0])
    a["on"] = sorted(sel - derived)
    a["off"] = sorted(derived - sel)


def wizard(a, intro=True):
    """Alla frågor i ordning. Svaren ligger kvar i a, så att "Ändra svaren" börjar från dem."""
    new_screen()
    if intro:
        say(L("We build a migration script and a checklist. Enter keeps the suggestion, Ctrl+C cancels.", "Vi bygger ett migreringsskript och en checklista. Enter behåller förslaget, Ctrl+C avbryter."), "happy",
            sub=f"muppa {VERSION}")
        print()
    ask_user(a)
    ask_malmapp(a)
    ask_folders(a)
    if any(FOLDER_BY_ID[f][2] == "shell" for f in a["folders"]):
        a["cloud"] = ask_choice(L("Files that only exist in the OneDrive cloud (Files On-Demand)", "Filer som bara finns i OneDrive-molnet (Files On-Demand)"), CLOUD, a["cloud"])
    res = choose_many([dict(label=l, value=v) for v, l in BROWSERS], set(a["browsers"]),
                      title=L("Browsers (bookmarks and passwords)", "Webbläsare (bokmärken och lösenord)"))
    if res is None:
        raise Cancel()
    a["browsers"] = res[0]
    a["bitlocker"] = ask_choice("BitLocker", BITLOCKER, a["bitlocker"])
    a["admin"] = confirm(L("Create a local admin account?", "Skapa ett lokalt admin-konto?"), a["admin"])
    if a["admin"]:
        a["admin_name"] = ask_text(L("Name of the admin account (the password is asked for when the script runs)", "Namn på admin-kontot (lösenordet frågas efter när skriptet körs)"),
                                   default=a.get("admin_name") or "lokaladmin", required=True, check=check_admin)
    a["domain"] = ask_choice(L("Domain and Entra ID", "Domän och Entra ID"), DOMAIN, a["domain"])
    if confirm(L("Fill in customer, responsible, computer etc.? (optional, goes in the checklist)", "Fylla i kund, ansvarig, dator m.m.? (valfritt, hamnar i checklistan)"),
               any(a.get(k) for k, _ in OPTIONAL_FIELDS)):
        for k, label in OPTIONAL_FIELDS:
            if k == "upn":
                a[k] = ask_upn(a)
                continue
            a[k] = ask_text(label + dim(L(" (optional, enter skips)", " (valfritt, enter hoppar över)")), default=a.get(k) or "")
    ask_steps(a)
    global DIRTY
    DIRTY = False                    # granskningen visar allt igen, ingen extra paus
    return a


def summary_lines(a):
    steps = final_steps(a)
    w = width()
    names = [FOLDER_BY_ID[f][1] for f in a["folders"]] + a["custom"]
    rows = [(L("User", "Användare"), f"{a['user']}  {dim(a['profile'])}"), (L("Target folder", "Målmapp"), malmapp_for(a)),
            (L("Folders", "Mappar"), ", ".join(names) or L("none", "inga")),
            (L("Browsers", "Webbläsare"), ", ".join(dict(BROWSERS)[b] for b in a["browsers"]) or L("none", "inga")),
            ("BitLocker", dict((v, l) for v, l, _ in BITLOCKER)[a["bitlocker"]]),
            (L("Admin account", "Admin-konto"), (a.get("admin_name") or "lokaladmin") if "admin" in steps else L("no", "nej")),
            (L("Domain/Entra", "Domän/Entra"), dict((v, l) for v, l, _ in DOMAIN)[a["domain"]])]
    rows += [(SHORT_LABEL[k], a[k]) for k, _ in OPTIONAL_FIELDS if a.get(k)]
    n_scr, n_man = len([s for s in steps if s in SCRIPT_STEPS]), len([s for s in steps if s.startswith("m_")])
    rows.append((L("Steps", "Steg"), L(f"{n_scr} in the script, {n_man} manual", f"{n_scr} i skriptet, {n_man} manuella")))
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
        rows = [dict(label=L("Save script and checklist", "Spara skript och checklista"), hint=f"~/Documents/Muppa/{name}", value="save", key="s"),
                dict(label=L("Show the script", "Visa skriptet"), hint=L(f"{len(ps.splitlines())} lines of PowerShell", f"{len(ps.splitlines())} rader PowerShell"), value="ps", key="v"),
                dict(label=L("Show the checklist", "Visa checklistan"), hint=L(f"{md.count('- [ ]')} items", f"{md.count('- [ ]')} punkter"), value="md", key="c"),
                dict(label=L("Change the answers…", "Ändra svaren…"), hint=L("the wizard again, with your answers filled in", "guiden igen, med dina svar ifyllda"), value="edit", key="a"),
                dict(label=L("Save as template…", "Spara som mall…"), hint=L("the choices without the user, no secrets", "valen utan användaren, inga hemligheter"), value="tpl", key="m"),
                dict(label="Avbryt", value="quit", key="q")]
        v = choose(rows, msg=L(f"Ready for {a['user']}. Have a look at the script before you save.", f"Klart för {a['user']}. Titta gärna på skriptet innan du sparar."), status=lambda: summary_lines(a),
                   start=last, screen=True, footer=L("↑/↓ · enter · digit/letter picks · q cancels", "↑/↓ · enter · siffra/bokstav väljer · q avbryter"),
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
            celebrate(L(f"Saved! Script and checklist for {a['user']} are in {OUT_DIR.name}.", f"Sparat! Skript och checklista för {a['user']} ligger i {OUT_DIR.name}."))
            print()
            out("   " + green("✔") + " " + str(p1))
            out("   " + green("✔") + " " + str(p2))
            print()
            out("   " + bold(L("Run on the computer being migrated", "Kör på datorn som migreras")) + dim(L(" (Windows PowerShell as administrator):", " (Windows PowerShell som administratör):")))
            out("     " + cyan(f"powershell -ExecutionPolicy Bypass -File .\\{p1.name} -Torrkorning"))
            out("     " + cyan(f"powershell -ExecutionPolicy Bypass -File .\\{p1.name}"))
            out("   " + dim(L("Then -Doman, -Entra and -Aterlas, as the checklist says.", "Sedan -Doman, -Entra och -Aterlas, som det står i checklistan.")))
            print()
            if not template_name and ITTY:
                if confirm(L("Save the choices as a template for next time?", "Spara valen som mall till nästa gång?"), False):
                    ask_save_template(a, None)
            return True


def ask_save_template(a, current):
    ts = templates()
    try:
        name = ask_text(L("Template name", "Mallens namn"), default=current or ("Standard" if "Standard" not in ts else ""), required=True)
    except Cancel:
        return
    if name in ts and name != current and not confirm(L(f"The template {name} exists. Overwrite?", f"Mallen {name} finns. Skriva över?"), False):
        return
    save_template(a, name)
    out("   " + green("✔") + L(f" Template {bold(name)} saved: ", f" Mallen {bold(name)} sparad: ") + dim(template_summary(templates()[name])))


def run_wizard(template=None):
    a = new_answers(template)
    try:
        wizard(a)
    except Cancel:
        out(dim(L("  Cancelled.", "  Avbrutet.")))
        return False
    return review(a, template)


# ───────────────────────────── menyer ─────────────────────────────
def templates_menu():
    while True:
        ts = templates()
        if not ts:
            say(L("No templates yet. Do a migration and choose Save as template, and the choices end up here.", "Inga mallar än. Gör en migrering och välj Spara som mall, så hamnar valen här."), "happy")
            return
        rows = [dict(label=n, hint=template_summary(t), value=n) for n, t in sorted(ts.items())]
        rows.append(dict(label=L("Back", "Tillbaka"), value="__back__", key="q"))
        v = choose(rows, msg=L("Pick a template to use or delete it.", "Välj en mall för att använda eller ta bort den."), screen=True, sub=f"muppa {VERSION}")
        if v in (None, "__back__"):
            return
        act = choose([dict(label=L("Use", "Använd"), hint=L("start the wizard with the template's choices", "starta guiden med mallens val"), value="use"),
                      dict(label=L("Delete", "Ta bort"), value="del"),
                      dict(label=L("Back", "Tillbaka"), value="back", key="q")], title=L(f"Template: {v}", f"Mall: {v}"))
        if act == "use":
            run_wizard(v)
            return
        if act == "del" and confirm(L(f"Delete the template {v}?", f"Ta bort mallen {v}?"), False):
            ts.pop(v, None)
            save_json(TEMPLATES, ts)


def greeting():
    if not templates():
        return L("Hi! I'm Muppa. I build a PowerShell script and a checklist for moving a computer "
                 "from an on-premises AD domain to Entra ID.",
                 "Hej! Jag är Muppa. Jag bygger ett PowerShell-skript och en checklista för att flytta en dator "
                 "från lokal AD-domän till Entra ID."), "happy"
    return L("Welcome back! Pick a template to be quick, or start from scratch.", "Välkommen tillbaka! Välj en mall så går det snabbt, eller börja från början."), "happy"


def main_menu():
    last = None
    while True:
        msg, mood = greeting()
        ts = templates()
        rows = [dict(label=L("New migration…", "Ny migrering…"), hint=L("the wizard: questions → script + checklist", "guiden: frågor → skript + checklista"), value="new", key="n")]
        if ts:
            rows.append(("head", L("From template", "Från mall")))
            rows += [dict(label=n, hint=template_summary(t), value="t:" + n) for n, t in sorted(ts.items())[:6]]
            rows.append(("head", ""))
        rows += [dict(label=L("Templates…", "Mallar…"), hint=L(f"{len(ts)} saved", f"{len(ts)} sparade") if ts else L("none yet", "inga än"), value="tpl", key="m"),
                 dict(label=L("Open the folder with scripts", "Öppna mappen med skript"), hint=str(OUT_DIR), value="open", key="o"),
                 dict(label=L("Language: English", "Språk: svenska"), hint=L("switch to Swedish (all the tools)", "byt till engelska (alla verktygen)"),
                      value="lang", key="s"),
                 dict(label=L("Quit", "Avsluta"), value="quit", key="q")]
        v = choose(rows, msg=msg, mood=mood, start=last or "new", screen=True,
                   footer=L("↑/↓ · enter · digit/letter picks · q quits", "↑/↓ · enter · siffra/bokstav väljer · q avslutar"), sub=f"muppa {VERSION}")
        last = v
        if v in (None, "quit"):
            print()
            say(random.choice([L("Bye!", "Hej då!"), L("See you!", "Vi ses!"), L("Good luck with the migration!", "Lycka till med migreringen!")]), "happy")
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
            elif v == "lang":
                set_lang("sv" if LANG == "en" else "en")
                sys.stdout.write("\033[H\033[2J")
                sys.stdout.flush()
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        except Cancel:
            out(dim(L("  Cancelled.", "  Avbrutet.")))


# ───────────────────────────── CLI ─────────────────────────────
def cmd_create(args):
    if args.mall and args.mall not in templates():
        print(L(f"  No template called {args.mall}. See: muppa templates", f"  Hittar ingen mall som heter {args.mall}. Se muppa mallar."), file=sys.stderr)
        return 1
    a = new_answers(args.mall)
    a["user"], a["profile"] = split_user(args.anvandare)
    if args.malmapp:
        a["malmapp"] = args.malmapp
    if args.mappar is not None:
        ids = [x.strip() for x in args.mappar.split(",") if x.strip()]
        bad = [x for x in ids if x not in FOLDER_BY_ID]
        if bad:
            print(L(f"  Unknown folders: {', '.join(bad)}. Choose from: {', '.join(FOLDER_BY_ID)}", f"  Okända mappar: {', '.join(bad)}. Välj bland: {', '.join(FOLDER_BY_ID)}"), file=sys.stderr)
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
        out(L("  No templates. Save one from the wizard (muppa new).", "  Inga mallar. Spara en från guiden (muppa ny)."))
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
    ap = argparse.ArgumentParser(prog="muppa", description=L("Muppa – migrate a computer from on-premises AD to Entra ID.", "Muppa – migrera en dator från lokal AD till Entra ID."))
    ap.add_argument("--version", action="version", version="muppa " + VERSION)
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("new", aliases=["ny"], help=L("the wizard", "guiden"))
    n.add_argument("--template", "--mall", dest="mall")
    s = sub.add_parser("create", aliases=["skapa"], help=L("create script + checklist without questions", "skapa skript + checklista utan frågor"))
    s.add_argument("anvandare", help=L("user name or profile folder", "användarnamn eller profilmapp"))
    s.add_argument("--template", "--mall", dest="mall")
    s.add_argument("--target", "--malmapp", dest="malmapp", help=L(r"default C:\temp\{användare}", r"standard C:\temp\{användare}"))
    s.add_argument("--folders", "--mappar", dest="mappar", help=L("e.g. skrivbord,dokument,bilder,hamtade (", "t.ex. skrivbord,dokument,bilder,hamtade (") + ",".join(FOLDER_BY_ID) + ")")
    s.add_argument("--path", "--egen", dest="egen", action="append", help=L("own path (can be repeated)", "egen sökväg (kan upprepas)"))
    s.add_argument("--browsers", "--webblasare", dest="webblasare", help=L("edge,chrome or empty", "edge,chrome eller tomt"))
    s.add_argument("--bitlocker", choices=[x[0] for x in BITLOCKER])
    s.add_argument("--domain", "--doman", dest="domain", choices=[x[0] for x in DOMAIN])
    s.add_argument("--cloud-files", "--molnfiler", dest="cloud", choices=[x[0] for x in CLOUD])
    s.add_argument("--admin", help=L("name of the local admin account", "namn på lokalt admin-konto"))
    s.add_argument("--no-admin", "--ingen-admin", dest="ingen_admin", action="store_true")
    en_flag = {"kund": "--customer", "ansvarig": "--responsible", "dator": "--computer", "tekniker": "--technician", "arende": "--ticket"}
    for k, label in OPTIONAL_FIELDS:
        s.add_argument(*([en_flag[k]] if k in en_flag else []), "--" + k, dest=k, help=label)
    sub.add_parser("templates", aliases=["mallar"], help=L("list saved templates", "lista sparade mallar"))
    sub.add_parser("hello", aliases=["hej"])
    lg = sub.add_parser("lang", aliases=["språk", "sprak"], help=L("show or set the language (en, sv) for the whole troupe",
                                                                   "visa eller välj språk (en, sv) för hela truppen"))
    lg.add_argument("value", nargs="?", choices=["en", "sv"])
    a = ap.parse_args(argv)
    a.cmd = {"ny": "new", "skapa": "create", "mallar": "templates", "hej": "hello", "språk": "lang", "sprak": "lang"}.get(a.cmd, a.cmd)
    if a.cmd == "lang":
        if a.value:
            set_lang(a.value)
        print(L("  Language: English · change with: muppa lang sv", "  Språk: svenska · byt med: muppa lang en"))
        return 0
    try:
        if a.cmd is None:
            if not ITTY or not TTY:
                print(L(__doc__, DOC_SV))
                return 0
            signal.signal(signal.SIGINT, signal.default_int_handler)
            main_menu()
            return 0
        if a.cmd == "new":
            if not ITTY:
                sys.exit(L("  the wizard needs a real terminal (or: muppa create USER)", "  guiden behöver en riktig terminal (eller: muppa skapa ANVÄNDARE)"))
            if a.mall and a.mall not in templates():
                say(L(f"No template called {a.mall}. See: muppa templates", f"Hittar ingen mall som heter {a.mall}. Se muppa mallar."), "sad")
                return 1
            return 0 if run_wizard(a.mall) else 1
        if a.cmd == "create":
            return cmd_create(a)
        if a.cmd == "templates":
            list_templates()
            return 0
        if a.cmd == "hello":
            print()
            say(random.choice(JOKES), random.choice(["happy", "proud", "wow", "left"]))
            print()
            return 0
    except Cancel:
        out(dim(L("  Cancelled.", "  Avbrutet.")))
        return 1
    except KeyboardInterrupt:
        sys.stdout.write("\033[?25h\n")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
