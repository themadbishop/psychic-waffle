#!/usr/bin/env python3
"""Test pattern: the first N pixels show one random color, changing every few seconds.

All other pixels on the string stay off. Run this on the Pi with FPP running
and no sequence playing. Press Ctrl+C to stop; the string is turned off on exit.
"""

import argparse
import colorsys
import random
import sys
import time

from fpp_overlay import FPPError, OverlayModel


def random_color(brightness):
    """A random, fully saturated color so every change is clearly visible."""
    r, g, b = colorsys.hsv_to_rgb(random.random(), 1.0, brightness)
    return round(r * 255), round(g * 255), round(b * 255)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost", help="FPP host (default: localhost)")
    parser.add_argument("--model", default="Mantle", help="Pixel Overlay Model name (default: Left Tree String)")
    parser.add_argument("--count", type=int, default=20, help="number of pixels to light (default: 20)")
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between colors (default: 3)")
    parser.add_argument("--brightness", type=float, default=1.0, help="0.0-1.0 (default: 1.0)")
    args = parser.parse_args()

    model = OverlayModel(args.model, host=args.host)
    try:
        model.load_geometry()
    except FPPError as e:
        sys.exit(f"{e}\nCreate it under Input/Output Setup > Pixel Overlay Models (see README), "
                 f"or pass --model <name> to use an existing one.")

    if args.count > model.pixel_count:
        sys.exit(f"Model '{args.model}' has only {model.pixel_count} pixels "
                 f"({model.width}x{model.height}); cannot light {args.count}.")

    model.clear()  # all pixels off
    model.enable()
    print(f"Lighting pixels 1-{args.count} of '{args.model}', new color every {args.interval}s. Ctrl+C to stop.")

    try:
        while True:
            color = random_color(args.brightness)
            for i in range(args.count):
                model.set_index(i, color)
            print(f"color RGB{color}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        model.clear()
        model.disable()
        print("\nStopped; string turned off.")


if __name__ == "__main__":
    main()
