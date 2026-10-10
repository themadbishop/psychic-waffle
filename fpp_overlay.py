"""Small client for FPP's Pixel Overlay Model REST API.

Uses only the Python standard library, so nothing extra has to be
installed on the Raspberry Pi.

Whole frames (set_all) go out the fastest way available:
  1. "shm":   write straight into fppd's shared-memory overlay buffer
              (/dev/shm, only when running on the Pi itself; FPP 7.x and up)
  2. "bulk":  one PUT .../data request per frame (newer FPP versions)
  3. "pixel": one PUT .../pixel request per changed pixel (any FPP; slow)
"""

import glob
import json
import mmap
import os
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

# Overlay model states understood by fppd.
STATE_DISABLED = 0
STATE_ENABLED = 1  # overlay fully replaces whatever else is driving these channels

BLACK = (0, 0, 0)

# Header of fppd's shared overlay buffer (OverlayBufferData in PixelOverlayModel.h):
# uint32 width, uint32 height, uint32 flags, then width*height RGB bytes, row-major.
SHM_HEADER = struct.Struct("<III")
SHM_FLAGS_OFFSET = 8
SHM_DIRTY = 0x1  # tells fppd to copy the buffer to the output on its next frame
SHM_DIR = "/dev/shm"


class FPPError(RuntimeError):
    pass


def request(method, url, body=None, timeout=5, raw=None):
    """Send a request to fppd. `body` is sent as JSON; `raw` as octet-stream bytes."""
    if raw is not None:
        data, ctype = raw, "application/octet-stream"
    elif body is not None:
        data, ctype = json.dumps(body).encode(), "application/json"
    else:
        data = None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace").strip()[:200]
        raise FPPError(f"{method} {url} failed: HTTP {e.code} {e.reason}" + (f": {detail}" if detail else "")) from e
    except urllib.error.URLError as e:
        raise FPPError(f"Cannot reach FPP at {url}: {e.reason}") from e
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text  # some endpoints (e.g. /api/command on FPP 7.5) reply with plain text


def list_models(host="localhost"):
    """Return the runtime info of every Pixel Overlay Model on the controller."""
    return request("GET", f"http://{host}/api/overlays/models") or []


_effect_cache = {}


def list_effects(host="localhost"):
    """Names of FPP's built-in overlay effects (WLED ones are "WLED - <name>")."""
    key = (host, None)
    if key not in _effect_cache:
        _effect_cache[key] = request("GET", f"http://{host}/api/overlays/effects") or []
    return _effect_cache[key]


def effect_definition(name, host="localhost"):
    """FPP's description of a built-in effect: {"name": ..., "args": [{name, type, default, ...}]}."""
    key = (host, name)
    if key not in _effect_cache:
        url = f"http://{host}/api/overlays/effects/{urllib.parse.quote(name)}"
        _effect_cache[key] = request("GET", url) or {"name": name, "args": []}
    return _effect_cache[key]


def run_command(command, args, host="localhost"):
    """Run an FPP command, e.g. run_command("Overlay Model Effect", [...])."""
    return request("POST", f"http://{host}/api/command", {"command": command, "args": args})


