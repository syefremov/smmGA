# Центральная модель данных — фаза 4

Пятый срез фазы 7: `0009_ingestion_recovery` добавляет version/start/finish и cancelled
для text/file jobs, actor-private cancel receipts и DB-triggered immutable transition events.
RLS ограничивает историю Owner/автором видимого parent; worker получает только служебные
переходы и bounded reconciler. Старые jobs не получают вымышленную историю задним числом.
Контракт и миграционная совместимость — [`ingestion-jobs.md`](ingestion-jobs.md).

Четвёртый срез фазы 7: `0008_ai_queue` добавляет actor/Owner-private immutable `ai_inputs`
(question, citations, semantic request payload/hash), `ai_cancel_receipts` и поля очереди
в `ai_runs`: originating identity, version, lease/token, start/finish timestamps.
API создаёт run/input и отменяет; worker читает входы и создаёт immutable artifact.
Прямой INSERT artifacts для API отозван. Reconciler имеет узкую SECURITY DEFINER функцию:
max 10 terminal transitions с аудитом, без возврата текстов или повторного dispatch.
Роли, state machine и миграционная совместимость — [`ai-jobs.md`](ai-jobs.md).

Третий срез фазы 7: `0007_knowledge_files` добавляет `knowledge_files` (метаданные/очередь),
immutable `knowledge_extractions` (текст/hash/parser/scan evidence) и retry receipts.
FORCE RLS: uploader либо Owner, не другие сотрудники; composite workspace/file/brand FKs.
Worker может claim/finalize, но не импортировать/активировать знания. Ready originals/extractions
не переписываются. Nullable `knowledge_document_versions.source_file_id` сохраняет provenance.
Бинарные оригиналы — **private media volume**, не PostgreSQL; backup должен включать оба.
Хранение, state machine, квоты и rollback — [`knowledge-files.md`](knowledge-files.md).

Второй срез фазы 7: `0006_retrieval_eval` добавляет append-only datasets/runs/reviews/receipts.
Owner-only FORCE RLS, INSERT actor=current, composite brand/family/hash FKs, без worker grants.
Versions, corpus snapshots, metrics, exact human review и stale semantics —
[`retrieval-evaluations.md`](retrieval-evaluations.md). Все данные входят в PostgreSQL backup.

Дополнение первого среза фазы 7: `0005_knowledge` добавляет документы/immutable versions,
queue/indexes/chunks/activations, retrieval traces, private receipts, owner notes/reviews,
actor-private AI runs и immutable artifacts. Text originals входят в PostgreSQL backup.
Composite tenant/document foreign keys, FORCE RLS, отдельные worker grants и terminal triggers
описаны в [`phase-7-implementation.md`](phase-7-implementation.md). Это FTS-only foundation,
не полный registry/hybrid RAG; старые published migrations не меняются.

Дополнение фазы 6: `0004_content` добавляет typed immutable `content_records`/`content_links`,
posts/revisions, review/decisions/comments, личные working copies, manual packages/cancellations,
idempotency receipts и task assignments/dependencies. Полная физическая модель, RLS, права,
ограничения и откат — [phase-6-implementation.md](phase-6-implementation.md).

Дополнение фазы 5: `0003_operations` добавляет tenant-таблицы `work_items`, `brands`,
`products`, `sources` и ограниченную функцию `smm_my_workspaces()` для собственных membership.
Права, state/version/idempotency и ограничения каталогов описаны в
[phase-5-implementation.md](phase-5-implementation.md). Предыдущие миграции неизменны.

Источник истины — PostgreSQL. Миграция `0002_identity` добавляет foundation к неизменённой `0001_phase_two`. SQLAlchemy models описывают актуальный контракт; migration содержит самостоятельный снимок DDL и не импортирует меняющиеся модели.

## Таблицы и границы

| Таблица | Область | Назначение |
|---|---|---|
| users | Глобальная identity | Человек, отображаемое имя, active |
| user_identities | Глобальная identity | Явная связь user с уникальной парой issuer + subject |
| login_flows | Временная identity | Одноразовый state, привязка к браузеру, PKCE verifier, nonce, TTL 5 минут |
| web_sessions | Глобальная identity | Хеш случайного session ID, identity, MFA, абсолютный/idle TTL, отзыв |
| workspaces | Tenant | Организация, slug, часовой пояс |
| memberships | Tenant | Одна активная роль человека в workspace |
| audit_events | Tenant / security | Append-only события; NULL workspace для глобального входа/отказа |
| system_jobs | Tenant | Долговечная диагностическая задача и инициатор |
| file_metadata | Tenant | Ключ хранения, MIME, размер, SHA-256; сами файлы вне БД |
| idempotency_keys | Tenant | Actor + операция + хеш ключа + fingerprint запроса + результат |
| outbox_events | Tenant | Событие, атомарно созданное с задачей; будущая доставка в очередь |

