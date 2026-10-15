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

On top of that, a watchdog thread exits the process (with a stack dump of
every thread, so the exact hang location ends up in the log) if the main
loop stops making progress or nothing has been delivered to MQTT for too
long. run.sh then starts the gateway again - a timeout we forgot somewhere
costs one restart instead of days of missing data.
"""
import faulthandler
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt

from hoymiles_client import HoymilesClient

log = logging.getLogger("hoymiles_live_gateway")

MQTT_TOPIC_PREFIX = "home/solar"
MQTT_CONNECT_TIMEOUT = 15  # seconds to wait for the broker handshake
MQTT_PUBLISH_TIMEOUT = 15  # seconds to wait until paho actually sent a message
MAX_BACKOFF = 300  # cap reconnect/retry backoff at 5 minutes, never longer
# Worst case for one loop iteration besides the sleep: login (3 requests) +
# realtime + device tree, each up to REQUEST_TIMEOUT for connect *and* read,
# plus MQTT publish waits. 10 minutes is far above that.
CYCLE_GRACE = 600
# Restart the process if nothing reached MQTT for this long, even though the
# loop itself is still alive (e.g. paho's network thread died silently).
MIN_SILENCE_LIMIT = 3600
WATCHDOG_CHECK_INTERVAL = 30


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
    bad auth response; this one never waits longer than MAX_BACKOFF.

    Threading model: paho's loop_start() thread owns the socket and does all
    (re)connecting itself. The main thread only ever calls publish() and
    reads the thread-safe Events below - it never calls connect() again,
    because doing that while the network thread is also reconnecting races
    on the same socket."""

    def __init__(self, host, port, user, password, use_tls):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="",
                                  clean_session=True, protocol=mqtt.MQTTv311)
        if user:
            self.client.username_pw_set(username=user, password=password)
        if use_tls:
            self.client.tls_set()
        # Without a logger, an exception inside a paho callback kills the
        # network thread without a single line of output.
        self.client.enable_logger(logging.getLogger("paho"))
        self.client.reconnect_delay_set(min_delay=1, max_delay=MAX_BACKOFF)
        self.host = host
        self.port = int(port)
        self._connected = threading.Event()
        # Set on every (re)connect: discovery configs are re-sent each time,
        # so a broker that lost its retained messages heals by itself.
        self.needs_discovery = threading.Event()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    @property
    def connected(self):
        return self._connected.is_set()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self._connected.clear()
            log.error("MQTT connect failed: %s", reason_code)
        else:
            self._connected.set()
            self.needs_discovery.set()
            log.info("MQTT connected")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._connected.clear()
        log.warning("MQTT disconnected: %s (paho reconnects automatically)", reason_code)

    def start(self):
        """Start paho's network thread exactly once. connect_async() makes
        even the *first* connection attempt happen (and be retried) inside
        that thread, so a broker that is down at startup is not fatal."""
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()
        if not self._connected.wait(MQTT_CONNECT_TIMEOUT):
            log.warning("MQTT not connected after %ss, paho keeps retrying in the background",
                        MQTT_CONNECT_TIMEOUT)

    def publish(self, topic, payload_dict, retain=False):
        """Publish and wait (bounded) until paho really wrote it to the socket.
        Raises instead of silently queueing into a dead connection."""
        if not self.connected:
            raise RuntimeError(f"MQTT not connected, cannot publish to {topic}")
        info = self.client.publish(topic, json.dumps(payload_dict), retain=retain)
        info.wait_for_publish(timeout=MQTT_PUBLISH_TIMEOUT)
        if not info.is_published():
            raise RuntimeError(f"MQTT publish to {topic} not sent within {MQTT_PUBLISH_TIMEOUT}s")


class Watchdog:
    """Kills the process if the gateway stops making progress, so the
    surrounding restart loop in run.sh can start a fresh one.

    Two independent conditions:
    - the main loop has not completed an iteration for `stall_limit` seconds
      (a call blocks forever somewhere - exactly the kind of silent hang
      the original add-on suffered from),
    - nothing has been delivered to MQTT for `silence_limit` seconds even
      though the loop still spins (e.g. paho's network thread is gone).

    Before exiting it dumps the stack of every thread to the log, so the
    next occurrence shows *where* it hung instead of just going quiet."""

    def __init__(self, stall_limit, silence_limit):
        self.stall_limit = stall_limit
        self.silence_limit = silence_limit
        now = time.monotonic()
        self._last_beat = now
        self._last_success = now

    def beat(self):
        self._last_beat = time.monotonic()

    def success(self):
        self._last_success = self._last_beat = time.monotonic()

    def start(self):
        threading.Thread(target=self._run, name="watchdog", daemon=True).start()

    def _run(self):
        while True:
            time.sleep(WATCHDOG_CHECK_INTERVAL)
            now = time.monotonic()
            if now - self._last_beat > self.stall_limit:
                self._die(f"main loop made no progress for {int(now - self._last_beat)}s")
            if now - self._last_success > self.silence_limit:
                self._die(f"nothing delivered to MQTT for {int(now - self._last_success)}s")

    @staticmethod
    def _die(reason):
        log.critical("WATCHDOG: %s - dumping all thread stacks and exiting for a restart", reason)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.flush()
        os._exit(3)  # os._exit: works even if other threads hold locks


