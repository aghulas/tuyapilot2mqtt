"""LAN channel: the thermostats read through their gateway (Tuya local protocol).

PROVISIONAL: the LAN client belongs in ``pytuya-pilot-wire`` (milestone M1) and is removed from
here once the library ships it. READ ONLY: this module calls ``status()`` and nothing else.

The gateway accepts few local connections: one connection per pass, opened then closed. It does
not return every data point on each read: the channel keeps the last value read of each data
point, with the time it was read, and never fills a data point it has not received.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from .devices import Gateway, Thermostat

log = logging.getLogger(__name__)

SOCKET_TIMEOUT_S = 9

# Returns, for each node id, the raw data points read now, or None when the device did not answer.
Reader = Callable[[Gateway, list[Thermostat]], dict[str, dict[str, Any] | None]]


def tinytuya_reader(host: str, version: str) -> Reader:
    def read(gateway: Gateway, thermostats: list[Thermostat]) -> dict[str, dict[str, Any] | None]:
        import tinytuya

        out: dict[str, dict[str, Any] | None] = {}
        parent = tinytuya.Device(gateway.dev_id, host, gateway.key, version=float(version), persist=True)
        parent.set_socketTimeout(SOCKET_TIMEOUT_S)
        try:
            for t in thermostats:
                try:
                    status = tinytuya.Device(t.dev_id, cid=t.node_id, parent=parent).status()
                except Exception:  # noqa: BLE001 - one silent device does not stop the others
                    status = None
                dps = status.get("dps") if isinstance(status, dict) else None
                out[t.node_id] = dps if isinstance(dps, dict) and dps else None
        finally:
            with contextlib.suppress(Exception):
                parent.close()
        return out

    return read


class LanChannel:
    name = "lan"

    def __init__(
        self,
        gateway: Gateway,
        thermostats: list[Thermostat],
        reader: Reader,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._gateway = gateway
        self._thermostats = [t for t in thermostats if t.supported]
        self._reader = reader
        self._clock = clock
        # dev_id → {dp: (value, time read)}
        self._seen: dict[str, dict[str, tuple[Any, float]]] = {}
        self.last_pass: float | None = None
        self.last_answering = 0
        self.error: str | None = None

    def read(self) -> dict[str, bool]:
        """One pass over the thermostats. Returns, per device id, whether it answered."""
        try:
            raw = self._reader(self._gateway, self._thermostats)
            self.error = None
        except Exception as err:  # noqa: BLE001 - the daemon outlives a failed pass
            raw = {}
            self.error = type(err).__name__
        now = self._clock()
        answered: dict[str, bool] = {}
        for t in self._thermostats:
            dps = raw.get(t.node_id)
            answered[t.dev_id] = dps is not None
            if dps:
                seen = self._seen.setdefault(t.dev_id, {})
                for dp, value in dps.items():
                    seen[str(dp)] = (value, now)
        self.last_pass = now
        self.last_answering = sum(answered.values())
        return answered

    def data_points(self, dev_id: str) -> dict[str, tuple[Any, float]]:
        """Last value read of each data point of a device, with the time it was read."""
        return dict(self._seen.get(dev_id, {}))

    def status(self) -> dict[str, Any]:
        if self.last_pass is None:
            state = "starting"
        elif self.error or not self.last_answering:
            state = "offline"
        else:
            state = "online"
        out: dict[str, Any] = {
            "enabled": True,
            "state": state,
            "devices": len(self._thermostats),
            "devices_answering": self.last_answering,
        }
        if self.error:
            out["error"] = self.error
        return out
