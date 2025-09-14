import requests
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import subprocess
from flask import Flask
import asyncio
from pydrawise import Auth, Hydrawise

# --- Konfigurace eStudna ---
EMAIL = "viskot@servis-zahrad.cz"
PASSWORD = "Poklop1234*"
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

# --- Geometrie nádrže (vodorovný válec) ---
TANK_DIAMETER_CM = 171.0
TANK_LENGTH_CM   = 245.8
LEVEL_OFFSET_CM  = 0.0
CAPACITY_L       = 5000.0
R_CM = TANK_DIAMETER_CM / 2.0

# --- Hydrawise konfigurace ---
EMAIL_HW = "viskot@servis-zahrad.cz"
PASSWORD_HW = "Poklop1234*"
ZONE_ID = 10729434   # Trávník
HIGH_LEVEL_HW = 150  # cm – spustit
LOW_LEVEL_HW  = 130  # cm – vypnout


# --- Výpočty objemu ---
def horiz_cyl_volume_l(h_cm: float) -> float:
    h = max(0.0, min(h_cm, TANK_DIAMETER_CM))
    r, L = R_CM, TANK_LENGTH_CM
    if h == 0:
        A = 0.0
    elif h == 2 * r:
        A = math.pi * r * r
    else:
        A = r*r*math.acos((r - h)/r) - (r - h)*math.sqrt(max(0.0, 2*r*h - h*h))
    return (A * L) / 1000.0  # cm³ -> l


# --- Logování ---
def check_and_clear_log():
    if not os.path.exists(LOG_FILE):
        return
    today = datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d")
    try:
        with open(LOG_FILE, "r") as f:
            first_line = f.readline()
        if first_line.startswith("[") and today not in first_line:
            open(LOG_FILE, "w").close()
    except:
        pass

def log(message):
    now_str = datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now_str}] {message}\n"
    print(log_line.strip())
    with open(LOG_FILE, "a") as f:
        f.write(log_line)


# --- HTTP pomocné funkce ---
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


# --- Token ---
def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        return json.load(f).get("token")

def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"token": token}, f)


# --- Třída ThingsBoard ---
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


# --- Funkce eStudna ---
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


# --- Uložení stavu ---
def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"phase": "off", "until": None}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


# --- Hydrawise funkce ---
async def HW_start_zone(zone_id, duration=3600):
    auth = Auth(EMAIL_HW, PASSWORD_HW)
    hw = Hydrawise(auth)
    controllers = await hw.get_controllers(fetch_zones=True)
    controller = controllers[0]
    zones = await hw.get_zones(controller)
    for z in zones:
        if z.id == zone_id:
            await hw.start_zone(z, custom_run_duration=duration)
            log(f"🌧 HW: Spouštím zónu {z.name} na {duration}s (hladina ≥ {HIGH_LEVEL_HW} cm).")
            return f"HW: Zóna {z.name} spuštěna na {duration}s"
    return None

async def HW_stop_zone(zone_id):
    auth = Auth(EMAIL_HW, PASSWORD_HW)
    hw = Hydrawise(auth)
    controllers = await hw.get_controllers(fetch_zones=True)
    controller = controllers[0]
    zones = await hw.get_zones(controller)
    for z in zones:
        if z.id == zone_id:
            await hw.stop_zone(z)
            log(f"🌧 HW: Vypínám zónu {z.name} (hladina ≤ {LOW_LEVEL_HW} cm).")
            return f"HW: Zóna {z.name} vypnuta"
    return None

def check_HW_logic(level_cm: float):
    if level_cm >= HIGH_LEVEL_HW:
        return asyncio.run(HW_start_zone(ZONE_ID))
    elif level_cm <= LOW_LEVEL_HW:
        return asyncio.run(HW_stop_zone(ZONE_ID))
    return "HW: žádná akce"


# --- Hlavní logika ---
def main():
    check_and_clear_log()
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
        msg = f"Mimo povolený čas – Hladina: {level_cm:.1f} cm | Objem: {volume_l:,.0f} l ({percent:.1f} %)"
        log(msg)
        return msg

    state = load_state()
    until = datetime.fromisoformat(state["until"]) if state["until"] else None

    if level_cm >= HIGH_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        save_state({"phase": "off", "until": None})
        msg = f"Hladina {level_cm:.1f} cm je dostatečná, čerpadlo VYPNUTO."
        log(msg)
    elif state["phase"] == "on" and until and now < until:
        msg = f"Čerpadlo běží do {until}"
        log(msg)
    elif state["phase"] == "on":
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        next_until = now + OFF_DURATION
        save_state({"phase": "off", "until": next_until.isoformat()})
        msg = f"Skončila fáze ON, přecházím do pauzy – do {next_until}"
        log(msg)
    elif state["phase"] == "off" and until and now < until:
        msg = f"Pauza do {until}"
        log(msg)
    elif state["phase"] == "off" and level_cm < LOW_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", True)
        next_until = now + ON_DURATION
        save_state({"phase": "on", "until": next_until.isoformat()})
        msg = f"Hladina nízká, čerpadlo ZAPNUTO do {next_until}"
        log(msg)
    else:
        msg = f"Čekám na pokles hladiny nebo konec pauzy"
        log(msg)

    # --- Hydrawise logika ---
    hw_msg = check_HW_logic(level_cm)

    return f"{msg} | {hw_msg}"


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

# --- Endpoint pro test Hydrawise ---
@app.route("/pydrawise")
def run_pydrawise():
    try:
        result = subprocess.run(
            ["python3", "test_pydrawise.py"],
            capture_output=True, text=True, check=True
        )
        return f"<pre>{result.stdout}</pre>"
    except subprocess.CalledProcessError as e:
        return f"❌ Chyba při spouštění test_pydrawise.py:\n<pre>{e.stderr}</pre>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
