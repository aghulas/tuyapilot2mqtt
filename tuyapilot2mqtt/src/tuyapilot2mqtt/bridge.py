"""The contract's messages, built from what the channels read. No I/O here.

READ ONLY: ``/set`` and ``bridge/request/*`` are answered by an explicit refusal and nothing is
sent to a device.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NamedTuple

from pytuya_pilot_wire import __version__ as library_version
from pytuya_pilot_wire import dps as library_dps

from . import CONTRACT_VERSION, __version__
from .config import CHANNELS, Settings
from .devices import Thermostat
from .dialect import to_state
from .lan import LanChannel

# A device is published offline after this many consecutive passes without an answer: the gateway
# drops a read now and then, and one missed pass is not an outage.
MISSES_BEFORE_OFFLINE = 3

READ_ONLY_ERROR = "read-only bridge: no write route is open in this version, nothing was sent to the device"
REQUEST_ERROR = "request not available in this version (read-only bridge), nothing was sent to the device"


class Message(NamedTuple):
    topic: str
    payload: str
    retain: bool


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Bridge:
    def __init__(
        self,
        settings: Settings,
        thermostats: list[Thermostat],
        lan: LanChannel | None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.thermostats = thermostats
        self.lan = lan
        self._clock = clock
        self._by_name = {t.friendly_name: t for t in thermostats}
        self._misses: dict[str, int] = {}
        self._last_seen: dict[str, float] = {}

    # --- topics ---------------------------------------------------------------------------

    def topic(self, *levels: str) -> str:
        return "/".join((self.settings.base_topic, *levels))

    @property
    def state_topic(self) -> str:
        return self.topic("bridge", "state")

    @property
    def subscriptions(self) -> list[str]:
        return [self.topic("+", "set"), self.topic("bridge", "request", "#")]

    @staticmethod
    def state_payload(online: bool) -> str:
        return _json({"state": "online" if online else "offline"})

    # --- bridge topics --------------------------------------------------------------------

    def info(self) -> Message:
        channels: dict[str, Any] = {}
        for name in CHANNELS:
            if name not in self.settings.channels:
                channels[name] = {"enabled": False}
            elif name == "lan" and self.lan is not None:
                channels[name] = self.lan.status()
            else:
                channels[name] = {"enabled": True, "state": "unsupported"}
        payload = {
            "version": __version__,
            "contract": CONTRACT_VERSION,
            "library": {"name": "pytuya-pilot-wire", "version": library_version},
            "read_only": True,
            "poll_interval": self.settings.poll_interval,
            "channels": channels,
        }
        return Message(self.topic("bridge", "info"), _json(payload), True)

    def devices(self) -> Message:
        return Message(self.topic("bridge", "devices"), _json([t.descriptor() for t in self.thermostats]), True)

    def startup(self) -> list[Message]:
        """What is published on every (re)connection: bridge topics, and each device's availability."""
        messages = [Message(self.state_topic, self.state_payload(True), True), self.info(), self.devices()]
        messages += [self._availability(t) for t in self.thermostats]
        return messages

    # --- devices --------------------------------------------------------------------------

    def _online(self, t: Thermostat) -> bool:
        return t.dev_id in self._last_seen and self._misses.get(t.dev_id, 0) < MISSES_BEFORE_OFFLINE

    def _availability(self, t: Thermostat) -> Message:
        return Message(self.topic(t.friendly_name, "availability"), self.state_payload(self._online(t)), True)

    def state(self, t: Thermostat) -> dict[str, Any] | None:
        """State object of a thermostat (contract §3), or None when nothing was ever read."""
        if self.lan is None or not t.supported:
            return None
        seen = self.lan.data_points(t.dev_id)
        if not seen:
            return None
        decoded = library_dps.decode_lan(t.product_id, {dp: value for dp, (value, _) in seen.items()})
        state, origin = to_state(decoded)
        dp_of = {point.key: str(point.dp) for point in library_dps.table(t.product_id)}
        read = {
            prop: {"channel": "lan", "at": _iso(min(seen[dp_of[key]][1] for key in keys))}
            for prop, keys in origin.items()
        }
        extension: dict[str, Any] = {"read": read}
        if "mode" in decoded:
            extension["mode"] = decoded["mode"]
        state["last_seen"] = _iso(self._last_seen[t.dev_id])
        state["tuya"] = extension
        return state

    def poll(self) -> list[Message]:
        """One read pass, and the messages it gives. A state is published only for a device that answered."""
        if self.lan is None:
            return [self.info()]
        answered = self.lan.read()
        now = self._clock()
        messages: list[Message] = []
        for t in self.thermostats:
            if answered.get(t.dev_id):
                self._misses[t.dev_id] = 0
                self._last_seen[t.dev_id] = now
                state = self.state(t)
                if state is not None:
                    messages.append(Message(self.topic(t.friendly_name), _json(state), True))
            else:
                self._misses[t.dev_id] = self._misses.get(t.dev_id, 0) + 1
            messages.append(self._availability(t))
        messages.append(self.info())
        return messages

    # --- commands: refused ----------------------------------------------------------------

    def handle(self, topic: str, payload: bytes | str) -> list[Message]:
        """Answer a message received on a subscribed topic. Never reaches a device."""
        prefix = self.settings.base_topic + "/"
        if not topic.startswith(prefix):
            return []
        levels = topic[len(prefix) :].split("/")
        try:
            body = json.loads(payload)
        except (TypeError, ValueError):
            body = None
        response: dict[str, Any] = {"status": "error", "error": READ_ONLY_ERROR}
        if isinstance(body, dict) and "transaction" in body:
            response["transaction"] = body["transaction"]

        if levels[:2] == ["bridge", "request"] and len(levels) > 2:
            action = levels[2:]
            response["data"] = {}
            response["error"] = REQUEST_ERROR
            return [Message(self.topic("bridge", "response", *action), _json(response), False)]
        if len(levels) == 2 and levels[1] == "set":
            keys = sorted(k for k in body if k != "transaction") if isinstance(body, dict) else []
            response["data"] = {"device": levels[0], "keys": keys}
            if levels[0] not in self._by_name:
                response["error"] = "unknown device; " + READ_ONLY_ERROR
            return [Message(self.topic("bridge", "response", "device", "set"), _json(response), False)]
        return []
