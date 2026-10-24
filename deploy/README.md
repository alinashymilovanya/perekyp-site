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
   TELEGRAM_CHAT_ID=...
   LISTINGS_SOURCE=json_api
   LISTINGS_SOURCE_URL=https://provider.example/iphone-feed
   LISTINGS_SOURCE_TOKEN=...
   ```

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
