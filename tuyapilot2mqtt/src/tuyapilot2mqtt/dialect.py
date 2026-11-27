"""Decoded reading → Zigbee2MQTT-dialect state (contract §3).

PROVISIONAL: this codec belongs in ``pytuya-pilot-wire`` (milestone M1) and is removed from here
once the library ships it. It takes what ``dps.decode_lan`` returns and nothing else.

Rule: a property appears only when the value it comes from was read. Nothing is defaulted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Raw mode (DP 2) → preset, in the vocabulary Zigbee2MQTT uses for the closest pilot-wire
# thermostat. Only values whose meaning is established. Any other value gives no ``preset``; the
# raw value stays readable in ``tuya.mode``. ``antifrost`` joins the table once the raw value the
# devices report for it is recorded in the library.
PRESETS = {"program": "program", "manual": "manual", "eco": "eco", "holiday": "holiday"}

WINDOW = {"open": True, "close": False}

# property → decoded key it copies
_COPIED = {
    "local_temperature": "temperature",
    "current_heating_setpoint": "setpoint",
    "holiday_temperature": "holiday_setpoint",
    "eco_temperature": "eco_setpoint",
    "antifrost_temperature": "frost_setpoint",
    "energy": "energy",
}


def _heating_origin(decoded: Mapping[str, Any]) -> tuple[str, ...]:
    return ("current_ma",) if "current_ma" in decoded else ("work_state",)


def faults(bitmap: int) -> list[str]:
    """Fault bitmap → names. The bit labels are not established yet: ``bit_<n>``."""
    return [f"bit_{n}" for n in range(bitmap.bit_length()) if bitmap >> n & 1]


def to_state(decoded: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, tuple[str, ...]]]:
    """Return the state object and, for each property, the decoded keys it was built from."""
    state: dict[str, Any] = {}
    origin: dict[str, tuple[str, ...]] = {}

    def put(prop: str, value: Any, *keys: str) -> None:
        state[prop] = value
        origin[prop] = keys

    for prop, key in _COPIED.items():
        if key in decoded:
            put(prop, decoded[key], key)
    if "switch" in decoded:
        put("system_mode", "heat" if decoded["switch"] else "off", "switch")
    if decoded.get("mode") in PRESETS:
        put("preset", PRESETS[decoded["mode"]], "mode")
    if decoded.get("window_state") in WINDOW:
        put("window_open", WINDOW[decoded["window_state"]], "window_state")
    if isinstance(decoded.get("fault"), int):
        put("fault", faults(decoded["fault"]), "fault")

    heating = decoded.get("heating")
    if heating is not None:
        via = _heating_origin(decoded)
        put("running_state", "heat" if heating else "idle", *via)
        if not heating:
            put("power", 0.0, *via)
        elif "power" in decoded:
            put("power", decoded["power"], "voltage", "current_ma")
        if heating and "voltage" in decoded:
            put("voltage", decoded["voltage"], "voltage")
        if heating and "current_ma" in decoded:
            put("current", decoded["current_ma"] / 1000, "current_ma")
    return state, origin
