"""Lighting effects that can be assigned to an overlay model in show.json.

Each effect's update() is called by run_show.py when it is due. It returns
the number of seconds until it should be called again, or None when the
effect has nothing more to change (e.g. a solid color).

To add an effect: write a class here and add it to EFFECTS.
"""

import colorsys
import difflib
import math
import random
import time

from fpp_overlay import effect_definition, list_effects


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

    def __str__(self):
        return type(self).__name__

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

    def stop(self):
        """Called before the model is cleared for another effect or at the end of the show."""


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


WLED_PREFIXES = ("WLED - ", "WLED\u266a - ")  # FPP names audio-reactive ones "WLED♪ - ..."


def short_effect_name(name):
    for prefix in WLED_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _setting_key(name):
    """Loose match for setting names: "Firing side", "firing_side" and "FiringSide" are equal."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


class Wled(Effect):
    """One of FPP's built-in effects (the 173 "WLED - ..." ones, or FPP's own).

    show.json: {"effect": "wled", "name": "Fireworks 1D", "Gravity": 100, "Palette": "Rainbow"}

    FPP renders the effect itself; the script only starts and stops it. Setting
    names and allowed values come from FPP (run_show.py --effects "Fireworks 1D"
    lists them); settings left out use FPP's defaults. Name matching ignores
    case, spaces and underscores.
    """

    def __init__(self, model, name, **settings):
        super().__init__(model)
        self.effect = self._resolve(model, name)
        definition = effect_definition(self.effect, model.host)
        given = {_setting_key(k): (k, v) for k, v in settings.items()}
        self.args = []
        for arg in definition.get("args", []):
            key = _setting_key(arg["name"])
            if key not in given:
                key = _setting_key(arg.get("description", ""))
            if key in given:
                label, value = given.pop(key)
                self.args.append(self._convert(arg, label, value))
            elif arg["name"] == "BufferMapping" and "Vertical" in arg.get("contents", []):
                # Lay 1D effects along the string: FPP's default (Horizontal) squeezes a
                # vertical 1-pixel-wide model into a single pixel.
                vertical = getattr(model, "width", 0) == 1 and getattr(model, "height", 0) > 1
                self.args.append("Vertical" if vertical else "Horizontal")
            elif "default" in arg:
                self.args.append(str(arg["default"]))
            elif arg.get("contents"):
                self.args.append(arg["contents"][0])
            else:
                raise ValueError(f"'{self.effect}' needs a value for \"{arg['name']}\"")
        if given:
            known = ", ".join(a["name"] for a in definition.get("args", [])) or "none"
            unknown = ", ".join(label for label, _ in given.values())
            raise ValueError(f"unknown setting(s) {unknown} for '{self.effect}'; its settings are: {known}")
        if self.effect.startswith(WLED_PREFIXES):
            # FPP 7.5 scales every pixel WLED writes by Brightness/128, so 128 is normal
            # brightness. Above that, effects that fade by reading pixels back (fireworks,
            # twinkles, ...) brighten instead of fading and get stuck at full white.
            for arg, value in zip(definition.get("args", []), self.args):
                if arg["name"] == "Brightness" and int(value) > 128:
                    raise ValueError(f"Brightness {value} is too high: in FPP's WLED effects 128 is full "
                                     f"brightness, and higher values stop fading effects from fading. Use 1-128.")

    @staticmethod
    def _resolve(model, name):
        names = list_effects(model.host)
        wanted = {_setting_key(p + str(name)) for p in ("",) + WLED_PREFIXES}
        for n in names:
            if _setting_key(n) in wanted:
                return n
        close = difflib.get_close_matches(f"WLED - {name}", names, n=3) or difflib.get_close_matches(str(name), names, n=3)
        hint = f"; did you mean {', '.join(close)}?" if close else "; run_show.py --effects lists them"
        raise ValueError(f"FPP has no effect named {name!r}{hint}")

    @staticmethod
    def _convert(arg, label, value):
        """Check a show.json value against FPP's definition and format it as FPP expects."""
        kind = arg.get("type")
        if kind in ("range", "int"):
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be a whole number, got {value!r}") from None
            low, high = arg.get("min"), arg.get("max")
            if (low is not None and number < low) or (high is not None and number > high):
                raise ValueError(f"{label} must be {low}-{high}, got {number}")
            return str(number)
        if kind == "color":
            return "#%02X%02X%02X" % parse_color(value)
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{label} must be true or false, got {value!r}")
            return "true" if value else "false"
        if arg.get("contents"):
            for option in arg["contents"]:
                if _setting_key(option) == _setting_key(value):
                    return option
            options = arg["contents"]
            shown = ", ".join(options[:12]) + (", ..." if len(options) > 12 else "")
            raise ValueError(f"{label} must be one of: {shown}; got {value!r}")
        return str(value)

    def __str__(self):
        return f"wled {short_effect_name(self.effect)!r}"

    def update(self):
        self.model.start_effect(self.effect, self.args)
        return None  # FPP keeps it running

    def stop(self):
        self.model.stop_effects()


EFFECTS["wled"] = Wled


RANDOM_DURATION = (60, 600)  # seconds, for "duration": "random"


