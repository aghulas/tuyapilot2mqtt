"""tuyapilot2mqtt: Tuya pilot-wire thermostats behind a Tuya gateway, published to MQTT.

A daemon around ``pytuya-pilot-wire``: it holds the sessions and keys, builds the channels, and
publishes the topics of ``docs/mqtt-contract.md``. Read only unless the owner opens the single
write of this version (the manual setpoint of one designated thermostat) in the options.
"""

__version__ = "0.2.2"

# Version of docs/mqtt-contract.md this code publishes (``bridge/info.contract``).
CONTRACT_VERSION = 4
