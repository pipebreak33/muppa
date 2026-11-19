#!/usr/bin/env sh
# Muppa för macOS och Linux – installera eller uppdatera (kör igen = senaste versionen):
#   curl -fsSL https://raw.githubusercontent.com/pipebreak33/muppa/main/install.sh | sh
# Från en lokal kopia:  ./install.sh
# Styr källan med MUPPA_REPO=ägare/repo och MUPPA_BRANCH=gren (eller en release-tagg).
# Inga frågor: exitkod 0 = installerat, annat = fel.
set -eu
# Language: TRUPPA_LANG, else ~/.truppa/config.json (shared by the whole troupe), else English
TL="${TRUPPA_LANG:-}"
if [ -z "$TL" ] && [ -f "$HOME/.truppa/config.json" ]; then
  TL="$(sed -n 's/.*"lang"[^"]*"\([a-z]*\)".*/\1/p' "$HOME/.truppa/config.json" 2>/dev/null | head -n 1)"
fi
t() { case "$TL" in sv*) printf '%s' "$2" ;; *) printf '%s' "$1" ;; esac; }
START="$(date +%s)"

REPO="${MUPPA_REPO:-pipebreak33/muppa}"
BRANCH="${MUPPA_BRANCH:-main}"
DIR="$HOME/.muppa"
APP="$DIR/app"
BIN="$HOME/.local/bin"

say()  { printf '  \033[38;5;177mM›\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✘\033[0m %s\n' "$1" >&2; exit 1; }

# 1. Python 3.8+ (Muppa är ett Python-skript utan beroenden)
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  case "$(uname -s)" in
    Darwin) die "$(t "Python 3.8+ is missing. Run: xcode-select --install   (or: brew install python)" "Python 3.8+ saknas. Kör: xcode-select --install   (eller: brew install python)")";;
    *)      die "$(t "Python 3.8+ is missing. Run: sudo apt install python3   (or the dnf/pacman equivalent)" "Python 3.8+ saknas. Kör: sudo apt install python3   (eller dnf/pacman motsvarande)")";;
  esac
fi

# 2. Källan: en lokal checkout bredvid skriptet, annars arkivet från GitHub
SRC=""
case "$0" in
  *install.sh)
    HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
    if [ -n "$HERE" ] && [ -f "$HERE/muppa.py" ] && [ "${MUPPA_FROM_WEB:-}" != "1" ]; then
      SRC="$HERE"
      say "$(t "Installing from $HERE" "Installerar från $HERE")"
    fi;;
esac
TMP=""
cleanup() { if [ -n "$TMP" ]; then rm -rf "$TMP"; fi; return 0; }
trap cleanup EXIT
if [ -z "$SRC" ]; then
  command -v curl >/dev/null 2>&1 || die "$(t "curl is missing." "curl saknas.")"
  command -v tar >/dev/null 2>&1 || die "$(t "tar is missing." "tar saknas.")"
  TMP="$(mktemp -d)"
  say "$(t "Fetching Muppa from github.com/$REPO ($BRANCH)" "Hämtar Muppa från github.com/$REPO ($BRANCH)")"
  curl -fsSL "https://github.com/$REPO/archive/$BRANCH.tar.gz" -o "$TMP/muppa.tar.gz" \
    || die "$(t "the download failed: check the network or MUPPA_REPO/MUPPA_BRANCH" "nedladdningen misslyckades – kolla nätet eller MUPPA_REPO/MUPPA_BRANCH")"
  tar -xzf "$TMP/muppa.tar.gz" -C "$TMP" || die "$(t "could not unpack the archive" "kunde inte packa upp arkivet")"
  SRC="$(find "$TMP" -maxdepth 2 -name muppa.py -exec dirname {} \; | head -n 1)"
  [ -n "$SRC" ] || die "$(t "the archive does not contain Muppa" "arkivet innehåller inte Muppa")"
fi
"$PY" -c 'import ast,sys; ast.parse(open(sys.argv[1],encoding="utf-8").read())' "$SRC/muppa.py" \
  || die "$(t "muppa.py is broken" "muppa.py är trasig")"

# 3. Byt ut programmet (~/.muppa/app). Dina mallar i ~/.muppa rörs inte.
mkdir -p "$DIR" "$BIN"
chmod 700 "$DIR" 2>/dev/null || true
rm -rf "$APP.new"
mkdir -p "$APP.new"
cp "$SRC/muppa.py" "$APP.new/muppa.py"
for f in README.md LICENSE; do [ -f "$SRC/$f" ] && cp "$SRC/$f" "$APP.new/$f"; done
rm -rf "$APP"
mv "$APP.new" "$APP"

# 4. Startkommando i ~/.local/bin (en ev. gammal symlänk tas bort först – skriv aldrig genom den)
rm -f "$BIN/muppa"
cat > "$BIN/muppa" <<SHIM
#!/usr/bin/env sh
exec "$PY" "$APP/muppa.py" "\$@"
SHIM
chmod +x "$BIN/muppa"

# 5. ~/.local/bin i PATH
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    case "${SHELL:-}" in
      */zsh)  RC="$HOME/.zshrc" ;;
      */bash) RC="$HOME/.bashrc"; [ "$(uname -s)" = Darwin ] && RC="$HOME/.bash_profile" ;;
      *)      RC="$HOME/.profile" ;;
    esac
    grep -qs '.local/bin' "$RC" 2>/dev/null || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC"
    say "$(t "Added ~/.local/bin to PATH ($RC). Open a new terminal." "La till ~/.local/bin i PATH ($RC). Öppna en ny terminal.")"
    ;;
esac

VER="$("$PY" "$APP/muppa.py" --version 2>/dev/null || echo muppa)"
SECS=$(( $(date +%s) - START )); if [ "$SECS" -ge 60 ]; then TOOK="$((SECS / 60)) min $((SECS % 60)) s"; else TOOK="$SECS s"; fi
printf "$(t '\n  \033[38;5;42m✔\033[0m %s installed in %s. Run: \033[1mmuppa\033[0m\n' '\n  \033[38;5;42m✔\033[0m %s installerad på %s. Kör: \033[1mmuppa\033[0m\n')" "$VER" "$TOOK"
printf "$(t '  \033[2mUninstall: curl -fsSL https://raw.githubusercontent.com/pipebreak33/muppa/main/uninstall.sh | sh\033[0m\n\n' '  \033[2mAvinstallera: curl -fsSL https://raw.githubusercontent.com/pipebreak33/muppa/main/uninstall.sh | sh\033[0m\n\n')"
