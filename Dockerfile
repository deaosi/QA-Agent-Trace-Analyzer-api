FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QA_ENV=production \
    QA_HOST=0.0.0.0 \
    QA_PORT=5000 \
    QA_DATA_DIR=/app/data

WORKDIR /app

COPY 111/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && playwright install --with-deps chromium

COPY 111 /app/111
COPY wsgi.py /app/wsgi.py

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "180", "wsgi:app"]
