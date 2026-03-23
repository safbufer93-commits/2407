FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium --with-deps

COPY . .

RUN mkdir -p input output artifacts logs

ENV HEADLESS=true \
    GLOBAL_CONCURRENCY=4 \
    MAX_PAGES=5 \
    NAVIGATION_TIMEOUT_MS=45000 \
    SELECTOR_TIMEOUT_MS=15000 \
    MAX_ATTEMPTS=3

CMD ["python", "main.py"]
