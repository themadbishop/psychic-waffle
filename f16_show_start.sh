#!/bin/bash
# Start the F16v4 show (f16_show.py) in the background, for FPP to run.
#
# Use it as a script entry in an FPP playlist: the show then stops by itself
# when FPP stops that playlist (Stop button, or the end of the playlist's
# schedule). If the show is already running it is left alone, so the entry
# can sit in the playlist's main section and repeat with it. Output goes to
# /home/fpp/media/logs/f16_show.log.

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/home/fpp/media/logs/f16_show.log
SHOW="python3.* .*f16_show\.py"  # same pattern as f16_show_stop.sh

# Writing straight into fppd's pixel buffers needs root.
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo -n"

pgrep -f "$SHOW" > /dev/null && exit 0  # already running
$SUDO nohup python3 -u "$DIR/f16_show.py" "$@" > "$LOG" 2>&1 &
