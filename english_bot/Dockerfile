# Use the official Playwright image — Chromium + all system deps already
# installed (libasound, libatk, libnss, fonts, etc.). Saves ~5 min build time
# and dodges the Ubuntu 24.04 libasound2 rename issue.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY bot ./bot

# SQLite + PNG cache live here (mount as Railway volume for persistence)
RUN mkdir -p /app/data/cache
ENV DB_PATH=/app/data/english_bot.db
ENV CARD_CACHE_DIR=/app/data/cache

# Unbuffered logs
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
