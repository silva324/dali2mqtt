# Multi-stage build for minimal image size
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libusb-1.0-0-dev \
    libudev-dev \
    pkg-config \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python dependencies
COPY requirements.txt /tmp/
RUN --mount=type=secret,id=GITHUB_TOKEN \
    pip install --no-cache-dir --upgrade pip && \
    git config --global url."https://$(cat /run/secrets/GITHUB_TOKEN)@github.com/".insteadOf "https://github.com/" && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Final stage - minimal runtime image
FROM python:3.11-slim

# OCI metadata (override via --build-arg)
ARG VERSION=unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG SOURCE_URL=https://github.com/silva324/dali2mqtt
LABEL org.opencontainers.image.title="dali2mqtt" \
    org.opencontainers.image.description="DALI to MQTT bridge" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.source="${SOURCE_URL}"
    
# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    libudev1 \
    udev \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create app directory and user
RUN useradd -r -u 1000 -m -d /app dali2mqtt && \
    mkdir -p /app/config /app/data && \
    chown -R dali2mqtt:dali2mqtt /app

WORKDIR /app

# Copy application files
COPY --chown=dali2mqtt:dali2mqtt dali2mqtt/ /app/dali2mqtt/
COPY --chown=dali2mqtt:dali2mqtt config.yaml /app/config/config.yaml.example

# Switch to non-root user
USER dali2mqtt

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the application
ENTRYPOINT ["python", "-m", "dali2mqtt.dali2mqtt"]
CMD ["--config", "/app/config/config.yaml"]
