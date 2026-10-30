#!/bin/sh
# Development: run Halcyon Messenger from this checkout.
# HALCYON_MESSENGER_DEBUG=1 prints bridge events and Discord warnings.
here=$(dirname "$(readlink -f "$0")")
export PYTHONPATH="$here"
exec /usr/bin/python3 -m halcyon_messenger.app "$@"
