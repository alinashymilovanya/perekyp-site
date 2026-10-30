FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    TZ=Europe/Astrakhan \
    DATABASE_PATH=/data/seen.sqlite3 \
    STATUS_PATH=/data/runtime-status.json \
    LOG_PATH=/data/bot.log \
    TELEGRAM_SESSION_PATH=/data/bot_mtproto

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY tasks /app/tasks

# Каталог для базы, логов и очереди уведомлений (в Amvera сюда монтируется /data).
# Контейнер работает от root: платформа монтирует /data со своими правами,
# и любой другой пользователь получает "attempt to write a readonly database".
RUN mkdir -p /data && chmod 777 /data

CMD ["python", "-m", "iphone_monitor.main"]
