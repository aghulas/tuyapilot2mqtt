"""tuyapilot2mqtt: Tuya pilot-wire thermostats behind a Tuya gateway, published to MQTT.

A daemon around ``pytuya-pilot-wire``: it holds the sessions and keys, reads, and publishes the
topics of ``docs/mqtt-contract.md``. Read only in this version.
"""

__version__ = "0.1.1"

# Version of docs/mqtt-contract.md this code publishes (``bridge/info.contract``).
CONTRACT_VERSION = 2
