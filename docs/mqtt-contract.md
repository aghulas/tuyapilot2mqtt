# MQTT contract

Contract version: **3** (2026-11-27). Published by `tuyapilot2mqtt` 0.2.0: sharing API and LAN
channels, **read only unless the owner opens the single write of this version** (§4). The same
number is in `bridge/info.contract`; it is incremented at every change of this document that a
reader of the topics can observe. History: [§7](#7-versions).

The device is identified (milestone M0,
[`device.md`](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/device.md)): Zigbee,
model `TS0601`, manufacturer `_TZE204_fu1bfwza`, with **no Zigbee2MQTT definition upstream**. The
property names are therefore those Zigbee2MQTT gives the closest pilot-wire thermostat
(`_TZE204_3regm3h6`); the converter written at M5 will use the same.

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
`#`, `/` and control characters become spaces, spaces are collapsed) and unique (` 2`, ` 3`…
appended to a repeated name; `bridge` is reserved). It can contain spaces, as in Zigbee2MQTT.

### 1.1 `bridge/info`

```json
{
  "version": "0.2.0",
  "contract": 3,
  "library": {"name": "pytuya-pilot-wire", "version": "0.2.0"},
  "read_only": true,
  "writes": {"enabled": false, "keys": ["current_heating_setpoint"], "device": null,
             "closed": "writes are disabled in the options"},
  "poll_interval": 60,
  "channels": {
    "sharing": {"enabled": true, "state": "online", "last_ok": "2026-11-27T14:08:26Z"},
    "lan": {"enabled": true, "state": "online", "last_ok": "2026-11-27T14:08:31Z"},
    "developer": {"enabled": false}
  }
}
```

- `read_only` is `false` only when a write can really pass: the owner enabled writes in the
  bridge's options, designated one thermostat, that thermostat is among the devices, and the
  sharing channel is connected. In every other case it is `true` and every command is refused
  (§4). A reader that offers a write control must follow this flag.
- `writes` says why: `enabled` is the option, `keys` the `/set` keys this version can open,
  `device` the friendly name of the designated thermostat (`null` when none or unknown), and
  `closed` the reason no write passes now (absent when `read_only` is `false`).
