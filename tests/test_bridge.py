"""Synthetic data only: no real device id, key, address or reading."""

from __future__ import annotations

import json

import pytest

from tuyapilot2mqtt import CONTRACT_VERSION, cli, config, devices, dialect
from tuyapilot2mqtt.bridge import MISSES_BEFORE_OFFLINE, Bridge
from tuyapilot2mqtt.lan import LanChannel

ENTRIES = [
    {"id": "gw", "key": "k" * 16, "name": "Gateway", "category": "wg2", "sub": False},
    {"id": "b", "key": "", "name": "Room/B #1", "category": "wk", "sub": True, "node_id": "nb"},
    {"id": "a", "key": "", "name": "Room A", "category": "wk", "sub": True, "node_id": "na", "product_id": "fu1bfwza"},
    {"id": "x", "key": "", "name": "Plug", "category": "cz", "sub": True, "node_id": "nx"},
]


class Clock:
    now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def make(readings: dict, channels: tuple[str, ...] = ("lan",)) -> tuple[Bridge, Clock]:
    clock = Clock()
    gateway, thermostats = devices.parse(ENTRIES, "fu1bfwza")
    lan = LanChannel(gateway, thermostats, lambda _g, _t: readings, clock)
    return Bridge(config.Settings(channels=channels), thermostats, lan, clock), clock


def by_topic(messages: list) -> dict[str, object]:
    return {m.topic: json.loads(m.payload) for m in messages}


def test_devices_from_keys_file() -> None:
    gateway, thermostats = devices.parse(ENTRIES, "fu1bfwza")
    assert gateway.dev_id == "gw"
    assert "k" not in repr(gateway).replace("key", "")
    assert [t.friendly_name for t in thermostats] == ["Room A", "Room B 1"]
    assert [t.product_id_source for t in thermostats] == ["file", "config"]
    descriptor = thermostats[0].descriptor()
    assert descriptor["supported"] is True
    assert descriptor["tuya"] == {
        "dev_id": "a",
        "product_id": "fu1bfwza",
        "product_id_source": "file",
        "category": "wk",
        "gateway": "gw",
    }


def test_friendly_names_are_unique_and_topic_safe() -> None:
    assert devices.friendly_names(["A", "A", "a/b+c#", ""]) == ["A", "A 2", "a b c", "thermostat 4"]


def test_unknown_product_is_listed_unsupported_and_never_published() -> None:
    _, thermostats = devices.parse(ENTRIES, "otherproduct")
    unsupported = next(t for t in thermostats if t.dev_id == "b")
    assert unsupported.descriptor()["supported"] is False
    assert unsupported.descriptor()["definition"] is None


def test_keys_file_needs_exactly_one_gateway() -> None:
    with pytest.raises(devices.DevicesError):
        devices.parse(ENTRIES[1:], "fu1bfwza")


def test_state_holds_only_what_was_read() -> None:
    bridge, _ = make({"na": {"16": 195, "124": "closed"}, "nb": None})
    messages = by_topic(bridge.poll())
    state = messages["tuyapilot2mqtt/Room A"]
    assert {k for k in state if k not in ("tuya", "last_seen")} == {"local_temperature", "running_state", "power"}
    assert state["local_temperature"] == 19.5
    assert state["running_state"] == "idle" and state["power"] == 0.0
    assert set(state["tuya"]["read"]) == {"local_temperature", "running_state", "power"}
    assert state["tuya"]["read"]["power"] == {"channel": "lan", "at": "2027-01-15T08:00:00Z"}
    assert "mode" not in state["tuya"]
    assert "tuyapilot2mqtt/Room B 1" not in messages
    assert messages["tuyapilot2mqtt/Room A/availability"] == {"state": "online"}
    assert messages["tuyapilot2mqtt/Room B 1/availability"] == {"state": "offline"}


def test_full_reading_while_heating() -> None:
    dps = {"1": True, "2": "program", "16": 182, "17": "open", "20": 5, "24": 120, "50": 205,
           "101": 2305, "102": 4300, "107": 1234, "116": 70, "117": 160, "124": "opened"}  # fmt: skip
    bridge, _ = make({"na": dps})
    state = by_topic(bridge.poll())["tuyapilot2mqtt/Room A"]
    assert state["system_mode"] == "heat" and state["preset"] == "schedule"
    assert state["current_heating_setpoint"] == 20.5 and state["holiday_temperature"] == 12.0
    assert state["frost_protection_temperature"] == 7.0 and state["eco_temperature"] == 16.0
    assert state["window_open"] is True and state["fault"] == ["bit_0", "bit_2"]
    assert state["running_state"] == "heat"
    assert state["voltage"] == 230.5 and state["current"] == 4.3 and state["power"] == 991.1
    assert state["energy"] == 123.4
    assert state["tuya"]["mode"] == "program"
    for absent in ("window_detection", "child_lock", "local_temperature_calibration"):
        assert absent not in state


def test_undeclared_mode_gives_no_preset_and_stays_readable() -> None:
    state, _ = dialect.to_state({"mode": "something_else", "switch": False})
    assert "preset" not in state and state["system_mode"] == "off"
    bridge, _ = make({"na": {"2": "something_else"}})
    assert by_topic(bridge.poll())["tuyapilot2mqtt/Room A"]["tuya"]["mode"] == "something_else"


def test_voltage_and_current_only_while_heating() -> None:
    state, _ = dialect.to_state({"voltage": 230.0, "current_ma": 0, "heating": False, "power": 0.0})
    assert "voltage" not in state and "current" not in state and state["power"] == 0.0


