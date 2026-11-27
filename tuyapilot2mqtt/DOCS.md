# tuyapilot2mqtt

Publishes **Tuya pilot-wire thermostats** (electric radiators, "FP Thermostat", behind a Tuya
gateway) to your MQTT broker in the **Zigbee2MQTT dialect**. It is the bridge the
[Tuya Pilot Wire Companion](https://github.com/aghulas/ha-tuya-pilot-wire) reads. Unofficial: not
affiliated with Tuya.

**This version is read only.** It sends nothing to a thermostat: every command is answered by a
refusal. It can run next to the stock `tuya` integration and next to any other reader.

## What it reads

Through the gateway, on your local network (Tuya local protocol): on/off, mode, room temperature,
manual setpoint, window state, fault, holiday / eco / frost setpoints, heating state, voltage,
current, power and cumulative energy.

Not read yet (they need the Smart Life sharing API, not available in this version): window
detection, child lock, temperature correction. The weekly schedule (developer API) is not
available either. A value that was not read is **absent** from the published state, never guessed.

## Before you start

1. **Mosquitto**: the "Mosquitto broker" add-on, or any MQTT broker.
2. **The gateway's local key**, in a keys file placed in your Home Assistant configuration folder.
   The file is the `devices.json` that [`tinytuya`](https://github.com/jasonacox/tinytuya)'s
   wizard writes: a JSON list with one entry per device.

   ```json
   [
     {"id": "<gateway id>", "key": "<gateway local key>", "name": "Gateway", "sub": false},
     {"id": "<thermostat id>", "name": "Living room", "category": "wk",
      "sub": true, "node_id": "<node id>", "product_id": "fu1bfwza"}
   ]
   ```

   The bridge needs exactly one gateway entry (`sub` false, with its `key`) and, for each
   thermostat, `id`, `node_id`, `name` and `category: "wk"`. Other entries are ignored. Getting a
   local key requires a Tuya developer project today; the `tinytuya` documentation explains how.
   Keep the file out of any backup you share: it is a secret.
3. **A fixed address for the gateway** (DHCP reservation in your router).

## Options

| Option | Default | Meaning |
|---|---|---|
| `base_topic` | `tuyapilot2mqtt` | Prefix of every topic |
| `mqtt_host` | empty | Empty: the add-on finds Home Assistant's Mosquitto by itself, with its credentials. Fill it (and the four options below) only for another broker |
| `mqtt_port` | `1883` | |
| `mqtt_username`, `mqtt_password` | empty | |
| `mqtt_tls` | `false` | |
| `poll_interval` | `60` | Seconds between two reads of the gateway, 15 to 3600. The gateway accepts few local connections: do not go lower than you need, and raise it if another program reads the same gateway |
| `channels` | `lan` | Channels to use. `sharing` and `developer` are accepted and reported as `unsupported` in this version |
| `lan_host` | empty | **Required.** Address of the gateway |
| `lan_keys_file` | `/homeassistant/tuyapilot2mqtt/devices.json` | Path of the keys file. `/homeassistant/` is your Home Assistant configuration folder, mounted read only |
| `lan_version` | `3.3` | Tuya local protocol version of the gateway |
| `lan_product_id` | `fu1bfwza` | Product assumed for the thermostats whose entry in the keys file has no `product_id`. Only `fu1bfwza` is supported |

## What it publishes

With the default prefix:

| Topic | Content |
|---|---|
| `tuyapilot2mqtt/bridge/state` | `{"state": "online"}`; `offline` when the add-on stops or dies (last will) |
| `tuyapilot2mqtt/bridge/info` | versions, `read_only`, state of each channel, number of thermostats answering |
| `tuyapilot2mqtt/bridge/devices` | the list of thermostats |
| `tuyapilot2mqtt/<name>` | state of a thermostat; `<name>` is its name in the Tuya app |
| `tuyapilot2mqtt/<name>/availability` | `online`, or `offline` after three reads without an answer |

All retained. The full description is the
[MQTT contract](https://github.com/aghulas/tuyapilot2mqtt/blob/main/docs/mqtt-contract.md).

Commands (`tuyapilot2mqtt/<name>/set`, `tuyapilot2mqtt/bridge/request/…`) are answered on
`tuyapilot2mqtt/bridge/response/…` with `"status": "error"` and a message saying the bridge is
read only.

The add-on creates **no entity** in Home Assistant by itself (no MQTT discovery): the entities are
the Companion's.

## Checking that it works

- The add-on's log says how many thermostats answered, each time that number changes:
  `LAN: 6 of 6 thermostat(s) answered`. The log never contains a key, a device id or a name.
- With an MQTT client, `tuyapilot2mqtt/bridge/info` shows `"lan": {"state": "online", …}`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `option 'lan_host': the gateway's address is required` | Fill `lan_host` |
| `keys file not found` | Wrong `lan_keys_file`; the path starts with `/homeassistant/` |
| `the keys file must hold exactly one gateway with its key` | The file has no entry with `sub` false and a `key`, or several |
| `LAN: 0 of N thermostat(s) answered`, channel `offline` | Wrong address, wrong local key (it changes when the gateway is paired again), wrong protocol version, or the gateway is off the network |
| Some thermostats miss a read from time to time | Another program reads the same gateway at the same moment; raise `poll_interval` |
| A property is missing from a state | The gateway has not returned that data point since the add-on started, or the property is not read in this version |

## Outside Home Assistant

The repository has a `docker-compose.yml`; the same settings are given as `TUYAPILOT_*`
environment variables (`TUYAPILOT_LAN_HOST`, `TUYAPILOT_POLL_INTERVAL`…).
