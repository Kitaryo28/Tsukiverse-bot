FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/app/data
# ffmpeg + opus are needed for the music player
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY cogs/ cogs/
COPY utils/ utils/
COPY web/ web/
# Server settings live in /app/data - mount a volume there so they survive updates and restarts
VOLUME ["/app/data"]
EXPOSE 8080
CMD ["python", "main.py"]