class OverlayModel:
    """One Pixel Overlay Model defined in FPP (Input/Output Setup > Pixel Overlay Models).

    `pixels` limits effects to the first N pixels, for models that FPP sized
    larger than the string actually attached (e.g. 1024 for a 300-pixel string).
    """

    _warned_no_bulk = False
    _shm_denied = False  # the shared buffers exist but this user can't write them

    def __init__(self, name, host="localhost", timeout=5, pixels=None):
        self.name = name
        self.host = host
        self.base = f"http://{host}/api"
        self.model_url = f"{self.base}/overlays/model/{urllib.parse.quote(name)}"
        self.timeout = timeout
        self.pixels = pixels
        self.frame_mode = None  # "shm", "bulk" or "pixel"; chosen by setup_frames()
        self._shm = None
        self._last_frame = None  # what the pixels currently show, for the per-pixel mode

    def _request(self, method, url, body=None, raw=None):
        return request(method, url, body, timeout=self.timeout, raw=raw)

    def info(self):
        """Return the model's runtime info (width, height, isActive, ...).

        fppd answers an unknown model name with 200 and a bare `null`, so that
        case is turned into an error listing the models that do exist.
        """
        info = self._request("GET", self.model_url)
        if not info:
            models = self._request("GET", f"{self.base}/overlays/models") or []
            names = ", ".join(m.get("Name", "?") for m in models) or "(none)"
            raise FPPError(f"FPP has no Pixel Overlay Model named '{self.name}'. Models found: {names}")
        return info

    def start_effect(self, effect, args):
        """Start one of FPP's built-in effects (e.g. "WLED - Fireworks 1D") on this model.

        `args` are the effect's settings as strings, in effect_definition() order.
        FPP renders the effect itself until stop_effects() is called.
        """
        run_command("Overlay Model Effect", [self.name, "Enabled", effect] + list(args), host=self.host)

    def stop_effects(self):
        """Stop any FPP built-in effect running on this model (FPP also clears the model)."""
        run_command("Overlay Model Effect", [self.name, "False", "Stop Effects"], host=self.host)

    def set_state(self, state):
        self._request("PUT", f"{self.model_url}/state", {"State": state})

    def enable(self):
        self.set_state(STATE_ENABLED)

    def disable(self):
        self.set_state(STATE_DISABLED)

    def clear(self):
        """Set every pixel in the model to black."""
        self._request("GET", f"{self.model_url}/clear")
        self._last_frame = [BLACK] * self.pixel_count if hasattr(self, "width") else None

    def fill(self, rgb):
        """Set every pixel in the model (including any beyond `pixels`) to rgb."""
        self._request("PUT", f"{self.model_url}/fill", {"RGB": list(rgb)})
        self._last_frame = [tuple(rgb)] * self.pixel_count if hasattr(self, "width") else None

    def set_pixel(self, x, rgb, y=0):
        self._request("PUT", f"{self.model_url}/pixel", {"X": x, "Y": y, "RGB": list(rgb)})

    def load_geometry(self):
        """Read width/height from FPP; needed before using pixel_count or set_index."""
        info = self.info()
        self.width, self.height = info.get("width", 0), info.get("height", 0)
        if self.pixels is not None and not (isinstance(self.pixels, int) and 1 <= self.pixels <= self.width * self.height):
            raise ValueError(f"pixels must be 1-{self.width * self.height}, got {self.pixels!r}")
        return info

    @property
    def pixel_count(self):
        """Pixels effects should use: the `pixels` limit if set, else the whole model."""
        return self.pixels or self.width * self.height

    def set_index(self, i, rgb):
        """Set pixel i counting along the string, whatever the model's orientation.

        A single string shows up as width x 1 (horizontal) or 1 x height
        (vertical); larger grids are walked row by row.
        """
        if self.width == 1:
            self.set_pixel(0, rgb, y=i)
        else:
            self.set_pixel(i % self.width, rgb, y=i // self.width)

    def _open_shm(self):
        """Map fppd's shared overlay buffer for this model, or return None.

        Only possible when running on the Pi itself. The `mmap` request makes
        fppd create the buffer if it doesn't exist yet.
        """
        if not os.path.isdir(SHM_DIR):
            return None
        try:
            self._request("PUT", f"{self.model_url}/mmap", {})
        except FPPError:
            return None
        # fppd names it "/FPP-Model-Overlay-Buffer-<name>", or a truncated
        # "/FPPMB-<name>" on systems with short shared-memory names.
        paths = [os.path.join(SHM_DIR, f"FPP-Model-Overlay-Buffer-{self.name}")]
        short = os.path.join(SHM_DIR, "FPPMB-")
        paths += [p for p in glob.glob(short + "*") if self.name.startswith(p[len(short):])]
        path = next((p for p in paths if os.path.exists(p)), None)
        if path is None:
            return None
        try:
            fd = os.open(path, os.O_RDWR)
        except PermissionError:
            OverlayModel._shm_denied = True
            return None
        try:
            shm = mmap.mmap(fd, 0)
        finally:
            os.close(fd)
        width, height, _ = SHM_HEADER.unpack_from(shm, 0)
        if (width, height) != (self.width, self.height) or len(shm) < SHM_HEADER.size + width * height * 3:
            shm.close()
            return None
        return shm

    def setup_frames(self):
        """Pick the fastest way set_all() can send frames; returns the mode name."""
        self._shm = self._open_shm()
        if self._shm is not None:
            self.frame_mode = "shm"
            return self.frame_mode
        # Probe bulk upload with an all-off frame (the model was just cleared).
        try:
            resp = self._request("PUT", f"{self.model_url}/data", raw=bytes(self.width * self.height * 3))
            bulk = isinstance(resp, dict) and resp.get("Status") == "OK"
        except FPPError:
            bulk = False
        self.frame_mode = "bulk" if bulk else "pixel"
        if not bulk and not OverlayModel._warned_no_bulk:
            OverlayModel._warned_no_bulk = True
            if OverlayModel._shm_denied:
                why = "no permission to write fppd's buffers in /dev/shm; run with sudo"
            else:
                why = "run this on the Pi itself, or upgrade FPP"
            print(f"Note: fast pixel updates aren't available ({why}); using one request per "
                  f"changed pixel, so animations will be slow.", file=sys.stderr)
        return self.frame_mode

    def close(self):
        if self._shm is not None:
            self._shm.close()
            self._shm = None

    def set_all(self, frame):
        """Set pixels 0..pixel_count-1 from a list of (r, g, b), in string order.

        Missing entries at the end are off. See the module docstring for how
        the frame is sent.
        """
        n = self.pixel_count
        frame = [tuple(c) for c in frame[:n]] + [BLACK] * (n - len(frame))
        if self.frame_mode is None:
            self.setup_frames()

        # Row-major RGB bytes, which is also the set_index() order.
        if self.frame_mode == "shm":
            start = SHM_HEADER.size
            self._shm[start:start + n * 3] = bytes(v for rgb in frame for v in rgb)
            flags = struct.unpack_from("<I", self._shm, SHM_FLAGS_OFFSET)[0]
            struct.pack_into("<I", self._shm, SHM_FLAGS_OFFSET, flags | SHM_DIRTY)
        elif self.frame_mode == "bulk":
            full = frame + [BLACK] * (self.width * self.height - n)
            resp = self._request("PUT", f"{self.model_url}/data", raw=bytes(v for rgb in full for v in rgb))
            if not (isinstance(resp, dict) and resp.get("Status") == "OK"):
                raise FPPError(f"Bulk pixel update to '{self.name}' failed: {resp!r}")
        else:
            last = self._last_frame or [None] * n
            for i, rgb in enumerate(frame):
                if rgb != last[i]:
                    self.set_index(i, rgb)
        self._last_frame = frame
