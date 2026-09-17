#!/usr/bin/with-contenv bashio

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
export SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN}

python3 /backfill.py

bashio::log.info "Hoymiles History Export finished. This add-on can be stopped now."
