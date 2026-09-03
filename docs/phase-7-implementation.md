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

**Четырнадцатый срез:** [`planner-adoption.md`](planner-adoption.md) — личный exact preview,
отдельное подтверждение сохранения/раскрытия notes, новая immutable draft-версия плана и
private receipt. Shared notes сохраняют цитаты, источники, warnings и gaps; последующие версии
видят ограничения предка. Новый Planner run обязан сохранить все inherited gaps. Никаких
brief/post/work item, публикации/approval или новых прав worker. MCP/REST общие, панель read-only.

**Тринадцатый срез:** [`planner-drafts.md`](planner-drafts.md) — testing Content Planner по exact
SQL plan/campaign и selected confirmed facts/profile/policy. Темы для 1–5 заданных слотов,
неизменные даты/targets/owner, citations/gaps, общая очередь и MCP/REST/read-only web.
Нет записи плана/brief/post/work item, adoption, расписания или approval; gates сохраняются.

**Двенадцатый срез:** [`copywriter-adoption.md`](copywriter-adoption.md) — личный exact preview,
явное человеческое подтверждение текста/передачи в общий пост, новая immutable редакция и
provenance receipt. Старое approval снято, рабочие копии сохранены, preflight нового текста;
MCP/REST shared, web — история. Без автоматического принятия, публикации или нового model call.

**Одиннадцатый срез:** [`copywriter-drafts.md`](copywriter-drafts.md) — testing text-only Copywriter
по exact SQL revision/brief/facts/policy. Предложения с fact IDs, цитатами и сохранёнными gaps,
shared queue, stale checks, MCP/REST и read-only web. Нет создания/одобрения редакции или media.

**Десятый срез:** [`editor-triage.md`](editor-triage.md) — явные решения Owner по exact findings:
needs_changes/dismissed/open, optimistic version, идемпотентность и immutable история.
Общая MCP/REST логика и read-only web; без исправления текста/approval или нового AI-вызова.

**Девятый срез:** [`editor-review.md`](editor-review.md) — testing text-only Editor по exact
SQL revision/brief/confirmed evidence/policy. Закрытые findings, общая очередь, stale checks,
MCP/REST и read-only web. Нет content writes, human approval, visual/legal verification.

**Восьмой срез:** [`ai-profile-registry.md`](ai-profile-registry.md) — DB registry, immutable
версии/решения, Owner testing selection/disable и точная привязка runs. Произвольных capabilities,
production activation и новых реализованных специалистов нет; paid defaults сохраняются.

**Седьмой срез:** [`memory-curation.md`](memory-curation.md) — отдельное человеческое принятие
memory proposal в новый неактивный reference. Exact context/review/text hashes, union evidence,
Owner-default visibility, ограниченный expiry и immutable initial version/index provenance.
Чат/API, без автоматической записи правил или фактов; activation остаётся отдельным решением.

**Шестой срез:** [`knowledge-file-client.md`](knowledge-file-client.md) — переносимый attachment
client в существующей приватной панели: локальный выбор PDF/DOCX, hash/base64, replay-safe
upload, список/история/отмена и plain-text extraction. Установка Python/CLI сотруднику не нужна.
Это тонкий клиент существующих REST/MCP сервисов; импорт и активация остаются отдельными.

**Третий срез:** optional PDF/DOCX binary workflow — [`knowledge-files.md`](knowledge-files.md).
Оригиналы в private media volume, ClamAV → sandbox → extraction → Owner import → обычный
текстовый pipeline ниже. Это не OCR и не автоматическая активация. Реальный scanner rollout
не выполнен, по умолчанию выключено.

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
  оформить отдельно и пройти его собственное подтверждение. Переход в reference реализован
  седьмым срезом; преобразование в SQL-факт/policy/eval case пока не реализовано.

## Worker и устойчивость

Пятый срез: [`ingestion-jobs.md`](ingestion-jobs.md) — управление/отмена заданий,
immutable transition history, reconciler и явный retry/reindex без потери старой ошибки.

`knowledge_indexes` — специализированная очередь PostgreSQL, не общий outbox dispatcher.
Celery Beat каждые 30 секунд отправляет только сигнал `knowledge.poll`; текст и личные credentials
в Redis не попадают. Fixed-search-path функция возвращает максимум 10 job identifiers,
затем worker восстанавливает actor/identity из БД.

