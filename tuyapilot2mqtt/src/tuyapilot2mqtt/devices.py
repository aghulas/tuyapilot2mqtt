"""The gateway and its thermostats, from the local keys file.

The file is the JSON list ``tinytuya`` writes (``devices.json``): one entry per device with
``id``, ``key``, ``name``, and for a sub-device ``sub: true`` and its ``node_id``. It never leaves
the machine; nothing here logs a key or an id.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytuya_pilot_wire import dps

THERMOSTAT_CATEGORY = "wk"
# Zigbee model id and product: the name the device would have in a Zigbee2MQTT definition.
MODEL = "TS0601_fu1bfwza"
_EUI64 = re.compile(r"[0-9a-fA-F]{16}")


class DevicesError(ValueError):
    """The keys file cannot be used. The message never contains a key or an id."""


@dataclass(frozen=True)
class Gateway:
    dev_id: str
    key: str = field(repr=False)


@dataclass(frozen=True)
class Thermostat:
    dev_id: str
    node_id: str
    friendly_name: str
    product_id: str
    product_id_source: str  # "file" or "config"
    category: str
    gateway_id: str

    @property
    def supported(self) -> bool:
        return self.product_id in dps.PRODUCTS

    @property
    def ieee_address(self) -> str | None:
        """The Zigbee address, when the gateway's node id is one (EUI-64, 16 hexadecimal digits)."""
        return f"0x{self.node_id.lower()}" if _EUI64.fullmatch(self.node_id) else None

    def descriptor(self) -> dict[str, Any]:
        """Entry of ``bridge/devices`` (contract §2)."""
        address = self.ieee_address
        return ({"ieee_address": address} if address else {}) | {
            "friendly_name": self.friendly_name,
            "type": "EndDevice",
            "supported": self.supported,
            "definition": {
                "vendor": "Tuya",
                "model": MODEL,
                "description": "Pilot-wire thermostat",
            }
            if self.supported
            else None,
            "tuya": {
                "dev_id": self.dev_id,
                "product_id": self.product_id,
                "product_id_source": self.product_id_source,
                "category": self.category,
                "gateway": self.gateway_id,
            },
        }


def friendly_names(names: list[str]) -> list[str]:
    """Topic-safe, unique names, in the order given. ``+``, ``#`` and ``/`` cannot be in a topic level."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for position, name in enumerate(names, start=1):
        clean = " ".join(name.replace("+", " ").replace("#", " ").replace("/", " ").split())
        clean = clean or f"thermostat {position}"
        seen[clean] = seen.get(clean, 0) + 1
        out.append(clean if seen[clean] == 1 else f"{clean} {seen[clean]}")
    return out


def parse(entries: Any, default_product_id: str) -> tuple[Gateway, list[Thermostat]]:
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise DevicesError("the keys file must hold a JSON list of devices")
    gateways = [e for e in entries if not e.get("sub") and e.get("id") and e.get("key")]
    if len(gateways) != 1:
        raise DevicesError(f"the keys file must hold exactly one gateway with its key, found {len(gateways)}")
    gateway = Gateway(str(gateways[0]["id"]), str(gateways[0]["key"]))
    subs = sorted(
        (
            e
            for e in entries
            if e.get("sub") and e.get("id") and e.get("node_id") and e.get("category") == THERMOSTAT_CATEGORY
        ),
        key=lambda e: (str(e.get("name", "")), str(e["id"])),
    )
    names = friendly_names([str(e.get("name", "")) for e in subs])
    thermostats = [
        Thermostat(
            dev_id=str(e["id"]),
            node_id=str(e["node_id"]),
            friendly_name=name,
            product_id=str(e.get("product_id") or default_product_id),
            product_id_source="file" if e.get("product_id") else "config",
            category=str(e["category"]),
            gateway_id=gateway.dev_id,
        )
        for e, name in zip(subs, names, strict=True)
    ]
    return gateway, thermostats


def load(path: str | Path, default_product_id: str) -> tuple[Gateway, list[Thermostat]]:
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise DevicesError("keys file not found (option 'lan_keys_file')") from err
    except (OSError, ValueError) as err:
        raise DevicesError("keys file unreadable or not JSON") from err
    return parse(entries, default_product_id)
