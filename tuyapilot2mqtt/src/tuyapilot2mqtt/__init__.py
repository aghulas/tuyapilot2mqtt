"""tuyapilot2mqtt: Tuya pilot-wire thermostats behind a Tuya gateway, published to MQTT.

A daemon around ``pytuya-pilot-wire``: it holds the sessions and keys, builds the channels, and
publishes the topics of ``docs/mqtt-contract.md``. Read only unless the owner opens the writes of
this version (preset ``manual`` / ``program`` and the manual setpoint, on the designated
thermostats) in the options.
"""

__version__ = "0.4.0"

# Version of docs/mqtt-contract.md this code publishes (``bridge/info.contract``).
CONTRACT_VERSION = 6
