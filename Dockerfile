FROM node:20-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_URL=
RUN npm run build


FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV STUDYPACE_STATIC_DIR=/app/frontend-dist
ENV PORT=8080

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY --from=frontend-build /app/frontend/dist ./frontend-dist

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/backend/data/source_material /app/backend/data/slide_images /app/backend/data/user_workspaces \
    && chown -R appuser:appuser /app

USER appuser
WORKDIR /app/backend
EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
