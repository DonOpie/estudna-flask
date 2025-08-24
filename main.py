import requests
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import traceback
from flask import Flask

# --- Konfigurace ---
EMAIL = "viskot@servis-zahrad.cz"
PASSWORD = "poklop1234"
SN = "SB824009"

START_HOUR = 0    # začátek povoleného čerpání (00:00)
END_HOUR = 6      # konec povoleného čerpání (06:00)

LOW_LEVEL = 60
HIGH_LEVEL = 70

ON_DURATION = timedelta(minutes=30)
OFF_DURATION = timedelta(minutes=30)

STATE_FILE = "stav.json"
LOG_FILE = "log.txt"
TOKEN_FILE = "token.json"

TZ = ZoneInfo("Europe/Prague")

# --- Logování ---
def log(message: str):
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] {message}\n"
    print(line.strip())
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass  # log nesmí shodit aplikaci

# --- Reset logu jednou denně (volitelné) ---
def check_and_clear_log():
    try:
        if not os.path.exists(LOG_FILE):
            return
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        with open(LOG_FILE, "r") as f:
            first = f.readline()
        if first.startswith("[") and today not in first:
            open(LOG_FILE, "w").close()
    except Exception:
        pass

# --- HTTP helper s retry při expiraci tokenu + lepší diagnostika ---
def http_request_with_retry(method, url, tb, header=None, params=None, data=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if header:
        headers.update(header)
    try:
        if method == "POST":
            r = requests.post(url, headers=headers, params=params or {}, data=json.dumps(data or {}), timeout=30)
        else:
            r = requests.get(url, headers=headers, params=params or {}, timeout=30)

        # Token expiroval -> relogin a jeden retry
        if r.status_code == 401:
            log("Token expiroval – provádím nový login.")
            tb.force_login(EMAIL, PASSWORD)
            headers["X-Authorization"] = f"Bearer {tb.userToken}"
            if method == "POST":
                r = requests.post(url, headers=headers, params=params or {}, data=json.dumps(data or {}), timeout=30)
            else:
                r = requests.get(url, headers=headers, params=params or {}, timeout=30)

        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            log(f"Neočekávaný obsah odpovědi (není JSON). Status={r.status_code}, Text='{r.text[:200]}'")
            raise
    except requests.HTTPError as e:
        txt = e.response.text if e.response is not None else ""
        log(f"HTTPError {getattr(e.response, 'status_code', 'N/A')}: {txt[:300]}")
        raise
    except Exception as e:
        log(f"Chyba HTTP požadavku: {repr(e)}")
        raise

# --- ThingsBoard klient ---
class ThingsBoard:
    def __init__(self):
        self.server = 'https://cml.seapraha.cz'
        self.userToken = None
        self.customerId = None
        self._load_token()

    def _load_token(self):
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, "r") as f:
                    data = json.load(f)
                self.userToken = data.get("token")
                self.customerId = data.get("customerId")
            except Exception:
                pass

    def _save_token(self):
        try:
            with open(TOKEN_FILE, "w") as f:
                json.dump({"token": self.userToken, "customerId": self.customerId}, f)
        except Exception as e:
            log(f"Uložení tokenu selhalo: {repr(e)}")

    def force_login(self, username, password):
        url = f'{self.server}/api/auth/login'
        r = requests.post(url, json={'username': username, 'password': password}, timeout=30)
        r.raise_for_status()
        self.userToken = r.json()["token"]
        url = f'{self.server}/api/auth/user'
        r = requests.get(url, headers={'X-Authorization': f"Bearer {self.userToken}"}, timeout=30)
        r.raise_for_status()
        self.customerId = r.json()["customerId"]["id"]
        self._save_token()
        log("🔑 Nový login do API – token uložen.")

    def login(self, username, password):
        if not self.userToken or not self.customerId:
            self.force_login(username, password)

    def getDevicesByName(self, name: str):
        url = f'{self.server}/api/customer/{self.customerId}/devices'
        params = {'pageSize': 100, 'page': 0, "textSearch": name}
        return http_request_with_retry("GET", url, self, {'X-Authorization': f"Bearer {self.userToken}"}, params=params)

    def getDeviceValues(self, deviceId, keys):
        url = f'{self.server}/api/plugins/telemetry/DEVICE/{deviceId}/values/timeseries'
        params = {'keys': keys}
        return http_request_with_retry("GET", url, self, {'X-Authorization': f"Bearer {self.userToken}"}, params=params)

    def setDeviceOutput(self, deviceId, output: str, value: bool):
        method = "setDout1" if output == "OUT1" else "setDout2"
        data = {"method": method, "params": value}
        url = f'{self.server}/api/rpc/twoway/{deviceId}'
        return http_request_with_retry("POST", url, self, {'X-Authorization': f"Bearer {self.userToken}"}, data=data)

