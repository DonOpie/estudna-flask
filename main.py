import requests
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
from flask import Flask
import asyncio
from pydrawise import Auth, Hydrawise
from apscheduler.schedulers.background import BackgroundScheduler

# --- Konfigurace eStudna ---
EMAIL = "viskot@servis-zahrad.cz"
PASSWORD = "krakonos1712"
SN = "SB824009"
TOKEN_FILE = "token.json"

# --- Home Assistant API (pro kontrolu manuálního override) ---
HA_URL = "http://192.168.5.249:8123"
HA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI2YWIxMzFjZDNmYjA0YjRmODI0NGI3NDBhYTMwZWZlZCIsImlhdCI6MTc3NTU3MDYxMywiZXhwIjoyMDkwOTMwNjEzfQ.G_T0-_sSE9TJj47nZBbYqA65iLZjLiXmW1zVaJSgo38"
HA_OVERRIDE_ENTITY = "input_boolean.studna_manual_override"

START_HOUR = 0
END_HOUR = 6

LOW_LEVEL = 80
HIGH_LEVEL = 90

ON_DURATION = timedelta(minutes=30)
OFF_DURATION = timedelta(minutes=30)

STATE_FILE = "stav.json"
LOG_FILE = "log.txt"

# --- Geometrie nádrže ---
TANK_DIAMETER_CM = 171.0
TANK_LENGTH_CM   = 245.8
LEVEL_OFFSET_CM  = 10.0
CAPACITY_L       = 5000.0
R_CM = TANK_DIAMETER_CM / 2.0

# --- Konfigurace Hydrawise ---
HW_EMAIL = "viskot@servis-zahrad.cz"
HW_PASSWORD = "Poklop1234*"
HW_ZONE_NAME = "Trávník"
HW_START_LEVEL = 149  # cm
HW_STOP_LEVEL  = 133  # cm

auth = Auth(HW_EMAIL, HW_PASSWORD)
hw = Hydrawise(auth)

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

# --- Manuální override check ---
def is_manual_override() -> bool:
    """Vrátí True pokud je v HA aktivní manuální override – Flask pak nic nemění."""
    try:
        r = requests.get(
            f"{HA_URL}/api/states/{HA_OVERRIDE_ENTITY}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            timeout=5,
        )
        return r.json().get("state") == "on"
    except Exception as e:
        log(f"WARN: nelze zkontrolovat HA override: {e}")
        return False  # při chybě pokračujeme normálně

# --- Logování ---
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
    r.raise_for_status()
    return r.json()

def httpGet(url, header={}, params={}):
    headers = {"Content-Type": "application/json", "Accept": "application/json", **header}
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

# --- Správa tokenu ---
def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        return json.load(f).get("token")

def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"token": token}, f)

# --- ThingsBoard komunikace ---
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
        return {"phase": "off", "until": None, "hw_phase": "idle"}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

# --- Hydrawise ovládání ---
async def HW_control(level_cm: float, state: dict):
    lines = ["🌊 Hydrawise:"]
    controllers = await hw.get_controllers(fetch_zones=True)
    ctrl = controllers[0]
    zone = next((z for z in ctrl.zones if z.name.strip() == HW_ZONE_NAME), None)

    if not zone:
        lines.append("❌ Zóna Trávník nenalezena v Hydrawise")
        return "\n".join(lines), state

    hw_phase = state.get("hw_phase", "idle")

    if level_cm >= HW_START_LEVEL and hw_phase != "on":
        await hw.start_zone(zone, custom_run_duration=3600)
        state["hw_phase"] = "on"
        lines.append(f"▶️ Spuštěna zóna {zone.name} (hladina {level_cm:.1f} cm ≥ {HW_START_LEVEL})")

    elif level_cm <= HW_STOP_LEVEL and hw_phase == "on":
        await hw.stop_zone(zone)
        state["hw_phase"] = "off"
        lines.append(f"⏹️ Zastavena zóna {zone.name} (hladina {level_cm:.1f} cm ≤ {HW_STOP_LEVEL})")

    else:
        lines.append(f"ℹ️ Beze změny (hladina {level_cm:.1f} cm, režim {hw_phase})")

    return "\n".join(lines), state

