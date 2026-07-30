FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UC_EXTERNAL=true \
    UC_RUNTIME_MODE=external \
    UC_DISABLE_MDNS_PUBLISH=true \
    UC_INTEGRATION_INTERFACE=0.0.0.0 \
    UC_INTEGRATION_HTTP_PORT=9090 \
    UC_AUTOMATIONS_WEB_HOST=0.0.0.0

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir . \
    && mkdir -p /data \
    && chmod 0777 /data

VOLUME ["/data"]
EXPOSE 9201 9090

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "/app/tools/healthcheck.py"]

# Invoke through /bin/sh so source archive executable bits cannot break startup.
ENTRYPOINT ["/bin/sh", "/app/tools/docker-entrypoint.sh"]
CMD ["uc-advanced-automations"]
