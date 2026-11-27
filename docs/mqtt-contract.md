# MQTT contract

Contract version: **1** (2026-11-27). Published by `tuyapilot2mqtt` 0.1.0, **read only**, LAN
channel. The same number is in `bridge/info.contract`; it is incremented at every change of this
document that a reader of the topics can observe. History: [§7](#7-versions).

The topic layout is settled. The property names are provisional until the device's Zigbee2MQTT
definition is known (milestone M0 of the
[roadmap](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/roadmap.md)).

The contract is the **Zigbee2MQTT dialect**: a thermostat published by `tuyapilot2mqtt` and the
same thermostat paired to Zigbee2MQTT must look the same to the Companion wherever they mean the
same thing. Where Zigbee2MQTT defines a topic or a property, this contract uses it unchanged; what
it adds is under `bridge/request/*` and in a `tuya` extension object.

`<prefix>` is `tuyapilot2mqtt` by default.

## 1. Topics

| Topic | Retain | Direction | Payload |
|---|---|---|---|
| `<prefix>/bridge/state` | yes | bridge → Companion | `{"state": "online"}` or `{"state": "offline"}` (last will) |
| `<prefix>/bridge/info` | yes | bridge → Companion | bridge version, contract version, state of each channel (§1.1) |
| `<prefix>/bridge/devices` | yes | bridge → Companion | JSON array of device descriptors (§2) |
| `<prefix>/<friendly_name>` | yes | bridge → Companion | JSON state object (§3) |
| `<prefix>/<friendly_name>/availability` | yes | bridge → Companion | `{"state": "online"}` or `{"state": "offline"}` |
| `<prefix>/<friendly_name>/set` | no | Companion → bridge | JSON command object (§4) |
| `<prefix>/bridge/request/<action>` | no | Companion → bridge | JSON request (§5) |
| `<prefix>/bridge/response/<action>` | no | bridge → Companion | `{"status": "ok" \| "error", "data": …, "error": …, "transaction": …}` |

A `<friendly_name>` is the name the device has in the Tuya app, made safe for a topic level (`+`,
`#` and `/` become spaces) and unique (` 2`, ` 3`… appended to a repeated name). It can contain
spaces, as in Zigbee2MQTT.

### 1.1 `bridge/info`

```json
{
  "version": "0.1.0",
  "contract": 1,
  "library": {"name": "pytuya-pilot-wire", "version": "0.0.1"},
  "read_only": true,
  "poll_interval": 60,
  "channels": {
    "lan": {"enabled": true, "state": "online", "devices": 6, "devices_answering": 6},
    "sharing": {"enabled": false},
    "developer": {"enabled": false}
  }
}
```

A channel's `state` is `starting` (no read pass yet), `online`, `offline` (the last pass reached
no device; `error` then names the kind of failure) or `unsupported` (enabled in the options, not
available in this version of the bridge). `read_only: true` means every command and request is
refused (§4, §5). `bridge/info` is republished after each read pass.

### 1.2 Availability

`<prefix>/<friendly_name>/availability` is `online` once the device has answered a read, and
becomes `offline` after **three consecutive** read passes without an answer (the gateway drops a
read now and then). A device that never answered since the bridge started is `offline` and has no
state topic published by this run.

## 2. Device descriptor

Same shape as a Zigbee2MQTT entry, limited to what the Companion reads:

```json
{
  "friendly_name": "Library 1",
  "type": "EndDevice",
  "supported": true,
  "definition": {"vendor": "Tuya", "model": "FP Thermostat", "description": "Pilot-wire thermostat"},
  "tuya": {"dev_id": "<device id>", "product_id": "fu1bfwza", "product_id_source": "config",
           "category": "wk", "gateway": "<gateway id>"}
}
```

- `definition.model` is the vendor's product name until M0 fixes the Zigbee2MQTT model.
- `tuya.product_id_source` is `file` when the local keys file carries the product id, `config`
  when the bridge took it from its `lan_product_id` option (keys files do not always carry it).
