FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm supervisor ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY zalo-gateway/package*.json zalo-gateway/tsconfig.json ./zalo-gateway/
RUN cd zalo-gateway && npm ci
COPY . .
RUN cd zalo-gateway && npm run build && npm prune --omit=dev
EXPOSE 10000
CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]
