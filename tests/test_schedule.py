"""Weekly schedule through the developer channel. Synthetic data only."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pytuya_pilot_wire import dps, schedule
from pytuya_pilot_wire.errors import AuthenticationError, ChannelError
from pytuya_pilot_wire.model import Channel, Device, Reading
from test_bridge import LAN_FULL, LOCAL, A, B, Clock, Lan, Sharing, Transport

from tuyapilot2mqtt import config
from tuyapilot2mqtt.bridge import Bridge

NIGHT = [{"time": "00:00", "temperature": 7.0}]
DAY = [
    {"time": "00:00", "temperature": 7.0},
    {"time": "07:30", "temperature": 19.0},
    {"time": "17:00", "temperature": 7.0},
]


def week_parts() -> dict[str, str]:
    parts: dict[str, str] = {}
    for day in range(1, 8):
        parts.update(schedule.encode_day(day, NIGHT))  # type: ignore[arg-type]
    return parts


class Developer:
    """A made-up developer project: ``issue_schedule_parts`` changes what is read, unless ``deaf``."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.parts = {A: week_parts(), B: week_parts()}
        self.issued: list[tuple[str, dict[str, str]]] = []
        self.calls = 0
        self.deaf = False
        self.failing: Exception | None = None

    def properties(self, dev_id: str) -> dict[str, Any]:
        self.calls += 1
        if self.failing is not None:
            raise self.failing
        return {**self.parts[dev_id], "eco_temp": 160, "frost_temp": 70}

    def read_with_schedule(self, device: Device) -> tuple[Reading, dict[str, Any]]:
        properties = self.properties(device.dev_id)
        values = dps.decode_codes(device.product_id, properties)
        return Reading(device.dev_id, Channel.DEVELOPER, self.clock(), values), schedule.decode_week(
            {k: v for k, v in properties.items() if k.startswith("week_program")}
        )

    def read_schedule(self, dev_id: str) -> dict[str, Any]:
        return schedule.decode_week(self.schedule_parts(dev_id))

    def schedule_parts(self, dev_id: str) -> dict[str, str]:
        return {k: v for k, v in self.properties(dev_id).items() if k.startswith("week_program")}

    def issue_schedule_parts(self, dev_id: str, parts: Mapping[str, str]) -> None:
        self.issued.append((dev_id, dict(parts)))
        if not self.deaf:
            self.parts[dev_id].update(parts)

    def lan_inventory(self) -> Any:  # pragma: no cover - the bridge lists devices itself
        raise AssertionError


def build(*, developer: bool = True, **options: Any) -> tuple[Bridge, Transport, Clock, Developer]:
    clock = Clock()
    transport = Transport()
    fake = Developer(clock)
    channels = ("lan", "sharing") + (("developer",) if developer else ())
    settings = config.Settings(channels=channels, **options)
    bridge = Bridge(
        transport,
        settings,
        sharing=Sharing(clock),
        lan=Lan(clock, LAN_FULL),
        developer=fake if developer else None,
        local=LOCAL,
        clock=clock,
        gate_options={"sleep": lambda seconds: setattr(clock, "now", clock.now + seconds), "monotonic": clock},
    )
    bridge.start()
    bridge.poll()
    return bridge, transport, clock, fake


OPEN = {"writes_enabled": True, "write_devices": ("Room A",)}


def request(bridge: Bridge, action: str, payload: Any) -> None:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    bridge.handle_message(f"tuyapilot2mqtt/bridge/request/{action}", raw)


def test_the_schedule_read_with_the_settings_is_in_the_state() -> None:
    bridge, transport, _, fake = build()
    state = transport.last("Room A")
    assert state["tuya"]["schedule"]["monday"] == NIGHT and len(state["tuya"]["schedule"]) == 7
    assert state["tuya"]["read"]["schedule"] == {"channel": "developer", "at": "2027-01-15T08:00:00Z"}
    assert fake.calls == 2  # one call per thermostat, settings and schedule together


def test_a_schedule_changed_elsewhere_is_published_at_the_next_developer_pass() -> None:
    bridge, transport, clock, fake = build()
    fake.parts[B].update(schedule.encode_day(6, DAY))  # type: ignore[arg-type]
    clock.now += 3601
    bridge.poll()
    assert transport.last("Room B 1")["tuya"]["schedule"]["saturday"] == DAY


def test_an_unreadable_schedule_never_replaces_the_one_held() -> None:
    bridge, transport, clock, fake = build()
    fake.parts[A] = {}
    clock.now += 3601
    bridge.poll()
    assert transport.last("Room A")["tuya"]["schedule"]["monday"] == NIGHT


def test_get_reads_the_schedule_of_any_thermostat() -> None:
    bridge, transport, _, fake = build()  # read only: get is still served
    fake.parts[B].update(schedule.encode_day(1, DAY))  # type: ignore[arg-type]
    request(bridge, "schedule/get", {"device": "Room B 1", "transaction": 3})
    answer = transport.last("bridge/response/schedule/get")
    assert answer["status"] == "ok" and answer["transaction"] == 3
    assert answer["data"]["device"] == "Room B 1" and answer["data"]["schedule"]["monday"] == DAY
    assert transport.last("Room B 1")["tuya"]["schedule"]["monday"] == DAY


def test_get_says_when_a_day_is_missing_or_the_channel_fails() -> None:
    bridge, transport, _, fake = build()
    del fake.parts[A]["week_program_18_3_2"]
    request(bridge, "schedule/get", {"device": "Room A"})
    answer = transport.last("bridge/response/schedule/get")
    assert answer["status"] == "error" and "wednesday" not in answer["data"]["schedule"]
    fake.failing = ChannelError("down")
    request(bridge, "schedule/get", {"device": "Room A"})
    assert transport.last("bridge/response/schedule/get")["error"] == "schedule: down"