- A channel's `state`, when it is `enabled`:

  | State | Meaning |
  |---|---|
  | `starting` | no call made yet |
  | `online` | the last call succeeded (`last_ok` is its time) |
  | `offline` | the last call failed; `error` says how; retried with a growing delay, 15 minutes at most |
  | `unauthenticated` | the credentials were refused; the channel is paused |
  | `login_required` | sharing channel without a session, and no login can start (`error` says what is missing) |
  | `waiting_for_scan` | sharing channel: a QR code is shown by the bridge (the add-on's page; its log outside Home Assistant), waiting for the owner to scan it with the Smart Life app |
  | `login_expired` | sharing channel: nobody scanned in time; the bridge shows a new code when it restarts |
  | `unavailable` | enabled in the options but not usable (`error`: credentials missing) |

`bridge/info` is republished when something in it other than a `last_ok` changes.

### 1.2 Availability

`<prefix>/<friendly_name>/availability` is `online` when a reading of the device is younger than
10 minutes and, while the sharing channel is connected, the account says the device is online.
It is `offline` otherwise. A device that was never read since the bridge started has no state
topic published by this run.

## 2. Device descriptor

Same shape as a Zigbee2MQTT entry, limited to what the Companion reads:

```json
{
  "ieee_address": "0xa4c1380000000001",
  "friendly_name": "Library 1",
  "type": "EndDevice",
  "supported": true,
  "definition": {"vendor": "Tuya", "model": "TS0601_fu1bfwza", "description": "Pilot-wire thermostat"},
  "tuya": {"dev_id": "<device id>", "product_id": "fu1bfwza", "product_id_source": "account",
           "category": "wk", "gateway": "<gateway id>"}
}
```

- `definition.model` is the Zigbee model id and the product id; the name the M5 converter will
  declare.
- `ieee_address` is the gateway's node id of the device, prefixed `0x`, published when it is a
  Zigbee address (16 hexadecimal digits) and absent otherwise. It is what links a thermostat on
  this bridge to the same thermostat on Zigbee2MQTT.
- `tuya.product_id_source` is `account` when the sharing API listed the device, `file` when the
  local keys file carries the product id, `config` when the bridge took it from its
  `lan_product_id` option (keys files do not always carry it).
- `tuya.gateway` is absent when no source gave it (the sharing API does not; the keys file does).
- Only supported thermostats are listed: a device of the account whose product is not in the
  library's tables is not in `bridge/devices`.
- The list comes from the sharing API when it is connected and from the local keys file until
  then. It is never replaced by an empty list because a channel failed.

The Companion recognises a pilot-wire thermostat by `tuya.product_id` on this bridge and by
`model_id` `TS0601` with a manufacturer name ending in `_fu1bfwza` on Zigbee2MQTT. Its identity is
`tuya.dev_id` when present, otherwise `ieee_address`.

## 3. State

"Read by" says which channel gives the property **when the sharing channel is connected**. While
it is not (no session yet, session refused, API failing), the LAN channel stands in for every
property it returns, as in versions 1 and 2. `tuya.read` says, for each value, where it came from.

| Property | Type | Source | Read by |
|---|---|---|---|
| `local_temperature` | float, °C | DP 16 `temp_current` | sharing API |
| `current_heating_setpoint` | float, °C, step 0.5 | DP 50 `temp_set` (setpoint of the manual mode) | sharing API |
| `system_mode` | `"heat"` \| `"off"` | DP 1 `switch` | sharing API |
| `preset` | `"program"` \| `"manual"` \| `"eco"` \| `"holiday"` \| `"antifrost"` | DP 2 `mode` | sharing API |
| `window_open` | bool | DP 17 `window_state` | sharing API |
| `window_detection` | `"ON"` \| `"OFF"` | DP 29 `window_check` | sharing API |
| `child_lock` | `"LOCK"` \| `"UNLOCK"` | DP 39 `child_lock` | sharing API |
| `local_temperature_calibration` | int, −9…9 | DP 19 `temp_correction` | sharing API |
| `holiday_temperature` | float, °C | DP 24 `holiday_temp_set` | sharing API |
| `fault` | list of fault names, empty when none | DP 20 `fault` (bitmap); the names are the labels the device declares (`e1`, `e2`, `e3`), `bit_<n>` for a bit without a label or while the sharing API has not listed the device | sharing API |
| `running_state` | `"heat"` \| `"idle"` | current > 0 when the current is known, else DP 124 `work_state` | LAN |
| `power` | float, W | voltage × current; `0.0` when idle | LAN |
| `voltage` / `current` | float, V / A | DP 101 / DP 102; published **only while heating** | LAN |
| `energy` | float, kWh, cumulative | DP 107 `energy_all` | LAN |
| `eco_temperature` / `antifrost_temperature` | float, °C | DP 117 / DP 116 | developer API, else LAN |
| `last_seen` | ISO 8601 UTC, `2026-11-27T14:08:26Z` | time of the last reading of the device, any channel | bridge |

Announced, **not published**: `temperature_sensibility` (DP 103), `power` from DP 11 `work_power`,
`energy_today` / `energy_yesterday`. The library's data-point table (0.2.0) has neither DP 11 nor
DP 103, and the daily-energy data points have not moved on the reference install since pairing.
They appear here when the library reads them.

Rules:

- A property the bridge has **not read** is absent from the object. It is never filled with a
  default or a previous guess. A reader must treat every property as optional.
- The sharing API returns no load state, power or energy: without the LAN channel
  `running_state`, `power`, `voltage`, `current` and `energy` are absent. The gateway does not
  return every data point on each LAN read: they can also be absent until a read brings them.
- The bridge keeps the last value **read** of each property and says when it was read
  (`tuya.read`, below); a state is republished when a reading of the device arrived.
- A property changes only from a reading of the device (zero optimistic state), a command
  included: after a write, what is published is what was read back.
- `preset` is `antifrost` for the frost-protection mode, which the devices report outside their
  declared list (raw value `hors`). For any other unknown raw value **there is no `preset`**; the
  raw value is always in `tuya.mode`.
- In `program` preset the target setpoint is in no data point. Whether the bridge publishes one
  derived from the weekly schedule is open (scope, question 6); `current_heating_setpoint` stays
  the manual-mode setpoint.

Extension object `tuya` in the state, for what has no Zigbee2MQTT equivalent:

```json
"tuya": {
  "mode": "program",
  "read": {
    "local_temperature": {"channel": "sharing", "at": "2026-11-27T14:08:26Z"},
    "energy": {"channel": "lan", "at": "2026-11-27T14:02:26Z"}
  }
}
```

- `mode`: the raw DP 2 value, when read.
- `read`: for each published property, the channel its value came from (`sharing`, `lan` or
  `developer`) and the time it was read.

## 4. Commands (`<prefix>/<friendly_name>/set`)

**One write in this version, closed by default**: `current_heating_setpoint`, on the single
thermostat the owner designates in the bridge's options, for its supervised trial (milestone M4).
Whether it is open is `bridge/info.read_only` (§1.1).

```json
{"current_heating_setpoint": 19.5, "transaction": "<optional, echoed in the response>"}
```

`transaction` is an identifier, not a command key. **Every** `/set` is answered on
`<prefix>/bridge/response/device/set` (not retained).

A command the bridge refuses without looking at its values — read-only bridge, unknown device,
another key (alone or next to the open one), another thermostat than the designated one — sends
nothing to the device and is answered:

```json
{"status": "error",
 "error": "read-only bridge: writes are disabled in the options; nothing was sent to the device",
 "data": {"device": "<friendly_name>", "keys": ["current_heating_setpoint"]},
 "transaction": "<echoed when the command carried one>"}
```

A command that passes goes through the library's write gate, in this order: the value is checked
against **the device's own bounds and step** (5 to 40 °C by 0.5 on this product) and against a
**30 °C ceiling** the bridge adds; the device is read, and the write is refused when it is offline
or in `antifrost` (where it stores the value and does not apply it); the command is sent **once**,
never retried; the device is read back, every 2 s for 20 s at most. The state object is
republished from that read-back, then the response is published:

```json
{"status": "ok",
 "data": {"device": "<friendly_name>",
          "results": [{"property": "current_heating_setpoint", "value": 19.5, "confirmed": true}]},
 "transaction": "<echoed>"}
```

`"status": "ok"` means **written and read back**. A value refused by the gate, a device that did
not take the value in time (`"confirmed": false`) or a channel failure give `"status": "error"`
with the reason in `error`; `results` is then empty or says which write was not confirmed. The
response comes within about 30 s of the command (a command that arrives during a read pass waits
for it); a reader should allow 45 s.

Routes of the other commands, for the later steps of M4. None is accepted in this version.

| Command | Route | State |
|---|---|---|
| `{"current_heating_setpoint": 19.5}` | sharing API | proven on one thermostat (2026-10-31); **open in this version for the designated thermostat** |
| `{"system_mode": "heat" \| "off"}` | sharing API | proven on one thermostat (2026-10-31); closed in this version |
| `{"preset": "program" \| "manual" \| "eco" \| "holiday"}` | sharing API | proven on one thermostat (2026-10-31); closed in this version |
| `{"child_lock": …}`, `{"window_detection": …}`, `{"local_temperature_calibration": …}`, `{"holiday_temperature": …}` | sharing API | candidates, closed: declared by the device, never written |
| `{"preset": "antifrost"}`, `{"eco_temperature": …}`, `{"antifrost_temperature": …}` | none | no route |

## 5. Requests (`<prefix>/bridge/request/<action>`)

**In this version** every request, `schedule/get` included, is answered on
`<prefix>/bridge/response/<action>` by
`{"status": "error", "error": "request not available in this version; nothing was sent to the device", "data": {}}`,
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
| 2 | 2026-11-27 | 0.1.1 | Names fixed after M0 on the Zigbee2MQTT vocabulary of the closest pilot-wire thermostat. Changed: `preset` value `schedule` → `program` (and the future `frost_protection` → `antifrost`); `frost_protection_temperature` → `antifrost_temperature`; `definition.model` `FP Thermostat` → `TS0601_fu1bfwza`. Added: `ieee_address` in the descriptor. Announced, not published yet: `temperature_sensibility`, `energy_today`, `energy_yesterday`, `power` from DP 11 |
| 3 | 2026-11-27 | 0.2.0 | Sharing API channel (QR-code login) and first write. Added: `bridge/info.writes`; channel states `unauthenticated`, `login_required`, `waiting_for_scan`, `login_expired`, `unavailable`, and `last_ok`; `window_detection`, `child_lock`, `local_temperature_calibration` published; `preset: "antifrost"`; `fault` names `e1` / `e2` / `e3`; `product_id_source: "account"`; `/set` accepts `current_heating_setpoint` for one designated thermostat when `read_only` is `false`, with a response to every `/set` (`data.results`, 30 °C ceiling). Changed: each property comes from the channel proven for it when the sharing API is connected (the LAN alone otherwise); availability is "a reading younger than 10 minutes, and online for the account" (was: three passes without an answer); `bridge/info` is republished on change only and its channel entries lose `devices` / `devices_answering` and the state `unsupported`; unsupported devices are no longer listed; `tuya.gateway` can be absent; refusal texts. Still announced, not published: `temperature_sensibility`, `energy_today`, `energy_yesterday`, `power` from DP 11 |
