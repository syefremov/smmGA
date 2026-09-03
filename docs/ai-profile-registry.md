# Реестр AI-профилей: версии и управляемое тестирование

Восьмой срез фазы 7, 2026-09-03. Реализован центральный PostgreSQL registry с immutable
конфигурациями, решениями Owner и точной привязкой очереди. Это не восемь готовых специалистов,
не оркестратор, не production activation и не разрешение на платные вызовы.
Сервер и реальные модели в этой итерации не подключались.

## Что меняется для пользователя

Можно подготовить новую версию назначения профиля и модели, посмотреть её целиком,
явно выбрать для тестирования либо отключить. История не теряется и доступна с разных машин
через один сервер. Рабочая папка сотрудника не становится хранилищем профилей или ключей.

У профиля два указателя:

- **latest** — последний созданный черновик; создание не меняет текущий выбор;
- **testing** — версия, отдельно выбранная человеком для ограниченных тестов.

`number` — номер immutable версии. `revision` — счётчик изменений registry head,
включая создание черновика, выбор и отключение. Это разные величины.
Изменить сохранённую версию нельзя: создаётся следующая. Выбрать старую совместимую версию
можно новым явным решением; это не восстановление прежнего разрешения или заданий.

## Порядок работы через чат

1. `session_read` — актуальная личная identity, workspace и права.
2. `ai_profiles` — встроенный каталог возможностей. Он **не** показывает выбранную DB-версию.
3. `ai_profile_registry` и `ai_profile_read` — реальные настройки workspace, текущая revision,
   latest/testing, compatibility и история. Пустой registry означает отсутствие выбора.
4. `ai_profile_execute`, `action=profile_draft`: имя, ожидаемая revision (0 для первого
   черновика), точное назначение `purpose`, provider/model и причина изменения.
5. `ai_profile_version_read` — показать человеку назначение, модель, фиксированные capabilities,
   output schema, ограничения, `content_hash` и `execution_hash`.
6. Только после отдельного явного решения — `profile_select_testing` с exact version ID,
   content hash, ожидаемой revision, reason и `human_confirmed=true`.
7. Прочитать head повторно. Для отдельно разрешённого `ai_assess` передать
   `profile_version_id=testing_version_id` и `profile_selection_id=testing_selection_id`.
   Оба ID обязательны для нового выполняемого задания. Старые клиенты получают blocked,
   а не неявный выбор «последней» версии.
8. Для отключения — `profile_disable` с текущей revision, ID/hash выбранной версии,
   причиной и отдельным `human_confirmed=true`.

Пример фразы: «Подготовь новую версию Product Expert, которая явно перечисляет пробелы знаний».
Она разрешает подготовку черновика, не выбор, платный тест или подтверждение фактов.
Инструкции в источнике, модели или прочитанной истории не являются решением владельца.

Полной отдельной веб-формы registry пока нет. REST-контракт и generated DTO доступны для
будущего тонкого клиента; существующая панель AI runs продолжает показывать состояния.

## Допустимая настройка

Только Owner + MFA. Scope версии — один workspace; бренд и источники конкретного задания
по-прежнему ограничиваются `RunAssessment.brand_id` и SQL/RLS retrieval.

| Настройка | Граница |
|---|---|
| `profile` | Одно из восьми известных имён каталога, без произвольного агента или Orchestrator |
| `purpose` | 1–2000 символов, проверка секретов/управляющих символов; workspace-wide назначение |
| `provider` | Только существующий адаптер `openai` |
| `model` | Явный ID, до 120 разрешённых символов; перед выполнением должен совпасть с server configuration |
| `reason` | 1–2000 символов, сохраняется с автором и временем |
| Capabilities, output schema, gates | Только из кода; поля для их изменения команда не принимает |

