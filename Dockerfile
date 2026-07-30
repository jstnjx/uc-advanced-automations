FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UC_AUTOMATIONS_DATA_DIR=/data \
    UC_EXTERNAL=true

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 8099 9090
CMD ["uc-advanced-automations"]
