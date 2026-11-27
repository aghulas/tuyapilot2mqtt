# MQTT contract

Status: **draft 0** (2026-11-27). Nothing publishes it yet. The topic layout is settled; the
property names are provisional until the device's Zigbee2MQTT definition is known (milestone M0 of
the [roadmap](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/roadmap.md)).

The contract is the **Zigbee2MQTT dialect**: a thermostat published by `tuyapilot2mqtt` and the
same thermostat paired to Zigbee2MQTT must look the same to the Companion wherever they mean the
same thing. Where Zigbee2MQTT defines a topic or a property, this contract uses it unchanged; what
it adds is under `bridge/request/*` and in a `tuya` extension object.

`<prefix>` is `tuyapilot2mqtt` by default.

## 1. Topics

| Topic | Retain | Direction | Payload |
|---|---|---|---|
| `<prefix>/bridge/state` | yes | bridge → Companion | `{"state": "online"}` or `{"state": "offline"}` (last will) |
| `<prefix>/bridge/info` | yes | bridge → Companion | bridge version, contract version, state of each channel |
| `<prefix>/bridge/devices` | yes | bridge → Companion | JSON array of device descriptors (§2) |
| `<prefix>/<friendly_name>` | yes | bridge → Companion | JSON state object (§3) |
| `<prefix>/<friendly_name>/availability` | yes | bridge → Companion | `{"state": "online"}` or `{"state": "offline"}` |
| `<prefix>/<friendly_name>/set` | no | Companion → bridge | JSON command object (§4) |
| `<prefix>/bridge/request/<action>` | no | Companion → bridge | JSON request (§5) |
| `<prefix>/bridge/response/<action>` | no | bridge → Companion | `{"status": "ok" \| "error", "data": …, "error": …, "transaction": …}` |

## 2. Device descriptor

Same shape as a Zigbee2MQTT entry, limited to what the Companion reads:

```json
{
  "friendly_name": "Library 1",
  "type": "EndDevice",
  "supported": true,
  "definition": {"vendor": "Tuya", "model": "<to fix at M0>", "description": "Pilot-wire thermostat"},
  "tuya": {"dev_id": "<device id>", "product_id": "fu1bfwza", "category": "wk", "gateway": "<gateway id>"}
}
```

The Companion recognises a pilot-wire thermostat by `tuya.product_id` on this bridge and by
`definition.model` / `model_id` on Zigbee2MQTT. Its identity is `tuya.dev_id` when present,
otherwise `ieee_address`; the link between the two, needed to keep a thermostat's entities when it
changes bridge, is an M5 question.

## 3. State

Provisional names: those Zigbee2MQTT uses for Tuya thermostats.

| Property | Type | Source |
|---|---|---|
| `local_temperature` | float, °C | DP 16 `temp_current` |
| `current_heating_setpoint` | float, °C, step 0.5 | DP 50 `temp_set` (setpoint of the manual mode) |
| `system_mode` | `"heat"` \| `"off"` | DP 1 `switch` |
| `preset` | `"schedule"` \| `"manual"` \| `"eco"` \| `"holiday"` \| `"frost_protection"` | DP 2 `mode` |
| `running_state` | `"heat"` \| `"idle"` | current > 0, else DP 124 `work_state` |
| `window_open` | bool | DP 17 `window_state` |
| `window_detection` | `"ON"` \| `"OFF"` | `window_check` |
| `child_lock` | `"LOCK"` \| `"UNLOCK"` | `child_lock` |
| `local_temperature_calibration` | int, −9…9 | `temp_correction` |
| `holiday_temperature` | float, °C | DP 24 `holiday_temp_set` |
| `eco_temperature` / `frost_protection_temperature` | float, °C | DP 117 / DP 116 |
| `power` | float, W | voltage × current; 0 when idle |
| `voltage` / `current` | float, V / A | DP 101 / DP 102, only while heating |
| `energy` | float, kWh, cumulative | DP 107 `energy_all` |
| `fault` | list of fault names, empty when none | DP 20 `fault` |

Rules:

- A property the bridge has **not read** is absent from the object. It is never filled with a
  default or a previous guess.
- A property changes only after the channel confirmed it (zero optimistic state).
- The mode value some devices report outside their declared list (frost protection) is published
  as `preset: "frost_protection"`.
- In `schedule` preset the target setpoint is in no data point. Whether the bridge publishes one
  derived from the weekly schedule is open (scope, question 6); `current_heating_setpoint` stays
  the manual-mode setpoint.

Extension object `tuya` in the state, for what has no Zigbee2MQTT equivalent: the raw `mode`, and
for each property the channel it came from and its age.

## 4. Commands (`<prefix>/<friendly_name>/set`)

A key is accepted only when a route is **proven** for it. Any other key is refused, and the
refusal is published on `<prefix>/bridge/response/device/set`; nothing is sent to the device.

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
