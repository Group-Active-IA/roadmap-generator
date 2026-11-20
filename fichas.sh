#!/bin/sh
# Lanzador POSIX del verificador de fichas: elige un Python >= 3.8.
case "$0" in
  */*) dir=${0%/*} ;;
  *) dir=. ;;
esac
dir=$(CDPATH= cd -- "$dir" && pwd)
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 &&
    "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    exec "$c" -B "$dir/fichas.py" "$@"
  fi
done
printf '%s\n' '{"ok": false, "errors": ["falta Python 3.8 o superior para verificar las fichas"]}'
exit 3
