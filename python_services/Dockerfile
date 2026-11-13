FROM python:3.10-slim

WORKDIR /app

# Install dependencies (headless OpenCV tidak butuh GUI library)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Jalankan Gunicorn dengan port dinamis dari $PORT (Railway default 5000 / dinamis)
CMD ["sh", "-c", "gunicorn -w 1 --threads 2 -b 0.0.0.0:${PORT:-5000} --timeout 120 app:app"]
