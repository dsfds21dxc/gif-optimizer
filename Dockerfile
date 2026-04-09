FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg gifsicle && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gif_optimizer_api.py .

CMD ["gunicorn", "gif_optimizer_api:app", "--bind", "0.0.0.0:8080", "--timeout", "180", "--workers", "2"]
