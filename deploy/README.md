# Развёртывание на VPS

## Что понадобится от владельца

1. VPS с Ubuntu 24.04 или аналогичным Linux и постоянным доступом в интернет.
2. Разрешённый источник объявлений: API URL и токен от официального или лицензированного поставщика.
3. Новый токен Telegram-бота. Его нужно ввести только в `.env` на VPS.

## Подготовка VPS

1. Установите Docker Engine и Docker Compose plugin по официальной инструкции Docker для вашего дистрибутива.
2. Установите часовой пояс Астрахани:

   ```bash
   sudo timedatectl set-timezone Europe/Astrakhan
   ```

3. Скопируйте проект в `/opt/iphone-monitor`.
4. Внутри этой папки создайте `.env` из `.env.example` и заполните только секреты и настройки источника:

   ```env
   TELEGRAM_BOT_TOKEN=...
   LISTINGS_SOURCE=json_api
   LISTINGS_SOURCE_URL=https://provider.example/iphone-feed
   LISTINGS_SOURCE_TOKEN=...
   DAILY_SUMMARY_TIME=20:00
   SUMMARY_TIMEZONE=Europe/Astrakhan
   ```

   `TELEGRAM_CHAT_ID` можно указать сразу, но это необязательно: при первом `/start` бот сохранит ID в своей SQLite-базе на постоянном томе. Не присылайте значения токенов в чат. Время сводки записывается в формате `HH:MM`; по умолчанию это 20:00 по Астрахани.

## Формат разрешённого источника

Режим `json_api` ждёт HTTPS-ответ с массивом или объектом `{"items": [...]}`. Каждая запись должна иметь нормализованные поля `id`, `title`, `url`, `text` и `images` (массив URL изображений):

```json
{
  "items": [
    {
      "id": "i123",
      "title": "iPhone 13 128 ГБ",
      "url": "https://provider.example/items/123",
      "text": "iPhone 13 128 ГБ\n20 000 ₽\nХорошее\nАккумулятор 90%",
      "images": ["https://provider.example/images/123.jpg"]
    }
  ]
}
```

Провайдер должен иметь право отдавать эти данные. Адаптер не предназначен для обхода CAPTCHA, IP-блокировок или других ограничений сайта.

5. Проверьте запуск вручную:

   ```bash
   cd /opt/iphone-monitor
   sudo docker compose up --build
   ```

   Убедитесь, что контейнер получает статус `healthy`:

   ```bash
   sudo docker compose ps
   ```

## Автозапуск и backup

После ручной проверки скопируйте systemd-файлы в `/etc/systemd/system/`, затем выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now iphone-monitor.service
sudo systemctl enable --now iphone-monitor-backup.timer
```

Проверка:

```bash
sudo systemctl status iphone-monitor.service
sudo systemctl list-timers iphone-monitor-backup.timer
sudo docker compose -f /opt/iphone-monitor/docker-compose.yml ps
```

## Откат

Если новый запуск работает неправильно, остановите контейнер:

```bash
cd /opt/iphone-monitor
sudo docker compose down
```

Затем вернитесь к предыдущему проверенному Git-коммиту, снова выполните `sudo docker compose up -d --build` и проверьте `docker compose ps`.
