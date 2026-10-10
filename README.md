# psychic-waffle

Python code for an LED display driven by [FPP (Falcon Player)](https://github.com/FalconChristmas/fpp)
on a Raspberry Pi with an M8-PI pixel hat.

The scripts don't drive the GPIO pins themselves. FPP owns the hat's outputs; the
scripts set pixel colors through FPP's **Pixel Overlay Model** REST API.

## Hardware

| Item | Value |
|------|-------|
| Controller | Raspberry Pi running FPP |
| Hat | M8-PI |
| Ports | one Pixel Overlay Model per port in use (e.g. `Left Tree String`, `Mantle`); any number is supported |

## One-time FPP setup (in the FPP web UI)

1. **Output:** *Input/Output Setup > Pi Pixel Strings* (or *Channel Outputs*): enable the
   string output, choose the M8-PI board, and set each port's start channel, pixel count,
   and color order (often `RGB` or `GRB`). Save, then restart FPPD.
2. **Overlay models:** *Input/Output Setup > Pixel Overlay Models*: one model per port, with
   the same start channel as the port and Channel Count = pixels × 3. Orientation can be
   horizontal or vertical; the scripts count pixels along the string (0, 1, 2, ...) either way.

## Installing on the Pi

Copy the `.py` files and `show.json` to the Pi (the default FPP user is `fpp`):

```bash
scp *.py show.json fpp@flourine.local:/home/fpp/media/scripts/
```

Only the Python 3 standard library is used, so there is nothing else to install.

## Scripts

### `run_show.py`: different lighting per model

Reads `show.json` (next to the script) and runs one effect on each model listed there,
all at the same time. A `"default"` entry applies to every model on the controller that
isn't listed under `"models"`; without it, unlisted models are left alone. Models are
read from the controller each time the show starts, so adding or removing models in FPP
needs no code changes. A model named in the file but missing from the controller (or with
no pixels) is skipped with a warning. Ctrl+C turns the show's
models off and hands them back to FPP.

```bash
sudo python3 /home/fpp/media/scripts/run_show.py
```

`sudo` is needed for fast animation: fppd (running as root) owns the shared-memory buffers the
script writes to. Without it the show still runs, using slow per-pixel updates.

```bash
python3 /home/fpp/media/scripts/run_show.py --list
```

`--list` prints the model names and sizes on the controller. You can also pass another
show file (`run_show.py christmas.json`) or `--host` to run from another machine.

The shipped `show.json` gives every model its own random solid color:

```json
{
  "default": { "effect": "random_solid" },
  "models": {}
}
```

A mix of a default and specific models:

```json
{
  "default": { "effect": "random_solid", "interval": 10 },
  "models": {
    "Left Tree String": { "effect": "random_cycle", "count": 20, "interval": 3 },
    "Mantle": { "effect": "solid", "color": "#ff0000" }
  }
}
```

Effects (in `effects.py`):

| Effect | Options |
|--------|---------|
| `off` | none |
| `solid` | `color` (`"#RRGGBB"` or `[r, g, b]`), `count` (default: all pixels) |
| `random_solid` | every pixel one random color, picked separately per model; `interval` seconds to pick a new one (default: never), `brightness` 0-1 |
| `random_cycle` | `count` (default: all), `interval` seconds (default 3), `brightness` 0-1 (default 1) |
| `chase` | `color` (default white), `length` lit pixels (default 10), `speed` pixels/second (default 30), `gap` off pixels between repeating segments (default: one segment wrapping the whole string), `background` (default off), `reverse` (`true`/`false`) |
| `comet` | comets: dark pixel, cubic-fade tail in a random palette color, white head, tiled along the string and moving. `size` (default 15), `speed` pixels/second (default 10), `colors` (palette list), `head` (default white), `brightness` 0-1, `reverse` |
| `twinkle` | every pixel twinkles on its own: random palette color, fades up and down over a random `min_steps`-`max_steps` steps (default 5-24), one step every `interval` seconds (default 0.1). Also `colors`, `brightness` |

`comet` and `twinkle` recreate `stripflare`/`stringflare` from the earlier
`treeMainProgram.py` prototype, with the same default palettes and timing. `colors` takes a
list like `["#ff0000", "#00ff00", [0, 0, 255]]`.

Chase example: `"Mantle": { "effect": "chase", "color": "#ff0000", "length": 3, "gap": 7, "speed": 15 }`.

Each model entry can also set `"pixels": 300` to limit effects to the string's real length.
FPP auto-creates port models at the port's maximum size (e.g. 1024 pixels), and without this
a chase spends most of its time past the end of a shorter string.

Animated effects send whole frames, using the fastest method available (shown at startup
as `shm`, `bulk` or `pixel` updates):

1. **shm**: written straight into fppd's shared-memory overlay buffer in `/dev/shm`. Needs no
   web requests; works when the script runs on the Pi (FPP 7.5 included).
2. **bulk**: one `PUT /api/overlays/model/<name>/data` request per frame (newer FPP versions).
3. **pixel**: one request per changed pixel. Slow, and busy patterns can overload FPP's web
   server; avoid animations in this mode.

`count` lights the first N pixels; the rest of that model stays off. Any mistakes in the
file (unknown effect, bad color, count too large) are all reported before
anything lights up.

### `random_first20.py`: test pattern

Pixels 1-20 all show the same random color, which changes every 3 seconds. All other
pixels stay off. Ctrl+C turns the string off and releases the overlay.

```bash
python3 /home/fpp/media/scripts/random_first20.py
```

Options: `--count`, `--interval`, `--brightness 0.0-1.0`, `--model`, and `--host` (to run it
from another machine on the network).

Make sure no FPP sequence or playlist is running on these channels while the test is running.
