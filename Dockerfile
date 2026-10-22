FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY scripts/fetch_db.py ./scripts/fetch_db.py

# База OpenStreetMap: на VPS подключается папкой data, на Render скачивается по ссылке при сборке
ARG PLACES_DB_URL=""
RUN python scripts/fetch_db.py "$PLACES_DB_URL"

# Ключи передаются переменными окружения хостинга, файл .env в образ не попадает
RUN useradd --create-home appuser
USER appuser

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
