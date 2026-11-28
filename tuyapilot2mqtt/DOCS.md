# tuyapilot2mqtt

Publishes **Tuya pilot-wire thermostats** (electric radiators, "FP Thermostat", behind a Tuya
gateway) to your MQTT broker in the **Zigbee2MQTT dialect**. It is the bridge the
[Tuya Pilot Wire Companion](https://github.com/aghulas/ha-tuya-pilot-wire) reads. Unofficial: not
affiliated with Tuya.

**Read only by default.** It sends nothing to a thermostat unless you open the writes of this
version (preset `manual` / `program` and the manual setpoint, on the thermostats you designate,
see [Writing](#writing-preset-setpoint-and-settings-on-designated-thermostats)). It must be the only client
of the Smart Life account (disable the stock `tuya` integration).

## What it reads

Two channels, each for what it is proven to give:

- **Smart Life account** (the device-sharing API, the one Home Assistant's `tuya` integration
  uses): on/off, mode, room temperature, manual setpoint, window state and detection, child lock,
  temperature correction, holiday setpoint, fault. One request per read for all thermostats.
- **The gateway, on your local network** (Tuya local protocol): heating state, voltage, current,
  power, cumulative energy, eco and frost setpoints. While the account is not connected, the
  gateway also gives everything else it returns.

The weekly schedule (developer API) is not available yet. A value that was not read is **absent**
from the published state, never guessed.

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
4. For the account channel: the **user code** of your Smart Life app (Me → Settings → Account and
   Security → User Code).

## Connecting the Smart Life account

1. In the add-on's **Configuration** tab, put the user code in `sharing_user_code` and add
   `sharing` to `channels`, then save: the add-on restarts.
2. Open the add-on's page: **Open web UI** on the add-on's Info tab. It shows a QR code. In the
   Smart Life app, tap the scan icon of the home screen, scan the code, then confirm in the app. A
   code is valid two minutes; the page shows the next one by itself, up to fifteen, then the
   add-on stops asking until you restart it.
3. The page says **Connected**, and the log `sharing channel: logged in`. The session is kept in
   the add-on's private storage (`/data/sharing_session.json`) and renewed by the add-on; you scan
   once.

Until you scan, the add-on keeps reading through the gateway and `bridge/info` says
`"sharing": {"state": "waiting_for_scan"}` (or `login_required` while the user code is empty).
**One client per account.** Home Assistant's `tuya` integration and other Smart Life tools log
in with the same application as this add-on: when one of them logs in, the others' sessions stop
working (`sign invalid`), and logging this add-on in breaks theirs. Disable the `tuya` integration
for these thermostats before scanning. If another client takes the session over, the sharing
channel turns `unauthenticated` and a new QR code appears on the page.
The QR code is a short-lived login token: it is never written in the log, and the page answers
Home Assistant's ingress only; do not share a screenshot of it.

## Writing: preset, setpoint and settings on designated thermostats

Closed by default. To open it:

1. connect the Smart Life account (above);
2. list in `write_devices` the thermostats that can be written: their names as published (or
   their Tuya device ids);
3. set `writes_enabled` to `true` and restart.

`bridge/info` then says `"read_only": false` and lists them in `writes.devices`. On
`tuyapilot2mqtt/<one of them>/set` the bridge accepts, one key per command:

- `{"preset": "manual"}` or `{"preset": "program"}` (`eco`, `holiday`, frost protection are
  refused);
- `{"current_heating_setpoint": <value>}`, within the device's own bounds (5 to 40 °C by 0.5) and
  not above 30 °C, only while the thermostat is in `manual`: in `program` mode the thermostat
  replaces a written setpoint with the one of the running schedule slot. Send
  `{"preset": "manual"}` first, and `{"preset": "program"}` afterwards.

Nothing else, on no other thermostat. A write is refused when the thermostat is offline, and
above **one write every 10 s and 30 an hour** per thermostat. The command is sent once, the
thermostat is read back, and the answer on `tuyapilot2mqtt/bridge/response/device/set` says `ok`
only when the thermostat reports the value. The published state changes from that read-back,
never from the request.

Set `writes_enabled` back to `false` to close it. Each write and each refusal is in the log,
with the raw command sent (data-point code and raw value, never an id or a key).

### Further settings, one by one

Each further setting of the thermostat is a **closed candidate** until a supervised trial on one
thermostat proves it; the library then marks it proven, and you open it by name in
`write_settings`:

| Setting | Command it opens |
|---|---|
| `preset_holiday` | `{"preset": "holiday"}` (the thermostat follows `holiday_temperature`) |
| `preset_antifrost` | `{"preset": "antifrost"}` (frost protection, raw mode `hors`) |
| `preset_eco` | `{"preset": "eco"}` (the thermostat follows its eco setpoint) |
| `child_lock` | `{"child_lock": "LOCK" \| "UNLOCK"}` |
| `window_detection` | `{"window_detection": "ON" \| "OFF"}` |
| `local_temperature_calibration` | `{"local_temperature_calibration": -9…9}` |
| `holiday_temperature` | `{"holiday_temperature": <°C>}`, the device's bounds and step, 30 °C at most |

A setting named in `write_settings` whose route is still a candidate stays closed:
`bridge/info.writes.refused` says so. The eco and frost-protection setpoints cannot be opened:
the sharing API does not declare them writable.

**A trial** opens one candidate on one designated thermostat, for the owner's supervised test:
`trial_setting` (one of the names above) and `trial_device` (a thermostat of `write_devices`).
`bridge/info.writes.trial` says what is open and where. Empty both once the trial is done.

## Options

| Option | Default | Meaning |
|---|---|---|
| `base_topic` | `tuyapilot2mqtt` | Prefix of every topic |
| `mqtt_host` | empty | Empty: the add-on finds Home Assistant's Mosquitto by itself, with its credentials. Fill it (and the four options below) only for another broker |
| `mqtt_port` | `1883` | |
| `mqtt_username`, `mqtt_password` | empty | |
| `mqtt_tls` | `false` | |
| `poll_interval` | `60` | Seconds between two reads of the gateway and of the account, 15 to 3600. The gateway accepts few local connections: do not go lower than you need, and raise it if another program reads the same gateway |
| `channels` | `lan` | Channels to use: `lan`, `sharing`, `developer` |
| `lan_host` | empty | **Required.** Address of the gateway |
| `lan_keys_file` | `/homeassistant/tuyapilot2mqtt/devices.json` | Path of the keys file. `/homeassistant/` is your Home Assistant configuration folder, mounted read only |
| `lan_version` | `3.3` | Tuya local protocol version of the gateway |
| `lan_product_id` | `fu1bfwza` | Product assumed for the thermostats whose entry in the keys file has no `product_id`. Only `fu1bfwza` is supported |
| `sharing_user_code` | empty | User code of the Smart Life app, for the QR-code login |
| `sharing_session_file` | `/data/sharing_session.json` | Where the session is kept. Leave it |
| `developer_access_id`, `developer_access_secret`, `developer_region` | empty, empty, `eu` | Tuya IoT platform project, for the `developer` channel. Without them the channel is reported `unavailable`. It only reads the eco and frost setpoints in this version |
| `writes_enabled` | `false` | Opens the writes of this version on the thermostats of `write_devices` |
| `write_devices` | empty | The thermostats that can be written: their published names, or their Tuya device ids (replaces `write_device` of 0.2.x) |
| `write_settings` | empty | Further settings to open on those thermostats, once proven (table above) |
| `trial_setting`, `trial_device` | empty | A supervised trial: one candidate setting opened on one designated thermostat |

## What it publishes

With the default prefix:

| Topic | Content |
|---|---|
| `tuyapilot2mqtt/bridge/state` | `{"state": "online"}`; `offline` when the add-on stops or dies (last will) |
| `tuyapilot2mqtt/bridge/info` | versions, `read_only`, what is open for writing and why not, state of each channel |
| `tuyapilot2mqtt/bridge/devices` | the list of thermostats |
| `tuyapilot2mqtt/<name>` | state of a thermostat; `<name>` is its name in the Tuya app |
| `tuyapilot2mqtt/<name>/availability` | `online`, or `offline` after ten minutes without a reading or when the account says the thermostat is offline |

All retained. The full description is the
[MQTT contract](https://github.com/aghulas/tuyapilot2mqtt/blob/main/docs/mqtt-contract.md).

Commands (`tuyapilot2mqtt/<name>/set`, `tuyapilot2mqtt/bridge/request/…`) are answered on
`tuyapilot2mqtt/bridge/response/…`. Everything but the writes described above gets
`"status": "error"` and a message saying why; nothing is sent to a thermostat.

The add-on creates **no entity** in Home Assistant by itself (no MQTT discovery): the entities are
the Companion's.

## Checking that it works

- The add-on's log says the number of thermostats and the state of each channel, each time one
  changes: `6 thermostat(s); channels: sharing ok, lan ok`. The log never contains a key, a
  device id or a name.
- With an MQTT client, `tuyapilot2mqtt/bridge/info` shows `"state": "online"` for each channel
  you enabled.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `option 'lan_host': the gateway's address is required` | Fill `lan_host` |
| `keys file not found` | Wrong `lan_keys_file`; the path starts with `/homeassistant/` |
| `the keys file must hold exactly one gateway with its key` | The file has no entry with `sub` false and a `key`, or several |
| `lan` channel `offline`, "no device answered" | Wrong address, wrong local key (it changes when the gateway is paired again), wrong protocol version, or the gateway is off the network |
| Some thermostats miss a read from time to time | Another program reads the same gateway at the same moment; raise `poll_interval` |
| A property is missing from a state | The gateway has not returned that data point since the add-on started, or the property is not read in this version |
| `sharing` stays `login_required` | `sharing_user_code` is empty or refused by Tuya (the log says which) |
| `sharing` is `login_expired` | Nobody scanned in thirty minutes: restart the add-on and scan |
| **Open web UI** shows no code | The `sharing` channel is not in `channels`, or the stored session works (the page says Connected) |
| `sharing` becomes `unauthenticated` then `waiting_for_scan` | The session was revoked (app sign-out, password change): scan again |
| `read_only` stays `true` with `writes_enabled` | `bridge/info.writes.closed` gives the reason |

## Outside Home Assistant

The repository has a `docker-compose.yml`; the same settings are given as `TUYAPILOT_*`
environment variables (`TUYAPILOT_LAN_HOST`, `TUYAPILOT_POLL_INTERVAL`…).
