import requests
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
from flask import Flask

# --- Konfigurace eStudna ---
EMAIL = "viskot@servis-zahrad.cz"
PASSWORD = "poklop1234"
SN = "SB824009"
TOKEN_FILE = "token.json"

START_HOUR = 0
END_HOUR = 6

LOW_LEVEL = 70
HIGH_LEVEL = 80

ON_DURATION = timedelta(minutes=30)
OFF_DURATION = timedelta(minutes=30)

STATE_FILE = "stav.json"
LOG_FILE = "log.txt"

# --- Konfigurace Hydrawise (HW) ---
HW_USER = "viskot@servis-zahrad.cz"
HW_PASS = "Poklop1234*"
HW_RELAY_ID = 10729434   # Trávník (svorka 1)
HW_TOKEN_FILE = "hw_token.json"

# --- Geometrie nádrže (vodorovný válec) ---
TANK_DIAMETER_CM = 171.0
TANK_LENGTH_CM   = 245.8
LEVEL_OFFSET_CM  = 0.0
CAPACITY_L       = 5000.0

R_CM = TANK_DIAMETER_CM / 2.0

def horiz_cyl_volume_l(h_cm: float) -> float:
    h = max(0.0, min(h_cm, TANK_DIAMETER_CM))
    r, L = R_CM, TANK_LENGTH_CM
    if h == 0:
        A = 0.0
    elif h == 2 * r:
        A = math.pi * r * r
    else:
        A = r*r*math.acos((r - h)/r) - (r - h)*math.sqrt(max(0.0, 2*r*h - h*h))
    return (A * L) / 1000.0

# --- Logování ---
def log(message):
    now_str = datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now_str}] {message}\n"
    print(log_line.strip())
    with open(LOG_FILE, "a") as f:
        f.write(log_line)

# --- Token pro HW ---
def HW_load_token():
    if not os.path.exists(HW_TOKEN_FILE):
        return None
    with open(HW_TOKEN_FILE, "r") as f:
        return json.load(f).get("access_token")

def HW_save_token(token):
    with open(HW_TOKEN_FILE, "w") as f:
        json.dump({"access_token": token}, f)

def HW_login(username, password):
    """Přihlášení přes OAuth2, získá access_token."""
    url = "https://api.hydrawise.com/oauth/token"
    data = {
        "grant_type": "password",
        "username": username,
        "password": password
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(url, headers=headers, data=data)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise Exception("Nepodařilo se získat HW access_token")
    HW_save_token(token)
    log("HW: získán nový access_token")
    return token

def HW_get_token():
    token = HW_load_token()
    if token:
        return token
    return HW_login(HW_USER, HW_PASS)

# --- Ovládání HW ---
def HW_runzone(relay_id=HW_RELAY_ID, duration=900):
    url = "https://api.hydrawise.com/v2/relay/run"
    token = HW_get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = {"relay_id": relay_id, "time": duration}
    r = requests.post(url, headers=headers, json=data)
    if r.status_code == 401:  # token expiroval
        token = HW_login(HW_USER, HW_PASS)
        headers["Authorization"] = f"Bearer {token}"
        r = requests.post(url, headers=headers, json=data)
    r.raise_for_status()
    return r.json()

def HW_stopzone(relay_id=HW_RELAY_ID):
    url = "https://api.hydrawise.com/v2/relay/stop"
    token = HW_get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = {"relay_id": relay_id}
    r = requests.post(url, headers=headers, json=data)
    if r.status_code == 401:  # token expiroval
        token = HW_login(HW_USER, HW_PASS)
        headers["Authorization"] = f"Bearer {token}"
        r = requests.post(url, headers=headers, json=data)
    r.raise_for_status()
    return r.json()

# --- ThingsBoard API pro eStudnu ---
def httpPost(url, header={}, params={}, data={}):
    headers = {"Content-Type": "application/json", "Accept": "application/json", **header}
    data = json.dumps(data)
    r = requests.post(url, data=data, headers=headers, params=params)
    if r.status_code == 401:
        raise Exception("Unauthorized")
    r.raise_for_status()
    return r.json()

def httpGet(url, header={}, params={}):
    headers = {"Content-Type": "application/json", "Accept": "application/json", **header}
    r = requests.get(url, headers=headers, params=params)
    if r.status_code == 401:
        raise Exception("Unauthorized")
    r.raise_for_status()
    return r.json()

def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        return json.load(f).get("token")

def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"token": token}, f)

