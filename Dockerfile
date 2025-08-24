FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Gunicorn spustí Flask aplikaci definovanou v main.py
# Přidali jsme --timeout 120
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "120", "main:app"]

EXPOSE 5000
