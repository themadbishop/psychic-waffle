"""Lighting effects that can be assigned to an overlay model in show.json.

Each effect's update() is called by run_show.py when it is due. It returns
the number of seconds until it should be called again, or None when the
effect has nothing more to change (e.g. a solid color).

To add an effect: write a class here and add it to EFFECTS.
"""

import colorsys
import random
import time


def parse_color(value):
    """Accept [r, g, b] (0-255) or "#RRGGBB"."""
    if isinstance(value, str):
        v = value.lstrip("#")
        if len(v) == 6:
            try:
                return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass
    elif isinstance(value, (list, tuple)) and len(value) == 3 and all(
        isinstance(c, int) and 0 <= c <= 255 for c in value
    ):
        return tuple(value)
    raise ValueError(f"bad color {value!r}; use [r, g, b] with 0-255 values or \"#RRGGBB\"")


def parse_palette(value, default):
    """A list of colors for effects that pick from a palette; None means `default`."""
    if value is None:
        return list(default)
    if not isinstance(value, list) or not value:
        raise ValueError(f"colors must be a non-empty list of colors, got {value!r}")
    return [parse_color(c) for c in value]


def check_brightness(brightness):
    if not isinstance(brightness, (int, float)) or not 0 < brightness <= 1:
        raise ValueError(f"brightness must be 0-1, got {brightness!r}")
    return brightness


def scale(rgb, factor):
    return tuple(int(c * factor) for c in rgb)


# Palettes from the treeMainProgram.py prototype.
FLARE_COLORS = [
    (0, 255, 255), (255, 0, 0), (255, 0, 128), (0, 0, 255),
    (255, 128, 0), (128, 0, 255), (0, 255, 0),
]
TWINKLE_COLORS = FLARE_COLORS + [
    (255, 255, 255), (255, 0, 0), (255, 128, 0), (0, 0, 204), (128, 0, 255),
]


def random_color(brightness=1.0):
    """A random, fully saturated color so every change is clearly visible."""
    r, g, b = colorsys.hsv_to_rgb(random.random(), 1.0, brightness)
    return round(r * 255), round(g * 255), round(b * 255)


class Effect:
    def __init__(self, model):
        self.model = model

    def _count(self, count):
        """Validate a pixel count option; None means the whole model."""
        if count is None:
            return self.model.pixel_count
        if not isinstance(count, int) or not 1 <= count <= self.model.pixel_count:
            raise ValueError(f"count must be 1-{self.model.pixel_count}, got {count!r}")
        return count

    def _light_first(self, count, rgb):
        """Set the first `count` pixels to rgb and the rest off."""
        if count == self.model.pixel_count:
            self.model.fill(rgb)
        else:
            self.model.set_all([rgb] * count)

    def update(self):
        return None


class Off(Effect):
    """All pixels off (run_show.py clears every model before starting)."""


class Solid(Effect):
    """One fixed color on the first `count` pixels (default: all)."""

    def __init__(self, model, color, count=None):
        super().__init__(model)
        self.color = parse_color(color)
        self.count = self._count(count)

    def update(self):
        self._light_first(self.count, self.color)
        return None


class RandomCycle(Effect):
    """The first `count` pixels (default: all) share a random color that changes every `interval` seconds."""

    def __init__(self, model, count=None, interval=3.0, brightness=1.0):
        super().__init__(model)
        self.count = self._count(count)
        if not interval > 0:
            raise ValueError(f"interval must be > 0, got {interval!r}")
        if not 0 < brightness <= 1:
            raise ValueError(f"brightness must be 0-1, got {brightness!r}")
        self.interval = interval
        self.brightness = brightness

    def update(self):
        self._light_first(self.count, random_color(self.brightness))
        return self.interval


class RandomSolid(RandomCycle):
    """Every pixel one random color, picked independently for each model.

    The color stays put unless `interval` is given, in which case a new one
    is picked every `interval` seconds.
    """

    def __init__(self, model, interval=None, brightness=1.0):
        super().__init__(model, interval=interval or 1, brightness=brightness)
        self.interval = interval  # None: update() runs once and the color stays


class Chase(Effect):
    """Lit segments of `length` pixels moving along the string at `speed` pixels/second.

    With `gap` unset, a single segment travels the whole string and wraps
    around; with `gap` set, the pattern repeats (`length` lit, `gap` off).
    """

    MAX_FPS = 40

    def __init__(self, model, color="#ffffff", length=10, gap=None, speed=30,
                 background="#000000", reverse=False):
        super().__init__(model)
        self.color = parse_color(color)
        self.background = parse_color(background)
        n = model.pixel_count
        if not isinstance(length, int) or not 1 <= length <= n:
            raise ValueError(f"length must be 1-{n}, got {length!r}")
        if gap is not None and (not isinstance(gap, int) or gap < 1):
            raise ValueError(f"gap must be a whole number >= 1, got {gap!r}")
        if not isinstance(speed, (int, float)) or speed <= 0:
            raise ValueError(f"speed must be > 0 pixels/second, got {speed!r}")
        self.period = length + gap if gap else n
        self.length = length
        self.speed = speed
        self.direction = -1 if reverse else 1
        self.interval = max(1 / speed, 1 / self.MAX_FPS)
        self.start = None
        self.last_pos = None

    def update(self):
        now = time.monotonic()
        if self.start is None:
            self.start = now
        # Position comes from elapsed time, so the speed holds even if an update runs late.
        pos = int((now - self.start) * self.speed) * self.direction % self.period
        if pos != self.last_pos:
            self.model.set_all([
                self.color if (i - pos) % self.period < self.length else self.background
                for i in range(self.model.pixel_count)
            ])
            self.last_pos = pos
        return self.interval


