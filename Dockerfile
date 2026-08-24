FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV HOST=127.0.0.1 \
    PORT=8787 \
    PYTHONUNBUFFERED=1 \
    APPIUM_SPAWN=false \
    APPIUM_URL=http://127.0.0.1:4723

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-127.0.0.1} --port ${PORT:-8787}"]
