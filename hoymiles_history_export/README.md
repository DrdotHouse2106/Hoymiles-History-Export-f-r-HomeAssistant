# Hoymiles History Export

A one-shot Home Assistant add-on that backfills **missing historical solar
production data** into one of your Home Assistant statistics, by pulling
per-day power curves directly from the **Hoymiles S-Miles Cloud** and
integrating them into hourly energy values.

## When you need this

- Your local Hoymiles gateway/add-on was misconfigured (wrong IP, offline,
  etc.) for a while, and Home Assistant has a gap in its solar production
  history for that period — even though your actual Hoymiles inverter kept
  reporting fine to the Hoymiles cloud/app the whole time.
- You started using Home Assistant *after* your PV system was already
  installed, and want to import the full history back to installation day.

It does **not** touch your live data or your Hoymiles add-on/integration —
it only fills in the target statistic's history for the date range you give
it, and (if there is already data right after that range) applies a single,
clean forward-shift so old and new data line up with no jump or double
counting.

## Setup

1. Add this repository to your Home Assistant Add-on Store
   (Settings → Add-ons → Add-on Store → ⋮ → Repositories), then install
   **Hoymiles History Export**.
2. Configure:
   - `hoymiles_user` / `hoymiles_password` — your Hoymiles / S-Miles Cloud
     account (the same you use in the Hoymiles app).
   - `hoymiles_plant_id` — your plant/station ID (visible in the Hoymiles
     app, or in your existing Hoymiles Home Assistant entities, e.g.
     `sensor.hoymiles_gateway_solarh_<plant_id>_...`).
   - `statistic_id` — the Home Assistant energy statistic to backfill, e.g.
     `sensor.hoymiles_gateway_solarh_3023680_today_eq` (this should be a
     `total_increasing`/`total` energy sensor, typically the "today"
     production counter your Hoymiles integration already exposes).
   - `start_date` / `end_date` — the date range to fill (`YYYY-MM-DD`,
     inclusive). Check your existing history/statistics graph first to find
     exactly which days are missing.
   - `time_zone` — your Home Assistant time zone (must match, so day
     boundaries line up correctly).
   - `days_per_batch` — how many days to process before each import call;
     the default (30) is a reasonable, polite pace against the Hoymiles
     servers.
3. Start the add-on and watch its log. It logs each day's computed energy as
   it goes, and finishes with a summary. The add-on naturally exits/stops
   when done — that's expected for a one-shot job, not an error.
4. Re-open the Energy dashboard / History graph for your `statistic_id` to
   confirm the gap is filled.

## How it works

- Logs into the Hoymiles cloud the same way the community "HoyMiles Solar
  Gateway" add-on does (Argon2 login, with an automatic fallback to the
  legacy MD5 login if that's unavailable on your account).
- Auto-discovers every micro-inverter under your plant.
- For each day in the range, calls Hoymiles' `count_by_day` endpoint per
  inverter (an intraday power curve, `MI_POWER`, sampled every 15–60
  minutes depending on time of day) and numerically integrates it into 24
  hourly Wh totals, summed across all inverters found.
- Writes those hourly values into Home Assistant's long-term statistics via
  `recorder/import_statistics`, chained onto whatever cumulative total your
  statistic already has right before `start_date` (or starting fresh at 0
  if there's nothing before it).
- If data already exists right after `end_date`, applies one
  `recorder/adjust_sum_statistics` shift so the two periods connect exactly,
  without disturbing anything else.

Talks to Home Assistant through the Supervisor's own API proxy
(`homeassistant_api: true`) — no long-lived access token needs to be pasted
in anywhere.

## Accuracy

Numeric integration of a sampled power curve is an approximation, not a
perfect meter reading — expect roughly 1–2% deviation per day versus what a
continuously-recording meter would have shown. Good enough to make a solar
history chart whole again; not intended as a billing-grade reconstruction.

## Attribution

The Hoymiles cloud login flow follows the same approach as
[dmslabsbr/hoymiles](https://github.com/dmslabsbr/hoymiles) (community
Home Assistant add-on). The `count_by_day` response's protobuf schema was
reverse-engineered by the
[ioBroker.hoymiles](https://github.com/Eistee82/ioBroker.hoymiles) project
(MIT License, Copyright (c) 2026 Eistee82); this add-on implements its own
from-scratch decoder against that published schema.

## Disclaimer

Unofficial, community tool. Not affiliated with or endorsed by Hoymiles.
Uses an undocumented cloud API that Hoymiles could change or restrict at
any time.