# (field_key, friendly_name, unit, device_class, state_class, icon) for the
# plant-level discovery payloads. These match the field names/units the
# original add-on's entities were already using (unique_id pattern
# "solarH_<plant_id>_<field_key>"), rediscovered from those entities'
# preserved attributes after they went unavailable - not guessed.
PLANT_FIELDS = [
    ("real_power", "Produktion aktuell W", "W", "power", "measurement", "mdi:solar-power"),
    ("real_power_kw", "Produktion aktuell kW", "kW", "power", "measurement", "mdi:solar-power"),
    ("array_size", "Gesamtleistung Solaranlage W", "W", "power", None, "mdi:solar-panel"),
    ("array_size_kW", "Gesamtleistung Solaranlage kW", "kW", "power", None, "mdi:solar-panel"),
    ("today_eq", "Produktion heute kWh", "kWh", "energy", "total_increasing", None),
    ("today_eq_Wh", "Produktion Heute Wh", "Wh", "energy", "total_increasing", None),
    ("month_eq", "Diesen Monat produziert", "kWh", "energy", "total_increasing", None),
    ("year_eq", "Produktion Jahr kWh", "kWh", "energy", "total_increasing", None),
    ("total_eq", "Produktion gesamt kWh", "kWh", "energy", "total_increasing", "mdi:infinity"),
    ("co2_emission_reduction", "Eingespartes CO2", "t", None, "total_increasing", "mdi:molecule-co2"),
    ("plant_tree", "Kompensierte Bäume", "Trees", None, "total_increasing", "mdi:tree"),
    ("power_ratio", "Leistungsverhältnis", "%", "power_factor", None, "mdi:gauge-full"),
    ("last_data_time", "Datum der letzten Aktualisierung", None, "timestamp", None, None),
]


def publish_discovery_configs(mqtt_client, plant_id):
    """(Re-)publish the MQTT discovery config for each plant-level entity,
    reusing the exact unique_id each one already has so Home Assistant binds
    to the *same* existing entity instead of creating duplicates. Needed
    because these configs are normally retained on the broker, but were
    lost at some point (e.g. when the old add-on was uninstalled) - without
    them Home Assistant no longer knows how to map incoming state_topic
    payloads to entities, even though the raw data keeps arriving fine."""
    node_id = f"solarH_{plant_id}"
    state_topic = f"{MQTT_TOPIC_PREFIX}/json_{plant_id}"
    device = {"identifiers": [node_id], "name": node_id, "manufacturer": "Hoymiles"}

    for field, name, unit, device_class, state_class, icon in PLANT_FIELDS:
        config = {
            "name": name,
            "unique_id": f"{node_id}_{field}",
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{field} }}}}",
            "device": device,
        }
        if unit:
            config["unit_of_measurement"] = unit
        if device_class:
            config["device_class"] = device_class
        if state_class:
            config["state_class"] = state_class
        if icon:
            config["icon"] = icon
        topic = f"homeassistant/sensor/{node_id}/{field}/config"
        mqtt_client.publish(topic, config, retain=True)
    log.info("Published %d MQTT discovery configs for plant %s", len(PLANT_FIELDS), plant_id)


def run(config):
    client = HoymilesClient(config["hoymiles_user"], config["hoymiles_password"],
                             config["hoymiles_plant_id"])
    mqtt_client = MqttPublisher(config["mqtt_host"], config["mqtt_port"],
                                 config["mqtt_user"], config["mqtt_password"],
                                 config["mqtt_tls"])

    poll_interval = config["poll_interval_seconds"]
    backoff = 5

    # Dumps a traceback even on a hard crash inside a C extension
    # (ssl, argon2), which otherwise just ends the log without a word.
    faulthandler.enable()
    watchdog = Watchdog(stall_limit=max(poll_interval, MAX_BACKOFF) + CYCLE_GRACE,
                        silence_limit=max(MIN_SILENCE_LIMIT, 4 * poll_interval))
    watchdog.start()

    log.info("Starting live gateway loop (poll every %ss)", poll_interval)
    mqtt_client.start()

    try:
        while True:
            try:
                if client.token is None:
                    client.login()
                if mqtt_client.needs_discovery.is_set():
                    mqtt_client.needs_discovery.clear()
                    try:
                        publish_discovery_configs(mqtt_client, client.plant_id)
                    except Exception:
                        mqtt_client.needs_discovery.set()  # retry next cycle
                        raise

                raw = client.get_realtime_data()
                solar_data = adjust_solar_data(raw)
                mqtt_client.publish(f"{MQTT_TOPIC_PREFIX}/json_{client.plant_id}", solar_data)
                log.info("Published realtime data (real_power=%sW, today_eq=%skWh)",
                          solar_data.get("real_power"), solar_data.get("today_eq"))
                watchdog.success()

                tree = client.get_device_tree()
                _publish_device_statuses(mqtt_client, tree)

                backoff = 5  # reset after a clean cycle
            except Exception as e:
                log.error("Cycle failed (%s: %s), will retry in %ss", type(e).__name__, e, backoff)
                client.token = None  # force re-login next cycle in case of auth issue
                watchdog.beat()
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            watchdog.beat()
            time.sleep(poll_interval)
    except BaseException:
        # Anything that escapes the per-cycle handler ends the process -
        # make sure it never does so without a traceback in the log.
        log.exception("Live gateway loop terminated unexpectedly")
        raise


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
