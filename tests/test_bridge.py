"""Synthetic data only: no real device id, key, address or reading."""

from __future__ import annotations

import json
import logging
import stat
from typing import Any

import pytest
from pytuya_pilot_wire import dps, router
from pytuya_pilot_wire.errors import ChannelError, WriteRefused
from pytuya_pilot_wire.model import Channel, Device, DeviceSpec, Reading

from tuyapilot2mqtt import CONTRACT_VERSION, __version__, cli, config, contract, devices, session
from tuyapilot2mqtt.bridge import Bridge, TrialGate

ENTRIES = [
    {"id": "gateway1", "key": "k" * 16, "name": "Gateway", "category": "wg2", "sub": False},
    {"id": "deviceb", "name": "Room/B #1", "category": "wk", "sub": True, "node_id": "A4C1380000000ABC"},
    {"id": "devicea", "name": "Room A", "category": "wk", "sub": True, "node_id": "na", "product_id": "fu1bfwza"},
    {"id": "devicex", "name": "Plug", "category": "cz", "sub": True, "node_id": "nx"},
    {"id": "devicey", "name": "Other", "category": "wk", "sub": True, "node_id": "ny", "product_id": "unknown0"},
]
LOCAL = devices.parse(ENTRIES, "fu1bfwza")
A, B = "devicea", "deviceb"


class Clock:
    now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any, bool]] = []

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        self.sent.append((topic, json.loads(payload), retain))

    def subscribe(self, topic: str, callback: Any) -> None:
        pass

    def last(self, topic: str) -> Any:
        return next(payload for sent, payload, _ in reversed(self.sent) if sent == f"tuyapilot2mqtt/{topic}")

    def all(self, topic: str) -> list[Any]:
        return [payload for sent, payload, _ in self.sent if sent == f"tuyapilot2mqtt/{topic}"]


class Lan:
    def __init__(self, clock: Clock, values: dict[str, dict[str, Any]]) -> None:
        self.clock, self.values = clock, values

    def read(self, listed: Any) -> list[Reading]:
        return [
            Reading(d.dev_id, Channel.LAN, self.clock(), self.values[d.dev_id])
            for d in listed
            if self.values.get(d.dev_id)
        ]


