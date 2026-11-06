FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements-runtime.lock ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --require-hashes -r requirements-runtime.lock \
    && python -m pip uninstall -y pip
COPY app ./app
COPY static ./static
RUN useradd --create-home --uid 10001 arbiter && mkdir -p /var/lib/arbiter/uploads && chown -R arbiter:arbiter /app /var/lib/arbiter
USER arbiter

ENV APP_DATABASE_PATH=/var/lib/arbiter/prototype.db \
    APP_UPLOAD_DIR=/var/lib/arbiter/uploads \
    APP_ALLOWED_HOSTS=localhost

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
