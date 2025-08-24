import requests
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
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

# --- Funkce pro kontrolu a vyčištění logu každý den ---
def check_and_clear_log():
    if not os.path.exists(LOG_FILE):
        return
    today = datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d")
    try:
        with open(LOG_FILE, "r") as f:
            first_line = f.readline()
        if first_line.startswith("[") and today not in first_line:
            open(LOG_FILE, "w").close()  # smaže obsah logu
    except:
        pass

# --- Logování ---
def log(message):
    now_str = datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now_str}] {message}\n"
    print(log_line.strip())
    with open(LOG_FILE, "a") as f:
        f.write(log_line)

# --- HTTP helper funkce s automatickým obnovením tokenu ---
def http_request_with_retry(method, url, tb, header={}, params={}, data={}):
    headers = {"Content-Type": "application/json", "Accept": "application/json", **header}
    try:
        if method == "POST":
            r = requests.post(url, headers=headers, params=params, data=json.dumps(data))
        else:
            r = requests.get(url, headers=headers, params=params)
        if r.status_code == 401:  # token expiroval
            log("Token expiroval – přihlašuji znovu.")
            tb.force_login(EMAIL, PASSWORD)
            headers["X-Authorization"] = f"Bearer {tb.userToken}"
            if method == "POST":
                r = requests.post(url, headers=headers, params=params, data=json.dumps(data))
            else:
                r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"Chyba HTTP požadavku: {e}")
        raise

# --- Třída ThingsBoard ---
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
            except:
                pass

    def _save_token(self):
        with open(TOKEN_FILE, "w") as f:
            json.dump({"token": self.userToken, "customerId": self.customerId}, f)

    def force_login(self, username, password):
        """Vynucené nové přihlášení (při expiraci tokenu)"""
        url = f'{self.server}/api/auth/login'
        response = requests.post(url, json={'username': username, 'password': password})
        response.raise_for_status()
        self.userToken = response.json()["token"]
        url = f'{self.server}/api/auth/user'
        response = requests.get(url, headers={'X-Authorization': f"Bearer {self.userToken}"})
        response.raise_for_status()
        self.customerId = response.json()["customerId"]["id"]
        self._save_token()

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

# --- Funkce pro čtení hladiny ---
def eStudna_GetWaterLevel(username: str, password: str, serialNumber: str) -> float:
    tb = ThingsBoard()
    tb.login(username, password)
    devices = tb.getDevicesByName(f"%{serialNumber}")
    values = tb.getDeviceValues(devices[0]["id"]["id"], "ain1")
    return float(values["ain1"][0]["value"]) * 100

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
    check_and_clear_log()
    now = datetime.now(ZoneInfo("Europe/Prague"))
    hour = now.hour
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    level = eStudna_GetWaterLevel(EMAIL, PASSWORD, SN)
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

    if state["phase"] == "on" and until and now < until:
        log(f"Čerpadlo běží, do {until}")
        return f"[{now_str}] Čerpadlo běží, do {until} – Hladina: {level:.1f} cm"
    elif state["phase"] == "on":
        log("30 minut ON skončilo, vypínám čerpadlo.")
        eStudna_SetOutput(EMAIL, PASSWORD, SN, "OUT1", False)
        next_until = now + OFF_DURATION
        save_state({"phase": "off", "until": next_until.isoformat()})
        return f"[{now_str}] Skončila fáze ON, přecházím do pauzy – Hladina: {level:.1f} cm"

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
        vysledek = main()
        return f"✅ Spuštěno: {vysledek}\n"
    except Exception as e:
        log(f"Chyba: {e}")
        return f"❌ Chyba: {e}\n"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
