# MYRA v1.0 - Production Docker Image
# Bundles: Pipeline, FastAPI backend, and built frontend

FROM python:3.12-slim AS builder

WORKDIR /app

# Install Node.js for frontend build
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg2 && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install frontend dependencies and build
COPY myra_web/package.json myra_web/package-lock.json ./myra_web/
RUN cd myra_web && npm install --production=false

COPY myra_web/ ./myra_web/
RUN cd myra_web && npm run build

# Production stage
FROM python:3.12-slim

WORKDIR /app

# Install only production Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY myra_app/ ./myra_app/
COPY myra_core/ ./myra_core/
COPY run_fastapi.py .
COPY run_pipeline.py .

# Copy built frontend
COPY --from=builder /app/myra_web/dist ./myra_web/dist

# Create necessary directories
RUN mkdir -p myra_app/db logs models results

# Expose ports
EXPOSE 8000 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default command: start FastAPI backend
CMD ["python", "run_fastapi.py"]
