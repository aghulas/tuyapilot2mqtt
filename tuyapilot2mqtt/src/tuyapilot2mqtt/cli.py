"""Entry point: ``tuyapilot2mqtt [--options FILE] [--dry-run]``."""

from __future__ import annotations

import argparse
import contextlib
import logging
import signal
import sys
import threading
from typing import Any

from . import CONTRACT_VERSION, __version__
from .bridge import Bridge, Message
from .config import ConfigError, Settings, load
from .devices import DevicesError, Gateway, Thermostat
from .devices import load as load_devices
from .lan import LanChannel, tinytuya_reader

log = logging.getLogger("tuyapilot2mqtt")


def _synthetic() -> tuple[Gateway, list[Thermostat], Any]:
    """A made-up gateway with two thermostats, for ``--dry-run``. No real id, no network."""
    gateway = Gateway("gateway0", "0" * 16)
    thermostats = [
        Thermostat(f"thermostat000000000000{n}", f"node{n}", f"Thermostat {n}", "fu1bfwza", "config", "wk", "gateway0")
        for n in (1, 2)
    ]
    readings = {
        "node1": {"1": True, "2": "manual", "16": 195, "17": "close", "20": 0, "50": 200, "107": 1234, "124": "closed"},
        "node2": {"1": True, "2": "program", "16": 182, "101": 2305, "102": 4300, "124": "opened"},
    }
    return gateway, thermostats, lambda _gateway, _thermostats: readings


def dry_run(settings: Settings) -> int:
    gateway, thermostats, reader = _synthetic()
    bridge = Bridge(settings, thermostats, LanChannel(gateway, thermostats, reader))
    messages = bridge.startup() + bridge.poll()
    messages += bridge.handle(bridge.topic("Thermostat 1", "set"), '{"current_heating_setpoint": 19.5}')
    messages += bridge.handle(bridge.topic("bridge", "request", "schedule", "get"), '{"device": "Thermostat 1"}')
    for m in messages:
        print(f"{'R' if m.retain else ' '} {m.topic} {m.payload}")
    print(f"dry run OK: tuyapilot2mqtt {__version__}, contract {CONTRACT_VERSION}, {len(messages)} messages, none sent")
    return 0


def run(settings: Settings) -> int:
    import paho.mqtt.client as mqtt

    lan: LanChannel | None = None
    thermostats: list[Thermostat] = []
    if "lan" in settings.channels:
        if not settings.lan_host:
            raise ConfigError("option 'lan_host': the gateway's address is required by the LAN channel")
        gateway, thermostats = load_devices(settings.lan_keys_file, settings.lan_product_id)
        lan = LanChannel(gateway, thermostats, tinytuya_reader(settings.lan_host, settings.lan_version))
    unsupported = sorted(c for c in settings.channels if c != "lan")
    if unsupported:
        log.warning("channels not available in this version, ignored: %s", ", ".join(unsupported))
    bridge = Bridge(settings, thermostats, lan)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{settings.base_topic}-bridge")
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
    if settings.mqtt_tls:
        client.tls_set()
    client.will_set(bridge.state_topic, bridge.state_payload(False), qos=1, retain=True)
    client.reconnect_delay_set(min_delay=2, max_delay=120)

    def publish(messages: list[Message]) -> None:
        for m in messages:
            client.publish(m.topic, m.payload, qos=1 if m.retain else 0, retain=m.retain)

    def on_connect(_client: Any, _userdata: Any, _flags: Any, reason: Any, _properties: Any = None) -> None:
        if getattr(reason, "is_failure", False):
            log.error("MQTT broker refused the connection: %s", reason)
            return
        log.info("connected to the MQTT broker")
        for topic in bridge.subscriptions:
            client.subscribe(topic)
        publish(bridge.startup())

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        answers = bridge.handle(message.topic, message.payload)
        if answers:
            log.info("command refused (read-only bridge)")
            publish(answers)

    client.on_connect = on_connect
    client.on_message = on_message

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info(
        "tuyapilot2mqtt %s, contract %s, read only: %d thermostat(s), channels %s, one read every %d s",
        __version__,
        CONTRACT_VERSION,
        len(thermostats),
        ",".join(settings.channels) or "none",
        settings.poll_interval,
    )
    client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    client.loop_start()
    answering: int | None = None
    try:
        while not stop.is_set():
            publish(bridge.poll())
            if lan is not None and lan.last_answering != answering:
                answering = lan.last_answering
                log.info("LAN: %d of %d thermostat(s) answered", answering, len(thermostats))
            stop.wait(settings.poll_interval)
    finally:
        info = client.publish(bridge.state_topic, bridge.state_payload(False), qos=1, retain=True)
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
