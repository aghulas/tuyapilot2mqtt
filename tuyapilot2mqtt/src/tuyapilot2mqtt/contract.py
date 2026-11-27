"""The MQTT contract of this repository, over the library's dialect codec.

``docs/mqtt-contract.md`` is changed here first and the library codec follows: the codec of
``pytuya-pilot-wire`` 0.2.0 still speaks draft 0 for a few names. This module is the difference,
and nothing else: names, the descriptor's identity fields, timestamps. No Tuya logic.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pytuya_pilot_wire.model import Device

# Zigbee model id and product: the name the device would have in a Zigbee2MQTT definition.
MODELS = {"fu1bfwza": "TS0601_fu1bfwza"}
VENDOR = "Tuya"
DESCRIPTION = "Pilot-wire thermostat"

# Draft-0 names of the library codec -> names of the contract (fixed at M0, contract version 2).
_PROPERTIES = {"frost_protection_temperature": "antifrost_temperature"}
_PRESETS = {"schedule": "program", "frost_protection": "antifrost"}

# The only `/set` key this version of the bridge can open.
OPEN_SET_KEY = "current_heating_setpoint"

_EUI64 = re.compile(r"[0-9a-fA-F]{16}")


def iso(timestamp: float) -> str:
    """Epoch seconds as ``2026-11-27T14:08:26Z``."""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _z(text: Any) -> Any:
    return text.replace("+00:00", "Z") if isinstance(text, str) else text


def state(encoded: dict[str, Any], last_seen: float | None) -> dict[str, Any]:
    """The state object of the contract (§3) from the library's ``dialect.encode_state``."""
    out: dict[str, Any] = {}
    extension = dict(encoded.get("tuya") or {})
    for name, value in encoded.items():
        if name == "tuya":
            continue
        name = _PROPERTIES.get(name, name)
        out[name] = _PRESETS.get(value, value) if name == "preset" else value
    read = {
        _PROPERTIES.get(name, name): {"channel": source.get("channel"), "at": _z(source.get("at"))}
        for name, source in (extension.pop("sources", None) or {}).items()
    }
    if last_seen is not None:
        out["last_seen"] = iso(last_seen)
    out["tuya"] = extension | {"read": read}
    return out


def ieee_address(device: Device) -> str | None:
    """The Zigbee address, when the gateway's node id is one (EUI-64, 16 hexadecimal digits)."""
    node_id = device.node_id or ""
    return f"0x{node_id.lower()}" if _EUI64.fullmatch(node_id) else None


def descriptor(device: Device, name: str, product_id_source: str) -> dict[str, Any]:
    """Entry of ``bridge/devices`` (contract §2)."""
    address = ieee_address(device)
    tuya: dict[str, Any] = {
        "dev_id": device.dev_id,
        "product_id": device.product_id,
        "product_id_source": product_id_source,
        "category": device.category,
    }
    if device.gateway_id:
        tuya["gateway"] = device.gateway_id
    return ({"ieee_address": address} if address else {}) | {
        "friendly_name": name,
        "type": "EndDevice",
        "supported": True,
        "definition": {
            "vendor": VENDOR,
            "model": MODELS.get(device.product_id, device.product_id),
            "description": DESCRIPTION,
        },
        "tuya": tuya,
    }
