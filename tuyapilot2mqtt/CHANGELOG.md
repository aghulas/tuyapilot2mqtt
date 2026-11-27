# Changelog

## 0.1.1

MQTT contract version 2: the property names are fixed on the Zigbee2MQTT vocabulary, now that the
device is identified (Zigbee `TS0601`, `_TZE204_fu1bfwza`).

- `preset` values are `program`, `manual`, `eco`, `holiday` (was `schedule` for `program`).
- `antifrost_temperature` replaces `frost_protection_temperature`.
- `definition.model` is `TS0601_fu1bfwza`; `ieee_address` is published when the gateway's node id
  is a Zigbee address.

## 0.1.0

First version. **Read only.**

- Reads the thermostats through their Tuya gateway on the LAN (one connection per pass).
- Publishes `bridge/state` (with last will), `bridge/info`, `bridge/devices`, and each
  thermostat's state and availability: MQTT contract version 1.
- Refuses every `/set` and every `bridge/request/*` with an explicit response; nothing is sent to
  a device.
- The sharing API and the developer API are not available yet.
