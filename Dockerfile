# Build stage: install Python dependencies into a virtualenv.
FROM python:3.14-slim-bookworm AS builder

RUN apt-get update --yes --quiet && apt-get install --yes --quiet --no-install-recommends \
    build-essential libjpeg62-turbo-dev zlib1g-dev libwebp-dev \
 && rm -rf /var/lib/apt/lists/* \
 && python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt


# Runtime stage
FROM python:3.14-slim-bookworm AS runtime

RUN apt-get update --yes --quiet && apt-get install --yes --quiet --no-install-recommends \
    libjpeg62-turbo libwebp7 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home erp

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PORT=8000 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=erp.settings.production

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
RUN chown erp:erp /app
COPY --chown=erp:erp . .
USER erp

# SECRET_KEY is only needed so settings import; the real one comes from the environment at runtime.
RUN SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput --clear

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn erp.wsgi:application --bind 0.0.0.0:${PORT} --workers ${GUNICORN_WORKERS:-3} --timeout 120"]
