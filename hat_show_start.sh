#!/bin/bash
# Start the Pi hat show (hat_show.py) in the background, for FPP to run.
#
# Use it as a script entry in an FPP playlist: the show then stops by itself
# when FPP stops that playlist (Stop button, or the end of the playlist's
# schedule). If the show is already running it is left alone, so the entry
# can sit in the playlist's main section and repeat with it. Output goes to
# /home/fpp/media/logs/hat_show.log.

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/home/fpp/media/logs/hat_show.log
SHOW="python3.* .*hat_show\.py"  # same pattern as hat_show_stop.sh

# Writing straight into fppd's pixel buffers needs root.
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo -n"

pgrep -f "$SHOW" > /dev/null && exit 0  # already running
$SUDO nohup python3 -u "$DIR/hat_show.py" "$@" > "$LOG" 2>&1 &
