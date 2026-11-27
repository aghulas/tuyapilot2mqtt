# tuyapilot2mqtt

Bridge between **Tuya pilot-wire thermostats** (electric radiators, behind a Tuya gateway) and a
local MQTT broker, speaking the **Zigbee2MQTT** dialect. It feeds the
[Tuya Pilot Wire Companion](https://github.com/aghulas/ha-tuya-pilot-wire) for Home Assistant, which
drives the same thermostats whether they sit behind a Tuya gateway or on a Zigbee coordinator.

```
Tuya gateway ──(sharing API · LAN · developer API)──► tuyapilot2mqtt ──┐
                                                                       ├──► Mosquitto ──► Companion (HA)
Zigbee coordinator ──────────────────────────────────► zigbee2mqtt ────┘
```

Unofficial: not affiliated with Tuya.

## Status

**Scoping — nothing to install yet.** This repository holds the
[MQTT contract](docs/mqtt-contract.md) (draft 0). The daemon and the Home Assistant add-on arrive
at milestone M2 of the
[roadmap](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/roadmap.md), read only
first. The add-on files are added then, so that the repository does not show an add-on that cannot
run.

| Repository | Role |
|---|---|
| [`pytuya-pilot-wire`](https://github.com/aghulas/pytuya-pilot-wire) | Tuya channels, codecs and guarded writes |
| **`tuyapilot2mqtt`** (this one) | bridge daemon, add-on, MQTT contract |
| [`ha-tuya-pilot-wire`](https://github.com/aghulas/ha-tuya-pilot-wire) | Home Assistant integration; [scope](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/scope.md), architecture, roadmap |

## What the bridge will do

- Hold the Tuya sessions and keys; the Companion never sees them.
- Publish each thermostat's descriptor, state and availability, and only what it has read.
- Execute `/set` and `bridge/request/*` through the route proven for each operation, refuse the
  rest, and publish a new state only after the device confirmed it.
- Poll conservatively: the cloud channels ride the user's own Tuya account.

## License

MIT — see [`LICENSE`](LICENSE).
