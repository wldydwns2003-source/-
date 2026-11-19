FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1 PORT=10000
# 인스타 구조가 바뀌면 yt-dlp를 최신으로: 시작할 때마다 업데이트 시도 (실패해도 계속 실행)
CMD pip install -q --no-cache-dir -U yt-dlp instaloader || true; \
    exec gunicorn -b 0.0.0.0:$PORT -w 1 --threads 8 --timeout 300 app:app
