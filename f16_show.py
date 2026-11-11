#!/usr/bin/env python3
"""Run f16_show.json on the FPP server that drives the Falcon F16v4 pixel controller.

Runs on the FPP server (192.168.86.110) and lights the show like
run_show.py: one effect per Pixel Overlay Model. Before starting it:
  1. reads the string ports set up on the F16v4 and makes FPP's overlay
     models match: one model per port with pixels, named Port1, Port2, ...
     (Port3-1, Port3-2, ... for a port with several strings). If they had to
     change, fppd is restarted to load them. Effects in the show file are
     assigned to these names, so each port gets its own effect.
  2. checks that FPP has an enabled E1.31/DDP output to the controller
     covering every show model's channels; that output is what carries the
     pixels to the F16v4.

Started from an FPP playlist (f16_show_start.sh as a playlist script entry),
the show follows that playlist: when FPP stops it, from the Stop button or
the end of its schedule, the show stops too. f16_show_stop.sh, or a
SIGTERM, stops it at any time.

Press Ctrl+C to stop; every model the show used is turned off and handed
back to FPP.
"""

import argparse
import os
import re
import signal
import socket
import sys
import threading
import time

import falcon
from fpp_overlay import FPPError, list_models, request
from run_show import load_show, print_effects, print_models, run

FPP_HOST = "localhost"  # this script runs on the FPP server itself (192.168.86.110)
CONTROLLER = "192.168.0.11"  # Falcon F16v4
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "f16_show.json")
PORT_MODEL = re.compile(r"Port\d+(-\d+)?$")  # names of the models made from the controller's ports
RESTART_TIMEOUT = 60  # seconds to wait for fppd to come back with the new models
PLAYLIST_POLL = 2  # seconds between checks that the playlist that started the show is still playing


def read_ports(controller):
    """The controller's strings, warning about any that can't be used or that overlap."""
    found, problems = falcon.ports(controller)
    for problem in problems:
        print(f"Warning: skipping {problem}.", file=sys.stderr)
    for a, b in falcon.overlaps(found):
        print(f"Warning: {falcon.model_name(a, found)} and {falcon.model_name(b, found)} use some of the "
              f"same channels on the controller, so they'll show the same pixels.", file=sys.stderr)
    return found


def port_models(found):
    """FPP overlay model settings for each of the controller's strings."""
    return [{
        "Name": falcon.model_name(p, found),
        "Type": "Channel",
        "StartChannel": p.start_channel,
        "ChannelCount": p.pixels * falcon.CHANNELS_PER_PIXEL,
        "ChannelCountPerNode": falcon.CHANNELS_PER_PIXEL,
        "Orientation": "horizontal",
        "StartCorner": "TL",
        "StringCount": 1,
        "StrandsPerString": 1,
    } for p in found]


def saved_models(host):
    """FPP's overlay model settings (Input/Output Setup > Pixel Overlay Models)."""
    return request("GET", f"http://{host}/api/models") or []


def same_channels(model, wanted):
    return (model.get("StartChannel"), model.get("ChannelCount")) == (wanted["StartChannel"], wanted["ChannelCount"])


def print_ports(host, controller):
    """List the controller's ports and whether FPP has a matching model for each."""
    found = read_ports(controller)
    models = {m.get("Name"): m for m in saved_models(host)}
    print(f"String ports on {controller}:")
    for p, wanted in zip(found, port_models(found)):
        have = models.get(wanted["Name"])
        status = "model in FPP" if have and same_channels(have, wanted) else \
            "FPP model has different channels" if have else "no FPP model yet"
        last = wanted["StartChannel"] + wanted["ChannelCount"] - 1
        print(f"  {wanted['Name']}: {p.pixels} pixels, channels {wanted['StartChannel']}-{last}  ({status})")


