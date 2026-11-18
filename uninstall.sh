#!/usr/bin/env sh
# Avinstallera Muppa på macOS och Linux:
#   curl -fsSL https://raw.githubusercontent.com/pipebreak33/muppa/main/uninstall.sh | sh
# Från en lokal kopia:  ./uninstall.sh
#
# Tar bort programmet (~/.muppa/app) och kommandot ~/.local/bin/muppa.
# Behåller dina mallar (~/.muppa) och skapade skript i ~/Documents/Muppa.
# MUPPA_PURGE=1 tar bort även ~/.muppa med dina data. MUPPA_YES=1 hoppar över frågan.
set -eu
START="$(date +%s)"

DIR="$HOME/.muppa"
SHIM="$HOME/.local/bin/muppa"

say()  { printf '  \033[38;5;177mM›\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✘\033[0m %s\n' "$1" >&2; exit 1; }

ask() {   # ask "Fråga" -> 0 om ja. Läser från terminalen även när skriptet kommer via curl | sh
  if [ -r /dev/tty ]; then
    printf '  %s [j/N] ' "$1"
    { read -r SVAR < /dev/tty; } 2>/dev/null || SVAR=""
    case "$SVAR" in j|J|ja|Ja|y|Y|yes) return 0 ;; esac
    return 1
  fi
  die "Ingen terminal att fråga i. Kör med MUPPA_YES=1 för att avinstallera utan fråga."
}

FOUND=""
TARGETS=""
if [ "${MUPPA_PURGE:-}" = "1" ]; then
  [ -e "$DIR" ] && { FOUND="$FOUND\n    $DIR (med dina data)"; TARGETS="$TARGETS $DIR"; }
else
  for p in app; do
    [ -e "$DIR/$p" ] && { FOUND="$FOUND\n    $DIR/$p"; TARGETS="$TARGETS $DIR/$p"; }
  done
fi
# startkommandot: bara om det är vårt (pekar på ~/.muppa)
if [ -e "$SHIM" ] && grep -qsF "/.muppa/" "$SHIM"; then FOUND="$FOUND\n    $SHIM"; TARGETS="$TARGETS $SHIM"; fi
if [ -z "$FOUND" ]; then
  say "Muppa är inte installerad här. Inget att ta bort."
  exit 0
fi

printf '\n  Det här tas bort:'
printf "$FOUND\n"
[ "${MUPPA_PURGE:-}" = "1" ] || printf '  Behålls: dina mallar (~/.muppa) och skapade skript i ~/Documents/Muppa.\n'
printf '\n'
if [ "${MUPPA_YES:-}" != "1" ]; then
  ask "Avinstallera Muppa?" || { say "Avbrutet. Ingenting har tagits bort."; exit 0; }
fi

for t in $TARGETS; do rm -rf "$t"; done
# tom ~/.muppa: städa bort mappen också
rmdir "$DIR" 2>/dev/null || true

SECS=$(( $(date +%s) - START ))
printf '\n  \033[38;5;42m✔\033[0m Muppa är avinstallerad%s (%s s).\n\n' "" "$SECS"
