# MQTT contract

Contract version: **7** (2026-11-28). Published by `tuyapilot2mqtt` 0.5.0 and later: sharing API, LAN
and developer API channels, **read only unless the owner opens the writes of this version** (§4,
§5) on the thermostats it designates. The same
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
  "version": "0.5.0",
  "contract": 7,
  "library": {"name": "pytuya-pilot-wire", "version": "0.5.0"},
  "read_only": true,
  "writes": {"enabled": false, "keys": ["current_heating_setpoint", "preset"],
             "presets": ["manual", "program"], "settings": [], "devices": [],
             "device": null, "closed": "writes are disabled in the options",
             "requests": [], "requests_closed": "writes are disabled in the options"},
  "poll_interval": 60,
  "channels": {
    "sharing": {"enabled": true, "state": "online", "last_ok": "2026-11-27T14:08:26Z"},
    "lan": {"enabled": true, "state": "online", "last_ok": "2026-11-27T14:08:31Z"},
    "developer": {"enabled": false}
  }
}
```

- `read_only` is `false` only when a write can really pass: the owner enabled writes in the
  bridge's options, designated thermostats (`write_devices`), at least one of them is among the
  devices, and the sharing channel is connected. In every other case it is `true` and every
  command is refused (§4). A reader that offers a write control must follow this flag, and offer
  it only on the thermostats of `writes.devices`.
- `writes` says why: `enabled` is the option, `keys` the `/set` keys open on every designated
  thermostat, `presets` the `preset` values open on them, `settings` the further settings the
  owner opened (§4.1), `devices` the friendly names of the designated thermostats found among
  the devices (in the order of their names; empty when none), `device` the first of them or
  `null` (kept for readers of version 4, which had a single designated thermostat), and `closed`
  the reason no write passes now (absent when `read_only` is `false`).
- `writes.refused` (absent when empty): the settings the owner named that do not open, with the
  reason (`"candidate route: opens once the library records its supervised trial"`).
- `writes.trial` (absent when no trial is set): a supervised trial, one candidate setting open on
  one thermostat only, `{"setting": "child_lock", "key": "child_lock", "device": "<friendly_name>"}`
  (`"preset"` added for a preset setting; `"device": null` and `"closed"` when the trial
  thermostat is not designated). **A reader must not offer a control for a trial**: it is the
  owner's test, run by hand.
- `writes.requests` lists the write requests of §5 that can pass now, on the thermostats of
  `writes.devices`: `["schedule/set"]` when writes are enabled, a designated thermostat is among
  the devices and the developer channel is `online`; empty otherwise, with the reason in
  `writes.requests_closed`. It does not depend on the sharing channel: `read_only` is about the
  `/set` commands of §4. A reader that offers a schedule write follows this list.
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
| `tuya.schedule` | weekly schedule, same shape as §5 | DP 125–152 `week_program_18_<day>_<part>` | developer API |
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
  the manual-mode setpoint. The schedule itself is published (`tuya.schedule`): a reader can
  derive the slot running now from it.
- `tuya.schedule` is read through the developer channel **once an hour**, in the same request as
  the eco and frost setpoints, and after every `schedule/get` and `schedule/set` (§5). A change
  made in the Tuya app shows within the hour. It holds the days that were read in full; it is
  absent until a first reading and never replaced by an unreadable one.

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
- `schedule`: the weekly schedule (above), when read.
- `read`: for each published property, the channel its value came from (`sharing`, `lan` or
  `developer`) and the time it was read; `read.schedule` for `schedule`.

## 4. Commands (`<prefix>/<friendly_name>/set`)

**Two writes open with the writes, closed by default**, on the thermostats the owner designates in
the bridge's options (`write_devices`), **one key per command**, and **further settings** the
owner opens one by one after their trial (§4.1):

| Key | Values | Condition |
|---|---|---|
| `preset` | `"manual"` or `"program"`; and each value of `writes.presets` | any mode; a value not in `writes.presets` is refused |
| `current_heating_setpoint` | °C, the device's bounds and step, 30 °C at most | the thermostat is in `manual` (`preset: "manual"`) |

Whether they are open is `bridge/info.read_only`, `bridge/info.writes.keys`, `writes.presets` and
`writes.devices` (§1.1); the mode is not part of `read_only`, it is checked per command.

### 4.1 Further settings

Each is a **closed candidate** until a supervised trial on one thermostat proves its route; the
library then marks it proven, and the owner opens it by name in the bridge's options
(`write_settings`). It is then in `writes.settings`, and its key (or preset value) in
`writes.keys` (or `writes.presets`), for every designated thermostat. A setting named in the
options whose route is still a candidate is in `writes.refused` and stays closed.

| Setting | `/set` | Values | Data point | State |
|---|---|---|---|---|
| `preset_holiday` | `preset` | `"holiday"` | DP 2 `mode` = `holiday` | candidate |
| `preset_antifrost` | `preset` | `"antifrost"` | DP 2 `mode` = `hors`, a value outside the declared list | candidate |
| `preset_eco` | `preset` | `"eco"` | DP 2 `mode` = `eco` | candidate |
| `child_lock` | `child_lock` | `"LOCK"` \| `"UNLOCK"` | DP 39 `child_lock` | candidate |
| `window_detection` | `window_detection` | `"ON"` \| `"OFF"` | DP 29 `window_check` | candidate |
| `local_temperature_calibration` | `local_temperature_calibration` | int, the device's bounds (−9…9, step 1) | DP 19 `temp_correction` | candidate |
| `holiday_temperature` | `holiday_temperature` | °C, the device's bounds and step (5…40 by 0.5), 30 °C at most | DP 24 `holiday_temp_set` | candidate |

Each is checked against the device's own declaration before sending, sent once and read back
like the two writes above; none depends on the mode. **No route**: `eco_temperature` and
`antifrost_temperature` (DP 117 / 116): the sharing API does not declare them writable (they
exist as developer-API shadow properties only).

```json
{"preset": "manual", "transaction": "<optional, echoed in the response>"}
{"current_heating_setpoint": 19.5, "transaction": "<optional>"}
```

`transaction` is an identifier, not a command key. **Every** `/set` is answered on
`<prefix>/bridge/response/device/set` (not retained). A reader that wants a manual setpoint sends
`preset: "manual"`, waits for its answer, then sends the setpoint; to give the thermostat back
to its weekly schedule, it sends `preset: "program"`. Entering `manual` restores the setpoint the thermostat
keeps for that mode, which can be far from the value read in `program` (21 °C was seen): the
thermostat regulates on it until the setpoint command is confirmed.

A command the bridge refuses without sending anything — read-only bridge, unknown device, no key,
two keys, a key not in `writes.keys`, a thermostat that is not designated, a `preset` not in
`writes.presets`, a setpoint for a thermostat whose last published `preset` is not
`manual` or whose mode is not known yet, a write over the rate limit — is answered:

```json
{"status": "error",
 "error": "read-only bridge: writes are disabled in the options; nothing was sent to the device",
 "data": {"device": "<friendly_name>", "keys": ["current_heating_setpoint"]},
 "transaction": "<echoed when the command carried one>"}
