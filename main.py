import requests
import json
from datetime import datetime, timedelta
import os
from flask import Flask

# --- Konfigurace ---
EMAIL = "viskot@servis-zahrad.cz"
PASSWORD = "poklop1234"
SN = "SB824009"

START_HOUR = 0
END_HOUR = 6

LOW_LEVEL = 60
HIGH_LEVEL = 70

ON_DURATION = timedelta(minutes=30)
OFF_DURATION = timedelta(minutes=30)

STATE_FILE = "stav.json"

# --- HTTP helper funkce ---
def httpPost(url, header={}, params={}, data={}):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **header
    }
    data = json.dumps(data)
    r = requests.post(url=url, data=data, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

def httpGet(url, header={}, params={}):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **header
    }
    r = requests.get(url=url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

# --- Třída ThingsBoard ---
class ThingsBoard:
    def __init__(self):
        self.server = 'https://cml.seapraha.cz'
        self.userToken = None
        self.customerId = None

    def login(self, username: str, password: str):
        url = f'{self.server}/api/auth/login'
        response = httpPost(url, {}, data={'username': username, 'password': password})
        self.userToken = response["token"]
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
        response = httpGet(url, {'X-Authorization': f"Bearer {self.userToken}"}, params=params)
        return response

    def setDeviceOutput(self, deviceId, output: str, value: bool):
        method = "setDout1" if output == "OUT1" else "setDout2"
        data = {"method": method, "params": value}
        url = f'{self.server}/api/rpc/twoway/{deviceId}'
        response = httpPost(url, {'X-Authorization': f"Bearer {self.userToken}"}, params={}, data=data)
        return response

# --- Funkce pro čtení hladiny ---
def eStudna_GetWaterLevel(username: str, password: str, serialNumber: str) -> float:
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    values = tb.getDeviceValues(devices[0]["id"]["id"], "ain1")
    level_m = float(values["ain1"][0]["value"])  # v metrech
    level_cm = level_m * 100
    return level_cm

# --- Funkce pro ovládání výstupu ---
def eStudna_SetOutput(username: str, password: str, serialNumber: str, output: str, state: bool):
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    tb.setDeviceOutput(devices[0]["id"]["id"], output, state)

# --- Ukládání a načítání stavu cyklu ---
def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"phase": "off", "until": None}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

# --- Hlavní logika řízení ---
def main():
    now = datetime.now()
    hour = now.hour

    # Získáme a vypíšeme hladinu hned na začátku
    level = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
    print(f"Aktuální hladina: {level:.1f} cm")

    if hour < START_HOUR or hour >= END_HOUR:
        print("Mimo povolený čas (00:00–06:00)")
        return f"Mimo povolený čas (00:00–06:00) – Hladina: {level:.1f} cm"

    state = load_state()
    until = datetime.fromisoformat(state["until"]) if state["until"] else None

    if level >= HIGH_LEVEL:
        print(f"Hladina {level:.1f} cm je dostatečná, vypínám čerpadlo.")
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        save_state({"phase": "off", "until": None})
        return f"Hladina dostatečná ({level:.1f} cm), čerpadlo vypnuto."

    if state["phase"] == "on" and until and now < until:
        print(f"Čerpadlo běží, do {until}")
        return f"Čerpadlo běží, do {until} – Hladina: {level:.1f} cm"
    elif state["phase"] == "on":
        print("30 minut ON skončilo, vypínám čerpadlo.")
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        next_until = now + OFF_DURATION
        save_state({"phase": "off", "until": next_until.isoformat()})
        return f"Skončila fáze ON, přecházím do pauzy – Hladina: {level:.1f} cm"

    if state["phase"] == "off" and until and now < until:
        print(f"Pauza, čekám do {until}")
        return f"Pauza do {until} – Hladina: {level:.1f} cm"
    elif state["phase"] == "off" and level < LOW_LEVEL:
        print("Hladina nízká, zapínám čerpadlo.")
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", True)
        next_until = now + ON_DURATION
        save_state({"phase": "on", "until": next_until.isoformat()})
        return f"Čerpadlo zapnuto – fáze ON začíná – Hladina: {level:.1f} cm"

    print("Čekám na pokles hladiny nebo konec pauzy.")
    return f"Čekám na pokles hladiny nebo konec pauzy – Hladina: {level:.1f} cm"

# --- Flask server pro spouštění skriptu přes web ---
app = Flask(__name__)

@app.route("/")
def spustit():
    try:
        vysledek = main()
        return f"✅ Spuštěno: {vysledek}\n"
    except Exception as e:
        return f"❌ Chyba: {e}\n"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=81)