class Comet(Effect):
    """Comets travelling along the string (stripflare in the prototype).

    The string is tiled with flares of `size` pixels: one dark pixel, a tail
    that brightens along a cubic curve in the flare's color, and a white
    head leading the way. Each flare's color is picked at random from
    `colors`. The pattern moves `speed` pixels per second.
    """

    MAX_FPS = 40

    def __init__(self, model, size=15, speed=10, colors=None, head="#ffffff",
                 brightness=1.0, reverse=False):
        super().__init__(model)
        n = model.pixel_count
        if not isinstance(size, int) or not 3 <= size <= n:
            raise ValueError(f"size must be 3-{n}, got {size!r}")
        if not isinstance(speed, (int, float)) or speed <= 0:
            raise ValueError(f"speed must be > 0 pixels/second, got {speed!r}")
        palette = parse_palette(colors, FLARE_COLORS)
        brightness = check_brightness(brightness)
        head = scale(parse_color(head), brightness)

        tail = size - 2
        self.pattern = []
        for _ in range(n // size):  # leftover pixels at the end stay dark, as in the prototype
            color = random.choice(palette)
            self.pattern.append((0, 0, 0))
            for g in range(tail):
                pct = (g / (tail - 1)) ** 3 if tail > 1 else 1
                self.pattern.append(scale(color, pct * brightness))
            self.pattern.append(head)
        self.pattern += [(0, 0, 0)] * (n - len(self.pattern))

        self.speed = speed
        self.direction = -1 if reverse else 1
        self.interval = max(1 / speed, 1 / self.MAX_FPS)
        self.start = None
        self.last_offset = None

    def update(self):
        now = time.monotonic()
        if self.start is None:
            self.start = now
        n = len(self.pattern)
        offset = int((now - self.start) * self.speed) * self.direction % n
        if offset != self.last_offset:
            # Pixel p of the pattern is shown at p + offset, so flares move head first.
            self.model.set_all(self.pattern[-offset:] + self.pattern[:-offset] if offset else self.pattern)
            self.last_offset = offset
        return self.interval


class Twinkle(Effect):
    """Every pixel twinkles on its own (stringflare in the prototype).

    Each pixel picks a random color from `colors` and a random length of
    `min_steps`-`max_steps` steps, fades linearly up to full brightness and
    back to off, then picks again. One step every `interval` seconds.
    """

    def __init__(self, model, colors=None, interval=0.1, min_steps=5, max_steps=24, brightness=1.0):
        super().__init__(model)
        self.palette = parse_palette(colors, TWINKLE_COLORS)
        self.brightness = check_brightness(brightness)
        if not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError(f"interval must be > 0, got {interval!r}")
        if not (isinstance(min_steps, int) and isinstance(max_steps, int) and 1 <= min_steps <= max_steps):
            raise ValueError(f"need whole numbers 1 <= min_steps <= max_steps, got {min_steps!r}, {max_steps!r}")
        self.interval = interval
        self.min_steps, self.max_steps = min_steps, max_steps
        n = model.pixel_count
        self.color = [(0, 0, 0)] * n
        self.state = [0] * n
        self.peak = [0] * n  # 0 means "pick a new color and peak on the next step"
        self.rising = [True] * n

    def update(self):
        frame = []
        for i in range(len(self.state)):
            if self.peak[i] == 0:
                self.color[i] = random.choice(self.palette)
                self.peak[i] = random.randint(self.min_steps, self.max_steps)
                self.state[i], self.rising[i] = 0, True
            self.state[i] += 1 if self.rising[i] else -1
            if self.state[i] >= self.peak[i]:
                self.rising[i] = False
            elif self.state[i] <= 0:
                self.peak[i] = 0  # faded out; start over next step
            frame.append(scale(self.color[i], self.state[i] / self.peak[i] * self.brightness)
                         if self.peak[i] else (0, 0, 0))
        self.model.set_all(frame)
        return self.interval


EFFECTS = {
    "off": Off,
    "solid": Solid,
    "random_solid": RandomSolid,
    "random_cycle": RandomCycle,
    "chase": Chase,
    "comet": Comet,
    "twinkle": Twinkle,
}


def create(model, spec):
    """Build an effect from one show.json entry, e.g. {"effect": "solid", "color": "#ff0000"}."""
    spec = dict(spec)
    name = spec.pop("effect", None)
    cls = EFFECTS.get(name)
    if cls is None:
        raise ValueError(f"unknown effect {name!r}; choose from {', '.join(EFFECTS)}")
    try:
        return cls(model, **spec)
    except TypeError as e:
        raise ValueError(f"bad options for '{name}': {e}") from e
