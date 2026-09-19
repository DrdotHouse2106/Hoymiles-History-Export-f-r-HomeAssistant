"""Backfill missing Hoymiles solar production history into a Home Assistant
long-term statistic, by pulling per-day power curves from the Hoymiles
S-Miles Cloud and integrating them into hourly energy values.

Runs as a Home Assistant add-on: reads its configuration from environment
variables set by run.sh, and talks to HA's own WebSocket API through the
Supervisor proxy (no manual long-lived token needed).
"""
import asyncio
import datetime
import json
import logging
import os
import sys
import time
import zoneinfo

import websockets

from hoymiles_client import HoymilesClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")

HA_WS_URL = "ws://supervisor/core/websocket"


def env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        log.error("Missing required option: %s", name)
        sys.exit(1)
    return val


HOYMILES_USER = env("HOYMILES_USER", required=True)
HOYMILES_PASSWORD = env("HOYMILES_PASSWORD", required=True)
HOYMILES_PLANT_ID = env("HOYMILES_PLANT_ID", required=True)
STATISTIC_ID = env("STATISTIC_ID", default="")
START_DATE = env("START_DATE", default="")
END_DATE = env("END_DATE", default="")
DAYS_PER_BATCH = int(env("DAYS_PER_BATCH", "30"))
UNIT = env("UNIT_OF_MEASUREMENT", "kWh")
TZ_NAME = env("TIME_ZONE", "Europe/Berlin")
TZ = zoneinfo.ZoneInfo(TZ_NAME)
SUPERVISOR_TOKEN = os.environ["SUPERVISOR_TOKEN"]

ENABLE_LIVE_GATEWAY = env("ENABLE_LIVE_GATEWAY", "false").lower() == "true"
POLL_INTERVAL_SECONDS = int(env("POLL_INTERVAL_SECONDS", "480"))
# Supervisor auto-injects these when config.json declares services: ["mqtt:want"]
# and the official Mosquitto add-on is installed - no manual entry needed.
MQTT_HOST = env("MQTT_HOST", default="")
MQTT_PORT = int(env("MQTT_PORT", "1883"))
MQTT_USERNAME = env("MQTT_USERNAME", default="")
MQTT_PASSWORD = env("MQTT_PASSWORD", default="")
MQTT_SSL = env("MQTT_SSL", "false").lower() == "true"


# ---------------------------------------------------------------- HA WS ----

async def ha_ws_call(message):
    async with websockets.connect(HA_WS_URL, max_size=None) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": SUPERVISOR_TOKEN}))
        auth = json.loads(await ws.recv())
        if auth["type"] != "auth_ok":
            raise RuntimeError(f"HA auth failed: {auth}")
        message["id"] = 1
        await ws.send(json.dumps(message))
        return json.loads(await ws.recv())


def anchor_sum_before(day):
    """Return the statistic's cumulative sum right before `day` (00:00 local),
    or 0.0 if there is no earlier data at all."""
    start = (day - datetime.timedelta(days=14)).isoformat()
    end = day.isoformat()
    resp = asyncio.run(ha_ws_call({
        "type": "recorder/statistics_during_period",
        "start_time": f"{start}T00:00:00{_utc_offset_str(day)}",
        "end_time": f"{end}T00:00:00{_utc_offset_str(day)}",
        "period": "hour",
        "statistic_ids": [STATISTIC_ID],
        "types": ["sum"],
    }))
    pts = resp.get("result", {}).get(STATISTIC_ID, [])
    if not pts:
        return 0.0
    return pts[-1]["sum"]


def sum_at_or_after(day):
    """Return the (start_iso, sum) of the first existing statistic point on or
    after `day`, or None if there is nothing there yet (i.e. this is the tip
    of all currently known data)."""
    start = day.isoformat()
    end = (day + datetime.timedelta(days=14)).isoformat()
    resp = asyncio.run(ha_ws_call({
        "type": "recorder/statistics_during_period",
        "start_time": f"{start}T00:00:00{_utc_offset_str(day)}",
        "end_time": f"{end}T00:00:00{_utc_offset_str(day)}",
        "period": "hour",
        "statistic_ids": [STATISTIC_ID],
        "types": ["sum"],
    }))
    pts = resp.get("result", {}).get(STATISTIC_ID, [])
    if not pts:
        return None
    return pts[0]["start"], pts[0]["sum"]


def _utc_offset_str(day):
    dt = datetime.datetime(day.year, day.month, day.day, tzinfo=TZ)
    offset = dt.utcoffset()
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def import_statistics(entries):
    resp = asyncio.run(ha_ws_call({
        "type": "recorder/import_statistics",
        "metadata": {
            "source": "recorder",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": UNIT,
            "has_mean": False,
            "has_sum": True,
            "name": None,
        },
        "stats": entries,
    }))
    if not resp.get("success"):
        log.error("import_statistics failed: %s", resp)
    return resp


