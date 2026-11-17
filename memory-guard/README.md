# Защита памяти сервиса на Linux VPS

Защита работает на Ubuntu с cgroup v2 и systemd. Она измеряет `memory.current` одной службы или одного контейнера раз в минуту. Память cgroup включает файловый кэш. Сигнал означает устойчивый рост потребления памяти вблизи бюджета. Для подтверждения утечки нужны данные приложения или профилировщика.

Бюджет равен меньшему из действующего ограничения `memory.max` и заданного `MEMORY_BUDGET_MIB`. Если ограничения нет, задайте бюджет явно. Настройка бюджета управляет порогом защиты. Лимит памяти контейнера или службы задаётся отдельно в Docker Compose или systemd.

Кандидат на утечку должен находиться выше или на уровне 85% бюджета, вырасти минимум на 5% бюджета за последние 10 минут и расти хотя бы между 7 из 10 пар отсчётов. Режим `observe` ведёт журнал. В режиме `restart` проверяются смена экземпляра, ответ HTTP 2xx и падение потребления не менее чем на 5% бюджета с возвратом ниже 85%. После неудачной проверки повторные перезапуски блокируются. Между попытками по умолчанию проходит минимум час. В `ALERT_WEBHOOK_URL` можно задать адрес, принимающий JSON-события.

## Установка на VPS

Все команды выполняются из `/root/monitoring_project` под root. Загрузите каталог `memory-guard` и обновлённые файлы `monitor/service.py` и `plugin/module.js`.

```bash
cd /root/monitoring_project
docker ps --format '{{.Names}}'
systemctl list-units --type=service --state=running --no-pager

install -d -m 755 /opt/nirs-memory-guard /var/lib/nirs-memory-guard
install -m 644 memory-guard/guard.py /opt/nirs-memory-guard/guard.py
install -m 644 memory-guard/nirs-memory-guard.{service,timer} /etc/systemd/system/
if [ ! -f /etc/nirs-memory-guard.env ]; then
    install -m 600 memory-guard/guard.env.example /etc/nirs-memory-guard.env
fi
nano /etc/nirs-memory-guard.env
```

Укажите `TARGET_KIND=docker` и точное имя контейнера из `docker ps`, либо `TARGET_KIND=systemd` и имя службы с окончанием `.service`. В `HEALTH_URL` укажите URL, который проверяет именно этот сервис с VPS и возвращает HTTP 2xx. При отсутствии настроенного лимита задайте `MEMORY_BUDGET_MIB` по фактическому потреблению сайта и запасу памяти VPS. Сначала оставьте `GUARD_MODE=observe`.

```bash
systemctl daemon-reload
systemctl start nirs-memory-guard.service
systemctl enable --now nirs-memory-guard.timer
journalctl -u nirs-memory-guard.service -n 20 --no-pager
```

## Обновление плагина на действующем VPS

Этот блок учитывает ранее настроенные порты этого стенда. Проверьте путь к проекту перед запуском.

```bash
cd /root/monitoring_project && bash <<'BASH'
set -e
if ! grep -qF '/var/lib/nirs-memory-guard:/memory-guard:ro' compose.yaml; then
  sed -i '\|./data:/data:ro|a\      - /var/lib/nirs-memory-guard:/memory-guard:ro' compose.yaml
fi
sed -i 's/127\.0\.0\.1:9090/127.0.0.1:19090/' monitor/service.py
sed -i 's/"version": "0\.1\.[012]"/"version": "0.1.3"/' plugin/plugin.json
grep -F '/var/lib/nirs-memory-guard:/memory-guard:ro' compose.yaml
docker compose config --quiet
docker compose up -d --build --no-deps monitor
docker compose restart grafana
BASH
```

Обновите страницу Grafana. В режиме «Сервер» появится панель «Защита памяти сервиса» после первого измерения. После проверки метрик и URL измените `GUARD_MODE=observe` на `GUARD_MODE=restart` в `/etc/nirs-memory-guard.env`. systemd прочитает настройку при следующем запуске таймера.

Если восстановление не подтвердилось, проверьте журнал и сайт. Для сброса блокировки после проверки остановите таймер, дождитесь завершения текущего запуска службы защиты и переименуйте `/var/lib/nirs-memory-guard/status.json` в архивный файл. Затем включите таймер. Начнётся накопление нового окна.

Если сервис угрожает доступности VPS, администратор может остановить его командой `docker stop ИМЯ_КОНТЕЙНЕРА` или `systemctl stop ИМЯ.service`. В этом случае защита покажет, что измерение недоступно. Возврат сервиса в работу выполняется администратором.
