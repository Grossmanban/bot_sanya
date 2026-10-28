# 1) build the Next.js Mini App into static files
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2) one Python process: Telegram bot + API + static Mini App
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/bot.db MEDIA_DIR=/data/media FRONTEND_DIST=/app/frontend/out PORT=8000
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /web/out frontend/out
WORKDIR /app/backend
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "app.main"]