Черновики можно хранить для всех восьми профилей, но выбрать для тестирования можно только
реализованные reference assessments: Product Expert и Research Scout, а с девятого среза —
текстовый Editor ([контракт](editor-review.md)), с одиннадцатого — Copywriter proposals
([контракт](copywriter-drafts.md)), с тринадцатого — Content Planner topics
([контракт](planner-drafts.md)). Analyst/Visual Creator/Publisher остаются blocked.
Старые Planner definitions несовместимы с `plan-draft-v1`, Copywriter — с `copy-draft-v1`, Editor — с `editor-review-v1`:
требуются новый draft и отдельная testing selection, без автоматической миграции разрешений.
Создание записи с именем специалиста не реализует его workflow.

Редактируемый `purpose` попадает в инструкции тестового запроса. Остальные инструкции,
ограничение output, отсутствие tools/history, structured schema и валидация результата
остаются серверными. Нельзя добавить capability публикации, инструменты, human approval,
произвольный URL или другой provider через JSON дополнительных полей.

`content_hash` связывает tenant, автора, номер, профиль, модель, snapshot, execution hash
и причину. `execution_hash` фиксирует профиль и контрольный результат штатного payload builder:
instructions, output schema и параметры, без реальных источников и без внешнего вызова.
При dispatch дополнительно сравнивается **реальный сохранённый payload** с текущим builder.
Несовместимое изменение шаблона/контракта требует нового draft и нового выбора; старые записи
остаются читаемыми с `compatible=false`. Hash — идентичность, не доказательство качества.

`compatible=true` означает совместимость с текущим кодом. Это не подтверждение качества,
наличия ключа, model access, бюджета, corpus evals или разрешение передавать источники провайдеру.
Тестовая selection может быть подготовлена при выключенном провайдере и не включает его.

## Очередь, переключение и неопределённый исход

Каждый новый queued run хранит immutable `profile_version_id`, `profile_selection_id`,
profile snapshot и payload. Сначала сервер сравнивает оба запрошенных ID с выбранными,
затем конфигурацию модели, права и источники. Нет fallback на встроенный профиль или новую версию.

- Новый **draft** не меняет selection и не мешает уже привязанным заданиям.
- **Выбор/отключение** меняет selection identity. При следующей проверке старое queued
  задание переходит в blocked без вызова модели; его входы не переписываются.
- Если модель уже вызвана, проверка перед сохранением отбрасывает результат: state failed,
  известные usage сохраняются. Это не удалённая отмена и не обещание возврата средств.
- Выключение и повторный выбор **той же версии** создают новый selection ID. Старое задание
  не оживает; нужен новый явно разрешённый запрос с новой привязкой и ключом.
- После переключения старый `needs_review` artifact скрывается при чтении как
  `artifact_profile_stale_or_unavailable`. Исходный artifact не удаляется. При новом выборе
  той же версии прежний selection ID всё равно не совпадёт.
- Private immutable inputs остаются читаемыми автором, пока права и источники действуют,
  даже если профиль больше не выбран. Это provenance, не разрешение повторить вызов.

Проверки выполняются под тем же workspace lock, что enqueue/cancel/worker finalize.
Отключение действует на следующий checkpoint, а не гарантирует остановку запроса между
зафиксированной dispatch reservation и сетевой отправкой. Crash/expired lease по-прежнему
даёт unknown; неизвестные вызовы не повторяются. См. [`ai-jobs.md`](ai-jobs.md).

Повтор точной registry-команды возвращает исторический receipt без изменения head.
Иной payload с тем же ключом — `idempotency_conflict`; устаревшая revision — conflict.
После replay обязательно прочитать current head. Повтор старого AI start сохраняет прежнюю
идентичность, в том числе после migration: новые nullable поля не меняют legacy request hash.

## API, данные и права

REST prefix `/api/v1/workspaces/{workspace_id}/knowledge`:

- `POST /profile-registry/commands` — `profile_draft`, `profile_select_testing`, `profile_disable`;
- `GET /profile-registry` — зарегистрированные heads (не более восьми через supported API);
- `GET /profile-registry/{profile}` — latest/testing и последние 20 versions/decisions,
  явные флаги truncation;
