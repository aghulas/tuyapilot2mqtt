"""The gateway and its thermostats, from the local keys file.

The file is the JSON list ``tinytuya`` writes (``devices.json``): one entry per device with
``id``, ``key``, ``name``, and for a sub-device ``sub: true`` and its ``node_id``. It never leaves
the machine; nothing here logs a key, an id or a name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytuya_pilot_wire.model import Device

THERMOSTAT_CATEGORY = "wk"


class DevicesError(ValueError):
    """The keys file cannot be used. The message never contains a key or an id."""


@dataclass(frozen=True)
class LocalInventory:
    """What the keys file gives: the gateway with its local key, and the supported thermostats."""

    gateway_id: str
    gateway_key: str = field(repr=False)
    devices: tuple[Device, ...] = ()
    # Devices whose product id is not in the file and was taken from the ``lan_product_id`` option.
    product_from_config: frozenset[str] = frozenset()

    def product_id_source(self, dev_id: str) -> str:
        return "config" if dev_id in self.product_from_config else "file"


def parse(entries: Any, default_product_id: str) -> LocalInventory:
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise DevicesError("the keys file must hold a JSON list of devices")
    gateways = [e for e in entries if not e.get("sub") and e.get("id") and e.get("key")]
    if len(gateways) != 1:
        raise DevicesError(f"the keys file must hold exactly one gateway with its key, found {len(gateways)}")
    gateway_id = str(gateways[0]["id"])
    devices: list[Device] = []
    from_config: set[str] = set()
    for e in entries:
        if not (e.get("sub") and e.get("id") and e.get("node_id") and e.get("category") == THERMOSTAT_CATEGORY):
            continue
        device = Device(
            dev_id=str(e["id"]),
            name=str(e.get("name") or "").strip(),
            product_id=str(e.get("product_id") or default_product_id),
            category=THERMOSTAT_CATEGORY,
            gateway_id=gateway_id,
            node_id=str(e["node_id"]),
        )
        if not device.supported:
            continue
        if not e.get("product_id"):
            from_config.add(device.dev_id)
        devices.append(device)
    return LocalInventory(gateway_id, str(gateways[0]["key"]), tuple(devices), frozenset(from_config))


def load(path: str | Path, default_product_id: str) -> LocalInventory:
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise DevicesError("keys file not found (option 'lan_keys_file')") from err
    except (OSError, ValueError) as err:
        raise DevicesError("keys file unreadable or not JSON") from err
    return parse(entries, default_product_id)