```

**Rate limit**, per thermostat, on the commands actually sent: one every 10 s and 30 an hour.
Above it the command is refused, nothing sent, and `error` says when to retry:
`"rate limit: one write every 10 s and 30 an hour per thermostat; retry in 4 s; nothing was sent to the device"`.
A command refused before sending is not counted.

A command that passes goes through the library's write gate, in this order: the value is checked
against **the device's own bounds and step** (5 to 40 °C by 0.5 on this product) and a **30 °C
ceiling** the bridge adds, or against the modes the device declares and the two this version
opens; the device is read, and the write is refused when it is offline or, for a setpoint, in
any mode but `manual` (see below); the command is sent **once**, never retried; the device is
read back, every 2 s for 20 s at most. The state object is republished from that read-back, then
the response is published:

```json
{"status": "ok",
 "data": {"device": "<friendly_name>",
          "results": [{"property": "preset", "value": "manual", "confirmed": true}]},
 "transaction": "<echoed>"}
```

`"status": "ok"` means **written and read back**. A value refused by the gate, a device that did
not take the value in time (`"confirmed": false`) or a channel failure give `"status": "error"`
with the reason in `error`; `results` is then empty or says which write was not confirmed. The
response comes within about 30 s of the command (a command that arrives during a read pass waits
for it); a reader should allow 45 s.

**Why `manual` only for the setpoint.** In `program` mode the thermostat follows its weekly
schedule: a written setpoint is accepted by the account, then replaced within seconds by the
setpoint of the running schedule slot, so the read-back never shows the value and the command is
answered `"confirmed": false` with nothing changed. This was observed on real hardware during the
first supervised trial (a setpoint sent once to a thermostat in `program`, read back at the
slot's value). In `antifrost` the device stores a written setpoint and does not apply it; in
`eco` and `holiday` it follows the setpoint of that mode, and nothing shows a written one would
be kept. The refusal names the mode:

```json
{"status": "error",
 "error": "current_heating_setpoint: the device is in 'program' mode, where the running schedule slot overwrites a written setpoint; a setpoint is written only in 'manual' mode; nothing was sent to the device",
 "data": {"device": "<friendly_name>", "keys": ["current_heating_setpoint"]}}
