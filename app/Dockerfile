FROM debian:bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y python3 python3-pip --no-install-recommends && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --break-system-packages
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
