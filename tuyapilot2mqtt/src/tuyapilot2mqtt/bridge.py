"""The bridge: the daemon core of ``pytuya-pilot-wire``, publishing this repository's contract.

The read loop, the channel health, the routing, the write gate and the read-back are the
library's (:class:`pytuya_pilot_wire.bridge.Bridge`). What is here is what the contract of this
repository says beyond the library's draft-0 codec, and the policy of this version:

- ``bridge/info``, descriptors and state objects in the shape of ``docs/mqtt-contract.md``;
- the thermostats of the local keys file when the sharing channel has no session yet, and the LAN
  readings standing in for the state while the sharing channel is not connected;
- **writes, one key per command**, on the thermostats the owner designated (``write_devices``),
  when the owner enabled writes and the sharing channel is connected: ``preset`` to ``manual`` or
  ``program``, and ``current_heating_setpoint`` while the thermostat is in ``manual`` mode; then
  each further setting the owner names in ``write_settings`` (``contract.SETTINGS``) once the
  library proves its route; and, for a supervised trial, one candidate setting on one designated
  thermostat (``trial_setting``, ``trial_device``). Everything else on ``/set`` and every
  ``bridge/request/*`` is refused before the library sees it, and refused a second time by the
  gate (which reads the device again just before sending);
- a rate limit per thermostat: one write every 10 s, 30 an hour, counted on what was sent;
- the raw command actually sent to the device is logged (data-point code and raw value, no id).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from pytuya_pilot_wire import __version__ as library_version
from pytuya_pilot_wire import dialect, dps
from pytuya_pilot_wire.bridge import Bridge as LibraryBridge
from pytuya_pilot_wire.bridge import BridgeConfig
from pytuya_pilot_wire.errors import WriteRefused
from pytuya_pilot_wire.gate import PlannedWrite, ScheduleResult, SharingWriter, WriteGate, setpoint_mode_refusal
from pytuya_pilot_wire.model import Channel, Device, DeviceSpec, DeviceState, Reading
from pytuya_pilot_wire.router import RouteState, operation_of

from . import CONTRACT_VERSION, __version__, contract
from .config import CHANNELS, Settings
from .devices import LocalInventory

log = logging.getLogger("tuyapilot2mqtt")

NOTHING_SENT = "nothing was sent to the device"
REQUEST_ERROR = f"request not available in this version; {NOTHING_SENT}"

# Library channel states -> contract states (§1.1).
_STATES = {"idle": "starting", "ok": "online", "error": "offline", "unauthenticated": "unauthenticated"}
# The library's model keys behind the `/set` keys this version can open.
_SET_KEYS = {"setpoint": contract.SETPOINT_KEY, "mode": contract.PRESET_KEY} | {
    setting.model_key: setting.key for setting in contract.SETTINGS.values()
}

# Rate limit per thermostat, on the commands actually sent.
WRITE_MIN_INTERVAL = 10.0
WRITE_HOURLY_MAX = 30


class _LoggedSend:
    """The sharing writer, logging the raw command it sends: data-point code and raw value only."""

    def __init__(self, writer: SharingWriter, on_send: Callable[[str], None]) -> None:
        self._writer = writer
        self._on_send = on_send

    def read(self, dev_ids: list[str]) -> list[Reading]:
        return self._writer.read(dev_ids)

    def send(self, dev_id: str, commands: list[dict[str, Any]]) -> None:
        sent = ", ".join(f"{command.get('code')}={command.get('value')!r}" for command in commands)
        log.warning("WRITE sent to a designated thermostat, once: %s", sent)
        self._on_send(dev_id)  # counted before the call: a send that fails may still have reached it
        self._writer.send(dev_id, commands)


class TrialGate(WriteGate):
    """The library's write gate, narrowed to the writes the options open.

    A write the bridge opens for a designated thermostat passes on to the library's checks
    (route, the device's own bounds, step and modes, ceiling, fresh read, setpoint refused in any
    mode but ``manual``, one send, read-back). Any other key, device or mode, and the weekly
    schedule, stop here. ``allows`` is the bridge's own rule: ``(dev_id, key, preset)`` to a
    refusal, or ``None``.
    """

    designated: frozenset[str] = frozenset()  # Tuya device ids of the designated thermostats
    allows: Callable[[str, str, str | None], str | None] = staticmethod(lambda _d, _k, _p: "closed")

    def __init__(
        self,
        router: Any,
        *,
        sharing: SharingWriter | None = None,
        on_send: Callable[[str], None] = lambda _dev_id: None,
        **options: Any,
    ) -> None:
        self._on_send = on_send
        super().__init__(router, sharing=self._logged(sharing), **options)

    def _logged(self, sharing: SharingWriter | None) -> SharingWriter | None:
        return None if sharing is None else _LoggedSend(sharing, self._on_send)

    def replace(self, *, sharing: SharingWriter | None = None, developer: Any = None) -> None:
        super().replace(sharing=self._logged(sharing), developer=developer)

    def plan(self, device: Device, spec: DeviceSpec | None, key: str, value: Any) -> PlannedWrite:
        if key not in _SET_KEYS:
            raise WriteRefused(f"{key}: cannot be written in this version")
        if device.dev_id not in self.designated:
            raise WriteRefused("writes are open for the designated thermostats only")
        preset = dialect.PRESETS.get(value) if key == "mode" and isinstance(value, str) else None
        refusal = self.allows(device.dev_id, _SET_KEYS[key], preset)
        if refusal is not None:
            raise WriteRefused(refusal)
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
        self._sent_at: dict[str, deque[float]] = {}
        self.gate = TrialGate(
            self.router,
            sharing=sharing,
            developer=None,
            enabled=settings.writes_enabled,
            on_send=self._count_send,
            **(gate_options or {}),
        )
        self.gate.allows = self._gate_allows
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

    def _designated(self) -> list[str]:
        """Tuya device ids of the designated thermostats that are among the known devices, in
        the order of their friendly names."""
        wanted = set(self.settings.write_devices)
        found = [dev_id for dev_id, name in self.names.items() if dev_id in wanted or name in wanted]
        return sorted(found, key=lambda dev_id: self.names[dev_id])

    # -- what the options open ---------------------------------------------------------------

    def settings_state(self) -> tuple[list[contract.Setting], dict[str, str]]:
        """The settings of ``write_settings`` that open (proven by the library), and why the
        others do not."""
        opened: list[contract.Setting] = []
        refused: dict[str, str] = {}
        for name in self.settings.write_settings:
            setting = contract.SETTINGS[name]
            state = self.router.state(dps.PRODUCT_FP_THERMOSTAT, operation_of(setting.model_key), setting.mode)
            if state is RouteState.PROVEN:
                opened.append(setting)
            else:
                refused[name] = f"{state.value} route: opens once the library records its supervised trial"
        return opened, refused

    def trial(self) -> tuple[contract.Setting, str | None, str | None]:
        """The trial of the options: its setting, the device id it opens on, and why it does not.

        Only meaningful when ``trial_setting`` is set. The trial device must be designated.
        """
        setting = contract.SETTINGS[self.settings.trial_setting]
        wanted = self.settings.trial_device
        dev_id = next((d for d in self._designated() if wanted in (d, self.names[d])), None)
        if dev_id is None:
            return setting, None, "the trial thermostat is not among the designated thermostats"
        return setting, dev_id, None

    def _sync_trial(self) -> None:
        """Designate the trial to the library's router (nothing when no trial is set)."""
        trials: dict[str, str] = {}
        if self.settings.trial_setting:
            setting, dev_id, _ = self.trial()
            if dev_id is not None:
                trials[setting.write_name] = dev_id
        self.router.trials = trials

    def open_keys(self) -> tuple[list[str], list[str]]:
        """The `/set` keys and the presets open on every designated thermostat."""
        opened, _ = self.settings_state()
        keys = list(contract.OPEN_SET_KEYS) + [s.key for s in opened if s.key not in contract.OPEN_SET_KEYS]
        presets = list(contract.WRITABLE_PRESETS) + [s.preset for s in opened if s.preset]
        return list(dict.fromkeys(keys)), presets

    def _allows(self, dev_id: str, key: str, preset: str | None, value: Any) -> str | None:
        """Why ``key`` (``preset`` for a mode) is not open on ``dev_id``, or ``None``."""
        keys, presets = self.open_keys()
        if self.settings.trial_setting:
            setting, trial_dev, _ = self.trial()
            if trial_dev == dev_id and setting.key == key and (key != contract.PRESET_KEY or setting.preset == preset):
                return None
        if key not in keys:
            return f"{key} is not open (open: {', '.join(keys)})"
        if key == contract.PRESET_KEY and preset not in presets:
            return f"preset: {value!r} is not open (open: {', '.join(presets)})"
        return None

    def _gate_allows(self, dev_id: str, key: str, preset: str | None) -> str | None:
        return self._allows(dev_id, key, preset, preset)

    def closed_reason(self) -> str | None:
        """Why no write can pass now, or ``None`` when the writes of this version are open."""
        if not self.settings.writes_enabled:
            return "writes are disabled in the options"
        if not self.settings.write_devices:
            return "no thermostat is designated in the options"
        if Channel.SHARING not in self.status:
            return "the sharing channel is not enabled"
        if not self._sharing_connected():
            return "the sharing channel is not connected"
        if not self._designated():
            return "none of the designated thermostats is among the devices"
        return None

    # -- rate limit -------------------------------------------------------------------------

    def _count_send(self, dev_id: str) -> None:
        self._sent_at.setdefault(dev_id, deque()).append(self._clock())

    def _rate_refusal(self, dev_id: str) -> str | None:
        """Why a write to ``dev_id`` would exceed the rate limit now, or ``None``."""
        now = self._clock()
        sent = self._sent_at.get(dev_id)
        if not sent:
            return None
        while sent and now - sent[0] >= 3600:
            sent.popleft()
        if sent and now - sent[-1] < WRITE_MIN_INTERVAL:
            wait = WRITE_MIN_INTERVAL - (now - sent[-1])
        elif len(sent) >= WRITE_HOURLY_MAX:
            wait = 3600 - (now - sent[0])
        else:
            return None
        return (
            f"rate limit: one write every {WRITE_MIN_INTERVAL:g} s and {WRITE_HOURLY_MAX} an hour per"
            f" thermostat; retry in {max(1, round(wait))} s"
        )

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
        self.gate.designated = frozenset(designated)
        self._sync_trial()
        names = [self.names[dev_id] for dev_id in designated]
        keys, presets = self.open_keys()
        opened, refused = self.settings_state()
        writes: dict[str, Any] = {
            "enabled": self.settings.writes_enabled,
            "keys": keys,
            "presets": presets,
            "settings": [s.name for s in opened],
            "devices": names,
            "device": names[0] if names else None,  # contract 4 and earlier; the first of `devices`
        }
        if refused:
            writes["refused"] = refused
        if self.settings.trial_setting:
            setting, trial_dev, why = self.trial()
            trial: dict[str, Any] = {"setting": setting.name, "key": setting.key}
            if setting.preset:
                trial["preset"] = setting.preset
            trial["device"] = self.names[trial_dev] if trial_dev else None
            if why:
                trial["closed"] = why
            writes["trial"] = trial
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
                log.warning(
                    "the writes (%s) are now %s",
                    ", ".join(keys),
                    f"OPEN on {len(names)} thermostat(s)" if self._open else "closed",
                )
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
        open_keys, _ = self.open_keys()
        if self.settings.trial_setting:
            open_keys = list(dict.fromkeys([*open_keys, contract.SETTINGS[self.settings.trial_setting].key]))
        if len(keys) != 1 or keys[0] not in open_keys:
            allowed = " or ".join(open_keys)
            return f"one key per command, {allowed} only, in this version; {NOTHING_SENT}", keys, transaction
        if dev_id not in self._designated():
            return f"writes are open for the designated thermostats only; {NOTHING_SENT}", keys, transaction
        key, value = keys[0], payload[keys[0]]
        preset = value if key == contract.PRESET_KEY and isinstance(value, str) else None
        refusal = self._allows(dev_id, key, preset, value)
        if refusal is not None:
            return f"{refusal}; {NOTHING_SENT}", keys, transaction
        if key != contract.SETPOINT_KEY:
            return self._rate_check(dev_id, keys, transaction)
        held = self.states.get(dev_id)
        mode = held.values.get("mode") if held is not None else None
        mode_value = getattr(mode, "value", None)
        if mode_value is None:
            return f"the mode of the thermostat is not known yet; {NOTHING_SENT}", keys, transaction
        if mode_value != dps.MODE_MANUAL:
            why = setpoint_mode_refusal(mode_value).removeprefix("setpoint: ")
            return f"{contract.SETPOINT_KEY}: {why}; {NOTHING_SENT}", keys, transaction
        return self._rate_check(dev_id, keys, transaction)

    def _rate_check(self, dev_id: str, keys: list[str], transaction: Any) -> tuple[str, list[str], Any] | None:
        limited = self._rate_refusal(dev_id)
        return None if limited is None else (f"{limited}; {NOTHING_SENT}", keys, transaction)

    def handle_set(self, name: str, raw: str) -> None:
        refusal = self._refusal(name, raw)
        if refusal is not None:
            error, keys, transaction = refusal
            log.info("command refused: %s", error.split(";")[0])
            self._respond("device/set", data={"device": name, "keys": keys}, error=error, transaction=transaction)
            return
        self.gate.designated = frozenset(self._designated())
        self._sync_trial()
        key = next(k for k in json.loads(raw) if k != "transaction")
        log.warning("WRITE: %s asked for a designated thermostat", key)
        try:
            super().handle_set(name, raw)
        except Exception as err:  # the answer must come, whatever broke
            log.error("write failed unexpectedly (%s)", type(err).__name__)
            self._respond(
                "device/set",
                data={"device": name, "keys": [key]},
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
