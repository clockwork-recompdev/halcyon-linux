#!/bin/sh
# Development: run the gadget host from this checkout with the built-in gadgets.
# `./run.sh quit` stops it. HALCYON_GADGETS_DEBUG=1 prints gadget console output.
here=$(dirname "$(readlink -f "$0")")
export PYTHONPATH="$here" HALCYON_GADGETS_DIR="$here/builtin"
exec /usr/bin/python3 -m halcyon_gadgets.host "$@"
