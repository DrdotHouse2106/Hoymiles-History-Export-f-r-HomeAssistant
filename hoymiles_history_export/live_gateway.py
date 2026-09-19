"""Continuous Hoymiles live-data -> MQTT gateway.

A robustness-focused reimplementation of what the community "HoyMiles Solar
Gateway" add-on (dmslabsbr/hoymiles) does, publishing to the *same* MQTT
topics with the *same* JSON field names so already-configured Home Assistant
MQTT sensors keep working unchanged.

Two concrete bugs in that add-on's source motivated writing this instead of
just re-using it (found by reading its code after our own instance silently
stopped publishing twice, with no error anywhere):

1. Its HTTP client (`sess.send(prepped)` in hoymilesapi.py) sets no
   `timeout` at all, so a single slow/hanging Hoymiles-cloud response can
   freeze the entire polling loop forever - the process stays "running",
   nothing crashes, but no new data is ever published again.
2. Its MQTT `on_connect` handler, on an auth failure (return code 4 or 5),
   calls `time.sleep(60000)` - not 60 seconds, sixty THOUSAND seconds
   (~16.6 hours) - freezing MQTT reconnect handling for the rest of the day.

Every network call here has an explicit timeout, and every loop iteration
is wrapped so one failure just logs and retries next cycle instead of
hanging or crashing the whole process.
"""
import json
import logging
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from hoymiles_client import HoymilesClient

log = logging.getLogger("hoymiles_live_gateway")

MQTT_TOPIC_PREFIX = "home/solar"
MQTT_CONNECT_TIMEOUT = 15  # seconds to wait for the broker handshake
MAX_BACKOFF = 300  # cap reconnect/retry backoff at 5 minutes, never longer


def adjust_solar_data(raw):
    """Reproduce the original add-on's field renaming/unit conversions so
    existing HA MQTT sensors (built against its output) keep working."""
    data = dict(raw)
    real_power = float(data["real_power"]) if data.get("real_power") else 0.0
    array_size = float(data.get("capacitor") or 0)
    if 0 < array_size < 100:
        array_size *= 1000
    power_ratio = round((real_power / array_size) * 100, 1) if array_size else 0.0

    data["real_power"] = str(real_power)
    data["real_power_kw"] = str(round(real_power / 1000, 3))
    data["real_power_measurement"] = str(real_power)
    data["real_power_total_increasing"] = str(real_power)
    data["power_ratio"] = str(power_ratio)
    data["array_size"] = str(array_size)
    data["array_size_kW"] = str(array_size / 1000)
    data.pop("capacitor", None)

    if data.get("co2_emission_reduction"):
        data["co2_emission_reduction"] = str(round(float(data["co2_emission_reduction"]) / 1_000_000, 5))

    if data.get("today_eq") is not None:
        data["today_eq_Wh"] = data["today_eq"]
        data["today_eq"] = str(round(float(data["today_eq"]) / 1000, 2))
    if data.get("month_eq") is not None:
        data["month_eq"] = str(round(float(data["month_eq"]) / 1000, 2))
    if data.get("year_eq"):
        data["year_eq"] = str(round(float(data["year_eq"]) / 1000, 2))
    if data.get("total_eq") is not None:
        data["total_eq"] = str(round(float(data["total_eq"]) / 1000, 2))

    last_data_time = data.get("last_data_time")
    if last_data_time:
        try:
            dt = datetime.strptime(last_data_time, "%Y-%m-%d %H:%M:%S")
            data["last_data_time"] = dt.astimezone().isoformat()
        except ValueError:
            pass  # leave as-is if the format ever changes upstream

    data.pop("reflux_station_data", None)
    return data


class MqttPublisher:
    """Thin paho-mqtt wrapper with sane, *bounded* reconnect backoff -
    the original add-on's equivalent can sleep for ~16.6 hours on a single
    bad auth response; this one never waits longer than MAX_BACKOFF."""

    def __init__(self, host, port, user, password, use_tls):
        self.client = mqtt.Client(client_id="", clean_session=True, protocol=mqtt.MQTTv311)
        if user:
            self.client.username_pw_set(username=user, password=password)
        if use_tls:
            self.client.tls_set()
        self.host = host
        self.port = port
        self.connected = False
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            log.info("MQTT connected")
        else:
            self.connected = False
            log.error("MQTT connect failed, rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        log.warning("MQTT disconnected, rc=%s", rc)

    def connect(self):
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        deadline = time.monotonic() + MQTT_CONNECT_TIMEOUT
        while not self.connected and time.monotonic() < deadline:
            time.sleep(0.2)
        if not self.connected:
            raise RuntimeError(f"MQTT did not connect within {MQTT_CONNECT_TIMEOUT}s")

    def publish(self, topic, payload_dict):
        self.client.publish(topic, json.dumps(payload_dict))


def run(config):
    client = HoymilesClient(config["hoymiles_user"], config["hoymiles_password"],
                             config["hoymiles_plant_id"])
    mqtt_client = MqttPublisher(config["mqtt_host"], config["mqtt_port"],
                                 config["mqtt_user"], config["mqtt_password"],
                                 config["mqtt_tls"])

    poll_interval = config["poll_interval_seconds"]
    backoff = 5

    log.info("Starting live gateway loop (poll every %ss)", poll_interval)

    while True:
        try:
            if client.token is None:
                client.login()
            if not mqtt_client.connected:
                mqtt_client.connect()

            raw = client.get_realtime_data()
            solar_data = adjust_solar_data(raw)
            mqtt_client.publish(f"{MQTT_TOPIC_PREFIX}/json_{client.plant_id}", solar_data)
            log.info("Published realtime data (real_power=%sW, today_eq=%skWh)",
                      solar_data.get("real_power"), solar_data.get("today_eq"))

            tree = client.get_device_tree()
            _publish_device_statuses(mqtt_client, tree)

            backoff = 5  # reset after a clean cycle
        except Exception as e:
            log.error("Cycle failed (%s), will retry in %ss", e, backoff)
            client.token = None  # force re-login next cycle in case of auth issue
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
            continue

        time.sleep(poll_interval)


def _publish_device_statuses(mqtt_client, tree, _depth=0):
    for node in tree:
        warn = node.get("warn_data", {})
        status = {
            "connect": "ON" if warn.get("connect") else "OFF",
            "alarm_code": 0,
            "alarm_string": "",
        }
        mqtt_client.publish(f"{MQTT_TOPIC_PREFIX}/json_{node['id']}", status)
        _publish_device_statuses(mqtt_client, node.get("children", []), _depth + 1)
