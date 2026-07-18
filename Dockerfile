# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-build
WORKDIR /workspace
COPY . /workspace
RUN if [ -f frontend/package-lock.json ]; then \
      cd frontend && npm ci && npm run build; \
    elif [ -f frontend/package.json ]; then \
      cd frontend && npm install --ignore-scripts && npm run build; \
    else \
      mkdir -p /workspace/frontend/dist && \
      printf '%s\n' '<!doctype html><title>PrintVault</title><div id="root"></div>' > /workspace/frontend/dist/index.html; \
    fi

FROM python:3.12-slim AS backend-build
WORKDIR /workspace
COPY . /workspace
RUN mkdir -p /workspace/backend && \
    python -m venv /opt/venv && \
    if [ -f backend/pyproject.toml ]; then \
      /opt/venv/bin/pip install --no-cache-dir /workspace/backend; \
    elif [ -f backend/requirements.txt ]; then \
      /opt/venv/bin/pip install --no-cache-dir -r /workspace/backend/requirements.txt; \
    else \
      /opt/venv/bin/pip install --no-cache-dir 'uvicorn[standard]>=0.30,<1'; \
    fi

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend
RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /app/backend /app/docker /usr/share/nginx/html /var/cache/nginx/client_temp /var/cache/nginx/proxy_temp /var/cache/nginx/fastcgi_temp /var/cache/nginx/scgi_temp /var/cache/nginx/uwsgi_temp /var/lib/printvault && \
    chown -R 99:100 /app /usr/share/nginx/html /var/cache/nginx /var/lib/printvault
COPY --from=backend-build /opt/venv /opt/venv
COPY --from=backend-build /workspace/backend /app/backend
COPY --from=frontend-build /workspace/frontend/dist /usr/share/nginx/html
COPY --chmod=0644 docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/entrypoint.sh docker/placeholder_server.py /app/docker/
RUN chmod 0755 /app/docker/entrypoint.sh && \
    chown -R 99:100 /app /usr/share/nginx/html /var/cache/nginx /var/lib/printvault
USER 99:100
ENTRYPOINT ["/app/docker/entrypoint.sh"]