Claim: row lock + lease 120 секунд + случайный fencing token. Парсинг вне транзакции,
ограничение 30 секунд и размера текста. Chunks и ready фиксируются одной транзакцией.
После потери worker reconciler фиксирует failed / processing_interrupted. Неявного повторного
захвата processing нет: для текста новый reindex; для файлов allowlisted retry, до 3 попыток
с новым scan/sandbox. Queue TTL — 24 часа. Ошибка формата terminal, без автоматического обхода.

Membership/user/identity проверяются до и после работы. Reconciler закрывает queued/processing
при отзыве доступа, недоступном документе, истечении TTL/lease (до 10 за tick каждого типа).
Истечение web-session само по себе не отменяет принятую задачу.
Worker не получает human approval: SELECT оригиналов, INSERT chunks, узкий UPDATE служебных
полей индекса; нет UPDATE документа/approval/content. Терминальные indexes/runs защищены trigger,
versions/chunks/receipts/decisions/artifacts append-only.

## AI: точная граница

Четвёртый срез переводит существующие testing assessments в серверную очередь.
Полный контракт — [`ai-jobs.md`](ai-jobs.md). Это не активация новых специалистов.

Есть каталог восьми профилей в коде и DB registry версий purpose/model с immutable snapshot.
Новый queued run требует exact version/selection IDs; старый клиент без них получает blocked.
Произвольные capabilities/production activation недоступны. `testing` не `active`.

| Профиль | Сейчас |
|---|---|
| Product Expert | Тестовая оценка источников, наблюдения/гипотезы/gaps, без verified facts |
| Research Scout | Аналогичная оценка уже разрешённых материалов, без интернет-сбора |
| Analyst | Blocked: нужны metric snapshots |
| Content Planner | Testing: темы exact SQL plan slots по кампании/confirmed facts; не запись плана или расписания |
| Copywriter | Testing: exact SQL revision/brief/facts/policy → отдельное text proposal; не запись редакции |
| Visual Creator | Blocked: нужны media rights/provenance и image pipeline |
| Editor | Testing: exact SQL revision review, без edits/approval; реальные profile evals ещё нужны |
| Publisher | Blocked: используйте manual package workflow фазы 6 |

Ни один профиль не получает инструменты, Principal или клиент доменных команд. Reference output закрыт
схемой: statements с citation IDs и source_observation/conflicting, hypotheses, knowledge_gaps.
Неизвестные поля отвергаются. Все context citations проверяются перед внешним вызовом,
сохранением и чтением artifact. Archive/replacement/expiry скрывают stale artifact при чтении.
Существующая ссылка не доказывает смысловую поддержку утверждения: человеческая проверка обязательна.
Editor отдельно возвращает `EditorialReview`: точные revision/context hashes, IDs SQL records,
цитаты/locations, findings и рекомендацию. Его evidence — SQL, не RAG; контекст проверяется повторно.
Copywriter возвращает `CopyDraft`: exact revision/context hashes, текстовые варианты, fact IDs,
цитаты из предложенного текста и факта, warnings/gaps. Это отдельный artifact, не PostRevision.
Content Planner возвращает `PlanDraft`: exact plan/context hashes и темы заданных слотов,
даты/destinations/owner не меняются; selected fact quotes, warnings/gaps. Не ContentPlan/brief.

Gateway: Protocol + OpenAI Responses structured text, fake HTTP tests. Нет embeddings/image adapters,
генерации файлов, post revisions, tool loop, model-directed network или автоактивации памяти.
Orchestrator не зарегистрирован. `ai_provider=disabled` по умолчанию.

Для paid testing нужны отдельно: Owner + MFA, `testing_only=true`, точная модель, серверный ключ,
allowlist workspace IDs и явное разрешение владельца на платный запрос/передачу корпуса провайдеру.
В этой итерации paid calls не выполнялись. Все разрешённые источники workspace, включая owner-only,
могут попасть провайдеру: allowlist нельзя включать без проверки состава корпуса.

