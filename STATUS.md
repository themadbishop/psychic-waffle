# Project status

_Last updated: 2026-10-10_

## Goal

Python code that runs an LED light display from a Raspberry Pi running FPP (Falcon Player)
with an M8-PI pixel hat. Code is copied to the Pi and run there
(`/home/fpp/media/scripts/`). The scripts control pixels through FPP's **Pixel Overlay
Models**; FPP itself drives the hat's outputs.

## Hardware / FPP setup

- Raspberry Pi 4, FPP 7.5 (FPP OS image v2023-12), M8-PI hat (8 pixel ports).
- Code: https://github.com/themadbishop/psychic-waffle (`main`, pushed over SSH).
- Hostname **`flourine`**: web UI at http://flourine.local/ (was 192.168.86.109).
  `fpp.local` does **not** exist on this network.
- Port pixel counts are set in FPP (*Input/Output Setup > Channel Outputs*, M8-PI tab); FPP
  auto-creates one overlay model per port at that size. Current ports (set 2026-10-10):

  | Port | Model | Channels | Pixels | Orientation | Color order |
  |------|-------|----------|--------|-------------|-------------|
  | 1 | `Mantle` | 1-900 | 300 | horizontal | RGB |
  | 2 | `Left Tree String` | 901-1050 | 50 | vertical | RGB |
  | 3 | `Left Tree Strip` | 1051-1200 | 50 | vertical | GRB |
  | 4-8 | (unused) | | 0 | | |

- Strings will grow past 1000 pixels: update the port pixel counts in FPP as lights are added
  (the scripts pick up new sizes automatically).

## Running it

```bash
scp *.py show.json fpp@flourine.local:/home/fpp/media/scripts/
```

```bash
sudo python3 /home/fpp/media/scripts/run_show.py
```

- `sudo` is required for smooth animation (see "Pixel update methods").
- `run_show.py --list` prints the controller's models and sizes.
- Ctrl+C stops the show and turns the show's models off.
- No FPP sequence/playlist should be playing on the same channels.

## Files

| File | Purpose |
|------|---------|
| `fpp_overlay.py` | Stdlib-only FPP client: `OverlayModel` (`set_all`, `fill`, `set_index`, `set_pixel`, `clear`, `enable`/`disable`, `load_geometry`, `setup_frames`, `pixel_count`) and `list_models()` |
| `effects.py` | Effect classes + the `EFFECTS` registry used by `show.json` |
| `run_show.py` | Runs `show.json`: one effect per model, all at once |
| `show.json` | Which effect each model runs |
| `random_first20.py` | Original single-model test (first 20 pixels, random color every 3 s) |
| `README.md` | Setup, install and usage |
| `STATUS.md` | This file |

Current `show.json`:

```json
{
  "default": { "effect": "random_solid" },
  "models": {
    "Left Tree String": { "effect": "twinkle" },
    "Left Tree Strip": { "effect": "comet", "size": 15, "speed": 10 },
    "Mantle": { "effect": "twinkle" }
  }
}
```

## show.json format

- `"models"`: model name -> `{ "effect": ..., options... }`.
- `"default"`: applied to every model on the controller not listed under `"models"`.
- `"pixels": N` in an entry limits effects to the first N pixels (rarely needed now that the
  port pixel counts are set correctly).
- Models named but missing on the controller (or with 0 pixels) are skipped with a warning.
  Real mistakes (unknown effect, bad color, bad option value) stop the show before anything
  lights, listing every problem.
- Models are discovered at startup, so any number of models is supported; restart the show
  after adding/removing models in FPP.

### Effects

| Effect | Options |
|--------|---------|
| `off` | none |
| `solid` | `color` (`"#RRGGBB"` or `[r, g, b]`), `count` |
| `random_solid` | `interval` (default: never changes), `brightness` |
| `random_cycle` | `count`, `interval` (default 3 s), `brightness` |
| `chase` | `color`, `length`, `speed` (px/s), `gap` (repeating segments), `background`, `reverse` |
| `comet` | `size` (15), `speed` (10 px/s), `colors`, `head` (white), `brightness`, `reverse` |
| `twinkle` | `colors`, `interval` (0.1 s), `min_steps` (5), `max_steps` (24), `brightness` |

`comet` and `twinkle` are ports of `stripflare` and `stringflare` from the earlier prototype
`~/Documents/xmasLights/treeMainProgram.py` (rpi_ws281x, different controller), with the same
palettes and timing. Deliberate differences: no red/green swap (FPP handles color order per
port) and default brightness 1.0 (the prototype ran at about 0.7; use `"brightness": 0.7`).

### Adding an effect

A class in `effects.py` with `__init__(self, model, **options)` (validate; raise
`ValueError` on bad input) and `update(self)` (set pixels, usually with
`self.model.set_all(frame)`; return seconds until the next update, or `None` if static),
registered in `EFFECTS`. Options come straight from the `show.json` entry.

## Pixel update methods

`set_all` picks the fastest method at startup and `run_show.py` prints it per model:

1. **shm**: writes fppd's shared-memory overlay buffer (`/dev/shm/FPP-Model-Overlay-Buffer-<name>`:
   12-byte header width/height/flags, then RGB bytes; sets flag bit 0 so fppd outputs it).
   No web requests. The buffers are root-owned, so this needs **`sudo`**.
2. **bulk**: one `PUT /api/overlays/model/<name>/data` per frame. Newer FPP only; **not in 7.5**.
3. **pixel**: one web request per changed pixel. Slow, and busy patterns can overload FPP's
   web server (this is what made the first chase "very slow").

## Testing

- On the Pi: `random_first20.py`; `run_show.py` with chase in shm mode under `sudo`
  (confirmed smooth).
- Mac only (mock FPP server / fake model): multi-model handling (12, 3, 0 models; missing and
  empty models; bad configs; Ctrl+C cleanup), all three update methods, `pixels`, and
  `comet`/`twinkle` (checked pixel-for-pixel against the prototype's math).
- **Not yet seen on the Pi:** `comet` and `twinkle`. The latest copy to the Pi was waiting for
  the `fpp` password at the end of the session; not confirmed complete.

## Troubleshooting notes

- Browser/terminal on the Mac couldn't reach the Pi while the Claude app could: likely the
  macOS **Local Network** permission (System Settings > Privacy & Security > Local Network).
  Allow the browser and Terminal/iTerm there.
- First `scp` to `flourine.local` asks to trust the host key; it is the same key already
  known for 192.168.86.109. The "post-quantum key exchange" warning is from the Pi's older
  OpenSSH and is harmless on the home network.

## Known limitations

- `pixel` mode with busy patterns can overload FPP's web server; always run with `sudo`.
- Each HTTP request opens a new connection (only matters for setup and `pixel` mode).
- Models are read only at startup.
- Random colors are picked independently, so two models can occasionally look alike.
- `comet` leaves leftover pixels dark when the string length isn't a multiple of `size`
  (with 50-pixel models: 3 comets + 5 dark pixels), as the prototype did.

## Next steps

1. Finish copying the files and check `comet` / `twinkle` on the Pi.
2. Optional: SSH key for the Pi so `scp` doesn't need a password.
3. Set port pixel counts in FPP as lights are added.
4. Optional: rescan models while running; evenly spaced random colors; start the show from
   FPP's scheduler (it must run as root for shm access); more effects.
