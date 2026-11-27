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

**0.1.0 — read only.** The bridge reads the thermostats through their gateway on the LAN and
publishes them: [MQTT contract](docs/mqtt-contract.md), version 1. It sends nothing to a device:
every command is refused. The Smart Life sharing API, the developer API and the writes come with
the next milestones of the
[roadmap](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/roadmap.md).

## Install

**Home Assistant add-on**: Settings → Add-ons → Add-on store → ⋮ → Repositories, add
`https://github.com/aghulas/tuyapilot2mqtt`, then install "tuyapilot2mqtt". Prerequisites and
options: [`tuyapilot2mqtt/DOCS.md`](tuyapilot2mqtt/DOCS.md).

**Plain container**: copy `.env.example` to `.env`, put the keys file in `./keys/devices.json`,
then `docker compose up -d --build`.

## Layout

| Path | Content |
|---|---|
| `docs/mqtt-contract.md` | the contract: topics, descriptors, state, commands |
| `tuyapilot2mqtt/` | the add-on: manifests, image, and the daemon (`src/tuyapilot2mqtt/`) |
| `tests/` | tests on synthetic data |

All Tuya logic belongs to [`pytuya-pilot-wire`](https://github.com/aghulas/pytuya-pilot-wire),
pinned in the image. Two modules of the daemon are provisional until the library ships them: the
LAN reader (`lan.py`) and the dialect codec (`dialect.py`).

| Repository | Role |
|---|---|
| [`pytuya-pilot-wire`](https://github.com/aghulas/pytuya-pilot-wire) | Tuya channels, codecs and guarded writes |
| **`tuyapilot2mqtt`** (this one) | bridge daemon, add-on, MQTT contract |
| [`ha-tuya-pilot-wire`](https://github.com/aghulas/ha-tuya-pilot-wire) | Home Assistant integration; [scope](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/scope.md), architecture, roadmap |

## What the bridge does, and will do

- Hold the Tuya sessions and keys; the Companion never sees them.
- Publish each thermostat's descriptor, state and availability, and only what it has read.
- Execute `/set` and `bridge/request/*` through the route proven for each operation, refuse the
  rest, and publish a new state only after the device confirmed it.
- Poll conservatively: the cloud channels ride the user's own Tuya account.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install "pytuya-pilot-wire @ git+https://github.com/aghulas/pytuya-pilot-wire" -e "./tuyapilot2mqtt[dev]"
```

```bash
cd tuyapilot2mqtt && ../.venv/bin/ruff check . ../tests && ../.venv/bin/pytest -q && ../.venv/bin/tuyapilot2mqtt --dry-run
```

## License

MIT — see [`LICENSE`](LICENSE).
