import asyncio
import datetime
import json
import zoneinfo

import websockets

from hoymiles_client import HoymilesClient

TZ = zoneinfo.ZoneInfo("Europe/Berlin")
HA_WS = "ws://192.168.72.3:8123/api/websocket"
HA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI1M2RmNzVkOTViMjk0ODViOWIyZWFmM2M0NTUwNDRiZiIsImlhdCI6MTc4OTYyNzUyNiwiZXhwIjoyMTA0OTg3NTI2fQ.1XiidK5sptrTVKLcK0abIQdLR_xEdMOG0SdAASb8o50"
STATISTIC_ID = "sensor.hoymiles_gateway_solarh_3023680_today_eq"


def hourly_energy_wh(x_minutes, power_w):
    hourly = [0.0] * 24
    for i in range(len(x_minutes) - 1):
        t0, t1 = x_minutes[i], x_minutes[i + 1]
        p0, p1 = power_w[i], power_w[i + 1]
        mid_hour = int(min(max(((t0 + t1) / 2) // 60, 0), 23))
        dt_h = (t1 - t0) / 60.0
        hourly[mid_hour] += (p0 + p1) / 2 * dt_h
    return hourly


async def ha_ws_call(message):
    async with websockets.connect(HA_WS, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        await ws.recv()
        message["id"] = 1
        await ws.send(json.dumps(message))
        return json.loads(await ws.recv())


def main():
    now_local = datetime.datetime.now(TZ)
    today = now_local.date()
    current_hour = now_local.hour
    print(f"Now: {now_local}, current_hour={current_hour}")

    client = HoymilesClient("hoymiles@ulber.me", "hoymilesFs4Hv1K!", "3023680")
    client.login()
    curves = client.fetch_day_curve([10638890], today.isoformat())
    combined_hourly = [0.0] * 24
    for mid, (x, y) in curves.items():
        hourly = hourly_energy_wh(x, y)
        for h in range(24):
            combined_hourly[h] += hourly[h]

    # anchor = sum right before today
    resp = asyncio.run(ha_ws_call({
        "type": "recorder/statistics_during_period",
        "start_time": (today - datetime.timedelta(days=1)).isoformat() + "T00:00:00+02:00",
        "end_time": today.isoformat() + "T00:00:00+02:00",
        "period": "hour",
        "statistic_ids": [STATISTIC_ID],
        "types": ["sum"],
    }))
    pts = resp["result"].get(STATISTIC_ID, [])
    anchor = pts[-1]["sum"] if pts else 0.0
    print("Anchor before today:", anchor)

    day_start_local = datetime.datetime(today.year, today.month, today.day, tzinfo=TZ)
    cum = 0.0
    entries = []
    for h in range(current_hour):
        cum += combined_hourly[h] / 1000.0
        ts = (day_start_local + datetime.timedelta(hours=h)).isoformat()
        entries.append({"start": ts, "state": round(cum, 3), "sum": round(anchor + cum, 3)})

    # Hours from now through end of day already have STALE placeholder rows
    # from the earlier accidental backfill (frozen at an old wrong value) -
    # simply not writing new rows would leave those stale rows untouched, so
    # overwrite them with a flat continuation at today's *real* total-so-far
    # instead. This makes today's number correct as of now; the live gateway
    # can't auto-update these remaining hours today because Home Assistant's
    # normal compiler skips hours that already have a stats row - a one-off
    # side effect of today's earlier accidental backfill, not an ongoing
    # problem (tomorrow onward, the live gateway alone writes today's data).
    for h in range(current_hour, 24):
        ts = (day_start_local + datetime.timedelta(hours=h)).isoformat()
        entries.append({"start": ts, "state": round(cum, 3), "sum": round(anchor + cum, 3)})

    print(f"Writing all 24 hours: 00:00..{current_hour-1}:00 with real data, "
          f"{current_hour}:00..23:00 flat at today-so-far = {cum:.3f} kWh")
    for e in entries:
        print(" ", e)

    resp = asyncio.run(ha_ws_call({
        "type": "recorder/import_statistics",
        "metadata": {
            "source": "recorder",
            "statistic_id": STATISTIC_ID,
            "unit_of_measurement": "kWh",
            "has_mean": False,
            "has_sum": True,
            "name": None,
        },
        "stats": entries,
    }))
    print("Import result:", json.dumps(resp))


if __name__ == "__main__":
    main()