class Sharing:
    """A made-up account: ``send`` changes what ``read`` returns, unless ``deaf``."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.sent: list[tuple[str, list[dict[str, Any]]]] = []
        self.deaf = False
        self.failing = False
        self.status = {
            A: {"switch": True, "mode": "manual", "temperature": 19.5, "setpoint": 20.0, "fault": 0},
            B: {"switch": True, "mode": "program", "temperature": 18.0, "setpoint": 9.0, "fault": 2},
        }

    def discover(self) -> list[Device]:
        if self.failing:
            raise ChannelError("down")
        return [
            Device(A, "Room A", "fu1bfwza", "wk", None, "na", True),
            Device(B, "Room/B #1", "fu1bfwza", "wk", None, "A4C1380000000ABC", True),
            Device("gateway1", "Gateway", "gatewayp", "wg2", None, None, True),
        ]

    def spec(self, dev_id: str) -> DeviceSpec:
        return DeviceSpec.from_instruction_set(
            "fu1bfwza",
            [
                {"code": "temp_set", "type": "Integer", "values": {"min": 50, "max": 400, "step": 5}},
                {"code": "switch", "type": "Boolean", "values": {}},
                {"code": "mode", "type": "Enum", "values": {"range": ["eco", "holiday", "program", "manual"]}},
                {"code": "child_lock", "type": "Boolean", "values": {}},
                {"code": "window_check", "type": "Boolean", "values": {}},
                {"code": "temp_correction", "type": "Integer", "values": {"min": -9, "max": 9, "step": 1}},
                {"code": "holiday_temp_set", "type": "Integer", "values": {"min": 50, "max": 400, "step": 5}},
            ],
            [{"code": "fault", "type": "Bitmap", "values": {"label": ["e1", "e2", "e3"]}}],
        )

    def read(self, dev_ids: list[str]) -> list[Reading]:
        if self.failing:
            raise ChannelError("down")
        return [Reading(d, Channel.SHARING, self.clock(), dict(self.status[d]), online=True) for d in dev_ids]

    def send(self, dev_id: str, commands: list[dict[str, Any]]) -> None:
        self.sent.append((dev_id, commands))
        if not self.deaf:
            command = commands[0]
            point = next(p for p in dps.table("fu1bfwza") if p.code == command["code"])
            raw = command["value"]
            self.status[dev_id][point.key] = raw / point.divisor if point.divisor != 1 else raw


LAN_FULL = {
    A: {"switch": True, "mode": "manual", "temperature": 19.4, "setpoint": 20.0, "energy": 12.3, "heating": False,
        "power": 0.0, "frost_setpoint": 7.0, "eco_setpoint": 16.0},
    B: {"temperature": 18.1, "mode": "hors", "voltage": 230.0, "current_ma": 4000, "heating": True, "power": 920.0},
}  # fmt: skip


def make(*, sharing: Any = None, with_lan: bool = True, **options: Any) -> tuple[Bridge, Transport, Clock, Any]:
    clock = Clock()
    transport = Transport()
    channels = (("lan",) if with_lan else ()) + (("sharing",) if sharing is not None else ())
    if sharing == "account":
        sharing = Sharing(clock)
    settings = config.Settings(channels=channels, **options)
    bridge = Bridge(
        transport,
        settings,
        sharing=sharing,
        lan=Lan(clock, LAN_FULL) if with_lan else None,
        local=LOCAL if with_lan else None,
        clock=clock,
        gate_options={"sleep": lambda _s: None, "monotonic": clock},
    )
    bridge.start()
    bridge.poll()
    return bridge, transport, clock, sharing


def set_(bridge: Bridge, name: str, payload: Any) -> None:
    bridge.handle_message(f"tuyapilot2mqtt/{name}/set", payload if isinstance(payload, str) else json.dumps(payload))


# --- keys file --------------------------------------------------------------------------------


def test_keys_file_gives_the_gateway_and_the_supported_thermostats() -> None:
    assert LOCAL.gateway_id == "gateway1"
    assert "kkkk" not in repr(LOCAL)
    assert [d.dev_id for d in LOCAL.devices] == [B, A]  # the plug and the unknown product are left out
    assert LOCAL.product_id_source(A) == "file" and LOCAL.product_id_source(B) == "config"
    with pytest.raises(devices.DevicesError, match="exactly one gateway"):
        devices.parse(ENTRIES[1:], "fu1bfwza")
    with pytest.raises(devices.DevicesError, match="JSON list"):
        devices.parse({}, "fu1bfwza")


def test_keys_file_errors_name_no_path(tmp_path: Any) -> None:
    with pytest.raises(devices.DevicesError, match="not found"):
        devices.load(tmp_path / "missing.json", "fu1bfwza")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(devices.DevicesError, match="not JSON"):
        devices.load(bad, "fu1bfwza")


# --- LAN alone: what versions 1 and 2 published -------------------------------------------------


def test_lan_alone_publishes_every_property_it_read() -> None:
    bridge, transport, _, _ = make()
    assert [d["friendly_name"] for d in transport.last("bridge/devices")] == ["Room A", "Room B 1"]
    a, b = transport.last("bridge/devices")
    assert "ieee_address" not in a and b["ieee_address"] == "0xa4c1380000000abc"
    assert a["definition"] == {"vendor": "Tuya", "model": "TS0601_fu1bfwza", "description": "Pilot-wire thermostat"}
    assert a["tuya"] == {
        "dev_id": A, "product_id": "fu1bfwza", "product_id_source": "file", "category": "wk", "gateway": "gateway1",
    }  # fmt: skip
    assert b["tuya"]["product_id_source"] == "config"

    state = transport.last("Room A")
    assert state["local_temperature"] == 19.4 and state["current_heating_setpoint"] == 20.0
    assert state["system_mode"] == "heat" and state["preset"] == "manual" and state["running_state"] == "idle"
    assert state["antifrost_temperature"] == 7.0 and "frost_protection_temperature" not in state
    assert state["last_seen"] == "2027-01-15T08:00:00Z"
    assert state["tuya"]["read"]["antifrost_temperature"] == {"channel": "lan", "at": "2027-01-15T08:00:00Z"}
    assert "sources" not in state["tuya"]

    other = transport.last("Room B 1")
    assert other["preset"] == "antifrost" and other["tuya"]["mode"] == "hors"
    assert other["power"] == 920.0 and other["current"] == 4.0
    assert "system_mode" not in other  # not read: absent, never guessed

    assert transport.last("Room A/availability") == {"state": "online"}
    info = transport.last("bridge/info")
    assert info["version"] == __version__ and info["contract"] == CONTRACT_VERSION == 7
    assert info["read_only"] is True
    assert info["writes"] == {
        "enabled": False, "keys": ["current_heating_setpoint", "preset"], "presets": ["manual", "program"],
        "settings": [], "devices": [], "device": None, "closed": "writes are disabled in the options",
    }  # fmt: skip
    assert info["channels"]["lan"]["state"] == "online"
    assert info["channels"]["sharing"] == info["channels"]["developer"] == {"enabled": False}


def test_a_device_goes_offline_when_its_last_reading_is_old() -> None:
    bridge, transport, clock, _ = make()
    bridge.lan.values = {A: LAN_FULL[A]}
    clock.now += 700
    bridge.poll()
    assert transport.last("Room A/availability") == {"state": "online"}
    assert transport.last("Room B 1/availability") == {"state": "offline"}
    assert len(transport.all("Room B 1")) == 1  # no state republished for a device that did not answer


def test_republish_sends_everything_retained_again() -> None:
    bridge, transport, _, _ = make()
    transport.sent.clear()
    bridge.republish()
    topics = [topic.removeprefix("tuyapilot2mqtt/") for topic, _, retain in transport.sent if retain]
    assert sorted(topics) == sorted([
        "bridge/state", "bridge/info", "bridge/devices", "Room A", "Room B 1",
        "Room A/availability", "Room B 1/availability",
    ])  # fmt: skip


# --- sharing channel ----------------------------------------------------------------------------


def test_without_a_session_the_lan_keeps_publishing_and_the_login_state_is_said() -> None:
    bridge, transport, clock, _ = make(sharing=session.NoSession())
    assert transport.last("Room A")["local_temperature"] == 19.4  # the LAN stands in
    assert transport.last("Room A/availability") == {"state": "online"}
    info = transport.last("bridge/info")
    assert info["channels"]["sharing"]["state"] == "unauthenticated" and info["read_only"] is True

    bridge._login_state = lambda: (session.WAITING_FOR_SCAN, "scan")
    bridge.publish_info()
    assert transport.last("bridge/info")["channels"]["sharing"] == {
        "enabled": True, "state": "waiting_for_scan", "error": "scan",
    }  # fmt: skip

    # The owner scanned: the account lists the devices, each channel feeds what is proven for it.
    clock.now += 60
    bridge.resume_sharing(Sharing(clock))
    bridge.poll()
    assert [d["friendly_name"] for d in transport.last("bridge/devices")] == ["Room A", "Room B 1"]
    descriptor = transport.last("bridge/devices")[0]
    assert descriptor["tuya"]["product_id_source"] == "account" and descriptor["tuya"]["gateway"] == "gateway1"
    state = transport.last("Room A")
    assert state["local_temperature"] == 19.5 and state["tuya"]["read"]["local_temperature"]["channel"] == "sharing"
    assert state["tuya"]["read"]["energy"]["channel"] == "lan"
    assert transport.last("Room B 1")["fault"] == ["e2"]
    assert transport.last("bridge/info")["channels"]["sharing"]["state"] == "online"


def test_a_failing_account_never_empties_the_device_list() -> None:
    bridge, transport, clock, sharing = make(sharing="account")
    listed = transport.last("bridge/devices")
    sharing.failing = True
    clock.now += 7 * 3600
    bridge.poll()
    assert transport.last("bridge/devices") == listed and len(transport.all("bridge/devices")) == 1
    assert transport.last("bridge/info")["channels"]["sharing"]["state"] == "offline"
    assert transport.last("Room A/availability") == {"state": "online"}  # the LAN still reads it


def test_no_channel_at_all_publishes_no_device() -> None:
    _, transport, _, _ = make(with_lan=False)
    assert transport.all("bridge/devices") == []
    assert transport.last("bridge/info")["read_only"] is True


def test_channel_enabled_but_not_buildable_is_unavailable() -> None:
    bridge = Bridge(
        Transport(), config.Settings(channels=("developer",)), unavailable={"developer": "credentials are empty"}
    )
    bridge.publish_info()
    assert bridge.transport.last("bridge/info")["channels"]["developer"] == {
        "enabled": True, "state": "unavailable", "error": "credentials are empty",
    }  # fmt: skip


# --- the single write ---------------------------------------------------------------------------

OPEN = {"sharing": "account", "writes_enabled": True, "write_devices": ("Room A",)}


def test_closed_by_default_every_set_is_refused_and_nothing_is_sent() -> None:
    bridge, transport, _, sharing = make(sharing="account")
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5, "transaction": "t1"})
    assert transport.last("bridge/response/device/set") == {
        "status": "error",
        "data": {"device": "Room A", "keys": ["current_heating_setpoint"]},
        "error": "read-only bridge: writes are disabled in the options; nothing was sent to the device",
        "transaction": "t1",
    }
    set_(bridge, "Nowhere", {"current_heating_setpoint": 20.5})
    assert transport.last("bridge/response/device/set")["error"].startswith("unknown device")
    set_(bridge, "Room A", "not json")
    assert transport.last("bridge/response/device/set")["error"].startswith("payload is not a JSON object")
    assert sharing.sent == []


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"writes_enabled": True}, "no thermostat is designated in the options"),
        (
            {"writes_enabled": True, "write_devices": ("Room Z",)},
            "none of the designated thermostats is among the devices",
        ),  # noqa: E501
        ({"write_devices": ("Room A",)}, "writes are disabled in the options"),
    ],
)
def test_read_only_until_everything_is_in_place(options: dict[str, Any], reason: str) -> None:
    bridge, transport, _, sharing = make(sharing="account", **options)
    info = transport.last("bridge/info")
    assert info["read_only"] is True and info["writes"]["closed"] == reason
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5})
    assert reason in transport.last("bridge/response/device/set")["error"] and sharing.sent == []


def test_read_only_without_a_connected_sharing_channel() -> None:
    bridge, transport, _, _ = make(writes_enabled=True, write_devices=("Room A",))
    assert transport.last("bridge/info")["writes"]["closed"] == "the sharing channel is not enabled"
    bridge, transport, _, _ = make(sharing=session.NoSession(), writes_enabled=True, write_devices=("Room A",))
    info = transport.last("bridge/info")
    assert info["read_only"] is True and info["writes"]["closed"] == "the sharing channel is not connected"


def test_the_setpoint_of_the_designated_thermostat_is_written_and_read_back() -> None:
    bridge, transport, clock, sharing = make(**OPEN)
    info = transport.last("bridge/info")
    assert info["read_only"] is False and info["writes"] == {
        "enabled": True, "keys": ["current_heating_setpoint", "preset"], "presets": ["manual", "program"],
        "settings": [], "devices": ["Room A"], "device": "Room A",
    }  # fmt: skip
    clock.now += 5
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5, "transaction": "abc"})
    assert sharing.sent == [(A, [{"code": "temp_set", "value": 205}])]
    assert transport.last("bridge/response/device/set") == {
        "status": "ok",
        "data": {
            "device": "Room A",
            "results": [{"property": "current_heating_setpoint", "value": 20.5, "confirmed": True}],
        },
        "transaction": "abc",
    }
    state = transport.last("Room A")
    assert state["current_heating_setpoint"] == 20.5
    assert state["tuya"]["read"]["current_heating_setpoint"] == {"channel": "sharing", "at": "2027-01-15T08:00:05Z"}


def test_designation_by_device_id() -> None:
    bridge, transport, clock, sharing = make(sharing="account", writes_enabled=True, write_devices=(B,))
    assert transport.last("bridge/info")["writes"]["devices"] == ["Room B 1"]
    set_(bridge, "Room B 1", {"current_heating_setpoint": 10})
    assert "'program' mode" in transport.last("bridge/response/device/set")["error"]  # trial 1, refused now
    assert sharing.sent == []
    sharing.status[B]["mode"] = "manual"
    clock.now += 60
    bridge.poll()
    set_(bridge, "Room B 1", {"current_heating_setpoint": 10})
    assert sharing.sent == [(B, [{"code": "temp_set", "value": 100}])]


@pytest.mark.parametrize(
    ("name", "payload", "error"),
    [
        ("Room B 1", {"current_heating_setpoint": 20.5}, "designated thermostats only"),
        ("Room B 1", {"preset": "program"}, "designated thermostats only"),
        ("Room A", {"system_mode": "off"}, "current_heating_setpoint or preset only"),
        ("Room A", {"preset": "manual", "current_heating_setpoint": 20.5}, "one key per command"),
        ("Room A", {"current_heating_setpoint": 20.5, "system_mode": "off"}, "one key per command"),
        ("Room A", {"transaction": "x"}, "one key per command"),
        ("Room A", {"preset": "eco"}, "preset: 'eco' is not open (open: manual, program)"),
        ("Room A", {"preset": "holiday"}, "'holiday' is not open"),
        ("Room A", {"preset": "antifrost"}, "'antifrost' is not open"),
        ("Room A", {"preset": "schedule"}, "'schedule' is not open"),
        ("Room A", {"preset": 3}, "3 is not open"),
        ("Room A", {"child_lock": "LOCK"}, "current_heating_setpoint or preset only"),
        ("Room A", {"current_heating_setpoint": 20.3}, "not on the device's step"),
        ("Room A", {"current_heating_setpoint": 4.5}, "outside the device's bounds"),
        ("Room A", {"current_heating_setpoint": 30.5}, "above the ceiling"),
        ("Room A", {"current_heating_setpoint": "20"}, "expected a number"),
        ("Room A", {"current_heating_setpoint": True}, "expected a number"),
    ],
)
def test_everything_else_is_refused_before_anything_is_sent(name: str, payload: Any, error: str) -> None:
    bridge, transport, _, sharing = make(**OPEN)
    before = transport.last("Room A")
    set_(bridge, name, payload)
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "error" and error in response["error"]
    assert sharing.sent == [] and transport.last("Room A") == before


def test_refused_in_frost_protection_and_when_offline() -> None:
    bridge, transport, _, sharing = make(**OPEN)
    sharing.status[A]["mode"] = "hors"
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5})
    assert "'hors' mode" in transport.last("bridge/response/device/set")["error"]
    sharing.status[A]["mode"] = "manual"
    sharing.read = lambda ids: [Reading(d, Channel.SHARING, 0.0, dict(sharing.status[d]), online=False) for d in ids]
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5})
    assert "offline" in transport.last("bridge/response/device/set")["error"]
    assert sharing.sent == []


@pytest.mark.parametrize("mode", ["program", "eco", "holiday", "hors"])
def test_refused_by_the_bridge_in_any_mode_but_manual(mode: str) -> None:
    bridge, transport, clock, sharing = make(**OPEN)
    sharing.status[A]["mode"] = mode
    clock.now += 60
    bridge.poll()  # the bridge now holds the mode read from the account
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5, "transaction": "m"})
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "error" and response["transaction"] == "m"
    assert f"'{mode}' mode" in response["error"] and "only in 'manual' mode" in response["error"]
    assert response["error"].endswith("nothing was sent to the device")
    assert response["data"] == {"device": "Room A", "keys": ["current_heating_setpoint"]}
    assert sharing.sent == []


def test_program_mode_says_why() -> None:
    bridge, transport, clock, sharing = make(**OPEN)
    sharing.status[A]["mode"] = "program"
    clock.now += 60
    bridge.poll()
    set_(bridge, "Room A", {"current_heating_setpoint": 9.5})
    assert "the running schedule slot overwrites" in transport.last("bridge/response/device/set")["error"]


def test_refused_by_the_gate_when_the_mode_changed_since_the_last_read() -> None:
    bridge, transport, _, sharing = make(**OPEN)
    sharing.status[A]["mode"] = "program"  # not polled yet: the bridge still holds "manual"
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5})
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "error" and "'program' mode" in response["error"]
    assert sharing.sent == []


def test_refused_while_the_mode_is_unknown() -> None:
    bridge, transport, _, sharing = make(**OPEN)
    bridge.states[A].values.pop("mode")
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5})
    assert "mode of the thermostat is not known yet" in transport.last("bridge/response/device/set")["error"]
    assert sharing.sent == []


def test_the_raw_command_sent_is_logged_without_the_device_id(caplog: pytest.LogCaptureFixture) -> None:
    bridge, _, _, sharing = make(**OPEN)
    with caplog.at_level(logging.WARNING, logger="tuyapilot2mqtt"):
        set_(bridge, "Room A", {"current_heating_setpoint": 20.5})
    assert sharing.sent == [(A, [{"code": "temp_set", "value": 205}])]
    assert "WRITE sent to a designated thermostat, once: temp_set=205" in caplog.text
    assert A not in caplog.text


def test_a_write_the_device_did_not_take_is_an_error_and_the_state_is_what_was_read() -> None:
    bridge, transport, clock, sharing = make(**OPEN)
    sharing.deaf = True
    real_read = sharing.read

    def read(ids: list[str]) -> list[Reading]:
        clock.now += 5
        return real_read(ids)

    sharing.read = read
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5, "transaction": "t"})
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "error" and "not confirmed" in response["error"] and response["transaction"] == "t"
    assert response["data"]["results"][0]["confirmed"] is False
    assert len(sharing.sent) == 1  # sent once, never retried
    assert transport.last("Room A")["current_heating_setpoint"] == 20.0  # no optimistic state


def test_an_unexpected_failure_still_answers() -> None:
    bridge, transport, _, sharing = make(**OPEN)

    def boom(dev_id: str, commands: Any) -> None:
        raise RuntimeError("boom")

    sharing.send = boom
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5, "transaction": "t"})
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "error" and "internal error (RuntimeError)" in response["error"]
    assert response["transaction"] == "t"


def test_the_write_closes_when_the_sharing_channel_fails() -> None:
    bridge, transport, clock, sharing = make(**OPEN)
    sharing.failing = True
    clock.now += 60
    bridge.poll()
    info = transport.last("bridge/info")
    assert info["read_only"] is True and info["writes"]["closed"] == "the sharing channel is not connected"


def test_every_request_is_refused() -> None:
    bridge, transport, _, sharing = make(**OPEN)
    for action in ("schedule/get", "schedule/set", "anything"):
        bridge.handle_message(f"tuyapilot2mqtt/bridge/request/{action}", '{"device": "Room A", "transaction": 7}')
        assert transport.last(f"bridge/response/{action}") == {
            "status": "error",
            "data": {},
            "error": "request not available in this version; nothing was sent to the device",
            "transaction": 7,
        }
    bridge.handle_message("tuyapilot2mqtt/bridge/request/schedule/get", "garbage")
    assert transport.last("bridge/response/schedule/get")["status"] == "error"
    assert sharing.sent == []


def test_the_gate_itself_refuses_what_the_bridge_filters() -> None:
    bridge, _, _, sharing = make(**OPEN)
    gate = bridge.gate
    assert isinstance(gate, TrialGate) and gate.designated == {A}
    a, b = bridge.devices[A], bridge.devices[B]
    with pytest.raises(WriteRefused, match="switch: cannot be written"):
        gate.plan(a, sharing.spec(A), "switch", False)
    with pytest.raises(WriteRefused, match="child_lock is not open"):
        gate.plan(a, sharing.spec(A), "child_lock", True)
    with pytest.raises(WriteRefused, match="designated thermostats only"):
        gate.plan(b, sharing.spec(B), "setpoint", 20.0)
    with pytest.raises(WriteRefused, match="'eco' is not open"):
        gate.plan(a, sharing.spec(A), "mode", "eco")
    with pytest.raises(WriteRefused, match="weekly schedule"):
        gate.write_schedule(a, {"monday": []})
    gate.designated = frozenset()
    with pytest.raises(WriteRefused, match="designated thermostats only"):
        gate.plan(a, sharing.spec(A), "setpoint", 20.0)


def test_preset_manual_then_setpoint_then_program_on_two_thermostats() -> None:
    bridge, transport, clock, sharing = make(sharing="account", writes_enabled=True, write_devices=("Room A", B))
    info = transport.last("bridge/info")
    assert info["read_only"] is False and info["writes"]["devices"] == ["Room A", "Room B 1"]
    sequence = [
        ({"preset": "manual"}, {"code": "mode", "value": "manual"}),
        ({"current_heating_setpoint": 9.5}, {"code": "temp_set", "value": 95}),
        ({"current_heating_setpoint": 9.0}, {"code": "temp_set", "value": 90}),
        ({"preset": "program"}, {"code": "mode", "value": "program"}),
    ]
    for payload, raw in sequence:
        clock.now += 11
        set_(bridge, "Room B 1", payload | {"transaction": "s"})
        response = transport.last("bridge/response/device/set")
        assert response["status"] == "ok", response
        [result] = response["data"]["results"]
        assert result["confirmed"] is True and result["property"] == next(iter(payload))
        assert sharing.sent[-1] == (B, [raw])
    state = transport.last("Room B 1")
    assert state["preset"] == "program" and state["tuya"]["mode"] == "program"
    assert state["current_heating_setpoint"] == 9.0
    assert len(sharing.sent) == 4


def test_a_preset_the_device_did_not_take_is_not_published() -> None:
    bridge, transport, clock, sharing = make(**OPEN)
    sharing.deaf = True
    real_read = sharing.read

    def read(ids: list[str]) -> list[Reading]:
        clock.now += 5
        return real_read(ids)

    sharing.read = read
    set_(bridge, "Room A", {"preset": "program"})
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "error" and "not confirmed" in response["error"]
    assert transport.last("Room A")["preset"] == "manual" and len(sharing.sent) == 1


def test_rate_limit_per_thermostat() -> None:
    bridge, transport, clock, sharing = make(sharing="account", writes_enabled=True, write_devices=("Room A", B))
    set_(bridge, "Room A", {"preset": "manual"})
    clock.now += 5
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5, "transaction": "r"})
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "error" and response["transaction"] == "r"
    assert response["error"] == (
        "rate limit: one write every 10 s and 30 an hour per thermostat; retry in 5 s; nothing was sent to the device"
    )
    set_(bridge, "Room B 1", {"preset": "manual"})  # another thermostat has its own count
    assert transport.last("bridge/response/device/set")["status"] == "ok"
    assert len(sharing.sent) == 2
    for _ in range(29):
        clock.now += 10
        set_(bridge, "Room A", {"preset": "manual"})
        assert transport.last("bridge/response/device/set")["status"] == "ok"
    clock.now += 10
    set_(bridge, "Room A", {"preset": "manual"})
    assert "30 an hour" in transport.last("bridge/response/device/set")["error"]
    assert len(sharing.sent) == 31


def test_a_refusal_is_not_counted_by_the_rate_limit() -> None:
    bridge, transport, _, sharing = make(**OPEN)
    set_(bridge, "Room A", {"current_heating_setpoint": 4.5})  # refused by the gate, nothing sent
    set_(bridge, "Room A", {"current_heating_setpoint": 20.5})
    assert transport.last("bridge/response/device/set")["status"] == "ok" and len(sharing.sent) == 1


def test_write_devices_from_the_environment_split_on_commas() -> None:
    settings = config.load(None, {"TUYAPILOT_WRITE_DEVICES": "F-0 Room 2, F-0 Room 1,,F-0 Room 2"})
    assert settings.write_devices == ("F-0 Room 2", "F-0 Room 1")
    assert config.load(None, {}).write_devices == ()


# --- contract names -----------------------------------------------------------------------------


def test_contract_names_over_the_library_codec() -> None:
    encoded = {
        "preset": "schedule",
        "frost_protection_temperature": 7.0,
        "tuya": {"mode": "program", "sources": {"frost_protection_temperature": {"channel": "lan", "at": "x+00:00"}}},
    }
    assert contract.state(encoded, None) == {
        "preset": "program",
        "antifrost_temperature": 7.0,
        "tuya": {"mode": "program", "read": {"antifrost_temperature": {"channel": "lan", "at": "xZ"}}},
    }
    assert contract.state({"preset": "frost_protection"}, 0.0) == {
        "preset": "antifrost", "last_seen": "1970-01-01T00:00:00Z", "tuya": {"read": {}},
    }  # fmt: skip


# --- session and login --------------------------------------------------------------------------


def test_session_file_is_private_and_survives_garbage(tmp_path: Any) -> None:
    store = session.SessionStore(tmp_path / "sub" / "session.json")
    assert store.load() is None
    store.save({"user_code": "u"})
    assert store.load() == {"user_code": "u"}
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    store.path.write_text("[1]", encoding="utf-8")
    assert store.load() is None


def test_qr_code_renders_as_text_lines() -> None:
    lines = session.render_qr("tuyaSmart--qrLogin?token=synthetic")
    assert len(lines) > 10 and len({len(line) for line in lines}) == 1
    assert all(line.startswith("\x1b[30;47m") and line.endswith("\x1b[0m") for line in lines)


def test_login_waits_for_a_user_code() -> None:
    login = session.Login("", request=lambda code: pytest.fail("no request"), poll=lambda qr, code: None)
    assert login.tick() is None
    assert login.state == session.LOGIN_REQUIRED and "sharing_user_code" in login.detail


def test_login_shows_a_code_then_returns_the_session() -> None:
    clock = Clock()
    shown: list[str] = []
    answers: list[Any] = [None, RuntimeError("network"), "the session"]

    def poll(qr: str, code: str) -> Any:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    login = session.Login("code", request=lambda code: f"qr-{len(shown)}", poll=poll, show=shown.append, clock=clock)
    assert login.tick() is None and shown == ["qr-0"] and login.state == session.WAITING_FOR_SCAN
    assert login.tick() is None and len(answers) == 3  # paced: nothing asked before the interval
    for _ in range(2):
        clock.now += session.POLL_INTERVAL
        assert login.tick() is None
    clock.now += session.POLL_INTERVAL
    assert login.tick() == "the session"


def test_login_renews_the_code_then_gives_up() -> None:
    clock = Clock()
    shown: list[str] = []
    login = session.Login("code", request=lambda code: "qr", poll=lambda qr, code: None, show=shown.append, clock=clock)
    for _ in range(session.MAX_QR_CODES + 3):
        login.tick()
        clock.now += session.QR_LIFETIME
    assert [q for q in shown if q] == ["qr"] * session.MAX_QR_CODES and shown[-1] is None
    assert login.state == session.LOGIN_EXPIRED


def test_login_says_when_the_code_request_fails() -> None:
    def refuse(code: str) -> str:
        raise ChannelError("refused")

    login = session.Login("code", request=refuse, poll=lambda qr, code: None, clock=Clock())
    assert login.tick() is None
    assert login.state == session.LOGIN_REQUIRED and "ChannelError" in login.detail


def test_login_page_shows_the_code_only_while_waiting() -> None:
    page = session.LoginPage(0)
    page.update(session.WAITING_FOR_SCAN, "")
    page.show("tuyaSmart--qrLogin?token=synthetic")
    shown = page.render()
    assert "<svg" in shown and "synthetic" not in shown and "<script" not in shown
    page.update("online")
    assert "<svg" not in page.render() and "Connected" in page.render()
    page.update(session.WAITING_FOR_SCAN)
    assert "<svg" not in page.render()  # the old code is not shown again


def test_login_page_answers_the_ingress_proxy_only() -> None:
    import urllib.error
    import urllib.request

    page = session.LoginPage(0, allowed=("192.0.2.1",), host="127.0.0.1")
    page.start()
    try:
        port = page._server.server_address[1]
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
        assert refused.value.code == 403
        page.allowed = ("127.0.0.1",)
        page.update(session.WAITING_FOR_SCAN)
        page.show("tuyaSmart--qrLogin?token=synthetic")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/anything", timeout=5) as answer:
            assert answer.headers["Cache-Control"] == "no-store" and b"<svg" in answer.read()
    finally:
        page.stop()


def test_login_clears_the_shown_code_once_logged_in() -> None:
    clock = Clock()
    shown: list[Any] = []
    login = session.Login(
        "code", request=lambda code: "qr", poll=lambda qr, code: "s", show=shown.append, where="here", clock=clock
    )
    login.tick()
    assert "here" in login.detail
    clock.now += session.POLL_INTERVAL
    assert login.tick() == "s" and shown == ["qr", None]


def test_no_session_refuses_every_call() -> None:
    from pytuya_pilot_wire.errors import AuthenticationError

    stand_in = session.NoSession()
    for call in (stand_in.discover, lambda: stand_in.read(["x"]), lambda: stand_in.send("x", [])):
        with pytest.raises(AuthenticationError):
            call()
    assert stand_in.spec("x") is None


# --- settings and entry point -------------------------------------------------------------------


def test_settings_defaults_are_read_only_and_hide_secrets(tmp_path: Any) -> None:
    defaults = config.Settings()
    assert defaults.writes_enabled is False and defaults.write_devices == () and defaults.channels == ("lan",)
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps({"poll_interval": 120, "channels": ["lan", "sharing"], "mqtt_password": "", "writes_enabled": True}),
        encoding="utf-8",
    )
    env = {"TUYAPILOT_SHARING_USER_CODE": "secretcode", "TUYAPILOT_DEVELOPER_ACCESS_SECRET": "secretkey"}
    settings = config.load(options, env)
    assert settings.poll_interval == 120 and settings.channels == ("lan", "sharing") and settings.writes_enabled
    assert "secretcode" not in repr(settings) and "secretkey" not in repr(settings)
    for bad in ({"poll_interval": 5}, {"channels": ["cloud"]}, {"base_topic": "a/#"}, {"developer_region": "mars"}):
        options.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(config.ConfigError):
            config.load(options, {})
    with pytest.raises(config.ConfigError, match="unusable"):
        config.load(None, {"TUYAPILOT_MQTT_TLS": "maybe"})


def test_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run OK" in out and '"read_only": false' in out and '"status": "ok"' in out


def test_lan_channel_needs_the_gateway_address() -> None:
    assert cli.main(["--options", "/nonexistent"]) == 2


# --- further settings: candidates, trials, opening ---------------------------------------------


def test_a_candidate_setting_named_in_the_options_stays_closed() -> None:
    bridge, transport, _, sharing = make(**OPEN, write_settings=("child_lock", "preset_holiday"))
    writes = transport.last("bridge/info")["writes"]
    assert writes["keys"] == ["current_heating_setpoint", "preset"] and writes["settings"] == []
    assert writes["refused"] == {
        "child_lock": "candidate route: opens once the library records its supervised trial",
        "preset_holiday": "candidate route: opens once the library records its supervised trial",
    }
    set_(bridge, "Room A", {"child_lock": "LOCK"})
    set_(bridge, "Room A", {"preset": "holiday"})
    assert sharing.sent == []


@pytest.mark.parametrize(
    ("setting", "payload", "command"),
    [
        ("preset_holiday", {"preset": "holiday"}, {"code": "mode", "value": "holiday"}),
        ("preset_antifrost", {"preset": "antifrost"}, {"code": "mode", "value": "hors"}),
        ("preset_eco", {"preset": "eco"}, {"code": "mode", "value": "eco"}),
        ("child_lock", {"child_lock": "UNLOCK"}, {"code": "child_lock", "value": False}),
        ("window_detection", {"window_detection": "ON"}, {"code": "window_check", "value": True}),
        ("local_temperature_calibration", {"local_temperature_calibration": -2},
         {"code": "temp_correction", "value": -2}),
        ("holiday_temperature", {"holiday_temperature": 7.5}, {"code": "holiday_temp_set", "value": 75}),
    ],
)  # fmt: skip
def test_a_trial_opens_one_setting_on_one_thermostat(setting: str, payload: Any, command: Any) -> None:
    bridge, transport, clock, sharing = make(
        sharing="account", writes_enabled=True, write_devices=("Room A", "Room B 1"),
        trial_setting=setting, trial_device="Room B 1",
    )  # fmt: skip
    writes = transport.last("bridge/info")["writes"]
    assert writes["trial"]["setting"] == setting and writes["trial"]["device"] == "Room B 1"
    assert "closed" not in writes["trial"] and writes["keys"] == ["current_heating_setpoint", "preset"]
    set_(bridge, "Room A", payload)  # designated, not the trial thermostat
    assert sharing.sent == [] and "is not open" in transport.last("bridge/response/device/set")["error"]
    set_(bridge, "Room B 1", payload)
    assert sharing.sent == [(B, [command])]
    response = transport.last("bridge/response/device/set")
    assert response["status"] == "ok", response
    key, value = next(iter(payload.items()))
    assert transport.last("Room B 1")[key] == value  # read back, not assumed
    clock.now += 11
    if key == "preset":
        others = {"holiday", "antifrost", "eco"} - {value}
        for other in others:
            set_(bridge, "Room B 1", {"preset": other})
            assert "is not open" in transport.last("bridge/response/device/set")["error"]
        set_(bridge, "Room B 1", {"preset": "program"})  # back to the schedule, proven
        assert transport.last("bridge/response/device/set")["status"] == "ok"
    assert bridge.router.trials == {contract.SETTINGS[setting].write_name: B}


def test_a_trial_on_a_thermostat_that_is_not_designated_does_not_open() -> None:
    bridge, transport, _, sharing = make(**OPEN, trial_setting="child_lock", trial_device="Room B 1")
    trial = transport.last("bridge/info")["writes"]["trial"]
    assert trial == {
        "setting": "child_lock", "key": "child_lock", "device": None,
        "closed": "the trial thermostat is not among the designated thermostats",
    }  # fmt: skip
    set_(bridge, "Room B 1", {"child_lock": "LOCK"})
    set_(bridge, "Room A", {"child_lock": "LOCK"})
    assert sharing.sent == [] and bridge.router.trials == {}


def test_a_proven_setting_opens_on_every_designated_thermostat(monkeypatch: pytest.MonkeyPatch) -> None:
    proven = router.Route((Channel.SHARING,), router.RouteState.PROVEN, "synthetic")
    monkeypatch.setitem(router.ROUTES["fu1bfwza"], router.Operation.CHILD_LOCK, proven)
    monkeypatch.setitem(router.MODE_ROUTES["fu1bfwza"], "holiday", proven)
    bridge, transport, clock, sharing = make(
        sharing="account", writes_enabled=True, write_devices=("Room A", "Room B 1"),
        write_settings=("child_lock", "preset_holiday", "window_detection"),
    )  # fmt: skip
    writes = transport.last("bridge/info")["writes"]
    assert writes["keys"] == ["current_heating_setpoint", "preset", "child_lock"]
    assert writes["presets"] == ["manual", "program", "holiday"]
    assert writes["settings"] == ["child_lock", "preset_holiday"]
    assert list(writes["refused"]) == ["window_detection"]
    set_(bridge, "Room A", {"child_lock": "UNLOCK"})
    set_(bridge, "Room B 1", {"preset": "holiday"})
    assert sharing.sent == [
        (A, [{"code": "child_lock", "value": False}]),
        (B, [{"code": "mode", "value": "holiday"}]),
    ]
    set_(bridge, "Room A", {"window_detection": "ON"})
    error = transport.last("bridge/response/device/set")["error"]
    assert "current_heating_setpoint or preset or child_lock only" in error


def test_settings_options_are_checked() -> None:
    with pytest.raises(config.ConfigError, match="unknown setting 'night_mode'"):
        config.validate(config.Settings(write_settings=("night_mode",)))
    with pytest.raises(config.ConfigError, match="trial_setting"):
        config.validate(config.Settings(trial_setting="eco"))
    with pytest.raises(config.ConfigError, match="go together"):
        config.validate(config.Settings(trial_setting="child_lock"))
    settings = config.load(None, {"TUYAPILOT_WRITE_SETTINGS": "child_lock, preset_holiday"})
    assert settings.write_settings == ("child_lock", "preset_holiday")
    assert set(config.SETTING_NAMES) == set(contract.SETTINGS)
