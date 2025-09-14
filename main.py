import requests
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
from flask import Flask

# --- eStudna konfigurace ---
EMAIL = "viskot@servis-zahrad.cz"
PASSWORD = "poklop1234"
SN = "SB824009"
TOKEN_FILE = "token.json"

STATE_FILE = "stav.json"
LOG_FILE = "log.txt"

# Geometrie nádrže
TANK_DIAMETER_CM = 171.0
TANK_LENGTH_CM   = 245.8
LEVEL_OFFSET_CM  = 10.0      # offset sondy ode dna
CAPACITY_L       = 5000.0
R_CM = TANK_DIAMETER_CM / 2.0

# --- Hydrawise konfigurace ---
from pydrawise import Auth, Hydrawise
HW_API_KEY = "d9c8-2212-cd08-6bb5"
HW_ZONE_NAME = "Trávník"  # název zóny v Hydrawise

auth = Auth(api_key=HW_API_KEY)
hw = Hydrawise(auth)

# --- Pomocné funkce ---
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
    line = f"[{now_str}] {message}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

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

# --- ThingsBoard (eStudna) ---
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

def eStudna_GetWaterLevel(username: str, password: str, serialNumber: str) -> float:
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    values = tb.getDeviceValues(devices[0]["id"]["id"], "ain1")
    return float(values["ain1"][0]["value"]) * 100  # cm

# --- Hydrawise pomocné ---
async def HW_control(level_cm: float):
    """Spustí nebo zastaví zónu podle hladiny."""
    controllers = await hw.get_controllers(fetch_zones=True)
    ctrl = controllers[0]
    zone = next((z for z in ctrl.zones if z.name.strip() == HW_ZONE_NAME), None)

    if not zone:
        log("❌ Zóna Trávník nenalezena v Hydrawise")
        return

    if level_cm >= 126:
        await hw.start_zone(zone, custom_run_duration=3600)
        log(f"🌊 Hydrawise: spuštěna zóna {zone.name} (hladina {level_cm:.1f} cm ≥ 126 cm)")
    elif level_cm <= 121:
        await hw.stop_zone(zone)
        log(f"🌊 Hydrawise: zastavena zóna {zone.name} (hladina {level_cm:.1f} cm ≤ 121 cm)")
    else:
        log(f"🌊 Hydrawise: beze změny (hladina {level_cm:.1f} cm)")

# --- Hlavní logika ---
import asyncio
def main():
    check_and_clear_log()
    level_cm = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
    h_eff = max(0.0, level_cm - LEVEL_OFFSET_CM)
    volume_l = horiz_cyl_volume_l(h_eff)
    percent  = (min(volume_l, CAPACITY_L) / CAPACITY_L) * 100.0

    log(f"Aktuální hladina: {level_cm:.1f} cm | Objem: {volume_l:.0f} l | {percent:.1f} %")
    asyncio.run(HW_control(level_cm))

    return f"Hladina {level_cm:.1f} cm | Objem: {volume_l:.0f} l | {percent:.1f} %"

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
