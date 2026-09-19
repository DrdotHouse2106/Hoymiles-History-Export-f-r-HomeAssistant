#!/usr/bin/with-contenv bashio

bashio::log.warning "=== PROOF OF CONCEPT / UNGETESTET ==="
bashio::log.warning "Dieses Add-on ist das Ergebnis einer Recherche, kein fertig getestetes Produkt."
bashio::log.warning "Die Kernlogik wurde manuell gegen einen echten Account getestet, diese"
bashio::log.warning "generalisierte Add-on-Verpackung selbst aber nicht. Details siehe README."
bashio::log.warning "======================================"
bashio::log.info "Starting Hoymiles History Export..."

export HOYMILES_USER=$(bashio::config 'hoymiles_user')
export HOYMILES_PASSWORD=$(bashio::config 'hoymiles_password')
export HOYMILES_PLANT_ID=$(bashio::config 'hoymiles_plant_id')
export STATISTIC_ID=$(bashio::config 'statistic_id')
export START_DATE=$(bashio::config 'start_date')
export END_DATE=$(bashio::config 'end_date')
export UNIT_OF_MEASUREMENT=$(bashio::config 'unit_of_measurement')
export TIME_ZONE=$(bashio::config 'time_zone')
export DAYS_PER_BATCH=$(bashio::config 'days_per_batch')
export ENABLE_LIVE_GATEWAY=$(bashio::config 'enable_live_gateway')
export POLL_INTERVAL_SECONDS=$(bashio::config 'poll_interval_seconds')
export SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN}

if bashio::config.true 'enable_live_gateway'; then
    if bashio::services.available "mqtt"; then
        export MQTT_HOST=$(bashio::services "mqtt" "host")
        export MQTT_PORT=$(bashio::services "mqtt" "port")
        export MQTT_USERNAME=$(bashio::services "mqtt" "username")
        export MQTT_PASSWORD=$(bashio::services "mqtt" "password")
        export MQTT_SSL=$(bashio::services "mqtt" "ssl")
        bashio::log.info "MQTT service found: ${MQTT_HOST}:${MQTT_PORT}"
    else
        bashio::log.warning "enable_live_gateway is on but no MQTT service was found."
        bashio::log.warning "Install/start the Mosquitto broker add-on first."
    fi
fi

python3 /backfill.py

bashio::log.info "Hoymiles History Export & Live Gateway: process ended."
