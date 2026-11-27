#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

# Auto-discover Home Assistant's Mosquitto broker when mqtt_host is left empty.
MQTT_HOST="$(bashio::config 'mqtt_host')"
if [ -z "${MQTT_HOST}" ] && bashio::services.available "mqtt"; then
    TUYAPILOT_MQTT_HOST="$(bashio::services mqtt "host")"
    TUYAPILOT_MQTT_PORT="$(bashio::services mqtt "port")"
    TUYAPILOT_MQTT_USERNAME="$(bashio::services mqtt "username")"
    TUYAPILOT_MQTT_PASSWORD="$(bashio::services mqtt "password")"
    export TUYAPILOT_MQTT_HOST TUYAPILOT_MQTT_PORT TUYAPILOT_MQTT_USERNAME TUYAPILOT_MQTT_PASSWORD
    bashio::log.info "Using Home Assistant Mosquitto service at ${TUYAPILOT_MQTT_HOST}:${TUYAPILOT_MQTT_PORT}"
fi

bashio::log.info "Starting tuyapilot2mqtt bridge (read only)..."
exec tuyapilot2mqtt --options /data/options.json
