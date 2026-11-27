"""The bridge: the daemon core of ``pytuya-pilot-wire``, publishing this repository's contract.

The read loop, the channel health, the routing, the write gate and the read-back are the
library's (:class:`pytuya_pilot_wire.bridge.Bridge`). What is here is what the contract of this
repository says beyond the library's draft-0 codec, and the policy of this version:

- ``bridge/info``, descriptors and state objects in the shape of ``docs/mqtt-contract.md``;
- the thermostats of the local keys file when the sharing channel has no session yet, and the LAN
  readings standing in for the state while the sharing channel is not connected;
- **one write only**: ``current_heating_setpoint``, on the one thermostat the owner designated,
  when the owner enabled writes, the sharing channel is connected and the thermostat is in
  ``manual`` mode. Everything else on ``/set`` and every ``bridge/request/*`` is refused before
  the library sees it, and refused a second time by the gate (which reads the mode again just
  before sending);
- the raw command actually sent to the device is logged (data-point code and raw value, no id).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from pytuya_pilot_wire import __version__ as library_version
from pytuya_pilot_wire import dialect, dps
from pytuya_pilot_wire.bridge import Bridge as LibraryBridge
from pytuya_pilot_wire.bridge import BridgeConfig
from pytuya_pilot_wire.errors import WriteRefused
from pytuya_pilot_wire.gate import PlannedWrite, ScheduleResult, SharingWriter, WriteGate, setpoint_mode_refusal
from pytuya_pilot_wire.model import Channel, Device, DeviceSpec, DeviceState, Reading

from . import CONTRACT_VERSION, __version__, contract
from .config import CHANNELS, Settings
from .devices import LocalInventory

log = logging.getLogger("tuyapilot2mqtt")

NOTHING_SENT = "nothing was sent to the device"
REQUEST_ERROR = f"request not available in this version; {NOTHING_SENT}"

# Library channel states -> contract states (§1.1).
_STATES = {"idle": "starting", "ok": "online", "error": "offline", "unauthenticated": "unauthenticated"}
_SETPOINT_KEY = "setpoint"  # the library's model key behind `current_heating_setpoint`


class _LoggedSend:
    """The sharing writer, logging the raw command it sends: data-point code and raw value only."""

    def __init__(self, writer: SharingWriter) -> None:
        self._writer = writer

    def read(self, dev_ids: list[str]) -> list[Reading]:
        return self._writer.read(dev_ids)

    def send(self, dev_id: str, commands: list[dict[str, Any]]) -> None:
        sent = ", ".join(f"{command.get('code')}={command.get('value')!r}" for command in commands)
        log.warning("WRITE sent to the designated thermostat, once: %s", sent)
        self._writer.send(dev_id, commands)


class TrialGate(WriteGate):
    """The library's write gate, narrowed to the single write this version opens.

    The manual setpoint of the designated thermostat passes on to the library's checks (route,
    the device's own bounds and step, ceiling, fresh read, refusal in any mode but ``manual``, one
    send, read-back). Any other key, any other device and the weekly schedule stop here.
    """

    designated: str | None = None  # Tuya device id of the designated thermostat, once known

    def __init__(self, router: Any, *, sharing: SharingWriter | None = None, **options: Any) -> None:
        super().__init__(router, sharing=None if sharing is None else _LoggedSend(sharing), **options)

    def replace(self, *, sharing: SharingWriter | None = None, developer: Any = None) -> None:
        super().replace(sharing=None if sharing is None else _LoggedSend(sharing), developer=developer)

    def plan(self, device: Device, spec: DeviceSpec | None, key: str, value: Any) -> PlannedWrite:
        if key != _SETPOINT_KEY:
            raise WriteRefused(f"only {contract.OPEN_SET_KEY} can be written in this version")
        if self.designated is None or device.dev_id != self.designated:
            raise WriteRefused("writes are open for the designated thermostat only")
        return super().plan(device, spec, key, value)

    def write_schedule(self, device: Device, days: Mapping[str, Any]) -> ScheduleResult:
        raise WriteRefused("the weekly schedule cannot be written in this version")


class Bridge(LibraryBridge):
    def __init__(
        self,
        transport: Any,
        settings: Settings,
        *,
        sharing: Any = None,
        lan: Any = None,
        developer: Any = None,
        local: LocalInventory | None = None,
        unavailable: Mapping[str, str] | None = None,
        login_state: Callable[[], tuple[str, str] | None] = lambda: None,
        clock: Callable[[], float] | None = None,
        gate_options: Mapping[str, Any] | None = None,
    ) -> None:
        config = BridgeConfig(
            prefix=settings.base_topic,
            sharing_interval=settings.poll_interval,
            lan_interval=settings.poll_interval,
            writes_enabled=settings.writes_enabled,
            bridge_version=__version__,
        )
        extra = {} if clock is None else {"clock": clock}
        super().__init__(transport, sharing=sharing, lan=lan, developer=developer, config=config, **extra)
        self.settings = settings
        self.gate = TrialGate(
            self.router, sharing=sharing, developer=None, enabled=settings.writes_enabled, **(gate_options or {})
        )
        self._local = local
        # Channels enabled in the options that could not be built, with the reason (no secret in it).
        self._unavailable = dict(unavailable or {})
        self._login_state = login_state
        self._sources: dict[str, str] = {}
        self._descriptors: list[dict[str, Any]] = []
        self._open = False

    # -- what the options and the channels allow --------------------------------------------

    def _sharing_connected(self) -> bool:
        status = self.status.get(Channel.SHARING)
        return status is not None and status.state == "ok"

    def _designated(self) -> str | None:
        """Tuya device id of the designated thermostat, when it is one of the known devices."""
        wanted = self.settings.write_device.strip()
        if not wanted:
            return None
        for dev_id, name in self.names.items():
            if wanted in (dev_id, name):
                return dev_id
        return None

    def closed_reason(self) -> str | None:
        """Why no write can pass now, or ``None`` when the single write of this version is open."""
        if not self.settings.writes_enabled:
            return "writes are disabled in the options"
        if not self.settings.write_device.strip():
            return "no thermostat is designated in the options"
        if Channel.SHARING not in self.status:
            return "the sharing channel is not enabled"
        if not self._sharing_connected():
            return "the sharing channel is not connected"
        if self._designated() is None:
            return "the designated thermostat is not among the devices"
        return None

    # -- bridge/info ------------------------------------------------------------------------

    def _channel_info(self, channel: Channel) -> dict[str, Any]:
        name = channel.value
        if name not in self.settings.channels:
            return {"enabled": False}
        if name in self._unavailable:
            return {"enabled": True, "state": "unavailable", "error": self._unavailable[name]}
        status = self.status.get(channel)
        if status is None:
            return {"enabled": True, "state": "unavailable"}
        info: dict[str, Any] = {"enabled": True, "state": _STATES.get(status.state, status.state)}
        if status.error:
            info["error"] = status.error
        if status.last_ok is not None:
            info["last_ok"] = contract.iso(status.last_ok)
        login = self._login_state() if channel is Channel.SHARING and status.state == "unauthenticated" else None
        if login is not None:
            info["state"], detail = login
            info.pop("error", None)
            if detail:
                info["error"] = detail
        return info

    def publish_info(self) -> None:
        """Publish ``bridge/info`` (contract §1.1) when something other than a timestamp changed."""
        reason = self.closed_reason()
        designated = self._designated()
        self.gate.designated = designated
        writes: dict[str, Any] = {
            "enabled": self.settings.writes_enabled,
            "keys": [contract.OPEN_SET_KEY],
            "device": self.names.get(designated) if designated else None,
        }
        if reason:
            writes["closed"] = reason
        info = {
            "version": __version__,
            "contract": CONTRACT_VERSION,
            "library": {"name": "pytuya-pilot-wire", "version": library_version},
            "read_only": reason is not None,
            "writes": writes,
            "poll_interval": self.settings.poll_interval,
            "channels": {channel.value: self._channel_info(channel) for channel in Channel},
        }
        assert set(info["channels"]) == set(CHANNELS)
        stable = json.loads(json.dumps(info))
        for channel in stable["channels"].values():
            channel.pop("last_ok", None)
        signature = json.dumps(stable, sort_keys=True)
        if signature != self._info:
            if (reason is None) != self._open:
                self._open = reason is None
                log.warning("the write of %s is now %s", contract.OPEN_SET_KEY, "OPEN" if self._open else "closed")
            self._info = signature
            self._publish(self._topic("bridge", "info"), info)

    # -- discovery --------------------------------------------------------------------------

    def discover(self) -> bool:
        """List the thermostats and publish ``bridge/devices``. False when nothing could be listed.

        The sharing API lists them when it is connected; until then the local keys file does. A
        list is never replaced by an empty one because a channel failed.
        """
        found: list[Device] | None = None
        if self.sharing is not None and self._usable(Channel.SHARING):
            found = self._attempt(Channel.SHARING, self.config.sharing_interval, self.sharing.discover)
        local = {d.dev_id: d for d in (self._local.devices if self._local else ())}
        sources: dict[str, str] = {}
        devices: dict[str, Device] = {}
        if found is not None:
            for device in found:
                if not device.supported:
                    continue
                known = local.get(device.dev_id)
                if known is not None:
                    device = dataclasses.replace(
                        device,
                        gateway_id=device.gateway_id or known.gateway_id,
                        node_id=device.node_id or known.node_id,
                    )
                devices[device.dev_id] = device
                sources[device.dev_id] = "account"
        elif not self.devices and self._local is not None:
            devices = local
            sources = {dev_id: self._local.product_id_source(dev_id) for dev_id in local}
        if not devices:
            return False
        for dev_id in set(self.devices) - set(devices):
            self._set_availability(dev_id, False)
            self.states.pop(dev_id, None)
        self.devices, self._sources = devices, sources
        self.names = dialect.friendly_names(list(devices.values()))
        for dev_id, device in devices.items():
            self.states.setdefault(dev_id, DeviceState(device)).device = device
        self._descriptors = [
            contract.descriptor(device, self.names[dev_id], sources[dev_id])
            for dev_id, device in sorted(devices.items(), key=lambda item: self.names[item[0]])
        ]
        self._publish(self._topic("bridge", "devices"), self._descriptors)
        return True

    def resume_sharing(self, channel: Any) -> None:
        """Put a logged-in sharing channel in service and list the devices again at once."""
        self.replace_channel(Channel.SHARING, channel)
        self._due.pop("discovery", None)

    # -- reads ------------------------------------------------------------------------------

    def _apply(self, channel: Channel, readings: list[Reading] | None) -> set[str]:
        """Merge readings. While the sharing API is not connected, the LAN stands in for the state.

        Connected, each channel is trusted for the keys the library's router proves for it. The
        LAN alone published every property in versions 1 and 2 of the contract, checked against
        the other readers (M3); it does so again whenever the sharing channel is away.
        """
        if channel is not Channel.LAN or self._sharing_connected():
            return super()._apply(channel, readings)
        applied: set[str] = set()
        for reading in readings or []:
            state = self.states.get(reading.dev_id)
            if state is None or not reading.values:
                continue
            state.apply(reading)
            self._last_read[reading.dev_id] = reading.at
            applied.add(reading.dev_id)
        return applied

    def publish_state(self, dev_id: str) -> None:
        encoded = dialect.encode_state(self.states[dev_id], self._spec(dev_id))
        self._publish(self._topic(self.names[dev_id]), contract.state(encoded, self._last_read.get(dev_id)))

    def _refresh_availability(self) -> None:
        """Online: a reading younger than ``stale_after`` and, when the sharing API is connected,
        the account saying the device is online."""
        now = self._clock()
        connected = self._sharing_connected()
        for dev_id, state in self.states.items():
            last = self._last_read.get(dev_id)
            fresh = last is not None and now - last <= self.config.stale_after
            online = state.online is True if connected else state.online is not False
            self._set_availability(dev_id, fresh and online)

    def republish(self) -> None:
        """Everything retained, again: after a (re)connection to the broker."""
        self.announce()
        self._info = None
        self.publish_info()
        if self._descriptors:
            self._publish(self._topic("bridge", "devices"), self._descriptors)
        for dev_id, state in self.states.items():
            if state.values:
                self.publish_state(dev_id)
        for dev_id, online in self._availability.items():
            if dev_id in self.names:
                self._publish(self._topic(self.names[dev_id], "availability"), dialect.encode_availability(online))

    # -- commands ---------------------------------------------------------------------------

    def _refusal(self, name: str, raw: str) -> tuple[str, list[str], Any] | None:
        """Why a ``/set`` does not reach the write gate: ``(error, keys, transaction)``, or None."""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            return f"payload is not a JSON object; {NOTHING_SENT}", [], None
        transaction = payload.get("transaction")
        keys = sorted(k for k in payload if k != "transaction")
        dev_id = next((d for d, known in self.names.items() if known == name), None)
        if dev_id is None:
            return f"unknown device; {NOTHING_SENT}", keys, transaction
        reason = self.closed_reason()
        if reason is not None:
            return f"read-only bridge: {reason}; {NOTHING_SENT}", keys, transaction
        if keys != [contract.OPEN_SET_KEY]:
            return f"only {contract.OPEN_SET_KEY} can be written in this version; {NOTHING_SENT}", keys, transaction
        if dev_id != self._designated():
            return f"writes are open for the designated thermostat only; {NOTHING_SENT}", keys, transaction
        held = self.states.get(dev_id)
        mode = held.values.get("mode") if held is not None else None
        mode_value = getattr(mode, "value", None)
        if mode_value is None:
            return f"the mode of the thermostat is not known yet; {NOTHING_SENT}", keys, transaction
        if mode_value != dps.MODE_MANUAL:
            why = setpoint_mode_refusal(mode_value).removeprefix("setpoint: ")
            return f"{contract.OPEN_SET_KEY}: {why}; {NOTHING_SENT}", keys, transaction
        return None

    def handle_set(self, name: str, raw: str) -> None:
        refusal = self._refusal(name, raw)
        if refusal is not None:
            error, keys, transaction = refusal
            log.info("command refused: %s", error.split(";")[0])
            self._respond("device/set", data={"device": name, "keys": keys}, error=error, transaction=transaction)
            return
        self.gate.designated = self._designated()
        log.warning("WRITE: %s asked for the designated thermostat", contract.OPEN_SET_KEY)
        try:
            super().handle_set(name, raw)
        except Exception as err:  # the answer must come, whatever broke
            log.error("write failed unexpectedly (%s)", type(err).__name__)
            self._respond(
                "device/set",
                data={"device": name, "keys": [contract.OPEN_SET_KEY]},
                error=f"internal error ({type(err).__name__}); read the device before trusting its state",
                transaction=json.loads(raw).get("transaction"),
            )
        self.publish_info()

    def _respond(self, action: str, **fields: Any) -> None:
        if action == "device/set" and "results" in (fields.get("data") or {}):  # an answer of the gate
            log.warning("WRITE answered: %s", "refused or not confirmed" if fields.get("error") else "confirmed")
        super()._respond(action, **fields)

    def handle_request(self, action: str, raw: str) -> None:
        """Every ``bridge/request/<action>`` is refused in this version."""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = None
        transaction = payload.get("transaction") if isinstance(payload, dict) else None
        self._respond(action, error=REQUEST_ERROR, transaction=transaction)
