from flask import Flask
from main import spustit   # ⬅️ tvoje funkce pro eStudnu je v main.py
from test_pydrawise import bp as pydrawise_bp  # ⬅️ blueprint pro Hydrawise

app = Flask(__name__)

# --- route pro eStudnu (hlavní logika) ---
@app.route("/")
def main_estudna():
    return spustit()

# --- připojení blueprintu pro Hydrawise testy ---
app.register_blueprint(pydrawise_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