Параметры: `store=false`, `background=false`, без history, до 5 citations, 2000 output tokens,
HTTP timeout 45 секунд и общий worker deadline 60 секунд,
ответ до 200 KB. Лимит 5 reservations на workspace за rolling 24 часа (настройка 1–100),
под workspace lock, учитывает blocked/failed. Это не строгий денежный бюджет.
Timeout/5xx не повторяется автоматически. Тот же ключ возвращает тот же run. Interrupted run
с истёкшим lease 120 секунд фиксируется reconciler как unknown без повторного dispatch;
новый ключ означает новый потенциальный расход. Queue TTL — 24 часа.

Сохраняются provider, фактическая model, response ID, tokens, max output, attempts, profile snapshot,
retrieval ID. Четвёртый срез отдельно сохраняет private immutable input: вопрос,
до 5 citations и semantic request payload/hash без headers/credentials/hidden reasoning.
`cost_usd=null`, не ноль: для точного денежного расхода нужна сверка с провайдером.
Прайс-версии, строгие денежные бюджеты и provider accounting ещё предстоит реализовать.

## Доступ и интерфейсы

`knowledge.write`: Owner/Strategist/Editor — workspace text documents/reindex и draft preview.
Owner-only документы подаёт Owner. Activation/archive/notes/review/testing — Owner + MFA.
Другие участники читают разрешённые documents и ищут активные источники.
Runs/artifacts/receipts/retrieval traces — только initiating actor в пределах текущих прав.
Публичного original download нет.

REST prefix `/api/v1/workspaces/{wid}/knowledge`:

- POST `/commands`: document_submit/index_activate/document_archive/document_reindex/note_propose/note_review;
- POST `/commands`: также `memory_document`; GET `/notes/{nid}` и `/documents/{did}/memory-origin`
  для exact review и Owner-only historical provenance.
- GET `/documents`, `/documents/{did}`, `/documents/{did}/indexes/{iid}/chunks`;
- POST `/search`: query, brand_id, limit;
- GET `/notes`, `/profiles`, `/runs`, `/runs/{rid}`; POST `/runs`: owner testing request.
- POST `/runs/{rid}/cancel`: exact version/idempotency key; GET `/runs/{rid}/inputs`: private input.
- POST `/editor-runs`, MCP `ai_review_revision`: exact SQL revision testing request,
  результат через общие run endpoints; веб показывает замечания без действий approval.
- POST `/copywriter-runs`, MCP `ai_draft_revision`: exact SQL inputs + direction → text proposal,
  общий run lifecycle, без сохранения/применения текста в пост.
- POST `/planner-runs`, MCP `ai_plan_content`: exact plan/campaign/selected facts → предложения тем,
  общий run lifecycle, без content writes или назначения отправок.
- GET `/runs/{rid}/copy-adoption/preview`, GET/POST `/runs/{rid}/copy-adoption`:
  exact preview, человеческое принятие новой редакции и private receipt; MCP
  `ai_copy_adoption_preview`, `ai_copy_adopt`, `ai_copy_adoption_read`. Не approval и не AI job.
- GET/POST `/runs/{rid}/editor-triage`, GET `/runs/{rid}/editor-triage/history`:
  human triage и private история; MCP `ai_editor_triage_read`, `ai_editor_finding_decide`,
  `ai_editor_triage_history`. Новая запись требует актуального отчёта, история — не approval.
- GET `/jobs?kind=index|file`, GET `/jobs/{kind}/{job_id}/history`, POST `/jobs/cancel`.
- POST `/profile-registry/commands`, GET `/profile-registry`, `/profile-registry/{profile}`,
  `/profile-registry/versions/{vid}`; MCP `ai_profile_execute`, `ai_profile_registry`,
  `ai_profile_read`, `ai_profile_version_read`.

MCP: `knowledge_execute`, `knowledge_documents`, `knowledge_document_read`,
`knowledge_index_preview`, `knowledge_search`, `knowledge_notes`, `ai_profiles`, `ai_assess`,
`ai_runs`, `ai_run_read`, `ai_run_inputs`, `ai_run_cancel`, `knowledge_jobs`, `knowledge_job_history`,
`knowledge_job_cancel`. `knowledge_note_read` и `knowledge_memory_origin` обслуживают управляемую curation.
`ai_assess` возвращает queued/blocked, готовый результат запрашивается отдельно.
Сначала `session_read`. Перед activation показать exact candidate/hash/
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

