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

**0.2.0 — read only by default, one write under trial.** The bridge reads the thermostats
through the Smart Life sharing API (QR-code login from the add-on's log) and through their gateway
on the LAN, and publishes them: [MQTT contract](docs/mqtt-contract.md), version 3. It sends
nothing to a device unless the owner opens the single write of this version: the manual setpoint
of one designated thermostat, validated, sent once and read back. Every other command is refused.
The other writes and the weekly schedule come with the next steps of the
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
pinned by tag in the image: channels, codecs, router, write gate and the daemon core. The daemon
here loads the options and the secrets, builds the channels, runs the QR-code login, and holds
what this repository's contract says beyond the library's codec (`contract.py`) and the write
policy of the version (`bridge.py`).

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
python3 -m venv .venv && .venv/bin/pip install "pytuya-pilot-wire[sharing,lan,developer,mqtt] @ git+https://github.com/aghulas/pytuya-pilot-wire@v0.2.0" -e "./tuyapilot2mqtt[dev]"
```

```bash
cd tuyapilot2mqtt && ../.venv/bin/ruff check . ../tests && ../.venv/bin/pytest -q && ../.venv/bin/tuyapilot2mqtt --dry-run
```

## License

MIT — see [`LICENSE`](LICENSE).
