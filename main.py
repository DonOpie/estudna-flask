import requests
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
from flask import Flask
import asyncio

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

# --- Geometrie nádrže ---
TANK_DIAMETER_CM = 171.0
TANK_LENGTH_CM   = 245.8
LEVEL_OFFSET_CM  = 10.0     # hloubka sondy ode dna
CAPACITY_L       = 5000.0

R_CM = TANK_DIAMETER_CM / 2.0

# --- Konfigurace Hydrawise ---
HW_API_KEY = "d9c8-2212-cd08-6bb5"
HW_ZONE_NAME = "Trávník"
HW_START_LEVEL = 150  # cm
HW_STOP_LEVEL  = 130  # cm

# --- Funkce objemu ve válci ---
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

# --- Log ---
def log(message):
    now_str = datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now_str}] {message}\n"
    print(log_line.strip())
    with open(LOG_FILE, "a") as f:
        f.write(log_line)

# --- HTTP helper ---
def httpPost(url, header={}, params={}, data={}):
    headers = {"Content-Type": "application/json", "Accept": "application/json", **header}
    data = json.dumps(data)
    r = requests.post(url, data=data, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

def httpGet(url, header={}, params={}):
    headers = {"Content-Type": "application/json", "Accept": "application/json", **header}
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

# --- Token správa ---
def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        return json.load(f).get("token")

def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"token": token}, f)

# --- ThingsBoard ---
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

# --- Stav ---
def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"phase": "off", "until": None}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

# --- Hydrawise API (GraphQL přes pydrawise) ---
from pydrawise import Auth, Hydrawise

HW_EMAIL = "viskot@servis-zahrad.cz"
HW_PASSWORD = "Poklop1234*"

auth = Auth(HW_EMAIL, HW_PASSWORD)
hw = Hydrawise(auth)


async def HW_control(level_cm: float):
    """Spustí nebo zastaví zónu podle hladiny."""
    result_lines = ["🌊 Hydrawise:"]

    controllers = await hw.get_controllers(fetch_zones=True)
    ctrl = controllers[0]
    zone = next((z for z in ctrl.zones if z.name.strip() == HW_ZONE_NAME), None)

    if not zone:
        result_lines.append("❌ Zóna Trávník nenalezena v Hydrawise")
        return "\n".join(result_lines)

    if level_cm >= HW_START_LEVEL:
        await hw.start_zone(zone, custom_run_duration=3600)
        result_lines.append(f"▶️ Spuštěna zóna {zone.name} (hladina {level_cm:.1f} cm ≥ {HW_START_LEVEL})")
    elif level_cm <= HW_STOP_LEVEL:
        await hw.stop_zone(zone)
        result_lines.append(f"⏹️ Zastavena zóna {zone.name} (hladina {level_cm:.1f} cm ≤ {HW_STOP_LEVEL})")
    else:
        result_lines.append(f"ℹ️ Beze změny (hladina {level_cm:.1f} cm)")

    return "\n".join(result_lines)

# --- Hlavní logika ---
def main():
    now = datetime.now(ZoneInfo("Europe/Prague"))
    hour = now.hour

    level_cm = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
    h_eff = max(0.0, level_cm - LEVEL_OFFSET_CM)
    volume_l = horiz_cyl_volume_l(h_eff)
    cap_l = min(volume_l, CAPACITY_L)
    percent = (cap_l / CAPACITY_L) * 100.0

    lines = ["✅ Spuštěno:"]
    lines.append(f"   Hladina: {level_cm:.1f} cm")
    lines.append(f"   Objem: {volume_l:,.0f} l ({percent:.1f} %)")

    in_allowed_time = START_HOUR <= hour < END_HOUR if START_HOUR < END_HOUR else hour >= START_HOUR or hour < END_HOUR

    if not in_allowed_time:
        lines.append("   Mimo povolený čas (čerpadlo nečinné)")
        return "\n".join(lines), level_cm

    state = load_state()
    until = datetime.fromisoformat(state["until"]) if state["until"] else None

    if level_cm >= HIGH_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        save_state({"phase": "off", "until": None})
        lines.append("   Čerpadlo VYPNUTO (hladina ≥ HIGH_LEVEL)")
        return "\n".join(lines), level_cm

    if state["phase"] == "on" and until and now < until:
        lines.append(f"   Čerpadlo běží do {until}")
        return "\n".join(lines), level_cm
    elif state["phase"] == "on":
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        next_until = now + OFF_DURATION
        save_state({"phase": "off", "until": next_until.isoformat()})
        lines.append(f"   Skončila fáze ON, pauza do {next_until}")
        return "\n".join(lines), level_cm

    if state["phase"] == "off" and until and now < until:
        lines.append(f"   Pauza do {until}")
        return "\n".join(lines), level_cm
    elif state["phase"] == "off" and level_cm < LOW_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", True)
        next_until = now + ON_DURATION
        save_state({"phase": "on", "until": next_until.isoformat()})
        lines.append(f"   Čerpadlo ZAPNUTO do {next_until}")
        return "\n".join(lines), level_cm

    lines.append("   Čekám na pokles hladiny nebo konec pauzy")
    return "\n".join(lines), level_cm

# --- Flask server ---
app = Flask(__name__)

@app.route("/")
def spustit():
    try:
        est_text, level_cm = main()
        hw_text = asyncio.run(HW_control(level_cm))
        return f"<pre>{est_text}\n\n{hw_text}</pre>"
    except Exception as e:
        log(f"Chyba: {e}")
        return f"<pre>❌ Chyba: {e}</pre>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