def parse_duration(value, label):
    """A cycle duration: seconds, {"min": a, "max": b} or "random". Returns (min, max)."""
    if value == "random":
        return RANDOM_DURATION
    if isinstance(value, dict):
        extra = set(value) - {"min", "max"}
        low, high = value.get("min"), value.get("max")
        if extra or not all(isinstance(v, (int, float)) for v in (low, high)):
            raise ValueError(f"{label}: a random duration needs numbers \"min\" and \"max\", got {value!r}")
        if not 0 < low <= high:
            raise ValueError(f"{label}: need 0 < min <= max, got min {low}, max {high}")
        return (low, high)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label}: duration must be seconds > 0, \"random\" or {{\"min\": .., \"max\": ..}}, "
                         f"got {value!r}")
    return (value, value)


class Cycle(Effect):
    """Runs a list of effects in turn, switching every `duration` seconds.

    show.json: {"duration": 30, "effects": [{"effect": "twinkle"},
                                            {"effect": "comet", "duration": 60}]}
    An effect's own "duration" overrides the cycle's. A duration can also be
    random, re-picked every time the effect starts: {"min": 60, "max": 600},
    or "random" for 60-600 s. Each switch clears the model and starts the
    next effect fresh; after the last it starts over. A list with a single
    effect runs it forever; any duration is ignored.
    """

    def __init__(self, model, effects, duration=None):
        super().__init__(model)
        if not isinstance(effects, list) or not effects:
            raise ValueError(f"effects must be a non-empty list, got {effects!r}")
        single = len(effects) == 1
        self.steps = []  # (spec, (min seconds, max seconds))
        for n, item in enumerate(effects, 1):
            if not isinstance(item, dict):
                raise ValueError(f"effect {n} must be an object like {{\"effect\": \"twinkle\"}}, got {item!r}")
            item = dict(item)
            seconds = item.pop("duration", duration)
            label = f"effect {n} ({item.get('effect')!r})"
            if single:
                seconds = (math.inf, math.inf)  # nothing to switch to
            elif seconds is None:
                raise ValueError(f"{label} has no duration; set \"duration\" on the cycle or the effect")
            else:
                seconds = parse_duration(seconds, label)
            if "effects" in item:
                raise ValueError(f"{label}: cycles can't be nested")
            try:
                create(model, item)  # validate now so mistakes show up before the show starts
            except ValueError as e:
                raise ValueError(f"{label}: {e}") from e
            self.steps.append((item, seconds))
        self.index = -1
        self.current = None
        self.switch_at = 0.0
        self.inner_due = None

    def __str__(self):
        if len(self.steps) == 1:
            return f"{self._label(self.steps[0][0])} (only effect in list; no switching)"
        return "Cycle(" + " -> ".join(f"{self._label(spec)} {self._span(s)}" for spec, s in self.steps) + ")"

    @staticmethod
    def _span(seconds):
        low, high = seconds
        return f"{low:g}s" if low == high else f"{low:g}-{high:g}s"

    @staticmethod
    def _label(spec):
        return f"wled {spec.get('name')!r}" if spec.get("effect") == "wled" else spec.get("effect")

    def update(self):
        now = time.monotonic()
        if now >= self.switch_at:
            if self.current is not None:
                self.current.stop()
                self.model.clear()
                self.model.enable()  # stopping an FPP effect may have disabled the model
            self.index = (self.index + 1) % len(self.steps)
            spec, (low, high) = self.steps[self.index]
            self.current = create(self.model, spec)
            seconds = low if low == high else random.uniform(low, high)
            self.switch_at = now + seconds
            self.inner_due = now
            if len(self.steps) > 1:
                print(f"  {self.model.name}: {self._label(spec)} for {seconds:.0f}s", flush=True)
        if self.inner_due is not None and now >= self.inner_due:
            delay = self.current.update()
            self.inner_due = None if delay is None else now + delay
        # Wake for the effect's next update or the next switch, whichever comes first.
        wake = self.switch_at if self.inner_due is None else min(self.switch_at, self.inner_due)
        if wake == math.inf:
            return None  # a single static effect: nothing more to do
        return max(0.0, wake - time.monotonic())

    def stop(self):
        if self.current is not None:
            self.current.stop()


def create(model, spec):
    """Build an effect from one show.json entry, e.g. {"effect": "solid", "color": "#ff0000"},
    or a cycle: {"duration": 30, "effects": [...]}."""
    if not isinstance(spec, dict):
        raise ValueError(f"expected an object like {{\"effect\": \"twinkle\"}}, got {spec!r}")
    spec = dict(spec)
    if "effects" in spec:
        try:
            return Cycle(model, **spec)
        except TypeError as e:
            raise ValueError(f"bad options for a cycle (use \"duration\" and \"effects\"): {e}") from e
    name = spec.pop("effect", None)
    cls = EFFECTS.get(name)
    if cls is None:
        raise ValueError(f"unknown effect {name!r}; choose from {', '.join(EFFECTS)}")
    try:
        return cls(model, **spec)
    except TypeError as e:
        raise ValueError(f"bad options for '{name}': {e}") from e
