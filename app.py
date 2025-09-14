from flask import Flask
from estudna import spustit   # ⬅️ tvoje původní funkce ze skriptu eStudna
from test_pydrawise import bp as pydrawise_bp  # ⬅️ nový blueprint pro Hydrawise

app = Flask(__name__)

# --- route pro eStudnu (hlavní logika) ---
@app.route("/")
def main_estudna():
    return spustit()

# --- připojení blueprintu pro Hydrawise testy ---
app.register_blueprint(pydrawise_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