```

The gate reads the mode again just before sending: a thermostat that left `manual` since the last
published state is refused there, with `results` empty and nothing sent.

**Why `manual` and `program` only for the preset.** They are the two modes a room needs (a
manual setpoint, then back to the schedule). In `eco` and `holiday` the thermostat follows a
setpoint that has no proven write route (`eco_temperature`) or a holiday period the bridge does
not set; `antifrost` is not a mode the device declares, and has no route.

Routes of the commands:

| Command | Route | State |
|---|---|---|
| `{"current_heating_setpoint": 19.5}` | sharing API | proven on one thermostat in `manual` mode (2026-10-31), then through this bridge on two thermostats (2026-11-28, [trial 2](https://github.com/aghulas/ha-tuya-pilot-wire/blob/main/docs/validation/2026-11-28-trial-2-preset-and-setpoint.md)); **open for the designated thermostats, in `manual` mode only** |
| `{"preset": "manual" \| "program"}` | sharing API | proven on one thermostat (2026-10-31), then through this bridge on two thermostats (2026-11-28, same trial); **open for the designated thermostats** |
| `{"preset": "eco" \| "holiday"}` | sharing API | candidates, closed (§4.1) |
| `{"preset": "antifrost"}` | sharing API | candidate, closed (§4.1): the raw value `hors` is outside the declared list; whether the account accepts it is the trial's to establish |
| `{"system_mode": "heat" \| "off"}` | sharing API | proven on one thermostat (2026-10-31); closed in this version |
| `{"child_lock": …}`, `{"window_detection": …}`, `{"local_temperature_calibration": …}`, `{"holiday_temperature": …}` | sharing API | candidates, closed (§4.1): declared by the device, never written |
| `{"eco_temperature": …}`, `{"antifrost_temperature": …}` | none | no route: not declared writable by the sharing API |

## 5. Requests (`<prefix>/bridge/request/<action>`)

Two requests, on the **weekly schedule**, through the developer channel. Every request is answered
on `<prefix>/bridge/response/<action>` (not retained), with the request's `transaction` echoed;
`data.device` repeats the request's `device`.

| Action | Request | Open | Response `data` |
|---|---|---|---|
| `schedule/get` | `{"device": "<friendly_name>"}` | every thermostat, while the developer channel is `online` | `{"device": …, "schedule": {"monday": [ … ], …}}` |
| `schedule/set` | `{"device": "<friendly_name>", "days": {"saturday": [ … ]}}` | the thermostats of `writes.devices`, while `writes.requests` holds `schedule/set` (§1.1) | `{"device": …, "days": [ … ], "written": <parts sent>, "confirmed": true, "schedule": { … }}` |

A **schedule** is an object of days, `monday` to `sunday`; a **day** is its list of setpoint
changes in time order:

```json
"saturday": [{"time": "00:00", "temperature": 7.0},
             {"time": "07:30", "temperature": 19.0},
             {"time": "17:00", "temperature": 7.0}]
