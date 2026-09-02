# Фаза 3: реализация и статус проверки

Дата: 2026-09-02. Статус: **репозиторная часть реализована; серверный exit gate не закрыт**.

## Сделано

- Отдельный `ops/compose.server.yaml`: PostgreSQL/Redis/API без host ports, Caddy только на loopback, непривилегированные UID, read-only rootfs, dropped capabilities, health checks и ограниченные container logs.
- Staging Caddy закрывает MCP, неразрешённые REST routes и Swagger/OpenAPI до будущей авторизации; status SPA остаётся доступной внутри приватного контура.
- `bootstrap-server.sh`: Ubuntu 24.04, официальные apt repositories, UTC/chrony, security updates, ограниченный journald, smm operator, ключи и двухэтапный SSH hardening, UFW и отдельное правило DOCKER-USER.
- `deploy.sh`/`rollback.sh`: полный SHA, immutable source/image metadata, preflight, backup перед заменой, health/queue/port checks, защита от rollback между разными migration fingerprints.
- `backup.sh`: quiesce writers, pg_dump, media/config snapshot, checksums и completion manifest; возобновление сервисов при ошибке.
- `restore.sh`: отдельный PostgreSQL без сети/host ports, сверка таблиц/revision и media hashes, сохранённый drill report. Активная БД не заменяется.
- `doctor-server.sh`, пример Tailscale grants и подробные deployment/operations runbooks.
- Unit tests для dry-run, secret redaction, unsafe archive, damaged backup, immutable SHA, managed configuration, rollback и восстановления сервиса после ошибок.
- Отдельный CI job проверяет два синтетических releases, повторный deploy, backup/restore, rollback и пересоздание контейнеров с сохранением данных. Он не подключается к целевому серверу.

## Проверки и ограничения

Локально прошли `pnpm check`, 42 Python unit tests, 2 React component tests, shell syntax, Compose interpolation и `pnpm build:web`. Generated OpenAPI contracts не изменились.

На commit `0a29608` прошли все три jobs [Linux CI](https://github.com/syefremov/smmGA/actions/runs/33664103388): `quality`, `integration`, `server-integration`. Последний подтвердил инициализацию без перезаписи секретов, два синтетических releases, повторный deploy, отсутствие file capabilities у Caddy, повторное применение firewall guard, backup/isolated restore с SQL fixture и media, rollback и `down` → `up` с сохранением данных. Обычный integration job также подтвердил MCP/queue и Playwright desktop/mobile smoke.

После этого в bootstrap явно добавлены Git/Python как host dependencies; локальные Python-проверки повторно прошли. Установка пакетов на реальной ОС ещё не выполнялась. Проверка CI — доказательство работы deployment-скриптов с синтетическими данными, а не настройка реального сервера.

Первый Linux-прогон обнаружил несовместимость upstream Caddy binary с `cap_drop: ALL`: у бинарника была file capability для low ports, вызывавшая `operation not permitted`. В нашем image она снимается на этапе сборки, поскольку proxy слушает 8080. Защитные ограничения контейнера сохранены; CI дополнительно проверяет отсутствие file capabilities.

Не выполнены:

- фактический bootstrap/изменения удалённого сервера — SSH banner exchange завершился timeout;
- подтверждение provider recovery console и второго key/sudo login;
- персональный Tailscale login, реальные ACL и private HTTPS;
- внешняя проверка публичных IPv4/IPv6 ports и реальный reboot;
- backup/restore на удалённой машине;
- полные локальные gates фаз 1–2: legacy Docker Engine остаётся недоступен;
- production backup encryption/offsite/retention/automation, registry digests и приложение с персональной identity — последующие фазы.

Данные прежнего Docker Desktop, ключи, remote credentials и настройки tailnet не изменялись. Реальные секреты и IP не записаны в новые файлы проекта.
