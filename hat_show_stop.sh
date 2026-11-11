#!/bin/bash
# Stop the Pi hat show (hat_show.py) if it is running, and wait for it to
# turn its ports off and hand them back to FPP.
#
# Use it from FPP as a "Run Script" command, e.g. a scheduled command at the
# end of the show, or a script entry in a playlist's lead-out.

SHOW="python3.* .*hat_show\.py"  # also catches a show started by hand

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo -n"

pgrep -f "$SHOW" > /dev/null || exit 0
$SUDO pkill -TERM -f "$SHOW"
for _ in $(seq 20); do  # up to 10 s
    pgrep -f "$SHOW" > /dev/null || exit 0
    sleep 0.5
done
echo "hat_show.py didn't stop within 10 s; forcing it." >&2
$SUDO pkill -KILL -f "$SHOW"