def test_set_writes_one_day_read_modify_write_and_answers_the_read_back() -> None:
    bridge, transport, clock, fake = build(**OPEN)
    info = transport.last("bridge/info")
    assert info["writes"]["requests"] == ["schedule/set"] and "requests_closed" not in info["writes"]
    request(bridge, "schedule/set", {"device": "Room A", "days": {"saturday": DAY}, "transaction": "w"})
    answer = transport.last("bridge/response/schedule/set")
    assert answer["status"] == "ok" and answer["transaction"] == "w"
    data = answer["data"]
    assert data["days"] == ["saturday"] and data["confirmed"] is True and data["written"] == 2
    assert data["schedule"]["saturday"] == DAY and data["schedule"]["friday"] == NIGHT
    ((dev_id, parts),) = fake.issued
    assert dev_id == A and sorted(parts) == ["week_program_18_6_2", "week_program_18_6_3"]
    assert transport.last("Room A")["tuya"]["schedule"]["saturday"] == DAY
    # the rate limit counts a schedule write
    request(bridge, "schedule/set", {"device": "Room A", "days": {"sunday": DAY}})
    assert transport.last("bridge/response/schedule/set")["error"].startswith("rate limit")
    clock.now += 11
    request(bridge, "schedule/set", {"device": "Room A", "days": {"saturday": NIGHT}})
    assert transport.last("bridge/response/schedule/set")["status"] == "ok" and len(fake.issued) == 2


def test_set_not_confirmed_is_an_error_with_the_read_back() -> None:
    bridge, transport, _, fake = build(**OPEN)
    fake.deaf = True
    request(bridge, "schedule/set", {"device": "Room A", "days": {"sunday": DAY}})
    answer = transport.last("bridge/response/schedule/set")
    assert answer["status"] == "error" and answer["error"].startswith("schedule: not confirmed")
    assert answer["data"]["confirmed"] is False and answer["data"]["schedule"]["sunday"] == NIGHT


def test_set_refusals_send_nothing() -> None:
    bridge, transport, _, fake = build(**OPEN)
    cases = [
        ({"device": "Room B 1", "days": {"monday": DAY}}, "writes are open for the designated thermostats only"),
        ({"device": "Nowhere", "days": {"monday": DAY}}, "unknown device"),
        ({"device": "Room A"}, "'days' must be an object holding at least one day"),
        ({"device": "Room A", "days": {"funday": DAY}}, "unknown day 'funday' (monday to sunday)"),
        ("[]", "payload is not a JSON object"),
    ]
    for payload, error in cases:
        request(bridge, "schedule/set", payload)
        assert transport.last("bridge/response/schedule/set")["error"] == f"{error}; nothing was sent to the device"
    # refused by the library's gate: a day that is not described in full
    request(bridge, "schedule/set", {"device": "Room A", "days": {"monday": [{"time": "06:00", "temperature": 19}]}})
    assert "first transition of a day must be at 00:00" in transport.last("bridge/response/schedule/set")["error"]
    request(bridge, "schedule/set", {"device": "Room A", "days": {"monday": [{"time": "00:00", "temperature": 35}]}})
    assert "out of bounds" in transport.last("bridge/response/schedule/set")["error"]
    assert fake.issued == []


def test_set_is_closed_until_writes_and_the_developer_channel_allow_it() -> None:
    _, transport, _, _ = build()
    assert transport.last("bridge/info")["writes"]["requests_closed"] == "writes are disabled in the options"
    _, transport, _, _ = build(writes_enabled=True)
    assert transport.last("bridge/info")["writes"]["requests_closed"] == "no thermostat is designated in the options"
    _, transport, _, _ = build(writes_enabled=True, write_devices=("Elsewhere",))
    info = transport.last("bridge/info")
    assert info["writes"]["requests_closed"] == "none of the designated thermostats is among the devices"
    bridge, transport, clock, fake = build(**OPEN)
    fake.failing = AuthenticationError("refused")
    clock.now += 3601
    bridge.poll()
    assert transport.last("bridge/info")["writes"]["requests_closed"] == "the developer project refused the credentials"
    request(bridge, "schedule/set", {"device": "Room A", "days": {"monday": DAY}})
    assert transport.last("bridge/response/schedule/set")["error"].startswith("schedule read only: the developer")
    fake.failing = ChannelError("down")
    bridge.replace_channel(Channel.DEVELOPER, fake)
    clock.now += 3601
    bridge.poll()
    assert transport.last("bridge/info")["writes"]["requests_closed"] == "the developer channel is not connected"


def test_developer_enabled_without_credentials() -> None:
    clock = Clock()
    transport = Transport()
    settings = config.Settings(channels=("lan", "developer"), **OPEN)
    bridge = Bridge(transport, settings, lan=Lan(clock, LAN_FULL), local=LOCAL, clock=clock)
    bridge.start()
    bridge.poll()
    info = transport.last("bridge/info")
    assert info["writes"]["requests_closed"] == "the developer channel is unavailable (credentials missing)"
    request(bridge, "schedule/get", {"device": "Room A"})
    assert "credentials missing" in transport.last("bridge/response/schedule/get")["error"]


def test_an_unexpected_failure_is_still_answered() -> None:
    bridge, transport, _, fake = build(**OPEN)
    fake.failing = RuntimeError("boom")
    request(bridge, "schedule/get", {"device": "Room A", "transaction": 1})
    answer = transport.last("bridge/response/schedule/get")
    assert answer["error"].startswith("internal error (RuntimeError)") and answer["transaction"] == 1
