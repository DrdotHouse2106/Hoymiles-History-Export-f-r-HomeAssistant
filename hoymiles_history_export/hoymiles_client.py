"""Minimal Hoymiles S-Miles Cloud client: login + device discovery + per-day
history curves. The login flow mirrors the one used by the community
"HoyMiles Solar Gateway" Home Assistant add-on (github.com/dmslabsbr/hoymiles).
"""
import hashlib
import json
import logging

import requests
from argon2.low_level import Type, hash_secret_raw

from protobuf_mini import parse_line_chart

log = logging.getLogger("hoymiles_history_export")

BASE_URL = "https://neapi.hoymiles.com"
HEADER_LOGIN = {"Content-Type": "application/json"}
HEADER_DATA = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}


class HoymilesClient:
    def __init__(self, user, password, plant_id):
        self.user = user
        self.password = password
        self.plant_id = int(plant_id)
        self.token = None

    # -- auth ---------------------------------------------------------

    def _pre_insp(self):
        r = requests.post(BASE_URL + "/iam/pub/3/auth/pre-insp", headers=HEADER_LOGIN,
                           data=json.dumps({"u": self.user}), timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "0":
            raise RuntimeError(f"pre_insp failed: {data}")
        return data["data"]

    def _login_argon2(self):
        insp = self._pre_insp()
        n, a = insp.get("n"), insp.get("a")
        if not a:
            raise RuntimeError("argon2 login unavailable (server did not return a salt)")
        salt = bytes.fromhex(a)
        raw = hash_secret_raw(secret=self.password.encode("utf-8"), salt=salt, time_cost=3,
                               memory_cost=32768, parallelism=1, hash_len=32, type=Type.ID)
        payload = json.dumps({"u": self.user, "ch": raw.hex(), "n": n})
        r = requests.post(BASE_URL + "/iam/pub/3/auth/login", headers=HEADER_LOGIN,
                           data=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "0":
            raise RuntimeError(f"login failed: {data}")
        return data["data"]["token"]

    def _login_legacy(self):
        pass_hex = hashlib.md5(self.password.encode()).hexdigest()
        payload = json.dumps({"user_name": self.user, "password": pass_hex})
        r = requests.post(BASE_URL + "/iam/pub/0/auth/login", headers=HEADER_LOGIN,
                           data=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "0":
            raise RuntimeError(f"legacy login failed: {data}")
        return data["data"]["token"]

    def login(self):
        try:
            self.token = self._login_argon2()
            log.info("Logged in (argon2)")
        except Exception as e:
            log.info("argon2 login unavailable (%s), falling back to legacy login", e)
            self.token = self._login_legacy()
            log.info("Logged in (legacy)")
        return self.token

    def _auth_header(self):
        h = dict(HEADER_DATA)
        h["Authorization"] = self.token
        return h

    def _post(self, path, payload):
        r = requests.post(BASE_URL + path, headers=self._auth_header(),
                           data=json.dumps(payload), timeout=30)
        return r

    # -- device discovery ----------------------------------------------

    def discover_micro_inverter_ids(self):
        """Return the list of micro-inverter device ids under this plant."""
        r = self._post("/pvm/api/0/station/select_device_of_tree", {"id": str(self.plant_id)})
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "0":
            raise RuntimeError(f"device tree failed: {data}")
        ids = []

        def walk(nodes):
            for node in nodes:
                # type 3 == microinverter in the observed device tree; collect
                # any node that has its own children==[] and looks like an inverter
                if node.get("type") == 3:
                    ids.append(node["id"])
                walk(node.get("children", []))

        walk(data["data"])
        return ids

    # -- history --------------------------------------------------------

    def fetch_day_curve(self, micro_ids, date_str, retries=2):
        """Return {micro_id: (x_axis_minutes, power_w_list)} for one calendar day."""
        for attempt in range(retries):
            payload = {"sid": self.plant_id, "date": date_str, "mi_list": micro_ids,
                       "quota": ["MI_POWER"]}
            r = self._post("/pvm-data/api/0/micro/data/count_by_day", payload)
            if r.status_code == 200 and r.content:
                chart = parse_line_chart(r.content)
                if chart["series"]:
                    x = [_to_minutes(t) for t in chart["x_axis"]]
                    out = {}
                    for s in chart["series"]:
                        out[s["did"]] = (x, s["data"])
                    return out
            log.warning("empty/failed response for %s (attempt %d), re-logging in", date_str, attempt + 1)
            self.login()
        return None


def _to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)