- A device whose product is not in the library's tables has `"supported": false` and
  `"definition": null`; the bridge publishes no state for it.
- There is no `ieee_address` on this bridge.

The Companion recognises a pilot-wire thermostat by `tuya.product_id` on this bridge and by
`definition.model` / `model_id` on Zigbee2MQTT. Its identity is `tuya.dev_id` when present,
otherwise `ieee_address`; the link between the two, needed to keep a thermostat's entities when it
changes bridge, is an M5 question.

## 3. State

Provisional names: those Zigbee2MQTT uses for Tuya thermostats. "Read by" says which channel
gives the property; in contract version 1 only the LAN channel publishes.

| Property | Type | Source | Read by |
|---|---|---|---|
| `local_temperature` | float, °C | DP 16 `temp_current` | LAN |
| `current_heating_setpoint` | float, °C, step 0.5 | DP 50 `temp_set` (setpoint of the manual mode) | LAN |
| `system_mode` | `"heat"` \| `"off"` | DP 1 `switch` | LAN |
| `preset` | `"schedule"` \| `"manual"` \| `"eco"` \| `"holiday"` (\| `"frost_protection"`, see below) | DP 2 `mode` | LAN |
| `running_state` | `"heat"` \| `"idle"` | current > 0 when the current is known, else DP 124 `work_state` | LAN |
| `window_open` | bool | DP 17 `window_state` | LAN |
| `holiday_temperature` | float, °C | DP 24 `holiday_temp_set` | LAN |
| `eco_temperature` / `frost_protection_temperature` | float, °C | DP 117 / DP 116 | LAN |
| `power` | float, W | voltage × current; `0.0` when idle | LAN |
| `voltage` / `current` | float, V / A | DP 101 / DP 102; published **only while heating** | LAN |
| `energy` | float, kWh, cumulative | DP 107 `energy_all` | LAN |
| `fault` | list of fault names, empty when none | DP 20 `fault` (bitmap); `bit_<n>` until the bit labels are established | LAN |
| `window_detection` | `"ON"` \| `"OFF"` | `window_check` | sharing API — **not published in version 1** |
| `child_lock` | `"LOCK"` \| `"UNLOCK"` | `child_lock` | sharing API — **not published in version 1** |
| `local_temperature_calibration` | int, −9…9 | `temp_correction` | sharing API — **not published in version 1** |
| `last_seen` | ISO 8601 UTC, `2026-11-27T14:08:26Z` | time of the last read the device answered | bridge |

Rules:

- A property the bridge has **not read** is absent from the object. It is never filled with a
  default or a previous guess. A reader must treat every property as optional.
- The gateway does not return every data point on each read. The bridge keeps the last value
  **read** of each data point and says when it was read (`tuya.read`, below); a state is
  republished only when the device answered the pass.
- A property changes only after the channel confirmed it (zero optimistic state).
- `preset` is published only for a mode value whose meaning is established: `program` →
  `schedule`, `manual`, `eco`, `holiday`. Some devices report a value outside their declared list
  (frost protection): it will be published as `preset: "frost_protection"` once the raw value is
  recorded in the library; until then, and for any other unknown value, **there is no `preset`**
  and the raw value is in `tuya.mode`.
- In `schedule` preset the target setpoint is in no data point. Whether the bridge publishes one
  derived from the weekly schedule is open (scope, question 6); `current_heating_setpoint` stays
  the manual-mode setpoint.

Extension object `tuya` in the state, for what has no Zigbee2MQTT equivalent:

```json
"tuya": {
  "mode": "program",
  "read": {
    "local_temperature": {"channel": "lan", "at": "2026-11-27T14:08:26Z"},
    "energy": {"channel": "lan", "at": "2026-11-27T14:02:26Z"}
  }
}
```

- `mode`: the raw DP 2 value, when read.
- `read`: for each published property, the channel it came from and the time its value was read
  (the oldest of its source data points for a derived property).

