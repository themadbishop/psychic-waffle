"""Read the string-port setup of a Falcon F16v4 pixel controller.

Uses the controller's JSON API (the one its web pages use), so it only
reads settings; nothing on the controller is changed.
"""

import json
import socket
import urllib.error
import urllib.request
from collections import namedtuple

from fpp_overlay import FPPError

CHANNELS_PER_PIXEL = 3

# One string on a port. A port can carry several strings ("virtual strings"),
# numbered from 1 in `string`. Channels are 1-based, like FPP's.
Port = namedtuple("Port", "number string pixels start_channel")


def query(controller, method, timeout=5):
    """Run one of the controller's query methods (e.g. "SP" for string ports)."""
    pieces = []
    for batch in range(64):  # long answers come in batches; "F": 1 marks the last one
        body = json.dumps({"T": "Q", "M": method, "B": batch, "E": 0, "I": 0, "P": {}}).encode()
        req = urllib.request.Request(f"http://{controller}/api", data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                answer = json.loads(resp.read().decode())
        except (urllib.error.URLError, socket.timeout) as e:
            raise FPPError(f"Cannot reach the pixel controller at {controller}: {getattr(e, 'reason', e)}") from e
        except ValueError as e:
            raise FPPError(f"The pixel controller at {controller} sent a reply that isn't JSON: {e}") from e
        if answer.get("R") != 200:
            raise FPPError(f"The pixel controller at {controller} refused query {method!r}: {answer!r}")
        pieces.append(answer.get("P", {}))
        if answer.get("F", 1):
            break
    return pieces


def ports(controller):
    """Every string with pixels on the controller, as Port tuples in port order.

    Returns (ports, problems): strings whose start is given as a universe
    instead of an absolute channel are listed in `problems` and left out.
    """
    found, problems = [], []
    for piece in query(controller, "SP"):
        for s in piece.get("A", []):
            if not s.get("n"):
                continue  # no pixels on this string
            number = s["p"] + 1
            if s.get("u"):
                problems.append(f"port {number} starts at universe {s['u']}; set it to an absolute "
                                f"start channel in the controller's String Ports page")
                continue
            found.append(Port(number, s.get("v", 0) + 1, s["n"], s["sc"] + 1))
    found.sort(key=lambda p: (p.number, p.string))
    return found, problems


def model_name(port, all_ports, prefix="Port"):
    """FPP overlay model name for a port: "Port3", or "Port3-2" when port 3 has several strings."""
    shared = sum(1 for p in all_ports if p.number == port.number) > 1
    return f"{prefix}{port.number}-{port.string}" if shared else f"{prefix}{port.number}"


def overlaps(all_ports):
    """Pairs of ports whose channels overlap (they'd show the same data)."""
    pairs = []
    for i, a in enumerate(all_ports):
        a_end = a.start_channel + a.pixels * CHANNELS_PER_PIXEL - 1
        for b in all_ports[i + 1:]:
            b_end = b.start_channel + b.pixels * CHANNELS_PER_PIXEL - 1
            if a.start_channel <= b_end and b.start_channel <= a_end:
                pairs.append((a, b))
    return pairs