Identity-таблицы — документированное исключение из workspace_id: пользователь должен входить до выбора workspace. API-процесс имеет только SELECT для users/identities и не умеет регистрировать пользователей, связывать email или выдавать роли. Worker не имеет доступа к сессиям и login flows. Доступ операторского CLI отдельный и привилегированный.

## Транзакции и изоляция

`Database.transaction` — Unit of Work: commit только при успешном выходе, rollback при исключении. `set_config(..., true)` устанавливает user/workspace только внутри транзакции; пул не сохраняет контекст следующему запросу. Параметры — UUID, не произвольный SQL.

`AccessService.authorized` каждый раз перечитывает active пользователя, identity и membership, проверяет permission/MFA. Ни workspace из URL, ни роль из JWT, ни имя AI-профиля не дают прав.

На tenant-таблицах включены ENABLE + FORCE RLS. Политика сопоставляет workspace с транзакционным контекстом и действующим membership. Узкая SECURITY DEFINER-функция `smm_member(uuid)` читает membership без рекурсивного RLS; у неё фиксирован search_path и нет динамического SQL. Её владелец — migration administrator; PUBLIC EXECUTE отозван. Приложению не выдаётся BYPASSRLS. Контекст устанавливает только доверенный backend: это дополнительная защита от пропущенного фильтра, не обещание защиты от полностью скомпрометированного сервера с произвольным SQL.

Составные FK не дают файлу/outbox/idempotency ссылаться на job чужого workspace. Runtime credentials различны: `smm_api_login → smm_app`, `smm_worker_login → smm_worker`. Мигратор и backup используют отдельную административную identity. Приложение с включённой авторизацией отказывается стартовать под владельцем таблиц/superuser/BYPASSRLS/CREATEROLE/CREATEDB.

## Аудит, повторы, worker

У аудита есть actor, request ID, UTC timestamp, workspace, action, target, outcome. Request ID создаёт сервер и возвращает в X-Request-ID. Payload, cookies, claims, headers, строки подключения и произвольные metadata в аудит не принимаются. Отказы записываются отдельной транзакцией после rollback. На уровне HTTP могут быть два связанных события: предметный отказ и общий отказ запроса.

Runtime получает только INSERT/SELECT аудита. Trigger запрещает UPDATE, DELETE и TRUNCATE даже обычной операторской командой. Это не криптографический WORM: администратор БД технически может изменить DDL; внешнее защищённое архивирование — эксплуатационный этап.

Диагностическая операция блокирует конкурентный idempotency key транзакционным advisory lock. Job, idempotency record, outbox и успешный audit фиксируются вместе. Повтор возвращает тот же job ID. Неизвестный outcome внешней публикации здесь не решается — внешних действий ещё нет.

`run_job` под отдельной worker role заново проверяет текущий доступ и сохранённого actor, блокирует job и безопасно принимает повтор. В фазе 4 это проверенный executor-контракт; автоматический outbox dispatcher, retries и Celery orchestration — фаза 5. Нельзя передавать в worker произвольный Principal из недоверенного сообщения: диспетчер должен восстановить actor/identity из доверенной БД и проверить credential context. Просроченная web-сессия не отменяет сама по себе уже принятую серверную задачу, отзыв membership отменяет доступ исполнителя.

## Миграции и восстановление

Новая установка: `python -m smm_gpt.cli --apply migrate` внутри migration container. Она выполняет Alembic и создаёт runtime logins из отдельно переданных случайных credentials. App/worker containers не получают административный пароль или полный `.env`. Старый server.env без новых runtime credentials отвергается, а не переписывается автоматически.

`pnpm env:init` добавляет в существующий локальный `.env` только отсутствующие runtime credentials; прежние значения сохраняются. `pnpm db:migrate` оставлен как чистый Alembic upgrade для настроенной локальной БД; Compose migration entrypoint дополнительно подготавливает logins.

Production — forward-only. Перед существующей БД: backup + isolated restore, проверка migration на копии, maintenance window, остановка writers, отдельное разрешение на migration, upgrade, grants/RLS проверки, затем совместимый release. Автоматический deploy по-прежнему **отказывается пересекать migration fingerprint**. Нельзя обойти это удалением current.json или редактированием старой migration. Downgrade с удалением таблиц допустим только на disposable fixture; rollback приложения не откатывает данные.

Restore drill заранее создаёт NOLOGIN group roles, затем восстанавливает ACL, policies и functions вместе с данными. Runtime logins/пароли заново привязываются только при отдельно разрешённом вводе восстановленной копии в эксплуатацию. Текущий restore остаётся изолированным и не заменяет активную БД.

RLS/owner semantics сверены с [PostgreSQL 17](https://www.postgresql.org/docs/17/ddl-rowsecurity.html). Тесты проверяют upgrade → downgrade → upgrade, совпадение metadata, cross-tenant FK, изоляцию без фильтра, запреты grants и append-only audit.
