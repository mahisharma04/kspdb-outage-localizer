# syntax=docker/dockerfile:1

# ---- Stage 1: build the React console ------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: backend + built assets -------------------------------------
FROM python:3.11-slim AS app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
# Built frontend served by FastAPI from FRONTEND_DIST.
COPY --from=frontend /frontend/dist /app/frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist

EXPOSE 8000
# ${PORT} lets platforms like Render inject the port; defaults to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