def sync_port_models(host, controller, apply=True):
    """Make FPP's Port models match the controller's ports, restarting fppd if anything changed.

    Models you made yourself (any other name) are kept; Port models for
    ports that no longer have pixels are removed. With apply=False only
    warns about differences (restarting fppd would stop a playing playlist).
    """
    found = read_ports(controller)
    if not found:
        sys.exit(f"The pixel controller at {controller} has no string ports with pixels.")
    wanted = port_models(found)
    names = {w["Name"] for w in wanted}
    current = saved_models(host)
    by_name = {m.get("Name"): m for m in current}
    stale = [m["Name"] for m in current if PORT_MODEL.match(m.get("Name", "")) and m["Name"] not in names]
    if not stale and all(w["Name"] in by_name and same_channels(by_name[w["Name"]], w) for w in wanted):
        print(f"FPP's overlay models match the {len(wanted)} port(s) on {controller}.")
        return
    if not apply:
        print(f"Warning: FPP's Port models don't match the ports on {controller}; not updating them "
              f"while a playlist is playing. Run f16_show.py --ports, then f16_show.py once from a "
              f"shell with no playlist playing, to update them.", file=sys.stderr)
        return

    # FPP rebuilds the models it auto-creates from its own outputs, so only save the user's.
    keep = [m for m in current if not m.get("autoCreated") and not PORT_MODEL.match(m.get("Name", ""))]
    body = {"models": keep + wanted}
    settings = request("GET", f"http://{host}/api/overlays/settings") or {}
    if isinstance(settings, dict) and "autoCreate" in settings:
        body["autoCreate"] = settings["autoCreate"]
    resp = request("POST", f"http://{host}/api/models", body)
    if not (isinstance(resp, dict) and resp.get("Status") == "OK"):
        raise FPPError(f"FPP didn't save the overlay models: {resp!r}")
    print(f"Saved {len(wanted)} port model(s) from {controller} to FPP"
          + (f" (removed {', '.join(stale)})" if stale else "") + "; restarting fppd to load them...")

    try:
        request("GET", f"http://{host}/api/system/fppd/restart", timeout=RESTART_TIMEOUT)
    except FPPError:
        pass  # the reply can be slow or lost while fppd restarts; the check below decides
    deadline = time.monotonic() + RESTART_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            loaded = {m.get("Name") for m in list_models(host)}
        except FPPError:
            continue  # fppd is still starting
        if names <= loaded:
            print("fppd restarted with the port models.")
            return
    sys.exit(f"fppd didn't come back with the port models within {RESTART_TIMEOUT}s; "
             f"check FPP's status page, then run this again.")


def playing_playlist(host):
    """Name of the playlist FPP is playing, or None."""
    status = request("GET", f"http://{host}/api/fppd/status") or {}
    if status.get("status_name") != "playing":
        return None
    return (status.get("current_playlist") or {}).get("playlist") or None


def follow_playlist(host, playlist):
    """Stop the show, as f16_show_stop.sh would, once FPP is no longer playing `playlist`.

    Two misses in a row are needed, so a brief hiccup reading FPP's status
    doesn't end the show.
    """
    misses = 0
    while True:
        time.sleep(PLAYLIST_POLL)
        try:
            misses = 0 if playing_playlist(host) == playlist else misses + 1
        except FPPError:
            continue
        if misses >= 2:
            print(f"FPP stopped playlist '{playlist}'; stopping the show.", flush=True)
            # SIGTERM, not SIGINT: a program started in the background by a script
            # (as f16_show_start.sh does) has SIGINT ignored.
            os.kill(os.getpid(), signal.SIGTERM)
            return


def stop_on_sigterm(signum, frame):
    raise KeyboardInterrupt  # same clean shutdown as Ctrl+C


def controller_outputs(host, controller):
    """Return [(first, last)] channel ranges (1-based) FPP sends to `controller`.

    Reads FPP's E1.31/DDP output setup (Input/Output Setup > Channel Outputs >
    E1.31/ArtNet/DDP). Universes that are disabled, or whose output block is
    disabled, are left out.
    """
    config = request("GET", f"http://{host}/api/channel/output/universeOutputs") or {}
    ranges = []
    for block in config.get("channelOutputs", []):
        if not block.get("enabled", 1):
            continue
        for u in block.get("universes", []):
            if not u.get("active", 1) or u.get("address") != controller:
                continue
            # One entry can stand for several consecutive universes of channelCount each.
            start = u.get("startChannel", 0)
            count = u.get("channelCount", 0) * u.get("universeCount", 1)
            if start > 0 and count > 0:
                ranges.append((start, start + count - 1))
    return ranges


