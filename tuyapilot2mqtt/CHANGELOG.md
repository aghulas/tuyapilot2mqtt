# Changelog

## 0.1.0

First version. **Read only.**

- Reads the thermostats through their Tuya gateway on the LAN (one connection per pass).
- Publishes `bridge/state` (with last will), `bridge/info`, `bridge/devices`, and each
  thermostat's state and availability: MQTT contract version 1.
- Refuses every `/set` and every `bridge/request/*` with an explicit response; nothing is sent to
  a device.
- The sharing API and the developer API are not available yet.
