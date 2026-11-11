#!/usr/bin/env python3
"""Run hat_show.json on an FPP server whose pixels hang off a Pi hat.

Works like f16_show.py, but the ports come from FPP's own Pi hat output
(Input/Output Setup > Pi Pixel Strings) instead of a Falcon controller.
Before starting it reads those ports and makes FPP's overlay models match:
one model per port with pixels, named HatPort1, HatPort2, ... (HatPort3-1,
HatPort3-2, ... for a port with several strings). If they had to change,
fppd is restarted to load them. Effects in the show file are assigned to
these names. Only HatPort models are managed, so f16_show.py's Port models
on the same server are left alone.

Started from an FPP playlist (hat_show_start.sh as a playlist script entry),
the show follows that playlist: when FPP stops it, from the Stop button or
the end of its schedule, the show stops too. hat_show_stop.sh, or a
SIGTERM, stops it at any time.

Press Ctrl+C to stop; every model the show used is turned off and handed
back to FPP.
"""

import argparse
import os
import sys

import falcon
from f16_show import FPP_HOST, playing_playlist, print_ports, run_until_stopped, sync_port_models, warn_overlaps
from fpp_overlay import FPPError, request
from run_show import load_show, print_effects, print_models

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hat_show.json")
PREFIX = "HatPort"
SOURCE = "the Pi hat"


def hat_ports(host):
    """The Pi hat's strings with pixels, as falcon.Port tuples in port order.

    Reads FPP's pixel string outputs; disabled outputs are left out.
    """
    config = request("GET", f"http://{host}/api/channel/output/co-pixelStrings") or {}
    if not isinstance(config, dict):
        raise FPPError(f"Unexpected reply reading FPP's Pi hat outputs: {config!r}")
    found = []
    for block in config.get("channelOutputs", []):
        if not block.get("enabled", 1):
            continue
        for output in block.get("outputs", []):
            number = output.get("portNumber", 0) + 1
            for string, s in enumerate(output.get("virtualStrings", []), 1):
                if not s.get("pixelCount"):
                    continue
                order = s.get("colorOrder", "RGB")
                if len(order) != falcon.CHANNELS_PER_PIXEL:
                    print(f"Warning: skipping port {number}: its {order} pixels use {len(order)} channels "
                          f"each; only {falcon.CHANNELS_PER_PIXEL}-channel pixels are supported.", file=sys.stderr)
                    continue
                found.append(falcon.Port(number, string, s["pixelCount"], s.get("startChannel", 0) + 1))
    if not found and not any(b.get("enabled", 1) for b in config.get("channelOutputs", [])):
        sys.exit("FPP has no enabled Pi hat output; set one up under Input/Output Setup > Pi Pixel Strings.")
    found.sort(key=lambda p: (p.number, p.string))
    warn_overlaps(found, PREFIX)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", nargs="?", default=DEFAULT_CONFIG, help="show file (default: hat_show.json next to this script)")
    parser.add_argument("--host", default=FPP_HOST, help=f"FPP host (default: {FPP_HOST})")
    parser.add_argument("--list", action="store_true", help="list FPP's overlay models and exit")
    parser.add_argument("--ports", action="store_true",
                        help="list the Pi hat's ports and their FPP models, and exit (changes nothing)")
    parser.add_argument("--no-sync", action="store_true",
                        help="don't create or update FPP's HatPort models from the Pi hat's ports")
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
            print_ports(args.host, hat_ports(args.host), SOURCE, PREFIX)
            return
        playlist = playing_playlist(args.host)
        if not args.no_sync:
            sync_port_models(args.host, hat_ports(args.host), SOURCE, PREFIX, apply=playlist is None)
        show = load_show(args.config, args.host)
    except FPPError as e:
        sys.exit(str(e))

    run_until_stopped(args.host, show, playlist, SOURCE)


if __name__ == "__main__":
    main()