# --- Robustní rozbalení telemetrie ain1 ---
def _extract_ain1_cm(telemetry_obj):
    """
    Vrátí poslední hodnotu ain1 v cm (float) z různých možných tvarů odpovědi TB.
    Podporované příklady:
    - {"ain1": [{"ts": 123, "value": "1.234"}]}
    - {"ain1": [{"value": "1.234"}]}
    - {"ain1": {"1234567890": [{"value": "1.234"}], "1234567891": [{"value": "1.235"}]}}
    - {"ain1": {"value": "1.234"}}
    - {"ain1": "1.234"}
    """
    if not isinstance(telemetry_obj, dict):
        return None

    ain1 = telemetry_obj.get("ain1")
    if ain1 is None:
        return None

    def to_float_cm(v):
        try:
            return float(v) * 100.0
        except Exception:
            return None

    # 1) List položek
    if isinstance(ain1, list):
        if not ain1:
            return None
        item = ain1[-1]  # poslední záznam
        if isinstance(item, dict):
            v = item.get("value")
            return to_float_cm(v)
        return to_float_cm(item)

    # 2) Slovník (často mapování ts -> list hodnot)
    if isinstance(ain1, dict):
        if "value" in ain1 and not isinstance(ain1["value"], (list, dict)):
            return to_float_cm(ain1["value"])

        candidates = []
        for _, v in ain1.items():
            if isinstance(v, list) and v:
                last = v[-1]
                if isinstance(last, dict) and "value" in last:
                    candidates.append(last["value"])
                else:
                    candidates.append(last)
            elif isinstance(v, dict) and "value" in v:
                candidates.append(v["value"])
            elif isinstance(v, (str, int, float)):
                candidates.append(v)
        for val in reversed(candidates):
            cm = to_float_cm(val)
            if cm is not None:
                return cm
        return None

    # 3) Fallback: přímá hodnota
    return to_float_cm(ain1)

# --- Čtení hladiny ---
def eStudna_GetWaterLevel(username: str, password: str, serialNumber: str):
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    if not devices:
        raise RuntimeError("Zařízení nenalezeno podle SN.")
    device_id = devices[0]["id"]["id"]

    data = tb.getDeviceValues(device_id, "ain1")
    level_cm = _extract_ain1_cm(data)
    if level_cm is None:
        log(f"Varování: nepodařilo se rozparsovat telemetrii ain1. Surová data: {str(data)[:300]}")
    return level_cm

# --- Ovládání výstupu ---
def eStudna_SetOutput(username: str, password: str, serialNumber: str, output: str, state: bool):
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    device_id = devices[0]["id"]["id"]
    tb.setDeviceOutput(device_id, output, state)

# --- Ukládání a načítání stavu cyklu ---
def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log(f"Uložení stavu selhalo: {repr(e)}")

def load_state():
    try:
        if not os.path.exists(STATE_FILE):
            return {"phase": "off", "until": None}
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        log(f"Načtení stavu selhalo: {repr(e)}")
        return {"phase": "off", "until": None}

# --- Hlavní logika řízení ---
def main():
    check_and_clear_log()
    now = datetime.now(TZ)
    hour = now.hour
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    level = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
    if level is None:
        log(f"Nelze načíst hladinu (čas serveru: {now_str})")
        return f"[{now_str}] Nelze načíst hladinu – zkusím příště znovu."

    log(f"Aktuální hladina: {level:.1f} cm (čas serveru: {now_str})")

    # Kontrola časového okna (00:00–06:00)
    in_allowed_time = START_HOUR <= hour < END_HOUR
    if not in_allowed_time:
        log("Mimo povolený čas (00:00–06:00)")
        return f"[{now_str}] Mimo povolený čas (00:00–06:00) – Hladina: {level:.1f} cm"

    state = load_state()
    until = datetime.fromisoformat(state["until"]) if state["until"] else None

    if level >= HIGH_LEVEL:
        log(f"Hladina {level:.1f} cm je dostatečná, vypínám čerpadlo.")
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        save_state({"phase": "off", "until": None})
        return f"[{now_str}] Hladina dostatečná ({level:.1f} cm), čerpadlo vypnuto."

    # Fáze ON
    if state["phase"] == "on" and until and now < until:
        log(f"Čerpadlo běží, do {until}")
        return f"[{now_str}] Čerpadlo běží, do {until} – Hladina: {level:.1f} cm"
    elif state["phase"] == "on":
        log("30 minut ON skončilo, vypínám čerpadlo.")
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        next_until = now + OFF_DURATION
        save_state({"phase": "off", "until": next_until.isoformat()})
        return f"[{now_str}] Skončila fáze ON, přecházím do pauzy – Hladina: {level:.1f} cm"

    # Fáze OFF
    if state["phase"] == "off" and until and now < until:
        log(f"Pauza, čekám do {until}")
        return f"[{now_str}] Pauza do {until} – Hladina: {level:.1f} cm"
    elif state["phase"] == "off" and level < LOW_LEVEL:
        log("Hladina nízká, zapínám čerpadlo.")
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", True)
        next_until = now + ON_DURATION
        save_state({"phase": "on", "until": next_until.isoformat()})
        return f"[{now_str}] Čerpadlo zapnuto – fáze ON začíná – Hladina: {level:.1f} cm"

    log("Čekám na pokles hladiny nebo konec pauzy.")
    return f"[{now_str}] Čekám na pokles hladiny nebo konec pauzy – Hladina: {level:.1f} cm"

# --- Flask server ---
app = Flask(__name__)

@app.route("/")
def spustit():
    try:
        return f"✅ Spuštěno: {main()}\n"
    except Exception as e:
        tb = traceback.format_exc()
        log(f"Chyba: {repr(e)}\n{tb}")
        return f"❌ Chyba: {repr(e)}\n"

@app.route("/health")
def health():
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    return f"OK {now}\n"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
