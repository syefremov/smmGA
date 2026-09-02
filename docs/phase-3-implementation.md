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

Локально проверяются Python formatting/lint/types, unit/component tests, shell syntax, Compose interpolation, frontend build и generated contracts. Результат Linux server lifecycle CI фиксируется после push. Проверка на CI — доказательство работы скриптов с синтетическими данными, а не настройка реального сервера.

Не выполнены:

- фактический bootstrap/изменения удалённого сервера — SSH banner exchange завершился timeout;
- подтверждение provider recovery console и второго key/sudo login;
- персональный Tailscale login, реальные ACL и private HTTPS;
- внешняя проверка публичных IPv4/IPv6 ports и реальный reboot;
- backup/restore на удалённой машине;
- полные локальные gates фаз 1–2: legacy Docker Engine остаётся недоступен;
- production backup encryption/offsite/retention/automation, registry digests и приложение с персональной identity — последующие фазы.

Данные прежнего Docker Desktop, ключи, remote credentials и настройки tailnet не изменялись. Реальные секреты и IP не записаны в новые файлы проекта.