# --- Hlavní logika eStudna ---
def main():
    now = datetime.now(ZoneInfo("Europe/Prague"))
    hour = now.hour

    if is_manual_override():
        level_cm = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
        h_eff = max(0.0, level_cm - LEVEL_OFFSET_CM)
        volume_l = horiz_cyl_volume_l(h_eff)
        percent = min((volume_l / CAPACITY_L) * 100.0, 100.0)
        lines = ["✅ Spuštěno:"]
        lines.append(f"   Hladina: {level_cm:.1f} cm")
        lines.append(f"   Objem: {volume_l:,.0f} l ({percent:.1f} %)")
        lines.append("   ⏸ MANUÁLNÍ OVERRIDE aktivní – Flask nečinný")
        return "\n".join(lines), level_cm, load_state()

    level_cm = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
    h_eff = max(0.0, level_cm - LEVEL_OFFSET_CM)
    volume_l = horiz_cyl_volume_l(h_eff)
    percent = min((volume_l / CAPACITY_L) * 100.0, 100.0)

    lines = ["✅ Spuštěno:"]
    lines.append(f"   Hladina: {level_cm:.1f} cm")
    lines.append(f"   Objem: {volume_l:,.0f} l ({percent:.1f} %)")

    in_allowed_time = START_HOUR <= hour < END_HOUR if START_HOUR < END_HOUR else hour >= START_HOUR or hour < END_HOUR
    if not in_allowed_time:
        lines.append("   Mimo povolený čas (čerpadlo nečinné)")
        return "\n".join(lines), level_cm, load_state()

    state = load_state()
    until = datetime.fromisoformat(state["until"]) if state["until"] else None

    if level_cm >= HIGH_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        save_state({**state, "phase": "off", "until": None})
        lines.append("   Čerpadlo VYPNUTO (hladina ≥ HIGH_LEVEL)")
        return "\n".join(lines), level_cm, state

    if state["phase"] == "on" and until and now < until:
        lines.append(f"   Čerpadlo běží do {until}")
        return "\n".join(lines), level_cm, state
    elif state["phase"] == "on":
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        next_until = now + OFF_DURATION
        save_state({**state, "phase": "off", "until": next_until.isoformat()})
        lines.append(f"   Skončila fáze ON, pauza do {next_until}")
        return "\n".join(lines), level_cm, state

    if state["phase"] == "off" and until and now < until:
        lines.append(f"   Pauza do {until}")
        return "\n".join(lines), level_cm, state
    elif state["phase"] == "off" and level_cm < LOW_LEVEL:
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", True)
        next_until = now + ON_DURATION
        save_state({**state, "phase": "on", "until": next_until.isoformat()})
        lines.append(f"   Čerpadlo ZAPNUTO do {next_until}")
        return "\n".join(lines), level_cm, state

    lines.append("   Čekám na pokles hladiny nebo konec pauzy")
    return "\n".join(lines), level_cm, state


# --- Scheduler ---
last_result = "Zatím nespuštěno"
last_run = None
last_data = {}

def run_job():
    global last_result, last_run, last_data
    try:
        est_text, level_cm, state = main()
        hw_text, state = asyncio.run(HW_control(level_cm, state))
        save_state(state)
        last_result = f"{est_text}\n\n{hw_text}"
        h_eff = max(0.0, level_cm - LEVEL_OFFSET_CM)
        volume_l = horiz_cyl_volume_l(h_eff)
        percent = min((volume_l / CAPACITY_L) * 100.0, 100.0)
        now_local = datetime.now(ZoneInfo("Europe/Prague"))
        in_time = (START_HOUR <= now_local.hour < END_HOUR) if START_HOUR < END_HOUR else (now_local.hour >= START_HOUR or now_local.hour < END_HOUR)
        last_data.update({
            "hladina_cm": round(level_cm, 1),
            "objem_l": round(volume_l, 0),
            "procent": round(percent, 1),
            "cerpadlo": state.get("phase", "off"),
            "cerpadlo_do": state.get("until"),
            "v_casovem_okne": in_time,
            "hydrawise": state.get("hw_phase", "idle"),
            "aktualizovano": now_local.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        log(f"Chyba: {e}")
        last_result = f"❌ Chyba: {e}"
    last_run = datetime.now(ZoneInfo("Europe/Prague"))

scheduler = BackgroundScheduler(timezone="Europe/Prague")
scheduler.add_job(run_job, 'interval', minutes=1)
scheduler.start()
run_job()  # spusť hned při startu


# --- Flask server ---
app = Flask(__name__)

@app.route("/")
def spustit():
    ran = last_run.strftime('%Y-%m-%d %H:%M:%S') if last_run else "nikdy"
    return f"<pre>{last_result}\n\nPosledni spusteni: {ran}</pre>"

@app.route("/trigger")
def trigger():
    run_job()
    return spustit()

@app.route("/api/status")
def api_status():
    from flask import jsonify
    return jsonify(last_data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
