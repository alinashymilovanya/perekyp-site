# Развёртывание бота

Бот — обычный Python-процесс без входящего HTTP. Ему нужны только: интернет
до Авито, доступ до Telegram и постоянный каталог `/data` (база, журнал,
очередь уведомлений, сессия Telegram).

## Вариант 1. Amvera (основной)

1. В Amvera создайте проект типа **Приложение**, окружение **Docker**, тариф с
   постоянным хранилищем (минимум 1 ГБ).
2. Загрузите проект: подключите GitHub-репозиторий или загрузите архив с кодом.
   В корне уже лежат `Dockerfile` и `amvera.yaml` (`persistenceMount: /data`).
3. В разделе **Переменные** добавьте секреты (значения из локального `.env`):
   `TELEGRAM_BOT_TOKEN`, при необходимости `TELEGRAM_CHAT_ID`,
   `TELEGRAM_TRANSPORT`, `TELEGRAM_PROXY`, а также настройки маржи и интервала.
   Файл `.env` в git не попадает и в контейнер не копируется — на сервере
   настройки берутся именно из переменных окружения.
4. Запустите сборку и дождитесь статуса «Приложение запущено».
5. В логах проекта должно появиться `bot_started`, затем
   `monitoring_poll_succeeded`. В Telegram пришлите боту `/start` — он
   запомнит чат и начнёт присылать карточки.

### Telegram с сервера

По умолчанию `TELEGRAM_TRANSPORT=botapi`: из дата-центра Bot API обычно
доступен. Если запросы к `api.telegram.org` блокируются, переключите
транспорт на MTProto и укажите прокси в том же формате, что и дома:

```env
TELEGRAM_TRANSPORT=mtproto
TELEGRAM_PROXY=host:port:secret
```

Сессия создаётся при первом запуске в `/data/bot_mtproto.session`, поэтому
повторный вход не требуется. Если прокси недоступен, сообщения не теряются:
они складываются в очередь `pending_notifications` и уходят, когда связь
восстанавливается.

### Часовой пояс

Серверы Amvera работают в UTC. В `Dockerfile` уже задано
`TZ=Europe/Astrakhan`, а ежедневная сводка считается по
`SUMMARY_TIMEZONE=Europe/Astrakhan`, поэтому время 20:00 остаётся астраханским.

Проверка после первого запуска:

```bash
python -m iphone_monitor.doctor      # проверки сети, базы и настроек
python -m iphone_monitor.healthcheck # код 0, если heartbeat свежий
```

## Вариант 2. Свой VPS с Docker

```bash
git clone <repo> /opt/iphone-monitor && cd /opt/iphone-monitor
cp .env.example .env      # заполнить секреты
sudo docker compose up -d --build
sudo docker compose logs -f
```

`docker-compose.yml` монтирует `./data` в `/data`, включает healthcheck и
перезапуск `unless-stopped`. Systemd-юниты из этой папки нужны только если
хочется запускать без Docker.

## Вариант 3. Windows (домашний ПК)

`tasks/run-bot.ps1` держит процесс живым: запускает
`.venv\Scripts\python.exe -m iphone_monitor.main`, ждёт 15 секунд после
падения и пишет вывод в `data\launcher-*.log`. Один экземпляр защищён файлом
`data/bot.lock`. Telegram дома работает только через MTProto-прокси:
`TELEGRAM_TRANSPORT=mtproto`, `TELEGRAM_PROXY=127.0.0.1:1443:<secret>`.

## Резервные копии

Бот сам делает копию базы раз в сутки после 03:00 по Астрахани в
`data/backups/seen-*.sqlite3` и хранит 7 последних снимков. Дополнительный
таймер не нужен; `deploy/iphone-monitor-backup.timer` оставлен для установок
без бота-планировщика.

## Откат

```bash
sudo docker compose down
git checkout <предыдущий-коммит>
sudo docker compose up -d --build
```

На Amvera достаточно выбрать предыдущую сборку в разделе «Контроль версий».