def test_partial_reads_are_merged_with_the_time_each_was_read() -> None:
    readings: dict = {"na": {"16": 195, "50": 200}}
    bridge, clock = make(readings)
    bridge.poll()
    clock.now += 60
    readings["na"] = {"16": 196}
    state = by_topic(bridge.poll())["tuyapilot2mqtt/Room A"]
    assert state["local_temperature"] == 19.6 and state["current_heating_setpoint"] == 20.0
    assert state["tuya"]["read"]["local_temperature"]["at"] == "2027-01-15T08:01:00Z"
    assert state["tuya"]["read"]["current_heating_setpoint"]["at"] == "2027-01-15T08:00:00Z"
    assert state["last_seen"] == "2027-01-15T08:01:00Z"


def test_offline_after_consecutive_misses_and_state_not_republished() -> None:
    readings: dict = {"na": {"16": 195}}
    bridge, _ = make(readings)
    bridge.poll()
    readings["na"] = None
    for miss in range(1, MISSES_BEFORE_OFFLINE + 1):
        messages = by_topic(bridge.poll())
        assert "tuyapilot2mqtt/Room A" not in messages
        expected = "offline" if miss == MISSES_BEFORE_OFFLINE else "online"
        assert messages["tuyapilot2mqtt/Room A/availability"] == {"state": expected}


def test_reader_failure_is_survived_and_reported() -> None:
    def broken(_g: object, _t: object) -> dict:
        raise OSError("unreachable")

    gateway, thermostats = devices.parse(ENTRIES, "fu1bfwza")
    bridge = Bridge(config.Settings(), thermostats, LanChannel(gateway, thermostats, broken))
    info = by_topic(bridge.poll())["tuyapilot2mqtt/bridge/info"]
    assert info["channels"]["lan"]["state"] == "offline" and info["channels"]["lan"]["error"] == "OSError"


def test_bridge_topics() -> None:
    bridge, _ = make({}, channels=("lan", "sharing"))
    messages = bridge.startup()
    assert all(m.retain for m in messages)
    topics = by_topic(messages)
    assert topics["tuyapilot2mqtt/bridge/state"] == {"state": "online"}
    info = topics["tuyapilot2mqtt/bridge/info"]
    assert info["contract"] == CONTRACT_VERSION and info["read_only"] is True
    assert info["channels"]["lan"]["state"] == "starting"
    assert info["channels"]["sharing"] == {"enabled": True, "state": "unsupported"}
    assert info["channels"]["developer"] == {"enabled": False}
    assert [d["friendly_name"] for d in topics["tuyapilot2mqtt/bridge/devices"]] == ["Room A", "Room B 1"]
    assert topics["tuyapilot2mqtt/Room A/availability"] == {"state": "offline"}
    assert bridge.subscriptions == ["tuyapilot2mqtt/+/set", "tuyapilot2mqtt/bridge/request/#"]


def test_set_is_refused() -> None:
    bridge, _ = make({})
    (answer,) = bridge.handle("tuyapilot2mqtt/Room A/set", b'{"current_heating_setpoint": 19.5, "transaction": 7}')
    assert answer.topic == "tuyapilot2mqtt/bridge/response/device/set" and answer.retain is False
    body = json.loads(answer.payload)
    assert body["status"] == "error" and "read-only" in body["error"] and body["transaction"] == 7
    assert body["data"] == {"device": "Room A", "keys": ["current_heating_setpoint"]}
    (unknown,) = bridge.handle("tuyapilot2mqtt/Nowhere/set", b"not json")
    assert json.loads(unknown.payload)["error"].startswith("unknown device")


def test_requests_are_refused() -> None:
    bridge, _ = make({})
    (answer,) = bridge.handle("tuyapilot2mqtt/bridge/request/schedule/set", b'{"device": "Room A", "days": {}}')
    assert answer.topic == "tuyapilot2mqtt/bridge/response/schedule/set"
    assert json.loads(answer.payload)["status"] == "error"
    assert bridge.handle("other/Room A/set", b"{}") == []
    assert bridge.handle("tuyapilot2mqtt/Room A", b"{}") == []


def test_settings_from_file_and_environment(tmp_path) -> None:  # noqa: ANN001
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"base_topic": "t", "mqtt_host": "", "mqtt_port": 1883, "poll_interval": 120,
                                   "channels": ["lan", "sharing"], "mqtt_tls": False}))  # fmt: skip
    env = {"TUYAPILOT_MQTT_HOST": "broker", "TUYAPILOT_MQTT_PORT": "8883", "TUYAPILOT_MQTT_PASSWORD": "s3"}
    s = config.load(options, env)
    assert (s.base_topic, s.mqtt_host, s.mqtt_port, s.poll_interval) == ("t", "broker", 8883, 120)
    assert s.channels == ("lan", "sharing") and "s3" not in repr(s)
    for bad in ({"poll_interval": 5}, {"channels": ["zigbee"]}, {"base_topic": "a/#"}, {"mqtt_port": "x"}):
        options.write_text(json.dumps(bad))
        with pytest.raises(config.ConfigError):
            config.load(options, {})


def test_dry_run_prints_the_contract_topics(capsys) -> None:  # noqa: ANN001
    assert cli.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "tuyapilot2mqtt/bridge/state" in out and "bridge/response/device/set" in out and "dry run OK" in out


def test_lan_channel_without_gateway_address_is_a_config_error() -> None:
    assert cli.main([]) == 2
