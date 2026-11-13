FROM python:3.10-slim

WORKDIR /app

# Install dependencies (headless OpenCV tidak butuh GUI library tambahan)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5000
ENV FLASK_DEBUG=false
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

# Gunicorn 1 worker + 2 thread (sangat hemat RAM ~70-110MB)
CMD ["gunicorn", "-w", "1", "--threads", "2", "-b", "0.0.0.0:5000", "--timeout", "120", "app:app"]
