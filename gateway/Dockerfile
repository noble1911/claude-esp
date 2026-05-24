FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY esp_gateway ./esp_gateway

EXPOSE 8770
CMD ["python", "-m", "esp_gateway"]
