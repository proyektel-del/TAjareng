FROM python:3.10-slim

# Install system libraries needed by OpenCV & Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5000
ENV FLASK_DEBUG=false

EXPOSE 5000

# Gunicorn dengan 1 worker + multiple threads agar state in-memory konsisten
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", "app:app"]

