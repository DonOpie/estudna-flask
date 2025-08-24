FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Spuštění Flask app přes Gunicorn s delším timeoutem a čitelnými access logy
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "120", "--access-logfile", "-", "--access-logformat", "[%(t)s] %(m)s %(U)s => %(s)s", "--log-level", "info", "main:app"]

EXPOSE 5000
