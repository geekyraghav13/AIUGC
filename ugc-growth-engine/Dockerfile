FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
# Cloud Run injects $PORT. Container disk is ephemeral, so the SQLite DB resets on restart.
CMD streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true