```

- A day is described **in full**: the first change is at `00:00`, times are `HH:MM` on a
  15-minute step and distinct, temperatures are °C on a 0.5 °C step, **5 to 30 °C**. A day of
  the same temperature all day is one change at `00:00`.
- `schedule/get` reads the device's 28 data points and answers the days read in full; a day
  with a missing part is left out and the answer is then `"status": "error"` ("some days could
  not be read in full"), with what was read.
- `schedule/set` takes **one or more days**; the days not given are left untouched. It is
  read-modify-write: the bridge reads the device's data points, encodes each day given keeping
  the 12-byte header each part carries, and sends **once** only the parts that differ (`written`
  is their number; `0` when the device already holds the days asked). It then reads the
  schedule back, every 5 s for 30 s at most. `"status": "ok"` means **written and read back**;
  `confirmed: false` (with `"status": "error"`) means the read-back still differs, and
  `schedule` is what was read. The response comes within about 40 s; a reader should allow 60 s.
- A `schedule/set` counts in the rate limit of §4 (one write every 10 s and 30 an hour per
  thermostat), like a `/set`.
- After both requests the state object is republished when the schedule read differs from the
  one held (`tuya.schedule`, §3).

Refused before anything is sent, `"status": "error"` and `error` ending in
`nothing was sent to the device`: an unknown action, a payload that is not a JSON object, an
unknown device; for `schedule/get` the developer channel not `online`
(`"schedule unavailable: …"`); for `schedule/set` the reason of `writes.requests_closed`
(`"schedule read only: …"`), a thermostat that is not designated, `days` missing or empty, an
unknown day, a write over the rate limit. A day that is not described as above is refused by the
library's gate (`"schedule: …"`), with nothing sent.

Route: developer API (device shadow properties), the only one that returns these data points;
the LAN does not return them and the sharing API does not declare them. Read on six thermostats
(2026-11-03, 2026-11-28); written read-modify-write and restored on one thermostat
(2026-11-14). The developer API signs in with the developer project's credentials, not as a
Smart Life client: it does not touch the account's session held by the sharing channel.

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
| 4 | 2026-11-27 | 0.2.2 | `current_heating_setpoint` is accepted only while the designated thermostat is in `manual` mode; in any other mode, or while the mode is unknown, the command is refused with nothing sent and the mode named in `error`. Added: the reason (§4, "Why `manual` only"): in `program` the running schedule slot overwrites a written setpoint |
| 5 | 2026-11-27 | 0.3.0 | Added: `preset` is writable, `manual` and `program` only (`eco`, `holiday`, `antifrost` refused, nothing sent); several designated thermostats, `bridge/info.writes.devices`; `writes.keys` is `["current_heating_setpoint", "preset"]`; a rate limit per thermostat (one write every 10 s, 30 an hour), refused with the delay in `error`. Changed: one key per command (two keys are refused); `writes.device` is the first of `devices`, kept for readers of version 4; refusal texts ("designated thermostats only", "one key per command") |
| 6 | 2026-11-28 | 0.4.0 | Weekly schedule through the developer channel. Added: `bridge/request/schedule/get` (every thermostat) and `schedule/set` (designated thermostats, one or more days in full, read-modify-write, read back; counted by the rate limit), answered with `data.device` and `data.schedule`; `tuya.schedule` and `tuya.read.schedule` in the state, read hourly with the settings; `bridge/info.writes.requests` and `writes.requests_closed`. Changed: a request is no longer always refused; refusal texts of §5 |
| 7 | 2026-11-28 | 0.5.0 | Added: further settings (§4.1), each a closed candidate until its trial, opened by name in the options once proven: `preset` `holiday`, `antifrost`, `eco`, `child_lock`, `window_detection`, `local_temperature_calibration`, `holiday_temperature`; `bridge/info.writes.presets`, `writes.settings`, `writes.refused`, `writes.trial`. Changed: `writes.keys` lists the open keys (unchanged while no setting is open); refusal texts for a preset or key that is not open (`"preset: 'eco' is not open (open: manual, program)"`) |
