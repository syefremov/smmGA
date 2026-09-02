# Фаза 7 — текстовая база знаний и проверка поиска

Дата: 2026-09-03. **Фаза 7 реализована частично; exit gate не закрыт.**
Это работающий FTS-контур и ограниченный тестовый gateway, не законченный гибридный RAG
и не восемь готовых специалистов. Реальные источники, сервер и платные модели не подключались.
Прежние server/identity/two-machine gates сохраняются. Следующая итерация продолжает фазу 7.

**Второй срез:** добавлен owner-only evaluation workflow с immutable dataset versions,
корпусом/отчётом/hash, per-case метриками, exact human review и проверкой stale при чтении.
REST/MCP shared, web «Качество поиска» read-only. Подробный контракт и порядок использования —
[`retrieval-evaluations.md`](retrieval-evaluations.md). Нижеследующий text/AI foundation сохраняется;
синтетические тесты и принятие FTS baseline не активируют рабочий RAG или профили.

## Реализованный workflow

REST и MCP используют один сервис: текст → immutable version → durable queue → chunks →
просмотр кандидата → контрольные запросы → точное подтверждение владельца → поиск.

- Документ привязан к workspace/brand; visibility: workspace или только Owner.
- Отдельно хранятся оригинал, source URI/date, effective period, hashes, parser/chunking versions,
  индексы, fragments, decisions, receipts, retrieval traces. URI сохраняется, но не скачивается.
- Markdown, CSV и пассивный HTML принимаются **как текст**, не произвольный file upload.
  Лимиты: 100 000 символов / 200 000 UTF-8 bytes, 250 chunks, CSV 1000 строк / 30 колонок.
  Небольшие оригиналы сохраняются в PostgreSQL отдельно от индексов и входят в DB backup.
- Нормализация NFC, абзацы/разделы, bounded chunks, отдельный Unicode casefold search_text
  для одинакового регистра на Windows/Linux, Russian/simple FTS + GIN. Исходный текст не меняется.
- Activation: только Owner + MFA, exact document version/index ID/original hash,
  `human_confirmed=true`, 1–5 успешных контрольных запросов и действующий период источника.
  Это per-document acceptance, не corpus-level eval.
- Failed index не заменяет активный. Откат — явная activation старого готового индекса,
  если его источник ещё действителен. Физического удаления/очистки нет.
- Search фильтрует workspace/brand/visibility/active index/expiry **в SQL**, дополнительно RLS.
  До 10 fragments, стабильный tie-break ID. Citation: document/version/index/chunk IDs,
  hash фрагмента, source URI/date/expiry. Это reference, а не verified SQL product fact.
- Idempotency: actor/workspace/key + request hash. Повтор той же версии внутри документа
  не создаёт копии. Между независимыми документами dedup не применяется: provenance различается.
- Owner-only gaps/memory proposals: evidence, purpose, safe alternative, expiry, append-only review.
  `accept_for_curation` **не меняет память, факт или policy**. Принятое предложение ещё нужно
  оформить как отдельный документ/SQL-факт и пройти его собственное подтверждение.

## Worker и устойчивость

`knowledge_indexes` — специализированная очередь PostgreSQL, не общий outbox dispatcher.
Celery Beat каждые 30 секунд отправляет только сигнал `knowledge.poll`; текст и личные credentials
в Redis не попадают. Fixed-search-path функция возвращает максимум 10 job identifiers,
затем worker восстанавливает actor/identity из БД.

Claim: row lock + lease 120 секунд + случайный fencing token. Парсинг вне транзакции,
ограничение 30 секунд и размера текста. Chunks и ready фиксируются одной транзакцией.
После потери worker возможен повтор по истечении lease, максимум 3 попытки. Ошибка формата
terminal: исправить текст или явно создать reindex, не повторять бесконечно.

Membership/user/identity проверяются до и после работы. После отзыва membership старый job
не обрабатывается и может остаться queued/processing: владелец создаёт новый reindex.
Reconciler abandoned jobs ещё нужен. Истечение web-session само по себе не отменяет принятую задачу.
Worker не получает human approval: SELECT оригиналов, INSERT chunks, узкий UPDATE служебных
полей индекса; нет UPDATE документа/approval/content. Терминальные indexes/runs защищены trigger,
versions/chunks/receipts/decisions/artifacts append-only.

