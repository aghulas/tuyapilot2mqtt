# Changelog

## 0.5.0

MQTT contract **version 7**. Built on `pytuya-pilot-wire` 0.5.0, pinned by tag.

- Add-on store icon and logo; the Home Assistant add-on linter runs in CI.

- **Further settings, closed until proven.** `preset` `holiday`, `antifrost` (raw mode `hors`)
  and `eco`, `child_lock`, `window_detection`, `local_temperature_calibration` and
  `holiday_temperature` can be opened by name in the new option `write_settings`, on the
  thermostats of `write_devices`, **once the library marks their route proven** after a
  supervised trial. Until then they stay closed and `bridge/info.writes.refused` says why.
- **Trials.** `trial_setting` and `trial_device` open one candidate setting on one designated
  thermostat, for a supervised trial; `bridge/info.writes.trial` says what is open and where.
- `bridge/info.writes` gains `presets` (the open preset values) and `settings` (the open
  settings); `keys` lists the open `/set` keys.
- The eco and frost-protection setpoints stay without a route: the sharing API does not declare
  them writable.

## 0.3.0

MQTT contract **version 5**. Built on `pytuya-pilot-wire` 0.3.0, pinned by tag.

- **`preset` is writable, `manual` and `program` only.** `eco`, `holiday` and frost protection
  are refused with nothing sent (the library's gate refuses them too). Written through the sharing
  API, sent once, read back: `ok` only when the thermostat reports the mode.
- **Several designated thermostats.** The option `write_devices` (a list of published names or
  Tuya device ids) replaces `write_device`; `bridge/info.writes.devices` lists those found, and
  `writes.keys` is `["current_heating_setpoint", "preset"]`. `writes.device` stays, the first of
  `devices`. A 0.2.x `write_device` option is ignored: list the thermostat in `write_devices`.
- **Rate limit per thermostat**: one write every 10 s and 30 an hour, counted on what was sent;
  above it the command is refused with the delay in the answer.
- **One key per command**: a `/set` carrying two keys is refused with nothing sent. Send
  `preset: manual`, then the setpoint, then `preset: program`.
- The setpoint is still written only in `manual` mode.

## 0.2.3

MQTT contract version 4, unchanged. Built on `pytuya-pilot-wire` 0.2.2, pinned by tag.

- **A session taken over by another client asks for a new login.** When another client logs in
  to the Smart Life account with the same application (Home Assistant's `tuya` integration, for
  instance), the bridge's session is answered `sign invalid` on every request. 0.2.2 retried it
  forever as a passing error (`offline`); the sharing channel is now `unauthenticated`, and the QR
  code to scan appears on the add-on's page. Keep a single client on the account.

## 0.2.2

MQTT contract version 4. Built on `pytuya-pilot-wire` 0.2.1, pinned by tag.

- **The setpoint is written only in `manual` mode.** A `current_heating_setpoint` command is
  refused, with nothing sent, when the designated thermostat is in `program`, `eco`, `holiday` or
  frost protection, or while its mode is not known; the bridge checks the mode it holds, and the
  library's gate checks it again on the read made just before sending. In `program` mode the
  thermostat replaces a written setpoint with the one of the running schedule slot: the write was
  sent, then answered `not confirmed`, and nothing changed (seen on real hardware).
- The log gives the raw command actually sent (data-point code and raw value, e.g.
  `temp_set=95`), never an id or a key.

## 0.2.1

MQTT contract version 3, unchanged.

- **The QR code of the Smart Life login is on the add-on's own page** ("Open web UI", through
  Home Assistant's ingress), no longer in the log. The page refreshes by itself and shows the next
  code when one expires. The log only says that a code is waiting; neither the code nor its text
  is written there any more (0.2.0 wrote the text of the login token). Outside Home Assistant,
  without that page, the code is still printed in the log, as a picture only.

## 0.2.0

MQTT contract version 3. Built on `pytuya-pilot-wire` 0.2.0 (channels, router, write gate, daemon
core), pinned by tag.

- **Sharing API channel** (Smart Life account): add `sharing` to `channels` and fill
  `sharing_user_code`; the QR code to scan is printed in the log. Until the scan, the add-on keeps
  reading through the gateway as before.
- With the account connected: `window_detection`, `child_lock`, `local_temperature_calibration`
  are published, faults are named `e1` / `e2` / `e3`, and on/off, mode and setpoint no longer
  depend on what the gateway returns on a given read. `preset` is `antifrost` in frost protection.
- **One write, closed by default**: `current_heating_setpoint` on the one thermostat designated in
  `write_device`, when `writes_enabled` is true and the account is connected. Checked against the
  device's bounds and step and a 30 °C ceiling, refused in frost protection, sent once, read back;
  answered on `bridge/response/device/set`. Everything else is refused. `bridge/info.read_only`
  is `false` only while that write can really pass.
- Developer channel: accepted with its credentials (reads the eco and frost setpoints); reported
  `unavailable` without them.
- Availability: a thermostat is `offline` after ten minutes without a reading, or when the account
  says so (was: three reads without an answer).
- Not published yet: `temperature_sensibility`, `power` from DP 11, `energy_today`,
  `energy_yesterday` (not read by the library).

## 0.1.1

MQTT contract version 2: the property names are fixed on the Zigbee2MQTT vocabulary, now that the
device is identified (Zigbee `TS0601`, `_TZE204_fu1bfwza`).

- `preset` values are `program`, `manual`, `eco`, `holiday` (was `schedule` for `program`).
- `antifrost_temperature` replaces `frost_protection_temperature`.
- `definition.model` is `TS0601_fu1bfwza`; `ieee_address` is published when the gateway's node id
  is a Zigbee address.

## 0.1.0

First version. **Read only.**

- Reads the thermostats through their Tuya gateway on the LAN (one connection per pass).
- Publishes `bridge/state` (with last will), `bridge/info`, `bridge/devices`, and each
  thermostat's state and availability: MQTT contract version 1.
- Refuses every `/set` and every `bridge/request/*` with an explicit response; nothing is sent to
  a device.
- The sharing API and the developer API are not available yet.
