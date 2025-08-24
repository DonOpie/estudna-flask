FROM python:3.11-slim

# Nastavíme pracovní adresář
WORKDIR /app

# Zkopírujeme requirements a nainstalujeme knihovny
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Zkopírujeme celý projekt
COPY . .

# Spustíme Flask aplikaci pomocí Gunicornu s vlastními logy
# %(t)s = čas, %(m)s = metoda (GET), %(U)s = URL, %(s)s = status kód
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "main:app", 
     "--access-logfile", "-", 
     "--access-logformat", "[%(t)s] %(m)s %(U)s => %(s)s", 
     "--log-level", "info"]

EXPOSE 5000