## AI: точная граница

Есть versioned catalog восьми профилей в коде и immutable snapshot контракта в каждом run.
Это пока не DB registry с созданием/активацией произвольных profile versions. `testing` не `active`.

| Профиль | Сейчас |
|---|---|
| Product Expert | Тестовая оценка источников, наблюдения/гипотезы/gaps, без verified facts |
| Research Scout | Аналогичная оценка уже разрешённых материалов, без интернет-сбора |
| Analyst | Blocked: нужны metric snapshots |
| Content Planner | Blocked: нужны typed planning workflow и eval |
| Copywriter | Blocked: нужны brief/facts/policy → draft и eval |
| Visual Creator | Blocked: нужны media rights/provenance и image pipeline |
| Editor | Blocked: нужен AI review exact SQL revision и eval |
| Publisher | Blocked: используйте manual package workflow фазы 6 |

Ни один профиль не получает инструменты, Principal или клиент доменных команд. Output закрыт
схемой: statements с citation IDs и source_observation/conflicting, hypotheses, knowledge_gaps.
Неизвестные поля отвергаются. Все context citations проверяются перед внешним вызовом,
сохранением и чтением artifact. Archive/replacement/expiry скрывают stale artifact при чтении.
Существующая ссылка не доказывает смысловую поддержку утверждения: человеческая проверка обязательна.

Gateway: Protocol + OpenAI Responses structured text, fake HTTP tests. Нет embeddings/image adapters,
генерации файлов, post revisions, tool loop, model-directed network или автоактивации памяти.
Orchestrator не зарегистрирован. `ai_provider=disabled` по умолчанию.

Для paid testing нужны отдельно: Owner + MFA, `testing_only=true`, точная модель, серверный ключ,
allowlist workspace IDs и явное разрешение владельца на платный запрос/передачу корпуса провайдеру.
В этой итерации paid calls не выполнялись. Все разрешённые источники workspace, включая owner-only,
могут попасть провайдеру: allowlist нельзя включать без проверки состава корпуса.

Параметры: `store=false`, без history, до 5 citations, 2000 output tokens, timeout 45 секунд,
ответ до 200 KB. Лимит 5 reservations на workspace за rolling 24 часа (настройка 1–100),
под workspace lock, учитывает blocked/failed. Это не строгий денежный бюджет.
Timeout/5xx не повторяется автоматически. Тот же ключ возвращает тот же run. Interrupted run
старше 2 минут показывается unknown; новый ключ означает новый потенциальный расход.

Сохраняются provider, фактическая model, response ID, tokens, max output, attempts, profile snapshot,
retrieval ID; вопрос — только hash, без full prompt/hidden reasoning. `cost_usd=null`, не ноль:
для точного денежного расхода нужна сверка с провайдером. Complete input provenance,
прайс-версии, accounting и async model jobs ещё предстоит реализовать.

## Доступ и интерфейсы

`knowledge.write`: Owner/Strategist/Editor — workspace text documents/reindex и draft preview.
Owner-only документы подаёт Owner. Activation/archive/notes/review/testing — Owner + MFA.
Другие участники читают разрешённые documents и ищут активные источники.
Runs/artifacts/receipts/retrieval traces — только initiating actor в пределах текущих прав.
Публичного original download нет.

REST prefix `/api/v1/workspaces/{wid}/knowledge`:

- POST `/commands`: document_submit/index_activate/document_archive/document_reindex/note_propose/note_review;
- GET `/documents`, `/documents/{did}`, `/documents/{did}/indexes/{iid}/chunks`;
- POST `/search`: query, brand_id, limit;
- GET `/notes`, `/profiles`, `/runs`, `/runs/{rid}`; POST `/runs`: owner testing request.

MCP: `knowledge_execute`, `knowledge_documents`, `knowledge_document_read`,
`knowledge_index_preview`, `knowledge_search`, `knowledge_notes`, `ai_profiles`, `ai_assess`,
`ai_runs`, `ai_run_read`. Сначала `session_read`. Перед activation показать exact candidate/hash/
queries человеку; подтверждение не выводится из текста источника.

