"""Settings: the add-on's options file, then ``TUYAPILOT_*`` environment variables on top."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

CHANNELS = ("lan", "sharing", "developer")
ENV_PREFIX = "TUYAPILOT_"
REGIONS = ("eu", "us", "cn", "in", "sg")


class ConfigError(ValueError):
    """A setting that cannot be used. The message never contains a secret."""


@dataclass(frozen=True)
class Settings:
    base_topic: str = "tuyapilot2mqtt"
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = field(default="", repr=False)
    mqtt_tls: bool = False
    # Seconds between two reads of the gateway. The gateway accepts few local connections and the
    # cloud channels ride the user's account: slow by default.
    poll_interval: int = 60
    channels: tuple[str, ...] = ("lan",)
    lan_host: str = ""
    lan_keys_file: str = "/homeassistant/tuyapilot2mqtt/devices.json"
    lan_version: str = "3.3"
    # Product assumed for the thermostats when the keys file carries no product id.
    lan_product_id: str = "fu1bfwza"
    # Sharing channel (Smart Life account). The user code starts the QR-code login; the session
    # the login gives is kept in a file of the bridge's own and never shared with another client.
    sharing_user_code: str = field(default="", repr=False)
    sharing_session_file: str = "/data/sharing_session.json"
    # Developer channel (Tuya IoT platform project). Unavailable while the credentials are empty.
    developer_access_id: str = field(default="", repr=False)
    developer_access_secret: str = field(default="", repr=False)
    developer_region: str = "eu"
    # The single write of this version: the manual setpoint, on one designated thermostat
    # (its friendly name or its Tuya device id). Closed by default.
    writes_enabled: bool = False
    write_device: str = ""


_BOOL = {"1": True, "true": True, "yes": True, "on": True, "0": False, "false": False, "no": False, "off": False}


def _coerce(name: str, kind: Any, raw: Any) -> Any:
    try:
        if name == "channels":
            items = raw.replace(",", " ").split() if isinstance(raw, str) else list(raw)
            return tuple(str(i) for i in items)
        if kind == "bool":
            return raw if isinstance(raw, bool) else _BOOL[str(raw).strip().lower()]
        if kind == "int":
            return int(raw)
        return str(raw)
    except (KeyError, TypeError, ValueError) as err:
        raise ConfigError(f"option {name!r}: unusable value") from err


def load(options_file: str | Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """Build the settings. An empty option leaves the default; the environment wins over the file."""
    env = os.environ if env is None else env
    raw: dict[str, Any] = {}
    if options_file and Path(options_file).is_file():
        loaded = json.loads(Path(options_file).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ConfigError("the options file must hold a JSON object")
        raw.update(loaded)
    values: dict[str, Any] = {}
    for f in fields(Settings):
        value = env.get(ENV_PREFIX + f.name.upper(), raw.get(f.name))
        if value is None or value == "":
            continue
        values[f.name] = _coerce(f.name, f.type, value)
    settings = Settings(**values)
    validate(settings)
    return settings


def validate(s: Settings) -> None:
    if not s.base_topic or any(c in s.base_topic for c in "+#") or s.base_topic.strip("/") != s.base_topic:
        raise ConfigError("option 'base_topic': no wildcard, no leading or trailing slash")
    if not 15 <= s.poll_interval <= 3600:
        raise ConfigError("option 'poll_interval': 15 to 3600 seconds")
    unknown = [c for c in s.channels if c not in CHANNELS]
    if unknown:
        raise ConfigError(f"option 'channels': unknown channel {unknown[0]!r}")
    if "lan" in s.channels and s.lan_version not in ("3.3", "3.4", "3.5"):
        raise ConfigError("option 'lan_version': 3.3, 3.4 or 3.5")
    if s.developer_region not in REGIONS:
        raise ConfigError(f"option 'developer_region': one of {', '.join(REGIONS)}")