## 4. Commands (`<prefix>/<friendly_name>/set`)

**Contract version 1: read only.** Every `/set` is refused, whatever its keys, with this response
on `<prefix>/bridge/response/device/set` (not retained), and nothing is sent to the device:

```json
{"status": "error",
 "error": "read-only bridge: no write route is open in this version, nothing was sent to the device",
 "data": {"device": "<friendly_name>", "keys": ["current_heating_setpoint"]},
 "transaction": "<echoed when the command carried one>"}
```

For an unknown `<friendly_name>` the error starts with `unknown device`. Whether the bridge is
read only is in `bridge/info.read_only`.

What follows is the target for the write milestones (M4). A key is accepted only when a route is
**proven** for it. Any other key is refused the same way; nothing is sent to the device.

| Command | Route | State |
|---|---|---|
| `{"system_mode": "heat" \| "off"}` | sharing API | proven on one thermostat (2026-10-31) |
| `{"preset": "schedule" \| "manual" \| "eco" \| "holiday"}` | sharing API | proven on one thermostat (2026-10-31) |
| `{"current_heating_setpoint": 19.5}` | sharing API | proven on one thermostat (2026-10-31); refused in `frost_protection`, where the device stores the value and does not apply it |
| `{"child_lock": …}`, `{"window_detection": …}`, `{"local_temperature_calibration": …}`, `{"holiday_temperature": …}` | sharing API | candidates, closed: declared by the device, never written |
| `{"preset": "frost_protection"}`, `{"eco_temperature": …}`, `{"frost_protection_temperature": …}` | none | no route |

Values are checked against the device's own bounds and step before sending. After a write the
bridge reads the device back and publishes what it reads; if the device did not take the value,
the response says so.

## 5. Requests (`<prefix>/bridge/request/<action>`)

**Contract version 1:** every request, `schedule/get` included (the developer channel is not
available yet), is answered on `<prefix>/bridge/response/<action>` by
`{"status": "error", "error": "request not available in this version (read-only bridge), nothing was sent to the device", "data": {}}`,
with the request's `transaction` echoed. Target for the later milestones:

| Action | Request | Response `data` |
|---|---|---|
| `schedule/get` | `{"device": "<friendly_name>"}` | `{"monday": [{"time": "00:00", "temperature": 16.0}, …], …}` |
| `schedule/set` | `{"device": "<friendly_name>", "days": {"monday": [ … ]}}` | the schedule read back after the write |

A day is described in full: first transition at 00:00, times on a 15-minute step, temperatures on
a 0.5 °C step. Days not given are left untouched. The write is read-modify-write on the device's
28 schedule data points (codec in `pytuya-pilot-wire`). Route: developer API, proven on one
thermostat (2026-11-14); it needs the user's developer project and an active subscription, and
`bridge/info` says whether that channel is usable.

Rooms are not part of this contract: they belong to the Companion.

## 6. Differences from the Danfoss contract

For readers coming from [`danfoss2mqtt`](https://github.com/aghulas/danfoss2mqtt): same topics and
same rules. The property names differ because Zigbee2MQTT names them differently for the two
families (`occupied_heating_setpoint` there, `current_heating_setpoint` here), and there is no
`bridge/rooms`: the Tuya side has no native room.

## 7. Versions

| Version | Date | Bridge | Change |
|---|---|---|---|
| draft 0 | 2026-11-27 | — | Topic layout and provisional property names; nothing published |
| 1 | 2026-11-27 | 0.1.0 | First published contract, read only, LAN channel. Added: `bridge/info` shape, availability rule, friendly-name rule, `tuya.product_id_source`, `last_seen`, `tuya.read`, refusal payloads. Changed: `preset` absent for an unestablished mode value (was: frost protection always mapped); `fault` uses `bit_<n>`; `voltage` / `current` absent when idle; `window_detection`, `child_lock`, `local_temperature_calibration` not published (sharing API not wired) |
