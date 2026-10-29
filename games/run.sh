#!/bin/sh
# Development: run the launcher (or `./run.sh osk show`) from this checkout,
# using the media player's venv for PySide6 and the system python-evdev.
here=$(dirname "$(readlink -f "$0")")
py=$(readlink -f "$here/../player/.venv")/bin/python
mkdir -p "$here/.devlib"
evdev=$(/usr/bin/python3 -I -c 'import evdev, os; print(os.path.dirname(evdev.__file__))' 2>/dev/null) &&
    ln -sfn "$evdev" "$here/.devlib/evdev"
export PYTHONPATH="$here:$here/.devlib"
if [ "$1" = osk ]; then shift; exec "$py" -m halcyon_games.osk "$@"; fi
exec "$py" -m halcyon_games.app "$@"
