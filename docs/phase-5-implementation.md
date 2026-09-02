# Фаза 5 — единое ядро, чат и веб

## Статус

Реализован репозиторный срез фазы 5. Это **не закрытие эксплуатационного exit gate**:
реальный сервер, private HTTPS, authentik и вход с двух компьютеров ещё не введены в работу.
Не следует начинать эксплуатацию фазы 6 как завершённой системы до проверки предыдущих gates.

## Что работает

- `Operations` — общие application commands/queries для REST и MCP; transport handlers
  не выбирают права и переходы. `AccessService` повторно проверяет identity/membership/MFA.
- `0003_operations` добавляет `work_items`, минимальные `brands/products/sources`, RLS
  и узкую функцию чтения **собственных** активных workspace. Runtime не получает права
  записи каталогов или административные credentials.
- Внутренняя задача: название до 200 символов, описание до 2000, состояние и версия.
  Создавать/менять могут owner, strategist, editor, analyst (`work_item.write`).
  Administrator/publisher/viewer читают; аудит — owner/administrator.
- Переходы: `open → in_progress/cancelled`, `in_progress → done/cancelled`.
  Терминальные состояния не открываются автоматически. Допустимые переходы приходят
  в DTO `allowed_transitions`, а не вычисляются отдельно браузером.
- Создание атомарно с аудитом и ключом идемпотентности, scoped workspace + actor.
  Конкурентные повторы сериализуются; тот же ключ/другой payload — `idempotency_conflict`.
  Повтор возвращает тот же объект в его **текущем** состоянии, не исторический HTTP snapshot.
- Изменение блокирует строку и сверяет `expected_version`; конфликт — 409,
  автоматического «подбора свежей версии» нет. GET и transition возвращают ETag версии.
- Списки имеют limit 1–50, UUID keyset cursor, фильтр состояния задач/target аудита.
  Порядок — по UUID, не по дате. Пагинация не является frozen snapshot; для новых записей
  перечитывать список с начала. Cursor не заменяет авторизацию.
- В аудит не копируются тексты, credentials или произвольные details. Возвращаются только
  UUID, время, action, outcome, target и correlation ID. MCP скрывает validation/SQL exceptions.
- `/api/v1/session` возвращает собственное имя, workspace capabilities и fingerprint
  `access_version`. Максимум 100 workspace; превышение — явная ошибка, не скрытая обрезка.
  MFA-gated роли без MFA не видят workspace. Нет автоматического enrolment.

## Контракт клиентов

| MCP | REST |
|---|---|
| `session_read` | `GET /api/v1/session` |
| `workspace_read` | `GET /api/v1/workspaces/{workspace_id}` |
| `catalog_list` | `GET /api/v1/workspaces/{workspace_id}/catalog/{kind}` |
| `work_item_list/read/create` | `GET/POST .../work-items`, `GET .../work-items/{item_id}` |
| `work_item_transition` | `POST .../work-items/{item_id}/transition` |
| `audit_read` | `GET .../audit` |
| `system_status`, `diagnostic_job_create` | прежние status/diagnostic endpoints |

MCP resource `smm://workspaces/{workspace_id}/catalog/{kind}` — разрешённая первая страница,
25 записей; продолжение через tool. Вход проверяется заново при чтении ресурса/вызове tool.
`kind`: brands/products/sources. Никаких raw SQL tools или широкого доступа к БД.

Ошибки новых REST endpoints: `error.code` и `error.correlation_id`; `detail` сохраняет
совместимость с фазой 4. MCP возвращает `isError` и тот же безопасный code.
OpenAPI и TypeScript DTO генерируются `pnpm generate`, drift проверяется в CI.

## Веб

`/` остаётся безопасной status-страницей. `/app/work`, `/app/brands`, `/app/products`,
`/app/sources`, `/app/audit` требуют личного входа. Прямые URL не обходят session check.
Выбор workspace передаётся в URL; чужой UUID показывает отсутствие доступа.

TanStack Router закреплён в lockfile. Каждый user/access_version/workspace получает отдельный
QueryClient: при смене контекста старые запросы отменяются, cache очищается, формы/инспектор
размонтируются. Навигация между компаниями выполняет полный переход — это намеренно
консервативный baseline. Session и список задач проверяются раз в 10 секунд и при focus;
REST/MCP авторизуют каждую операцию независимо от этого интервала. 401/403 предметного API
немедленно закрывает приватную область. Ошибка фонового чтения не выдаётся за свежие данные.

Нет localStorage/IndexedDB, browser bearer/refresh tokens, service worker или offline writes.
Logout сначала скрывает приватные данные; при сетевой ошибке сообщает, что серверный отзыв
не подтверждён, и позволяет повторить logout. Cookie/Origin/CSRF проверяются сервером.

