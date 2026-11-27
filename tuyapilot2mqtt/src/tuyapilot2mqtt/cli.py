"""Entry point: ``tuyapilot2mqtt [--options FILE] [--dry-run]``."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sys
import threading
from typing import Any

from pytuya_pilot_wire.errors import PilotWireError
from pytuya_pilot_wire.model import Channel, Device, DeviceSpec, Reading

from . import CONTRACT_VERSION, __version__
from .bridge import Bridge
from .config import ConfigError, Settings, load
from .devices import DevicesError, LocalInventory
from .devices import load as load_devices
from .session import Login, LoginPage, NoSession, SessionStore

log = logging.getLogger("tuyapilot2mqtt")


# --- dry run: a made-up install, no network ---------------------------------------------------


class _Printer:
    """Transport of the dry run: prints what would be published."""

    def __init__(self) -> None:
        self.count = 0

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        self.count += 1
        print(f"{'R' if retain else ' '} {topic} {payload}")

    def subscribe(self, topic: str, callback: Any) -> None:
        pass


class _MadeUpSharing:
    """Two made-up thermostats behind a made-up account. ``send`` changes what ``read`` returns."""

    def __init__(self) -> None:
        self.setpoints = {"device1": 20.0, "device2": 19.0}

    def discover(self) -> list[Device]:
        return [
            Device(f"device{n}", f"Thermostat {n}", "fu1bfwza", "wk", None, f"a4c13800000000{n:02x}", True)
            for n in (1, 2)
        ]

    def spec(self, dev_id: str) -> DeviceSpec:
        return DeviceSpec.from_instruction_set(
            "fu1bfwza",
            [{"code": "temp_set", "type": "Integer", "values": {"min": 50, "max": 400, "step": 5}}],
            [{"code": "fault", "type": "Bitmap", "values": {"label": ["e1", "e2", "e3"]}}],
        )

    def read(self, dev_ids: list[str]) -> list[Reading]:
        return [
            Reading(
                dev_id,
                Channel.SHARING,
                1_800_000_000.0,
                {"switch": True, "mode": "manual", "temperature": 19.5, "setpoint": self.setpoints[dev_id], "fault": 0},
                online=True,
            )
            for dev_id in dev_ids
        ]

    def send(self, dev_id: str, commands: list[dict[str, Any]]) -> None:
        self.setpoints[dev_id] = commands[0]["value"] / 10


class _MadeUpLan:
    def read(self, devices: Any) -> list[Reading]:
        values = {"voltage": 230.5, "current_ma": 4300, "heating": True, "power": 991.2, "energy": 123.4}
        return [Reading(d.dev_id, Channel.LAN, 1_800_000_000.0, values) for d in devices]


def dry_run(settings: Settings) -> int:
    import dataclasses

    printer = _Printer()
    for writes in (False, True):
        made_up = dataclasses.replace(
            settings, channels=("lan", "sharing"), writes_enabled=writes, write_devices=("Thermostat 1",)
        )
        bridge = Bridge(
            printer,
            made_up,
            sharing=_MadeUpSharing(),
            lan=_MadeUpLan(),
            clock=lambda: 1_800_000_000.0,
            gate_options={"sleep": lambda _seconds: None},
        )
        print(f"--- made-up install, writes_enabled={writes}")
        bridge.start()
        bridge.poll()
        prefix = settings.base_topic
        bridge.handle_message(f"{prefix}/Thermostat 1/set", '{"current_heating_setpoint": 20.5, "transaction": "t1"}')
        bridge.handle_message(f"{prefix}/Thermostat 2/set", '{"current_heating_setpoint": 20.5}')
        bridge.handle_message(f"{prefix}/Thermostat 1/set", '{"system_mode": "off"}')
        bridge.handle_message(f"{prefix}/bridge/request/schedule/get", '{"device": "Thermostat 1"}')
    print(
        f"dry run OK: tuyapilot2mqtt {__version__}, contract {CONTRACT_VERSION},"
        f" {printer.count} messages, none sent to a broker or a device"
    )
    return 0


# --- the daemon -------------------------------------------------------------------------------


def _build_lan(settings: Settings) -> tuple[Any, LocalInventory]:
    from pytuya_pilot_wire.channels.lan import Gateway, LanChannel

    if not settings.lan_host:
        raise ConfigError("option 'lan_host': the gateway's address is required by the LAN channel")
    local = load_devices(settings.lan_keys_file, settings.lan_product_id)
    gateway = Gateway(local.gateway_id, settings.lan_host, local.gateway_key, float(settings.lan_version))
    return LanChannel(gateway), local


def _sharing_channel(session: Any, store: SessionStore) -> Any:
    from pytuya_pilot_wire.channels.sharing import SharingChannel

    return SharingChannel(session, on_token=lambda renewed: store.save(renewed.to_dict()))


def _build_sharing(store: SessionStore) -> Any:
    """The sharing channel on the stored session, or a stand-in that asks for a login."""
    from pytuya_pilot_wire.channels.sharing import SharingSession

    stored = store.load()
    if stored is not None:
        try:
            return _sharing_channel(SharingSession.from_dict(stored), store)
        except PilotWireError:
            log.warning("sharing channel: the stored session is unusable")
    return NoSession()


def _mqtt_client(settings: Settings) -> Any:
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{settings.base_topic}-bridge")
    if settings.mqtt_tls:
        client.tls_set()
    client.reconnect_delay_set(min_delay=2, max_delay=120)
    return client


# Port of the login page behind Home Assistant's ingress, set by run.sh in the add-on. Unset (outside
# Home Assistant): no page, the QR code is printed in the log.
INGRESS_PORT_ENV = "TUYAPILOT_INGRESS_PORT"


def _login_page() -> LoginPage | None:
    raw = os.environ.get(INGRESS_PORT_ENV, "").strip()
    if not raw or raw == "0":
        return None
    try:
        page = LoginPage(int(raw))
        page.start()
    except (OSError, ValueError) as err:
        log.warning("login page unavailable (%s): the QR code goes to the log", type(err).__name__)
        return None
    return page


def _sharing_state(bridge: Any, login: Login | None) -> tuple[str, str]:
    status = bridge.status.get(Channel.SHARING)
    if status is None:
        return "disabled", ""
    if status.state == "unauthenticated" and login is not None:
        return login.state, login.detail
    return status.state, ""


def run(settings: Settings) -> int:
    from pytuya_pilot_wire.mqtt import PahoTransport

    lan: Any = None
    local: LocalInventory | None = None
    sharing: Any = None
    developer: Any = None
    unavailable: dict[str, str] = {}
    if "lan" in settings.channels:
        lan, local = _build_lan(settings)
    store = SessionStore(settings.sharing_session_file)
    login: Login | None = None
    if "sharing" in settings.channels:
        sharing = _build_sharing(store)
    if "developer" in settings.channels:
        if settings.developer_access_id and settings.developer_access_secret:
            from pytuya_pilot_wire.channels.developer import DeveloperChannel, DeveloperProject

            developer = DeveloperChannel(
                DeveloperProject(
                    settings.developer_access_id, settings.developer_access_secret, settings.developer_region
                )
            )
        else:
            unavailable["developer"] = "options 'developer_access_id' and 'developer_access_secret' are empty"
            log.warning("developer channel: enabled without credentials, unavailable")

    client = _mqtt_client(settings)
    transport = PahoTransport(
        settings.mqtt_host,
        settings.mqtt_port,
        username=settings.mqtt_username or None,
        password=settings.mqtt_password or None,
        prefix=settings.base_topic,
        client=client,
    )
    bridge = Bridge(
        transport,
        settings,
        sharing=sharing,
        lan=lan,
        developer=developer,
        local=local,
        unavailable=unavailable,
        login_state=lambda: None if login is None else (login.state, login.detail),
    )
    connected = threading.Event()
    transport.on_connect = connected.set

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info(
        "tuyapilot2mqtt %s, contract %s: channels %s, one read every %d s, writes %s",
        __version__,
        CONTRACT_VERSION,
        ",".join(settings.channels) or "none",
        settings.poll_interval,
        (
            f"enabled in the options ({len(settings.write_devices)} thermostat(s) designated)"
            if settings.writes_enabled
            else "disabled"
        ),
    )
    page = _login_page() if "sharing" in settings.channels else None
    client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    client.loop_start()
    bridge.start()
    said: tuple[Any, ...] = ()
    try:
        while not stop.is_set():
            try:
                bridge.step(1.0)
                if connected.is_set():
                    connected.clear()
                    log.info("connected to the MQTT broker")
                    bridge.republish()
                status = bridge.status.get(Channel.SHARING)
                if status is not None and status.state == "unauthenticated":
                    if login is None:
                        from pytuya_pilot_wire.channels.sharing import poll_login, request_qr_code

                        login = (
                            Login(settings.sharing_user_code, request=request_qr_code, poll=poll_login)
                            if page is None
                            else Login(
                                settings.sharing_user_code,
                                request=request_qr_code,
                                poll=poll_login,
                                show=page.show,
                                where="on the add-on's page (Open web UI)",
                            )
                        )
                    before = (login.state, login.detail)
                    session = login.tick()
                    if session is not None:
                        store.save(session.to_dict())
                        bridge.resume_sharing(_sharing_channel(session, store))
                        login = None
                    elif (login.state, login.detail) != before:
                        bridge.publish_info()
                if page is not None:
                    page.update(*_sharing_state(bridge, login))
            except Exception as err:  # one bad turn must not take the thermostats offline
                log.error("loop error (%s), continuing", type(err).__name__)
                stop.wait(5)
            now = tuple((c.value, s.state) for c, s in bridge.status.items()) + (len(bridge.devices),)
            if now != said:
                said = now
                log.info(
                    "%d thermostat(s); channels: %s",
                    len(bridge.devices),
                    ", ".join(f"{c.value} {s.state}" for c, s in bridge.status.items()) or "none",
                )
    finally:
        if page is not None:
            page.stop()
        info = client.publish(
            f"{settings.base_topic}/bridge/state", json.dumps({"state": "offline"}), qos=1, retain=True
        )
        with contextlib.suppress(RuntimeError, ValueError):
            info.wait_for_publish(timeout=5)
        client.disconnect()
        client.loop_stop()
    log.info("stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tuyapilot2mqtt", description=__doc__)
    parser.add_argument("--options", help="JSON options file (the add-on's /data/options.json)")
    parser.add_argument("--dry-run", action="store_true", help="print the messages of a made-up install and exit")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    try:
        settings = load(args.options)
        return dry_run(settings) if args.dry_run else run(settings)
    except (ConfigError, DevicesError) as err:
        log.error("%s", err)
        return 2


if __name__ == "__main__":
    sys.exit(main())
