# ── ProcureIQ — Production Dockerfile ──
FROM python:3.11-slim

# Install system deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk-bridge2.0-0 libxss1 libgtk-3-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpangocairo-1.0-0 libatspi2.0-0 \
    wget gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser setup — conditional to support environments with disk limits.
# Build with --build-arg INSTALL_PLAYWRIGHT=false to skip browser install (uses fallback vendors).
# Build with --build-arg INSTALL_PLAYWRIGHT=true (default) to enable live scraping.
ARG INSTALL_PLAYWRIGHT=true
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        python -m playwright install chromium && \
        python -m playwright install-deps chromium; \
    else \
        echo "Skipping Playwright browser install — vendor discovery will use fallback mocks"; \
    fi

COPY . .

# Render (and most cloud platforms) set PORT env var automatically
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