class ThingsBoard:
    def __init__(self):
        self.server = 'https://cml.seapraha.cz'
        self.userToken = load_token()
        self.customerId = None

    def login(self, username: str, password: str):
        try:
            if self.userToken:
                url = f'{self.server}/api/auth/user'
                response = httpGet(url, {'X-Authorization': f"Bearer {self.userToken}"})
                self.customerId = response["customerId"]["id"]
                return
        except:
            pass
        url = f'{self.server}/api/auth/login'
        response = httpPost(url, {}, data={'username': username, 'password': password})
        self.userToken = response["token"]
        save_token(self.userToken)
        url = f'{self.server}/api/auth/user'
        response = httpGet(url, {'X-Authorization': f"Bearer {self.userToken}"})
        self.customerId = response["customerId"]["id"]

    def getDevicesByName(self, name: str):
        url = f'{self.server}/api/customer/{self.customerId}/devices'
        params = {'pageSize': 100, 'page': 0, "textSearch": name}
        response = httpGet(url, {'X-Authorization': f"Bearer {self.userToken}"}, params=params)
        if response["totalElements"] < 1:
            raise Exception(f"Device SN {name} has not been found!")
        return response["data"]

    def getDeviceValues(self, deviceId, keys):
        url = f'{self.server}/api/plugins/telemetry/DEVICE/{deviceId}/values/timeseries'
        params = {'keys': keys}
        return httpGet(url, {'X-Authorization': f"Bearer {self.userToken}"}, params=params)

    def setDeviceOutput(self, deviceId, output: str, value: bool):
        method = "setDout1" if output == "OUT1" else "setDout2"
        data = {"method": method, "params": value}
        url = f'{self.server}/api/rpc/twoway/{deviceId}'
        return httpPost(url, {'X-Authorization': f"Bearer {self.userToken}"}, {}, data)

# --- Funkce pro eStudnu ---
def eStudna_GetWaterLevel(username: str, password: str, serialNumber: str) -> float:
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    values = tb.getDeviceValues(devices[0]["id"]["id"], "ain1")
    return float(values["ain1"][0]["value"]) * 100  # cm

def eStudna_SetOutput(username: str, password: str, serialNumber: str, output: str, state: bool):
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    tb.setDeviceOutput(devices[0]["id"]["id"], output, state)

# --- Stav čerpadla ---
def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"phase": "off", "until": None}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

# --- Hlavní logika řízení eStudny ---
def main():
    now = datetime.now(ZoneInfo("Europe/Prague"))
    hour = now.hour

    LEVEL_OFFSET_CM = 10.0
    level_cm = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
    h_eff = max(0.0, level_cm - LEVEL_OFFSET_CM)

    volume_l = horiz_cyl_volume_l(h_eff)
    cap_l    = min(volume_l, CAPACITY_L)
    percent  = (cap_l / CAPACITY_L) * 100.0

    log(f"Aktuální hladina: {level_cm:.1f} cm | Objem: {volume_l:,.0f} l | Zaplnění do 5000 l: {percent:.1f} %")

    in_allowed_time = START_HOUR <= hour < END_HOUR if START_HOUR < END_HOUR else hour >= START_HOUR or hour < END_HOUR

    if not in_allowed_time:
        msg = f"Mimo povolený čas (00:00–06:00) – Hladina: {level_cm:.1f} cm | Objem: {volume_l:,.0f} l ({percent:.1f} %)"
        log(msg)
        return msg

    state = load_state()
    until = datetime.fromisoformat(state["until"]) if state["until"] else None

    if level_cm >= HIGH_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        save_state({"phase": "off", "until": None})
        msg = f"Hladina {level_cm:.1f} cm je dostatečná, čerpadlo VYPNUTO."
        log(msg)
        return msg

    if state["phase"] == "on" and until and now < until:
        msg = f"Čerpadlo běží do {until}"
        log(msg)
        return msg
    elif state["phase"] == "on":
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        next_until = now + OFF_DURATION
        save_state({"phase": "off", "until": next_until.isoformat()})
        msg = f"Skončila fáze ON, přecházím do pauzy – do {next_until}"
        log(msg)
        return msg

    if state["phase"] == "off" and until and now < until:
        msg = f"Pauza do {until}"
        log(msg)
        return msg
    elif state["phase"] == "off" and level_cm < LOW_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", True)
        next_until = now + ON_DURATION
        save_state({"phase": "on", "until": next_until.isoformat()})
        msg = f"Hladina nízká, čerpadlo ZAPNUTO do {next_until}"
        log(msg)
        return msg

    msg = f"Čekám na pokles hladiny nebo konec pauzy"
    log(msg)
    return msg

# --- Flask server ---
app = Flask(__name__)

@app.route("/")
def spustit():
    try:
        vysledek = main()
        return f"✅ Spuštěno: {vysledek}\n"
    except Exception as e:
        log(f"Chyba: {e}")
        return f"❌ Chyba: {e}\n"

@app.route("/hw_start")
def hw_start():
    try:
        res = HW_runzone()
        return f"✅ HW zóna spuštěna: {res}\n"
    except Exception as e:
        log(f"Chyba HW_start: {e}")
        return f"❌ Chyba spuštění: {e}\n"

@app.route("/hw_stop")
def hw_stop():
    try:
        res = HW_stopzone()
        return f"✅ HW zóna zastavena: {res}\n"
    except Exception as e:
        log(f"Chyba HW_stop: {e}")
        return f"❌ Chyba zastavení: {e}\n"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