def adjust_sum(start_iso, adjustment):
    resp = asyncio.run(ha_ws_call({
        "type": "recorder/adjust_sum_statistics",
        "statistic_id": STATISTIC_ID,
        "start_time": start_iso,
        "adjustment": adjustment,
        "adjustment_unit_of_measurement": UNIT,
    }))
    if not resp.get("success"):
        log.error("adjust_sum_statistics failed: %s", resp)
    return resp


# ------------------------------------------------------------ integration ----

def hourly_energy_wh(x_minutes, power_w):
    hourly = [0.0] * 24
    for i in range(len(x_minutes) - 1):
        t0, t1 = x_minutes[i], x_minutes[i + 1]
        p0, p1 = power_w[i], power_w[i + 1]
        mid_hour = int(min(max(((t0 + t1) / 2) // 60, 0), 23))
        dt_h = (t1 - t0) / 60.0
        hourly[mid_hour] += (p0 + p1) / 2 * dt_h
    return hourly


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += datetime.timedelta(days=1)


# ------------------------------------------------------------------ main ----

def run_history_backfill():
    start_day = datetime.date.fromisoformat(START_DATE)
    final_day = datetime.date.fromisoformat(END_DATE)

    client = HoymilesClient(HOYMILES_USER, HOYMILES_PASSWORD, HOYMILES_PLANT_ID)
    client.login()

    micro_ids = client.discover_micro_inverter_ids()
    if not micro_ids:
        log.error("No micro-inverters found under plant %s - aborting.", HOYMILES_PLANT_ID)
        sys.exit(1)
    log.info("Discovered micro-inverters: %s", micro_ids)

    running_sum = anchor_sum_before(start_day)
    log.info("Anchor sum before %s: %.3f %s", start_day, running_sum, UNIT)

    day = start_day
    while day <= final_day:
        batch_end = min(day + datetime.timedelta(days=DAYS_PER_BATCH - 1), final_day)
        log.info("Batch: %s .. %s", day, batch_end)
        entries = []

        for cur in daterange(day, batch_end):
            date_str = cur.isoformat()
            curves = client.fetch_day_curve(micro_ids, date_str)
            if not curves:
                log.warning("  %s: FAILED to fetch (no data from cloud, skipping)", date_str)
                continue

            combined_hourly = [0.0] * 24
            for mid, (x, y) in curves.items():
                hourly = hourly_energy_wh(x, y)
                for h in range(24):
                    combined_hourly[h] += hourly[h]

            day_start_local = datetime.datetime(cur.year, cur.month, cur.day, tzinfo=TZ)
            cum_today = 0.0
            for h in range(24):
                cum_today += combined_hourly[h] / 1000.0
                ts = (day_start_local + datetime.timedelta(hours=h)).isoformat()
                entries.append({
                    "start": ts,
                    "state": round(cum_today, 3),
                    "sum": round(running_sum + cum_today, 3),
                })
            running_sum += cum_today
            log.info("  %s: %.2f %s  (running total %.2f %s)", date_str, cum_today, UNIT,
                      running_sum, UNIT)
            time.sleep(0.8)  # be polite to Hoymiles' servers

        if entries:
            import_statistics(entries)
        day = batch_end + datetime.timedelta(days=1)

    log.info("Backfill of %s..%s complete. Final running sum: %.3f %s",
              start_day, final_day, running_sum, UNIT)

    # If there is already data right after the filled range, connect the two
    # seamlessly with a one-time forward shift (does not touch anything else).
    tail = sum_at_or_after(final_day + datetime.timedelta(days=1))
    if tail is not None:
        tail_start_ms, tail_sum = tail
        gap = running_sum - tail_sum
        if abs(gap) > 0.01:
            tail_start_iso = datetime.datetime.fromtimestamp(
                tail_start_ms / 1000, tz=datetime.timezone.utc).astimezone(TZ).isoformat()
            log.info("Existing data found after the gap (sum=%.3f) - applying a "
                      "one-time +%.3f %s shift at %s to connect seamlessly.",
                      tail_sum, gap, UNIT, tail_start_iso)
            adjust_sum(tail_start_iso, gap)
        else:
            log.info("Existing data after the gap already lines up, no shift needed.")
    else:
        log.info("No existing data after the filled range (this was the newest gap).")

    log.info("Done.")


def main():
    if START_DATE and END_DATE:
        run_history_backfill()
    else:
        log.info("No start_date/end_date given - skipping history backfill.")

    if ENABLE_LIVE_GATEWAY:
        if not MQTT_HOST:
            log.error("enable_live_gateway is on but no MQTT broker was found "
                       "(install/start the Mosquitto broker add-on) - aborting.")
            sys.exit(1)
        import live_gateway
        live_gateway.run({
            "hoymiles_user": HOYMILES_USER,
            "hoymiles_password": HOYMILES_PASSWORD,
            "hoymiles_plant_id": HOYMILES_PLANT_ID,
            "mqtt_host": MQTT_HOST,
            "mqtt_port": MQTT_PORT,
            "mqtt_user": MQTT_USERNAME,
            "mqtt_password": MQTT_PASSWORD,
            "mqtt_tls": MQTT_SSL,
            "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        })
    else:
        log.info("enable_live_gateway is off - add-on finished, will stop now.")


if __name__ == "__main__":
    main()