Текстовый/eval срез не добавлял dependencies. Binary срез фиксирует pypdf 6.16.2,
defusedxml 0.7.1 и runtime libseccomp2; optional ClamAV image зафиксирован digest.
`pnpm check`, `pnpm test`, `pnpm build:web`; DB tests только disposable.
Миграции `0005_knowledge`–`0017_plan_adoption` требуют privileged migration role,
runtime остаётся restricted. Новые eval tables append-only, owner-only; worker grants отсутствуют.
Перед реальной БД: отдельное разрешение, backup/restore rehearsal, остановка writers, проверка копии.
Deployment guard обновлён, schema fingerprint не обходится. Старые миграции не менялись.
Rollback кода не удаляет знания; destructive downgrade — только disposable/отдельно разрешённый restore.

Worker включается `SMM_KNOWLEDGE_WORKER_ENABLED=true` только в worker container; Compose передаёт
flag, default false. Нужен работающий scheduler. На настоящем owner server timer cycle не проверен.
При выключенном worker документы честно остаются в очереди.

AI configuration для API и AI-worker задаётся согласованно через защищённую server configuration: `SMM_AI_PROVIDER`,
`SMM_AI_MODEL`, `SMM_AI_API_KEY`, `SMM_AI_ALLOWED_WORKSPACES` (JSON UUID array), `SMM_AI_DAILY_RUN_LIMIT`.
`SMM_AI_WORKER_ENABLED=true` разрешает только серверный worker; default false.
Compose не раздаёт provider secrets всем сервисам и не включает автоматически: authenticated deployment,
egress/provider smoke — отдельный rollout. `store=false` не обещает нулевого хранения у провайдера.

## Остаток фазы 7

1. Binary workflow реализован в третьем срезе, но нужны реальный ClamAV smoke/signature updates,
   проверка RAM/диска/backup/recovery на разрешённом сервере. Удобный attachment client
   реализован в шестом срезе через браузер; реальный вход/загрузка с двух машин ещё не проверены.
   Windows production fallback, OCR и полноценный DLP не реализованы.
2. Реальный owner-approved GreenAurum корпус и eval questions/expected sources/conflicts.
   Инструмент сохранения наборов/прогонов/review реализован во втором срезе; наполнение и
   человеческая проверка реальных ожиданий ещё нужны. Принятый FTS benchmark не закрывает exit gate.
   Автоматического обнаружения противоречий пока нет; model finding — только предположение.
3. После baseline: pgvector, embedding provider/model/dimension/version, hybrid fusion,
   corpus-level parallel reindex/eval switch, сравнение с FTS. Нынешний switch per-document.
4. DB profile registry/testing selection реализован в восьмом срезе; остались production eval
   activation, typed specialist inputs/outputs/handoff/work items;
   полный Planner/Copywriter/Analyst, visual provenance/image gateway, Publisher gates.
   Text-only Editor реализован девятым срезом, human triage — десятым, Copywriter proposals —
   одиннадцатым, человеческое принятие новой редакции с AI provenance — двенадцатым,
   bounded Planner topics — тринадцатым. Остаются model evals, Planner adoption/briefs,
   доказанное исправление между редакциями, визуальная/юридическая верификация и production-включение.
5. Async assessment jobs/cancel/reconciliation и input provenance реализованы в четвёртом срезе.
   Седьмой срез добавил memory → отдельно подтверждаемый reference с provenance.
   Остались строгий денежный accounting/budgets/provider reconciliation, memory → SQL facts/rules/
   eval cases, dependency recall и остальные типы AI jobs. Abandoned ingestion reconciliation,
   отмена и история реализованы в пятом срезе; orphan file cleanup не выполняется автоматически.
6. Server/private HTTPS/authentik/two-machine gates, backup/recovery, явно разрешённый provider smoke.
   Только после этого — рабочий RAG и переход к следующей фазе.

## Технические источники

Контракт text gateway сверён с [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
FTS — с [PostgreSQL 17 text search](https://www.postgresql.org/docs/17/textsearch-controls.html)
и [Tables and indexes](https://www.postgresql.org/docs/17/textsearch-tables.html).
