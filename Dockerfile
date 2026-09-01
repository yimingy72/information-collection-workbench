FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM golang:1.25-alpine AS seamoon
ARG GOPROXY=https://goproxy.cn,direct
ENV GOPROXY=${GOPROXY}
WORKDIR /src/seamoon
COPY seamoon/go.mod seamoon/go.sum ./
RUN go mod download
COPY seamoon/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/seamoon-core ./cmd/seamoon-core

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY --from=frontend /src/frontend/dist ./frontend/dist
COPY --from=seamoon /out/seamoon-core /usr/local/bin/seamoon-core
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
