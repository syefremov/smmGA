# Дорожная карта полной реализации: 10 фаз

## Назначение документа

Этот roadmap задаёт порядок, в котором система SMM GPT должна быть доведена от текущего набора проектных документов до рабочего многопользовательского продукта. Он охватывает:

- подготовку основной Windows-машины разработчика;
- воспроизводимый локальный запуск;
- подготовку удалённого Linux-сервера;
- центральную базу данных, авторизацию и аудит;
- управление через чат Codex и приватный плагин;
- дополнительную внутреннюю веб-панель;
- полный цикл контента, RAG, AI-профили, интеграции и аналитику;
- установку на компьютеры сотрудников;
- резервное копирование, наблюдаемость, восстановление и безопасный запуск пилота.

Roadmap описывает зависимости и ворота качества, а не обещает календарные сроки. Каждая фаза разбивается на небольшие проверяемые итерации. После каждой законченной итерации связанные изменения коммитятся и отправляются в настроенный Git remote согласно [`git-workflow.md`](git-workflow.md).

## Что считается полной реализацией

После фазы 10 владелец и сотрудники могут работать с одной системой с разных компьютеров:

1. Основные команды выполняются обычным языком в Codex.
2. Приватный плагин подключает Codex к удалённому MCP-серверу от имени конкретного сотрудника.
3. Внутренняя веб-панель даёт визуальный доступ к тем же операциям, но не содержит отдельной бизнес-логики.
4. Общие данные, документы, редакции, решения, метрики и аудит находятся на центральном сервере.
5. Сотрудник не получает пароль PostgreSQL, ключи социальных сетей или копию общей базы.
6. Ни один материал не публикуется без одобрения человеком точной неизменяемой редакции.
7. Система исследует разрешённые источники, создаёт черновики, проверяет факты и claims, планирует контент, публикует через разрешённые интеграции и анализирует результат.
8. Сервер можно обновить, восстановить из резервной копии и откатить по документированной процедуре.

## Зафиксированная техническая база

Версии ниже являются стартовой базой на 2 сентября 2026 года. В начале фазы 1 точные patch-версии проверяются на совместимость и фиксируются в version- и lock-файлах. Обновление major-версии выполняется отдельной итерацией с тестами и планом отката.

| Область | Принятое решение | Где фиксируется |
|---|---|---|
| Основная машина разработки | Windows 11, PowerShell 7+, Git, OpenSSH, WSL2 и Docker Desktop с WSL2 backend | `scripts/bootstrap-dev.ps1`, `scripts/doctor.ps1` |
| Python | Python 3.13 под управлением `uv` | `.python-version`, `pyproject.toml`, `uv.lock` |
| Frontend | Node.js 24 LTS и `pnpm` через Corepack | `.node-version`, корневой `package.json`, `pnpm-lock.yaml` |
| Контейнеры | Docker Compose v2; локальные инфраструктурные сервисы не устанавливаются в Windows напрямую | `compose.yaml`, Dockerfiles |
| Сервер | Ubuntu Server 24.04 LTS; Docker Engine из официального apt-репозитория и Compose plugin | `scripts/bootstrap-server.sh`, operations docs |
| База | PostgreSQL 18 с расширением `pgvector`; используется актуальный minor-релиз ветки 18 | Compose image и lock/deploy manifest |
| Очередь | Redis 7.x и Celery; Redis не является источником истины | Compose image и Python lockfile |
| Identity | self-hosted authentik, OIDC для веба и OAuth 2.1 Authorization Code + PKCE для MCP | server configuration и auth docs |
| Приватная сеть | Tailscale; пилот доступен только внутри tailnet, Tailscale Funnel выключен | server runbook |
| Reverse proxy | Caddy перед API, MCP и статическими файлами SPA; в пилоте слушает только loopback/Tailscale path | `Caddyfile`, Compose |
| Browser testing | Playwright для Chromium, Firefox и WebKit; Vitest и Testing Library для frontend unit tests | `web/playwright.config.ts`, package scripts |
| CI | GitHub Actions с теми же lint, typecheck, test, migration и build-командами, что используются локально | `.github/workflows/` |

Почему выбраны не самые новые prerelease-компоненты:

- Python 3.13 остаётся стабильной поддерживаемой веткой до октября 2029 года и даёт более консервативную совместимость библиотек, чем предрелизная 3.15: [статус версий Python](https://devguide.python.org/versions/).
- Node.js рекомендует production-приложениям Active LTS или Maintenance LTS; ветка 24 является LTS: [график релизов Node.js](https://nodejs.org/en/about/previous-releases).
- PostgreSQL 18 поддерживается до ноября 2030 года; внутри major-ветки нужно устанавливать актуальный minor-релиз: [политика версий PostgreSQL](https://www.postgresql.org/support/versioning/).
- Ubuntu 24.04 LTS входит в официальный список поддерживаемых Docker Engine систем: [установка Docker Engine на Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

### Что устанавливается на компьютер сотрудника

Обычному сотруднику не нужны Python, Node.js, Docker, PostgreSQL или исходный код серверной части. На его машине нужны:

- Codex;
- Tailscale с персональной учётной записью и доступом в tailnet;
- приватный SMM-плагин;
- современный браузер, если сотрудник использует внутреннюю веб-панель;
- короткая программа проверки соединения без вывода токенов.

Исходный репозиторий и полный developer toolchain требуются только тем сотрудникам, которые действительно разрабатывают систему.

## Правила прохождения фаз

- Фазы выполняются по порядку. Допускается готовить код следующей фазы, но её нельзя считать начатой в рабочем контуре, пока не пройден exit gate предыдущей.
- Каждая итерация должна давать один законченный результат, иметь тест или воспроизводимую проверку, обновлённую документацию и атомарный commit.
- Сначала используется fake connector и тестовые данные. Реальные внешние изменения выполняются только в отдельно разрешённом sandbox-сценарии.
- Любая схема данных меняется миграцией. Любая новая интеграция реализуется через adapter и идемпотентные операции.
- Локальная среда, CI и сервер вызывают одни и те же проектные команды. Нельзя иметь отдельный «ручной» способ, который работает только на машине разработчика.
- Секреты вводятся непосредственно в защищённое окружение и не попадают в Git, документацию, логи, диагностические архивы или команды, выводимые в чат.
- Для каждой фазы до завершения фиксируются процедура развёртывания и путь возврата к последней рабочей версии.

## Сводка фаз и контрольные точки

| Фаза | Результат | Контрольная точка |
|---:|---|---|
| 1 | Подготовлена Windows-машина и зафиксированы версии | Воспроизводимая рабочая станция |
| 2 | Запускается пустой, но исполняемый monorepo и CI | Технический локальный каркас |
| 3 | Подготовлен защищённый Linux-сервер | Серверный staging внутри Tailscale |
| 4 | Работают данные, identity, роли, изоляция и аудит | Безопасный многопользовательский фундамент |
| 5 | Codex, MCP, REST и web shell используют одно ядро | Удалённый chat-first alpha |
| 6 | Реализован полный внутренний цикл контента | Полезный MVP без реальной автопубликации |
| 7 | Работают база знаний, гибридный RAG и AI-профили | Source-grounded AI beta |
| 8 | Работают VK, фоновые задания, импорт WB и community intake | Контролируемый интеграционный пилот |
| 9 | Завершены UX, аналитика, оркестрация и пакет сотрудника | Командный release candidate |
| 10 | Пройдены эксплуатационные, security и recovery проверки | Production-ready пилот |

## Фаза 1. Локальная рабочая станция и правила воспроизводимости

### Цель

Получить основную Windows-машину, на которой любой следующий шаг выполняется одинаковыми проектными командами и не зависит от случайно установленных глобальных библиотек.

### Работы

1. Провести read-only аудит Windows, WSL2, виртуализации, свободного места, Git, OpenSSH, PowerShell, Docker и Tailscale.
2. Установить или обновить PowerShell 7, Git, OpenSSH Client, WSL2 и Docker Desktop с WSL2 backend.
3. Установить Tailscale и выполнить персональный вход; не использовать общую учётную запись команды.
4. Установить `uv`, через него установить Python 3.13 и создать локальное окружение проекта. Официальный способ установки и pinning описан в [документации uv](https://docs.astral.sh/uv/getting-started/installation/).
5. Установить Node.js 24 LTS, включить Corepack и зафиксировать выбранную версию `pnpm` в `packageManager`.
6. Создать version-файлы, `.env.example` только с пустыми/тестовыми placeholders и единые PowerShell-команды bootstrap/doctor.
7. Настроить локальные Git hooks через репозиторий, чтобы они запускали быстрые проверки и не были скрытой ручной настройкой одной машины.
8. Зафиксировать команды `bootstrap`, `dev`, `check`, `test`, `build`, `db:migrate`, `doctor` до появления их реализаций в следующих фазах.

### Артефакты

- `scripts/bootstrap-dev.ps1` — идемпотентная установка/проверка проектных зависимостей;
- `scripts/doctor.ps1` — безопасный отчёт о версиях и доступности сервисов;
- `.python-version`, `.node-version`, `pyproject.toml`, корневой `package.json`;
- `.env.example` без реальных значений;
- `docs/local-development.md` с инструкцией «чистая машина → готовая среда».

### Проверки

- `git`, `pwsh`, `ssh`, `wsl`, `docker`, `docker compose`, `tailscale`, `uv`, `python`, `node` и `pnpm` возвращают ожидаемые версии;
- повторный запуск bootstrap ничего не ломает и не создаёт дубликаты;
- secret scan не находит токены, пароли, приватные ключи и реальные connection strings;
- line endings и executable bits не создают шумный diff между Windows и Linux.

### Exit gate

На чистом Windows-профиле разработчика репозиторий можно клонировать, запустить bootstrap и получить успешный doctor без ручной установки Python-пакетов или Node-зависимостей вне lockfiles.

## Фаза 2. Исполняемый monorepo, локальная инфраструктура и CI

### Цель

Превратить документацию в минимальный исполняемый каркас, который локально поднимает API, MCP, worker, scheduler, PostgreSQL, Redis и web shell.

### Работы

1. Создать Python package `smm_gpt` по модульной структуре из `README.md`.
2. Создать FastAPI-приложение с `/health/live`, `/health/ready`, версионированным `/api/v1` и отдельным MCP transport endpoint.
3. Создать Celery worker и scheduler с одной безопасной тестовой задачей.
4. Создать React + TypeScript + Vite приложение, пока только с системной страницей состояния.
5. Добавить multi-stage Dockerfiles с non-root runtime user и health checks.
6. Добавить `compose.yaml` для app, worker, scheduler, PostgreSQL, Redis и web/reverse-proxy; инфраструктурные порты привязать только к `127.0.0.1`.
7. Подключить Ruff, статическую проверку типов, pytest, ESLint, Prettier, TypeScript, Vitest и Playwright.
8. Добавить GitHub Actions: dependency sync from lockfiles, lint, typecheck, unit tests, integration smoke, frontend build и проверка, что generated-файлы актуальны.
9. Добавить fake social connector как единственный доступный connector.
10. Создать единый набор project commands для Windows, Linux и CI.

### Артефакты

- исполняемые `src/`, `web/`, `tests/`, `migrations/` и Dockerfiles;
- `compose.yaml`, `Caddyfile`, lockfiles;
- `.github/workflows/ci.yml`;
- начальные unit, integration, contract и browser smoke tests;
- `docs/local-development.md` с фактическими командами.

### Проверки

- сборка образов из пустого Docker cache;
- запуск всего стека одной командой;
- readiness становится успешным только после готовности PostgreSQL и Redis;
- worker принимает тестовую задачу, а web shell получает health через API;
- Playwright устанавливает закреплённые browser binaries и выполняет smoke test. Поддерживаемая установка и системные требования сверяются с [официальной документацией Playwright](https://playwright.dev/docs/intro);
- локальные проверки и CI используют одинаковые команды и проходят.

### Exit gate

Свежий clone поднимается по документации, все контейнеры healthy, тестовая очередь работает, web shell открывается, MCP отвечает на capability handshake, CI зелёный.

## Фаза 3. Базовая настройка и защита Linux-сервера

### Цель

Подготовить воспроизводимый staging на удалённом сервере, доступный только доверенным устройствам через Tailscale.

### Работы

1. После получения разрешённого доступа собрать факты: дистрибутив, архитектура, диски, RAM, сеть, активные службы, firewall и состояние SSH. Не переустанавливать ОС без отдельного решения.
2. Применить обновления безопасности, настроить UTC, синхронизацию времени, журналирование и автоматические security updates.
3. Создать отдельного операционного пользователя `smm`; root использовать только для bootstrap. Контейнеры приложения должны работать внутри с непривилегированными UID.
4. Добавить SSH-ключ, проверить вход во второй независимой сессии и только затем отключать password login и прямой root login. Никогда не менять оба механизма доступа вслепую.
5. Настроить firewall и rate limiting для SSH. Учесть, что опубликованные Docker ports могут обходить правила UFW; базы и Redis не публиковать, а фильтрацию контейнерного трафика проверять через `DOCKER-USER`.
6. Установить Docker Engine из официального apt-репозитория и Compose plugin. Ручной standalone Compose не использовать, поскольку он не получает обычные package updates: [официальная установка Compose plugin](https://docs.docker.com/compose/install/linux/).
7. Установить Tailscale, включить персональный/групповой ACL-доступ и оставить Funnel выключенным.
8. Создать файловую раскладку:
   - `/opt/smm-gpt` — deployment manifest и release metadata;
   - `/etc/smm-gpt` — root-readable configuration и secrets;
   - `/var/lib/smm-gpt` — PostgreSQL, Redis, authentik и media volumes;
   - `/var/backups/smm-gpt` — локальная staging-зона резервных копий.
9. Поднять базовый стек фазы 2, направить Tailscale Serve на Caddy, а Caddy — на web/API/MCP внутри приватной Docker network.
10. Создать `bootstrap-server.sh`, `deploy.sh`, `rollback.sh`, `backup.sh`, `restore.sh` и `doctor-server.sh`; все опасные шаги должны иметь preflight и понятную остановку.

### Артефакты

- `docs/deployment.md` и `docs/operations.md`;
- versioned server configuration без секретов;
- staging deployment внутри tailnet;
- базовая резервная копия и журнал результата восстановления;
- инвентаризация открытых портов и сервисов.

### Проверки

- SSH по ключу из новой сессии до отключения резервного способа входа;
- с публичного интернета недоступны PostgreSQL, Redis, authentik admin, API и MCP;
- из разрешённого Tailscale-устройства доступен HTTPS endpoint;
- контейнеры перезапускаются после reboot и не работают как root без необходимости;
- deploy той же версии повторяем и идемпотентен;
- тестовая backup/restore процедура восстанавливает отдельный временный экземпляр.

### Exit gate

Сервер переживает reboot, доступен по приватному HTTPS, не раскрывает инфраструктурные порты, разворачивается и откатывается скриптами, а тестовое восстановление документировано.

## Фаза 4. Данные, identity, роли, изоляция и аудит

Репозиторная реализация и ограничения: [`phase-4-implementation.md`](phase-4-implementation.md). Реальный identity/server exit gate пока не закрыт.

### Цель

Создать безопасный центральный фундамент, на котором можно подключать нескольких пользователей и все последующие предметные модули.

### Работы

1. Настроить SQLAlchemy, Alembic и транзакционный Unit of Work.
2. Создать базовые таблицы `users`, `user_identities`, `web_sessions`, `workspaces`, `memberships`, `audit_events`, `idempotency_keys`, `outbox_events`, file metadata и системные job records.
3. Создать GreenAurum как seed workspace и первого владельца через одноразовую bootstrap-команду без зашитого пароля.
4. Реализовать роли Owner, Administrator, Strategist, Editor, Publisher, Analyst и Viewer с deny-by-default policy.
5. Настроить authentik: OIDC Authorization Code + PKCE для web, MFA для Owner/Admin/Publisher, персональные identity links и отзыв сессий.
6. Реализовать серверные `HttpOnly`, `Secure`, `SameSite` web sessions, CSRF protection, Origin validation и session rotation.
7. Для MCP реализовать OAuth 2.1 с PKCE S256, protected-resource metadata, корректными issuer/audience/scopes и проверкой токена на каждом вызове. Использовать DCR или заранее зарегистрированный client, если выбранная версия authentik не поддерживает нужный CIMD-сценарий. Требования сверяются с [официальной документацией OpenAI по аутентификации MCP](https://developers.openai.com/plugins/build/auth).
8. Обеспечить `workspace_id` в tenant-owned данных, составные foreign keys/unique constraints и автоматический tenant context. Добавить PostgreSQL RLS для tenant-таблиц; отдельные migration/worker roles получают только минимально нужный bypass.
9. Добавить append-only audit trail с actor, request/correlation ID, временем, workspace, действием, целью и redacted metadata.
10. Добавить миграционные проверки upgrade/downgrade на disposable database и forward-only процедуру для production data migrations.

### Артефакты

- первая production-shaped схема и миграции;
- auth/permission domain services;
- authentik configuration templates;
- audit и idempotency middleware;
- `docs/data-model.md`, `docs/authentication.md` и role matrix.

### Проверки

- пользователь одного workspace не читает и не изменяет данные другого через API, MCP, worker или прямой repository layer;
- истёкший/отозванный, неверно подписанный или выпущенный для другого ресурса token отвергается; действующий украденный bearer требует явного отзыва, без token binding его кража автоматически не распознаётся;
- CSRF и неверный Origin отвергаются для state-changing web calls;
- повышенные роли нельзя получить изменением frontend payload;
- аудит сохраняется для успешных и отклонённых чувствительных действий без секретов;
- миграции проходят на пустой базе и на snapshot предыдущей версии.

### Exit gate

Два тестовых workspace и пользователи с разными ролями проходят изоляционные и permission tests; web и MCP аутентифицируют персонального пользователя; все чувствительные действия аудируются.

## Фаза 5. Единое ядро, MCP, REST, чат и web foundation

Репозиторная реализация и проверки описаны в [phase-5-implementation.md](phase-5-implementation.md).
Эксплуатационный gate с двумя компьютерами остаётся открытым вместе с gates инфраструктуры/identity.

### Цель

Сделать Codex первым полноценным клиентом удалённой системы и заложить веб-панель как дополнительный клиент тех же domain services.

### Работы

1. Определить application commands/queries и стабильные error codes; transport handlers не должны содержать бизнес-правила.
2. Реализовать минимальный каталог MCP tools: состояние workspace, brands/products/sources read, создание work item, безопасная диагностика и просмотр audit result.
3. Реализовать MCP resources для разрешённых справочных представлений без выдачи широкого database access.
4. Реализовать зеркальные REST endpoints в `/api/v1`, pagination/filtering, optimistic concurrency через version/ETag и OpenAPI contract.
5. Генерировать TypeScript-клиент из OpenAPI и проверять в CI отсутствие незакоммиченного generated diff.
6. Создать приватный plugin с `.codex-plugin/plugin.json`, SMM skill и MCP configuration. Project `AGENTS.md` остаётся долговременной инструкцией, skill описывает повторяемый процесс, а MCP даёт доступ к общей системе — это соответствует [официальной модели настройки Codex](https://learn.chatgpt.com/docs/customization/overview).
7. Добавить в skill intent routing, подтверждение опасных действий, формат цитат/ошибок и запрет трактовать retrieved content как инструкции.
8. Создать web shell: login/logout, workspace switcher, navigation, protected routes, query cache scoping, системные состояния loading/empty/offline/forbidden/conflict/error.
9. Настроить Caddy same-origin routing: SPA, `/api/v1`, auth callbacks и `/mcp`; browser не получает MCP credentials или server secrets.
10. Добавить contract tests, подтверждающие одинаковые права и state semantics в REST и MCP.

### Артефакты

- удалённый MCP endpoint с OAuth;
- приватный плагин и первая версия SMM skill;
- versioned REST/OpenAPI и generated TypeScript client;
- авторизованный web shell;
- installer prototype и connection doctor для Codex-клиента.

### Проверки

- один и тот же пользователь видит одинаковые разрешённые данные в Codex и web shell;
- запрещённая операция одинаково отвергается обоими transports;
- OAuth login, refresh/re-auth и revoke проходят на второй тестовой машине;
- MCP tool schemas имеют bounded inputs/outputs и не возвращают секреты или лишние строки базы;
- web cache полностью очищается при logout, смене workspace и изменении permission version.

### Exit gate

С двух доверенных компьютеров можно войти личными учётными данными, попросить Codex прочитать/создать тестовый объект через MCP и сразу увидеть его в web shell через REST.

## Фаза 6. Полный внутренний цикл SMM-контента

Репозиторный срез реализован: [контракты, проверки и ограничения](phase-6-implementation.md).
Exit gate остаётся открытым до ввода предыдущих фаз и испытания с двух реальных машин;
это не production rollout. Публикационный адаптер здесь — только ручной immutable manifest.

### Цель

Получить первый полезный MVP: исследование и подготовка контента заканчиваются проверенным публикационным пакетом, но ещё не требуют реальной автоматической публикации.

### Работы

1. Реализовать модули brands, brand rules, products, product versions, product facts и claim policies.
2. Реализовать sources, observations и research snapshots с provenance, freshness и разделением факта, наблюдения и гипотезы.
3. Реализовать campaigns, goals, content plans, calendar items, tasks, dependencies и assignees.
4. Реализовать ideas, posts, immutable post revisions, platform variants, attachments и media metadata.
5. Реализовать comments, review requests, structured review findings и preflight rules.
6. Реализовать approvals точной revision: изменение текста/медиа инвалидирует approval; AI review никогда не создаёт human approval.
7. Реализовать schedule request и publication package в manual/dry-run mode; никакой внешний connector пока не вызывается.
8. Реализовать optimistic locking и понятное разрешение конфликтов одновременного редактирования.
9. Добавить MCP tools и REST endpoints для первого вертикального сценария: brief → идеи → draft → revision → review → human approval → schedule proposal → publication package.
10. Добавить минимальные web screens этого сценария: рабочая очередь, календарь, редактор, сравнение revisions, комментарии и approve/reject.

### Артефакты

- доменная модель и миграции полного content lifecycle;
- state machine и permission matrix;
- fake/manual publication adapter;
- chat workflow и минимальный web workflow;
- fixtures GreenAurum без реальных секретов и неподтверждённых claims.

### Проверки

- все допустимые и запрещённые переходы state machine;
- approval привязан к content hash/revision ID и инвалидируется при любом изменении;
- только Owner одобряет; Owner/Publisher готовят ручной пакет одобренной редакции;
- concurrent edit возвращает конфликт, а не затирает новую версию;
- публикационный пакет содержит destination, exact revision, media manifest, schedule, approvals и preflight result;
- весь вертикальный сценарий проходит в Codex и web на fake data.

### Exit gate

Команда может полностью подготовить и согласовать контент с разных машин; система формирует однозначный manual publication package, но не способна случайно отправить его во внешнюю сеть.

## Фаза 7. База знаний, гибридный RAG и специализированные AI-профили

**Статус 2026-09-03:** реализован первый текстовый FTS-срез, не вся фаза.
Во втором срезе добавлены versioned eval datasets, corpus snapshots/reports, exact baseline
review, stale detection и owner web reports: [`retrieval-evaluations.md`](retrieval-evaluations.md).
Это готовый инструмент проверки, но реальные вопросы/ожидания GreenAurum ещё не утверждены.
Версии/очередь/chunks/owner activation, shared REST/MCP, тестовый text gateway,
каталог профилей и web knowledge workspace описаны в
[`phase-7-implementation.md`](phase-7-implementation.md).
Третий срез: [`knowledge-files.md`](knowledge-files.md) — private PDF/DOCX originals,
scan/sandbox jobs, preview, exact Owner import, provenance и retry/rescan. Default disabled;
реальное включение антивируса/ресурсный smoke ещё требуется.
Binary commissioning, pgvector/hybrid, полные specialist workflows и реальные corpus/provider/server
gates ещё не закрыты. Следующая итерация продолжает фазу 7, не начинает фазу 8 автоматически.

Четвёртый срез: [`ai-jobs.md`](ai-jobs.md) — durable AI queue, cancel, immutable request inputs,
lease fencing и bounded reconciliation. Тестовые assessments больше не исполняются внутри
HTTP/MCP-запроса. Денежный accounting и DB registry специалистов остаются незавершёнными.

### Цель

Сделать генерацию и анализ grounded: каждое фактическое утверждение связано с разрешённым источником, а специализированные AI-профили работают в ограниченных границах.

### Работы

1. Реализовать knowledge sources, documents, immutable document versions, chunks, ingestion jobs, index versions и knowledge gaps.
2. Реализовать загрузку PDF/DOCX/HTML/Markdown/CSV с malware/type/size checks, content hash, parser version и безопасным хранением оригинала.
3. Нормализовать текст, дедуплицировать, разбивать на chunks и строить PostgreSQL full-text index.
4. Собрать eval dataset из реальных вопросов, ожидаемых источников, негативных примеров, устаревших документов и попыток prompt injection.
5. Только после baseline FTS подключить `pgvector`, серверный embedding provider и versioned embeddings. Система остаётся работоспособной в FTS-only mode, если внешний embedding API недоступен.
6. Реализовать hybrid retrieval: permission/lifecycle/freshness filters до поиска, затем keyword + vector candidates, deterministic fusion и цитаты на source records.
7. Реализовать параллельную reindexing-версию и атомарное переключение только после eval; не уничтожать последний рабочий индекс при ошибке.
8. Реализовать server-side model gateway для structured text, embeddings и image generation: provider adapters, timeouts, retries, quotas, redaction и точная фиксация provider/model/parameters/cost. Ключи провайдеров остаются только на сервере.
9. Создать versioned AI profile registry и runs/artifacts для Product Expert, Research Scout, Analyst, Content Planner, Copywriter, Visual Creator, Editor и Publisher. Для каждого профиля задать inputs, outputs, tool allowlist, denied actions, quality gates, escalation и model/prompt version; Visual Creator сохраняет права на inputs, prompt, параметры, generation ID и provenance, а Publisher не редактирует approved revision.
10. Реализовать memory proposals с evidence и human review; исключить автоматическую запись постоянной «памяти» из обычного чата.

### Артефакты

- ingestion/indexing pipeline и knowledge admin operations;
- FTS и hybrid search services;
- provider-neutral model gateway и визуальные artifacts с provenance;
- retrieval eval harness с измеряемыми показателями precision/recall/citation;
- AI profile registry, work items, artifacts и run audit;
- интерфейсы источников, пробелов знаний и AI runs в чате и web.

### Проверки

- cross-workspace и access-level leakage отсутствуют до и после semantic search;
- exact identifiers/facts извлекаются SQL/FTS, а не заменяются похожим vector result;
- устаревшие и конфликтующие источники помечаются;
- prompt injection внутри документа не меняет policy или tool permissions;
- source-backed ответ содержит проверяемые ссылки, AI hypothesis явно помечена;
- падение embedding provider не ломает core workflow;
- каждый профиль не может вызвать запрещённые инструменты даже при прямой попытке.

### Exit gate

На утверждённом корпусе GreenAurum retrieval проходит заранее заданные пороги качества и изоляции, drafts показывают источники и knowledge gaps, а AI-профили проходят capability/eval tests.

## Фаза 8. Фоновые процессы, VK, Wildberries и community intake

### Цель

Подключить реальные разрешённые данные и действия, сохранив ручное одобрение, идемпотентность и возможность безопасной остановки.

### Работы

1. Реализовать transactional outbox, scheduled jobs, retries с backoff/jitter, dead-letter handling, leases и distributed locks.
2. Создать VK adapter по официальному API: account discovery, capability report, token validation, upload media, publish, fetch publication status и доступные metrics.
3. Все write-вызовы VK сначала выполнять в dry-run; реальный sandbox/test-account publish включать отдельным feature flag и только для явно подтверждённого post ID/revision.
4. Реализовать publication attempts с idempotency key, external correlation ID, unknown-outcome state и reconciliation job. Не повторять неизвестный результат вслепую.
5. Реализовать scheduler preflight непосредственно перед отправкой: revision, approval, permission, destination, token health, schedule window и media.
6. Реализовать append-only metric snapshots и нормализацию доступных VK показателей без выдуманных полей.
7. Создать ручной importer Wildberries CSV/XLSX: schema mapping, preview, validation, deduplication, import report и rollback batch. Official API оставить отдельным будущим adapter.
8. Создать community intake для ручного текста/CSV, классификации, draft replies, escalation и consent records; автоматическую отправку ответов не включать.
9. Добавить connector health, rate-limit awareness, credential expiry notifications и admin revoke/rotate flow без показа секрета.
10. Документировать sandbox, incident и emergency stop процедуры.

### Артефакты

- production-shaped worker/scheduler pipeline;
- VK connector и contract tests;
- WB manual importer;
- community intake/draft workflow;
- connector admin/status interfaces;
- `docs/connectors.md` и внешние integration runbooks.

### Проверки

- fake/sandbox connector проверяет успех, rate limit, timeout, partial upload, duplicate retry и unknown outcome;
- реальная тестовая публикация выполняется только после отдельного явного разрешения владельца;
- повтор job не создаёт второй внешний пост;
- отозванное approval или emergency cancel останавливает ещё не начавшуюся публикацию;
- импорт WB не применяет строки до preview/confirmation и может откатить batch;
- community drafts не отправляются наружу и не используют отзыв/UGC без consent record.

### Exit gate

Один явно разрешённый VK test post проходит approve → schedule → publish → reconcile → metrics без дублей; WB-файл импортируется с отчётом; community data заканчиваются только черновиком ответа.

## Фаза 9. Полный UX, аналитика, оркестрация и установка сотруднику

### Цель

Довести техническую систему до удобного командного инструмента и подготовить переносимый клиентский пакет.

### Работы

1. Завершить web routes: dashboard/work queue, calendar, campaigns, content, review, sources, knowledge, products/claims, analytics, community, integrations, users/roles и audit.
2. Реализовать спокойную информационно-плотную visual system, responsive layouts и обязательный mobile scope: review, comment, approve/reject, status и emergency cancel.
3. Добавить intentional states loading, empty, partial, stale, offline, forbidden, conflict, retry и failed; опасные действия показывать только после server confirmation.
4. Пройти WCAG 2.2 AA для ключевых сценариев: keyboard, focus, labels, errors, contrast, reduced motion и non-color status cues.
5. Реализовать analytics formulas: reach/views, engagement rate by reach, VK/WB outbound clicks, verified sales/promo attribution при наличии данных, сравнение периодов и experiments.
6. Добавить in-app notifications, assignments, mentions, overdue work и digest внутри системы без email/push в MVP.
7. Добавить Orchestrator последним: decomposition, dependency routing, monitoring и escalation только через database work items; запретить обход specialist quality gates.
8. Версионировать private plugin и skill, создать `install-smm.ps1`, `update-smm.ps1`, `uninstall-smm.ps1` и безопасный connection doctor.
9. Подготовить employee onboarding: персональная учётная запись, Tailscale access, plugin install, login, first-task tutorial, revoke/offboarding.
10. Выполнить чистую установку минимум на второй Windows-компьютер без исходного server repo и без передачи общих секретов.

### Артефакты

- функционально полная внутренняя веб-панель;
- dashboards, reports и experiments;
- ограниченный Orchestrator;
- versioned employee package и инструкции установки/обновления/удаления;
- onboarding/offboarding checklist и role-specific acceptance scenarios.

### Проверки

- critical Playwright flows во всех поддерживаемых browser engines и mobile viewports;
- accessibility automation плюс ручная keyboard/screen-reader smoke проверка;
- аналитические формулы сверены на фиксированных fixtures и не смешивают несопоставимые метрики;
- Orchestrator не выполняет specialist work и не обходит dependency/approval gates;
- чистая employee machine проходит install, login, daily workflow, revoke и uninstall;
- после отзыва пользователь теряет MCP и web access, но общий server state сохраняется.

### Exit gate

Сотрудник без developer toolchain устанавливает пакет по короткой инструкции и выполняет свой рабочий сценарий через Codex или web; владелец видит изменения, аудит и аналитику в общей системе.

## Фаза 10. Production hardening, восстановление и запуск пилота

### Цель

Подтвердить, что система не только функциональна, но и безопасно эксплуатируется, наблюдается, обновляется и восстанавливается.

### Работы

1. Разделить staging и production configuration/data, ввести release IDs, image digests, migration preflight и blue/green либо recreate-with-tested-rollback процедуру для текущего масштаба.
2. Настроить structured logs, correlation IDs, metrics, traces по необходимости, dashboards и alerts для API/MCP, worker queue, scheduler lag, connector errors, backup age, disk, DB и certificate/auth failures.
3. Зафиксировать SLO пилота, RPO/RTO, retention, capacity thresholds и on-call/escalation contacts.
4. Настроить зашифрованные автоматические PostgreSQL/media/config backups, минимум одну offsite copy и регулярную проверку восстановления. Redis backup не заменяет source-of-truth backup.
5. Провести полный restore drill на отдельной среде и измерить фактические RPO/RTO; устранить расхождения с runbook.
6. Провести threat model и security review: auth, tenant isolation, SSRF/upload, prompt injection, secrets, supply chain, dependencies, container privileges, network exposure, audit tampering и external publishing abuse.
7. Добавить dependency/image scanning, SBOM, secret scanning, rate limits, upload quotas, AI/API cost budgets и data retention jobs.
8. Провести load/soak/failure tests: concurrent editors, queue backlog, DB restart, Redis loss, connector timeout, disk pressure, expired credentials и interrupted deploy.
9. Провести UAT с владельцем и сотрудником на реальных разрешённых материалах GreenAurum; зафиксировать defects, training gaps и accepted limitations.
10. Выпустить внутренний production pilot только после подписанного go-live checklist; публичный домен, Tailscale Funnel и новые соцсети остаются отдельными решениями.

### Артефакты

- `docs/operations.md`, incident/runbook, backup/restore и disaster-recovery инструкции;
- dashboards/alerts и security report;
- release/rollback manifests;
- UAT protocol, accepted limitations и go-live checklist;
- первая versioned production release внутри приватного контура.

### Проверки

- automated checks, full integration/e2e, migration, backup/restore, security и load suites проходят;
- restore drill восстанавливает согласованные PostgreSQL, media и configuration данные;
- потеря Redis не уничтожает бизнес-состояние и задачи можно безопасно восстановить/reconcile;
- rollback возвращает приложение к предыдущему release без несовместимости schema;
- секреты отсутствуют в Git, images, logs, backups manifest и клиентском пакете;
- monitoring обнаруживает заранее созданные тестовые отказовые условия;
- go-live и одна контролируемая публикация подтверждены уполномоченным человеком.

### Exit gate

Внутренний production-пилот работает на центральном сервере, доступен нескольким персональным пользователям, имеет проверенное восстановление, наблюдаемость, rollback и документированную эксплуатацию.

## Входы владельца, которые понадобятся по ходу работ

Дополнительные планёрочные вопросы сейчас не нужны. Значения по умолчанию уже приняты. Владелец подключается только там, где система не может законно или технически решить за него:

| Когда | Что требуется | Почему нельзя придумать автоматически |
|---|---|---|
| Фаза 3 | Рабочий SSH-доступ или консоль сервера | Это внешнее право доступа и recovery channel |
| Фаза 4 | Список первых персональных пользователей | Учётные записи и полномочия принадлежат организации |
| Фаза 7 | Действующие материалы GreenAurum и server-side API key для embeddings при выборе внешнего provider | Нужны реальные источники и секрет, который нельзя хранить в Git |
| Фаза 8 | VK application/test account credentials и разрешение на конкретный test post; разрешённые WB-файлы | Это реальные внешние действия и данные |
| Фаза 10 | UAT-подтверждение и явное разрешение go-live | Запуск production и публикации необратимы организационно |

Если вход ещё не предоставлен, соответствующая интеграция остаётся на fake/dry-run implementation, а остальная работа продолжается.

## Карта покрытия требований

| Требование | Основные фазы |
|---|---|
| Подготовка локальной машины | 1–2 |
| Git, lockfiles, CI и воспроизводимость | 1–2, 10 |
| Подготовка и защита Linux-сервера | 3, 10 |
| Центральная PostgreSQL и очередь | 2–4 |
| Несколько пользователей и машин | 4–5, 9 |
| Управление из этого чата через Codex | 5–9 |
| Переносимый пакет сотрудника | 5, 9 |
| Внутренняя веб-панель | 5–6, 9 |
| Контент, согласование и календарь | 6 |
| База знаний, RAG и проверяемые источники | 7 |
| AI-профили и ограниченная оркестрация | 7, 9 |
| VK, Wildberries и community workflow | 8 |
| Метрики и аналитика | 8–9 |
| Безопасная публикация без дублей | 6, 8, 10 |
| Backups, observability, recovery и production | 3, 10 |

## Текущее состояние и следующий шаг

Фаза 1 формально остаётся `in progress`: runtime и отдельный Compose установлены, но legacy Docker Engine не запускается. Удаление старой Docker Desktop и её VHDX не выполнялось, чтобы не потерять потенциальные пользовательские volumes/images.

Репозиторная реализация фазы 2 создана: Python package, FastAPI health/API, MCP Streamable HTTP, Celery worker/scheduler, fake connector, React status shell, generated OpenAPI client, Dockerfiles, Compose, тесты и CI. Все доступные локальные проверки проходят. На commit `7c227f8` полный Linux CI подтвердил сборку образов из пустого cache, healthy stack, queue/MCP/integration и Playwright smoke. Фаза 2 не отмечается завершённой до закрытия локального gate фазы 1 и повторного запуска стека на текущей Windows-машине. Доказательство и remaining gate описаны в [`phase-2-implementation.md`](phase-2-implementation.md).

Следующий технический шаг — безопасно решить судьбу старых Docker-данных 2021 года, восстановить Docker Engine, затем выполнить полный gate фаз 1–2. Установка серверных служб начинается только в фазе 3 и после восстановления надёжного SSH/recovery-доступа.

По запросу следующей фазы подготовлена репозиторная реализация фазы 3: отдельный hardened staging manifest, bootstrap/deploy/rollback/backup/restore/doctor, policy template и runbooks. Изменяющие команды не выполняются без `--apply`. Remote MCP закрыт до identity фазы 4, restore работает только в отдельном экземпляре. При проверке 2026-09-02 SSH остановился по timeout до авторизации; удалённый сервер не изменён. Фаза 3 остаётся `in progress`, её фактический SSH/Tailscale/firewall/reboot gate не закрыт. См. [`phase-3-implementation.md`](phase-3-implementation.md), [`deployment.md`](deployment.md) и [`operations.md`](operations.md).