Простая двухполевая форма использует native HTML validation и generated DTO; React Hook Form/Zod
отложены до сложных форм фазы 6. После неизвестного результата фиксируются payload/key и
предлагается безопасный повтор; перезагрузка предупреждается, но восстановление формы после
закрытия браузера пока отсутствует. После такого закрытия сначала проверить список задач,
а не создавать дубль. Версионный конфликт требует явного перечитывания.

Реализованы loading, empty, network failure, offline, forbidden, conflict, logout failure,
keyboard focus, mobile layout и reduced motion. Скриншоты — только синтетические данные.
Полный screen-reader/WCAG аудит и матрица Safari/Firefox остаются перед production.

## Плагин и перенос

Пакет находится в `plugins/greenaurum-smm`. В исходнике `.mcp.json` содержит пустой список:
пакет не подключается к выдуманному серверу. `scripts/employee.py export` создаёт **новую**
настроенную копию; без `--apply` только план. Существующий каталог не перезаписывается.
Ни marketplace, ни глобальная конфигурация Codex автоматически не меняются.
Пошаговая инструкция — [employee-setup.md](employee-setup.md).

Skill задаёт intent routing, границы разрешений/подтверждений, повтор ключа, обработку конфликтов
и недоверенных текстов. `AGENTS.md` — политика разработки, skill — процесс, MCP/БД — состояние.
Формат пакета и HTTP OAuth конфигурации сверены с официальными
[plugin docs](https://learn.chatgpt.com/docs/build-plugins) и
[MCP docs](https://learn.chatgpt.com/docs/extend/mcp).
Маршрутизация/cache используют [TanStack Router](https://tanstack.com/router/latest/docs/quick-start)
и [QueryClient](https://tanstack.com/query/latest/docs/reference/QueryClient).

## Proxy и миграция

Default staging `ops/Caddyfile.staging` **не изменён и не открыт**.
`ops/Caddyfile.authenticated` — отдельный opt-in шаблон: SPA/API/MCP/auth metadata на одном
origin, no-store, CSP, запрет docs, лимит body 128 KB. TLS обязан завершаться на проверенном
Tailscale Serve; наружу не публиковать HTTP port. Browser не получает MCP OAuth credentials.
Этот файл не переключается автоматически и пока не означает deployed profile.

Перед вводом: backup, миграция `0003_operations` административной ролью, runtime role check,
конфигурация из [authentication.md](authentication.md), затем проверка routing/PKCE/MFA/CSRF.
Downgrade `0003` удаляет новые таблицы: только disposable тесты либо отдельно одобренный
откат с восстановлением данных. Старые миграции не изменялись. Deployment schema manifest обновлён.

## Проверки и незакрытые пункты

Локально пройдены `pnpm check`, 138 Python unit tests, 2 React component tests,
9 PostgreSQL integration tests, production build и 14 новых Playwright сценариев
(desktop/mobile, установленный Edge). Изолированный PostgreSQL 15 остановлен после тестов;
основная CI-матрица использует PostgreSQL 17 и pinned Chromium. Визуальная проверка выполнена
по снимкам `output/playwright/workspace-*.png`; отдельный Playwright CLI не завершил запуск,
поэтому снимки получены существующим проектным Playwright runner. Снимки не коммитятся.

- Python unit tests и disposable PostgreSQL contract tests включены в CI; миграции
  проверяются upgrade/downgrade/upgrade и сравнением ORM metadata.
- Тесты покрывают параллельные повторы, stale version, RLS, revoked membership,
  REST/MCP parity, resources и отсутствие отражения входных секретов в ошибках.
- Playwright desktop/mobile покрывает login gate, create/read, смену workspace,
  logout, permission version, 403, конфликт и offline. Browser API fixtures синтетические:
  это **не** реальный authentik/OAuth E2E. Реальные HTTP adapters отдельно тестируются с БД.
- Локально Chromium download вернул региональный 403; разрешён запуск существующего Edge
  через `SMM_TEST_BROWSER_CHANNEL=msedge`. CI по умолчанию использует pinned Chromium.
- Плагин и skill проверены штатными валидаторами; export/doctor имеют unit tests.

Остаются реальные gates фаз 1–4, установка plugin через personal marketplace и
OAuth login/refresh/re-auth/revoke на двух компьютерах. Нужны точные callback URI из установленного
Codex и корректный loopback port policy в authentik — не угадывать URI по шаблону.
Ссылки tool/resources не считаются проверенными продуктовыми фактами.
Каталоги сейчас пустые read foundations, без наполнения, claims, research, постов и публикаций.
Durable diagnostic jobs пока сохраняются в outbox; автоматическая доставка/исполнение из
этого outbox ещё не подключена. Не обещать выполнение по одному `job_id`.