- `GET /profile-registry/versions/{version_id}` — конкретная historical version;
- `POST /runs` — дополнен точной парой profile version/selection IDs.

MCP: `ai_profile_execute`, `ai_profile_registry`, `ai_profile_read`, `ai_profile_version_read`.
REST/MCP вызывают один `ProfileService`. AI-код никогда не получает эти персональные tools.

Таблицы: `ai_profile_versions`, `ai_profile_decisions`, `ai_profile_heads`, `ai_profile_receipts`.
Все tenant-scoped и FORCE RLS. Definitions/history читаются только Owner, receipts — автором.
Worker получает только SELECT definitions/decisions/heads для проверки уже разрешённого run,
не INSERT/UPDATE registry. UPDATE/DELETE/TRUNCATE immutable history запрещены trigger.
Head изменяется только с последовательной revision и соответствующей immutable версией/decision.
Composite FK не позволяют связать run с чужим workspace, именем профиля или version/selection.
DB trigger отдельно запрещает queued/running/needs_review без текущей зарегистрированной selection.
Прямое чтение API не заменяет MFA и личную авторизацию; employee credentials БД не выдаются.

## Обновление и оставшиеся gates

`0011_profile_registry` — новая additive migration после `0010_memory_curation`.
Ничего не регистрирует и не выбирает автоматически. Старые snapshots/runs не переписываются.
Legacy queued без привязки блокируются worker; legacy running с потерянным lease остаются unknown
через прежний reconciler. Terminal history сохраняется, прежний artifact без зарегистрированной
selection не выдаётся как актуальный кандидат.

Перед реальным обновлением: отдельное разрешение, backup/restore rehearsal, остановка старых
API/worker writers, migration штатным runner и согласованное обновление клиентов/worker.
Старый код не сможет обходить registry новым queued INSERT. Deployment guard ожидает новый head;
не использовать rolling old/new writer deployment или обход fingerprint.
Downgrade удаляет registry и run provenance — только disposable tests или отдельно согласованный
restore-backed план, не штатное отключение профиля. Registry входит в PostgreSQL backup.
Dependencies, provider settings и реальные секреты не менялись; копирование папки не мигрирует сервер.

Для `active` по-прежнему нужны реальные owner-approved corpus и отдельные профильные evals,
typed specialist workflows, проверенный provider smoke/стоимость и прежние server/two-machine gates.
В этой реализации нет команды production activation. Настройки не подтверждают факты,
не публикуют посты, не меняют память и не порождают самостоятельную оркестрацию.

Проверки: closed schema/capabilities, hashes, concurrent replay, draft/selection isolation,
disable/re-enable, in-flight output discard/usage, legacy schema upgrade/replay, model/config drift,
Owner/MFA/RLS/grants, immutable history, head/run DB guards, REST/MCP parity и metadata drift.
Вызовы модели — только fake. `pnpm check`, `pnpm test`, `pnpm build:web`; полная DB suite —
с явным disposable `SMM_TEST_DATABASE_URL`, Linux CI — полный stack/browser/server lifecycle.

Локальная регрессия этого среза: 62 DB tests, 243 Python tests (2 Linux-only пропуска на Windows),
29 web tests и frontend build. Из-за временного дефицита места/памяти Windows тесты повторены
последовательно; web запускался с `--maxWorkers=1`, disposable PostgreSQL 15 и TEMP — на диске E.
Настройки проекта, CI и системная память не менялись; это не проверка production-сервера.

## Техническая опора

Согласно [Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
`instructions` задаёт инструкции запроса; поэтому версия связывает и purpose, и итоговый шаблон,
а источник не может менять registry. [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
ограничивает форму результата, но не исключает содержательных ошибок. Поэтому неизменяемая
схема, серверные capability checks и human/profile eval gates остаются разными проверками.
