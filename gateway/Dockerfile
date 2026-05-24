FROM python:3.12-slim

WORKDIR /app
# cairosvg needs the native cairo library at runtime (it loads libcairo via cffi).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY esp_gateway ./esp_gateway

EXPOSE 8770
CMD ["python", "-m", "esp_gateway"]
