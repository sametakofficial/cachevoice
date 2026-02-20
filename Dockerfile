FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "cachevoice.server:app", "--host", "0.0.0.0", "--port", "8844"]
