from flask import Flask, render_template
from main import spustit

app = Flask(__name__)

@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/status")
def main_estudna():
    return spustit()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
