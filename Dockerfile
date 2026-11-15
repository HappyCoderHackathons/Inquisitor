# Stage 1: Build browser TypeScript assets and install Node dependencies
FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/Frontend

# Install dependencies using package-lock.json
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci

# Copy TypeScript source, styles, and config
COPY Frontend/frontend/ ./frontend/
COPY Frontend/static/ ./static/
COPY Frontend/tsconfig.json ./

# Compile browser TypeScript bundles into static/
RUN npm run build

# Remove development dependencies, retaining only production modules for Better Auth
RUN npm prune --omit=dev

# Stage 2: Runtime environment with Python 3.13 and Node.js
FROM python:3.13-slim

# Copy Node.js 22 runtime binary from Node stage
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

# Upgrade pip and install Python dependencies including Gunicorn
COPY Frontend/requirements.txt /app/Frontend/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/Frontend/requirements.txt gunicorn

# Copy backend, shared schemas/fixtures, and Frontend application
COPY backend /app/backend
COPY shared /app/shared
COPY Frontend /app/Frontend

# Copy compiled static assets and production node_modules from build stage
COPY --from=frontend-build /build/Frontend/static /app/Frontend/static
COPY --from=frontend-build /build/Frontend/node_modules /app/Frontend/node_modules

# Copy and configure container entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

# Create non-root user
RUN groupadd -r inquisitor && useradd -r -g inquisitor -d /app inquisitor && \
    chown -R inquisitor:inquisitor /app

USER inquisitor

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=5000

EXPOSE 5000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