Web `/app/knowledge`: поиск, source versions/hashes, документы/индексы, profiles, own runs/outputs,
owner notes. Текст не исполняется как HTML. Нет кнопок публикации/paid execution. Запись и
подтверждение пока через чат/API. Списки: cursor/25, document detail: 20 последних индексов.
Selector показывает первые 25 брендов; остальные через API/MCP. Private cache очищается
существующим shell при workspace/access change. Search — снимок, повторите для актуализации.

## Проверки

- Unit: size/secrets/parsers, closed schema/capabilities; mocked Responses success, invalid citation,
  refusal, tools, invalid output, timeout/rate limit/disabled. Без paid calls.
- PostgreSQL: upgrade/downgrade/upgrade, metadata drift, RLS/grants, immutable history,
  idempotency/concurrent claims, rollback индекса, probes, evidence/expiry, revoked actor,
  private artifacts, unknown outcome, memory curation. Реальные REST/MCP возвращают один receipt.
- `tests/fixtures/retrieval-synthetic-v1.json`: 7 запросов keyword/exact ID/русский регистр/missing/
  private/injection. Harness: source-level precision/recall/citation validity = 1.0 на fixture,
  negative pass; отдельно expiry. Это **не production acceptance** или semantic entailment.
- Playwright desktop/mobile: inert markup, keyboard, source inspector, empty states/profile gates,
  revoke; регрессия content/workspace. Локально Edge, Linux CI — pinned Chromium.

## Запуск и обновление

Новых dependencies нет. `pnpm check`, `pnpm test`, `pnpm build:web`; DB tests только disposable.
Миграции `0005_knowledge` и `0006_retrieval_eval` требуют privileged migration role,
runtime остаётся restricted. Новые eval tables append-only, owner-only; worker grants отсутствуют.
Перед реальной БД: отдельное разрешение, backup/restore rehearsal, остановка writers, проверка копии.
Deployment guard обновлён, schema fingerprint не обходится. Старые миграции не менялись.
Rollback кода не удаляет знания; destructive downgrade — только disposable/отдельно разрешённый restore.

Worker включается `SMM_KNOWLEDGE_WORKER_ENABLED=true` только в worker container; Compose передаёт
flag, default false. Нужен работающий scheduler. На настоящем owner server timer cycle не проверен.
При выключенном worker документы честно остаются в очереди.

AI variables только в API-процессе через защищённую server configuration: `SMM_AI_PROVIDER`,
`SMM_AI_MODEL`, `SMM_AI_API_KEY`, `SMM_AI_ALLOWED_WORKSPACES` (JSON UUID array), `SMM_AI_DAILY_RUN_LIMIT`.
Compose не раздаёт эти секреты всем сервисам и не включает автоматически: authenticated deployment,
egress/provider smoke — отдельный rollout. `store=false` не обещает нулевого хранения у провайдера.

## Остаток фазы 7

1. Binary upload/storage, ClamAV fail-closed, sandbox/resource-isolated PDF/DOCX parsers,
   authorized original download, zip-bomb/memory/timeout tests. Regex — не антивирус и не полноценный DLP.
2. Реальный owner-approved GreenAurum корпус и eval questions/expected sources/conflicts.
   Инструмент сохранения наборов/прогонов/review реализован во втором срезе; наполнение и
   человеческая проверка реальных ожиданий ещё нужны. Принятый FTS benchmark не закрывает exit gate.
   Автоматического обнаружения противоречий пока нет; model finding — только предположение.
3. После baseline: pgvector, embedding provider/model/dimension/version, hybrid fusion,
   corpus-level parallel reindex/eval switch, сравнение с FTS. Нынешний switch per-document.
4. DB profile registry/eval activation, typed specialist inputs/outputs/handoff/work items;
   полный Planner/Copywriter/Editor/Analyst, visual provenance/image gateway, Publisher gates.
5. Async model jobs/cancel/reconciliation, usage accounting/budgets/input provenance,
   memory → отдельно подтверждаемые предметные artifacts, abandoned ingestion reconciliation.
6. Server/private HTTPS/authentik/two-machine gates, backup/recovery, явно разрешённый provider smoke.
   Только после этого — рабочий RAG и переход к следующей фазе.

## Технические источники

Контракт text gateway сверён с [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
FTS — с [PostgreSQL 17 text search](https://www.postgresql.org/docs/17/textsearch-controls.html)
и [Tables and indexes](https://www.postgresql.org/docs/17/textsearch-tables.html).
