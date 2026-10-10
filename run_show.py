#!/usr/bin/env python3
"""Run the lighting described in show.json: one effect per Pixel Overlay Model.

A "default" entry applies to every model not listed under "models"; without
it, unlisted models are left alone. Press Ctrl+C to stop; every
model the show used is turned off and handed back to FPP.
"""

import argparse
import json
import os
import sys
import time

import effects
from fpp_overlay import FPPError, OverlayModel, effect_definition, list_effects, list_models

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "show.json")


def print_models(host):
    for m in list_models(host):
        w, h = m.get("width", 0), m.get("height", 0)
        print(f"  {m.get('Name', '?')!r}: {w * h} pixels ({w}x{h}), start channel {m.get('StartChannel', '?')}")


def print_effects(host, name):
    """List FPP's built-in effects, or the settings of one of them."""
    if not name:
        names = list_effects(host)
        print("FPP effects (use with \"effect\": \"wled\", \"name\": ...; WLED ones without the prefix):")
        for n in names:
            if n != "Stop Effects":
                print(f"  {effects.short_effect_name(n)}")
        return
    model = OverlayModel("", host=host)
    full = effects.Wled._resolve(model, name)
    print(f"{full} settings (show.json name: default [allowed values]):")
    for arg in effect_definition(full, host).get("args", []):
        if arg.get("contents"):
            options = arg["contents"]
            allowed = ", ".join(options[:8]) + (f", ... ({len(options)} total)" if len(options) > 8 else "")
        elif arg.get("type") in ("range", "int"):
            allowed = f"{arg.get('min')}-{arg.get('max')}"
        else:
            allowed = arg.get("type", "")
        default = arg.get("default", arg["contents"][0] if arg.get("contents") else "(required)")
        print(f"  \"{arg['name']}\": {default}  [{allowed}]")


def load_show(path, host):
    """Return [(model, effect)], or exit listing every problem in the config."""
    try:
        with open(path) as f:
            config = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Show file not found: {path}\n"
                 f"Copy show.json into the same folder as run_show.py, or pass a show file's path.")
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"Cannot read {path}: {e}")

    on_controller = [m.get("Name") for m in list_models(host)]
    available = set(on_controller)
    specs = dict(config.get("models", {}))
    # "default" applies to every model on the controller not listed under "models".
    if "default" in config:
        for name in on_controller:
            specs.setdefault(name, config["default"])

    # The controller may have more or fewer models than the show file mentions:
    # listed models that aren't there are skipped with a warning, not an error.
    show, errors, skipped = [], [], []
    for name, spec in specs.items():
        if name not in available:
            skipped.append(f"'{name}': not on the controller")
            continue
        # "pixels" is for the model (its real string length), not the effect.
        spec = dict(spec)
        model = OverlayModel(name, host=host, pixels=spec.pop("pixels", None))
        try:
            model.load_geometry()
            if model.pixel_count == 0:
                skipped.append(f"'{name}': has no pixels")
                continue
            show.append((model, effects.create(model, spec)))
        except ValueError as e:
            errors.append(f"'{name}': {e}")

    if skipped:
        print("Skipping:", *(f"  {s}" for s in skipped), sep="\n", file=sys.stderr)
    if errors:
        print(f"Problems in {path}:", *(f"  {e}" for e in errors), sep="\n", file=sys.stderr)
    if errors or not show:
        if not on_controller:
            print("The controller has no Pixel Overlay Models; create them under "
                  "Input/Output Setup > Pixel Overlay Models.", file=sys.stderr)
        elif not errors:
            print(f"No models to light: add \"models\" or \"default\" to {path}, "
                  f"using models on the controller.", file=sys.stderr)
        print(f"Models on the controller ({len(on_controller)}):", file=sys.stderr)
        print_models(host)
        sys.exit(1)
    return show


def run(show):
    due = {}
    for model, effect in show:
        model.clear()
        model.enable()
        mode = model.setup_frames()
        due[effect] = time.monotonic()
        print(f"  {model.name}: {effect} ({model.pixel_count} pixels, {mode} updates)")
    print(f"{len(show)} model(s) running.")

    while True:
        now = time.monotonic()
        for model, effect in show:
            if effect in due and due[effect] <= now:
                delay = effect.update()
                if delay is None:
                    del due[effect]
                else:
                    due[effect] = now + delay
        # Static effects are done; keep running so the overlays stay on until Ctrl+C.
        time.sleep(max(0.0, min(due.values(), default=now + 1) - time.monotonic()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default=DEFAULT_CONFIG, help="show file (default: show.json next to this script)")
    parser.add_argument("--host", default="localhost", help="FPP host (default: localhost)")
    parser.add_argument("--list", action="store_true", help="list the controller's overlay models and exit")
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
        show = load_show(args.config, args.host)
    except FPPError as e:
        sys.exit(str(e))

    print("Running show (Ctrl+C to stop):")
    try:
        run(show)
    except KeyboardInterrupt:
        pass
    finally:
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
