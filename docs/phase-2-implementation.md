# Фаза 2: отчёт об имплементации

## Статус

Репозиторная часть фазы 2 реализована. Проверки, не требующие Docker daemon, проходят. `compose.yaml` успешно преобразуется Docker Compose 5.5.0 без ошибок модели.

Exit gate пока не пройден: legacy Docker Desktop 3.2.1 не предоставляет работающий Engine. Поэтому локально не подтверждены image build, healthy stack, фактическая очередь, сетевой MCP handshake и Playwright smoke через Compose. Фаза остаётся `in progress`, а не `complete`.

## Что реализовано

- модульный Python package `smm_gpt` с отдельными domain, services, infrastructure, API, MCP, integrations и workers boundaries;
- FastAPI с `/health/live`, dependency-aware `/health/ready` и `/api/v1/system/status`;
- MCP Streamable HTTP на `/mcp/` с одной read-only tool `system_status`;
- общий `SystemStatusService` для REST и MCP вместо дублирования логики;
- PostgreSQL и Redis probes с timeout и без возврата внутренних ошибок/секретов;
- Celery worker, beat scheduler и безопасная задача `smm_gpt.system.ping`;
- fake social connector в явно read-only режиме без метода внешней публикации;
- Alembic baseline до появления предметных таблиц;
- React + TypeScript + Vite status console с loading, degraded, ready и recoverable error states;
- OpenAPI export и generated TypeScript schema с CI drift check;
- multi-stage application/web Dockerfiles с непривилегированными runtime users и health checks;
- Compose services `postgres`, `redis`, `migrate`, `app`, `worker`, `scheduler`, `web`;
- Caddy same-origin routing для SPA, `/api`, `/health` и streaming `/mcp`;
- Ruff, strict mypy, pytest, ESLint, Prettier, TypeScript, Vitest и Playwright configuration;
- GitHub Actions quality и full integration jobs;
- кроссплатформенные Node project commands и безопасный локальный `.env` initializer.

## Контракты фазы

| Контракт | Поведение |
|---|---|
| `GET /health/live` | `200`, если ASGI process отвечает; не зависит от БД |
| `GET /health/ready` | `200` только при готовых PostgreSQL и Redis, иначе `503` |
| `GET /api/v1/system/status` | Состояние зависимостей и connector capabilities без секретов |
| `POST /mcp/` | MCP Streamable HTTP handshake и read-only `system_status` |
| `smm_gpt.system.ping` | JSON-safe timestamp/status, без бизнес-данных и внешних действий |

Codex поддерживает удалённые Streamable HTTP MCP servers и сохраняет их конфигурацию на конкретном host. Локальная endpoint-форма соответствует [официальной документации Codex MCP](https://learn.chatgpt.com/docs/extend/mcp) и [официальному Python SDK MCP](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md). Production-аутентификация и Tailscale HTTPS относятся к фазам 3–5; текущий локальный endpoint нельзя публиковать в интернет.

## Проверено локально

На Windows-машине выполнены:

```text
pnpm check:fast
pnpm check:python
pnpm check:web
pnpm generated:check
pnpm test
pnpm build:web
docker-compose -f compose.yaml config --quiet
```

Результат:

- Python: 6 unit tests passed, 2 Compose integration tests deselected;
- frontend: 2 component tests passed;
- Ruff, strict mypy, ESLint, Prettier и TypeScript passed;
- Vite production build passed;
- Python и pnpm lockfiles согласованы;
- generated OpenAPI и TypeScript schema не расходятся;
- Compose configuration model valid;
- реальный локальный Uvicorn ответил `200` на liveness и ожидаемым `503` на readiness без инфраструктуры;
- MCP client выполнил Streamable HTTP handshake и вызвал `system_status` через `http://127.0.0.1:8001/mcp/`.

## Как пройти оставшийся gate

После безопасного восстановления Docker Engine:

```text
pnpm run doctor
pnpm build
pnpm dev
pnpm worker:smoke
pnpm test:integration
pnpm --dir web exec playwright install chromium
pnpm test:e2e
```

Затем нужно проверить `docker compose ps`: `postgres`, `redis`, `app`, `worker`, `scheduler` и `web` должны быть healthy, а `migrate` — завершиться кодом `0`. Отдельно подтверждается GitHub Actions CI на отправленном commit.

## Ограничения, принятые намеренно

- нет пользователей, OIDC, ролей и реальных server sessions — это фаза 4;
- MCP локальный и неаутентифицированный, поэтому доступен только loopback Compose — remote access появится после server hardening;
- нет предметных таблиц и публикаций; Alembic migration является пустым baseline;
- Redis не хранит долговременное бизнес-состояние;
- fake connector не может публиковать;
- Playwright package закреплён lockfile, но browser binary не установлен локально из-за текущего disk/Docker gate; CI устанавливает Chromium через официальный Playwright installer.
