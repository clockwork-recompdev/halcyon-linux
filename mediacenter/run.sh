#!/bin/sh
# Development: run Halcyon Media Center from this checkout (--window for a window).
here=$(dirname "$(readlink -f "$0")")/..
export PYTHONPATH="$here/mediacenter:$here/player:$here/games:$here/games/.devlib"
exec /usr/bin/python3 -m halcyon_mediacenter.app "$@"
