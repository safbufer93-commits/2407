FROM python:3.11-slim

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget curl gnupg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

# Create directories
RUN mkdir -p /app/output /app/logs /app/config

# Environment variables (override via docker-compose or -e flags)
ENV OUTPUT_DIR=/app/output
ENV LOG_DIR=/app/logs
ENV LOG_LEVEL=INFO
ENV REQUEST_DELAY_MIN=1.0
ENV REQUEST_DELAY_MAX=3.0
ENV MAX_RETRIES=5
ENV PLAYWRIGHT_HEADLESS=true
ENV STORAGE_STATE_PATH=/app/config/storage_state_poland.json
ENV ROW_LIMIT=500000

VOLUME ["/app/output", "/app/logs", "/app/config"]

ENTRYPOINT ["python", "main.py"]
CMD []