def uncovered(first, last, ranges):
    """Number of channels in first..last that no range covers."""
    return sum(1 for ch in range(first, last + 1) if not any(a <= ch <= b for a, b in ranges))


def check_controller(host, controller, model_names):
    """Exit if FPP isn't sending to the controller; warn about anything else that looks wrong."""
    try:
        ranges = controller_outputs(host, controller)
    except FPPError as e:
        print(f"Warning: could not read FPP's channel outputs ({e}); not checking the "
              f"output to {controller}.", file=sys.stderr)
        return
    if not ranges:
        sys.exit(f"FPP has no enabled E1.31/DDP output to the pixel controller at {controller}.\n"
                 f"Add one under Input/Output Setup > Channel Outputs > E1.31/ArtNet/DDP, "
                 f"covering every overlay model's channels, then restart fppd.")
    print(f"FPP sends channels {', '.join(f'{a}-{b}' for a, b in sorted(ranges))} to {controller}.")

    for m in list_models(host):
        name = m.get("Name")
        if name not in model_names:
            continue
        first = m.get("StartChannel", 0)
        count = m.get("ChannelCount") or m.get("width", 0) * m.get("height", 0) * m.get("ChannelCountPerNode", 3)
        if first > 0 and count > 0:
            missing = uncovered(first, first + count - 1, ranges)
            if missing:
                print(f"Warning: {missing} of '{name}''s channels ({first}-{first + count - 1}) "
                      f"aren't sent to {controller}; those pixels won't light.", file=sys.stderr)

    # The F16v4 serves its setup page on port 80; if that doesn't answer, nothing
    # FPP sends it will light either.
    try:
        socket.create_connection((controller, 80), timeout=2).close()
    except OSError as e:
        print(f"Warning: the pixel controller at {controller} isn't answering ({e}); "
              f"check its power and network cable.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", nargs="?", default=DEFAULT_CONFIG, help="show file (default: f16_show.json next to this script)")
    parser.add_argument("--host", default=FPP_HOST, help=f"FPP host (default: {FPP_HOST})")
    parser.add_argument("--controller", default=CONTROLLER, help=f"pixel controller's IP address (default: {CONTROLLER})")
    parser.add_argument("--list", action="store_true", help="list FPP's overlay models and exit")
    parser.add_argument("--ports", action="store_true",
                        help="list the controller's string ports and their FPP models, and exit (changes nothing)")
    parser.add_argument("--no-sync", action="store_true",
                        help="don't create or update FPP's Port models from the controller's ports")
    parser.add_argument("--effects", nargs="?", const="", metavar="NAME",
                        help="list FPP's built-in (WLED) effects, or one effect's settings, and exit")
    args = parser.parse_args()

    try:
        if args.list:
            print_models(args.host)
            return
        if args.effects is not None:
            try:
                print_effects(args.host, args.effects)
            except ValueError as e:
                sys.exit(str(e))
            return
        if args.ports:
            print_ports(args.host, args.controller)
            return
        playlist = playing_playlist(args.host)
        if not args.no_sync:
            sync_port_models(args.host, args.controller, apply=playlist is None)
        show = load_show(args.config, args.host)
        check_controller(args.host, args.controller, {model.name for model, _ in show})
    except FPPError as e:
        sys.exit(str(e))

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    signal.signal(signal.SIGINT, signal.default_int_handler)  # Ctrl+C, even if started with SIGINT ignored
    if playlist:
        threading.Thread(target=follow_playlist, args=(args.host, playlist), daemon=True).start()
        print(f"Running show on the pixel controller at {args.controller} until FPP stops "
              f"playlist '{playlist}':", flush=True)
    else:
        print(f"Running show on the pixel controller at {args.controller} (Ctrl+C to stop):", flush=True)
    try:
        run(show)
    except KeyboardInterrupt:
        pass
    finally:
        # Don't let a second stop request cut the clean-up short.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        for model, effect in show:
            try:
                effect.stop()
                model.clear()
                model.disable()
            except Exception as e:  # keep going so the other models still get turned off
                print(f"Could not turn off {model.name}: {e}", file=sys.stderr)
            model.close()
        print("\nStopped; show models turned off.")


if __name__ == "__main__":
    main()
