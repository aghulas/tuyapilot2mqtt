# tuyapilot2mqtt — guide for agent sessions

Bridge and (later) Home Assistant add-on of the pilot-wire project. Scope, architecture and
roadmap: `~/dev/ha-tuya-pilot-wire/docs/` (read `scope.md` first). This repository owns the
contract, `docs/mqtt-contract.md`: change it here first, then the library codec and the Companion.

## Rules

- **Public repository.** No secret, no real device id, no site data anywhere, logs included.
- **All Tuya logic is in `pytuya-pilot-wire`.** This repository is a daemon around it (sessions,
  polling, MQTT) and its packaging; pin the library by version in the image.
- **Zigbee2MQTT dialect first.** Before naming a topic or a property, check what Zigbee2MQTT
  publishes for the same thing and use it unchanged.
- **Proven routes only, zero optimistic state.** A command with no proven route is refused; a
  state is published after the device confirmed it.
- **Model**: `~/dev/danfoss2mqtt` (add-on layout: `repository.yaml`, one folder per add-on with
  `config.yaml`, `Dockerfile`, `build.yaml`, `run.sh`, `DOCS.md`; CI validating the manifests and
  smoke-testing the image).
- A write to real hardware needs the owner's agreement on the exact operation, every time.

Commit messages in English. Commit and push only when the owner asks.
