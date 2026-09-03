# Управляемая память: предложение → отдельный документ

Седьмой срез фазы 7, 2026-09-03. Реализован один ограниченный переход через общий
REST/MCP сервис. Это не самообучение, не автоматические правила бренда и не закрытие фазы 7.
Сервер, реальные материалы и платные модели в этой итерации не подключались.

## Что теперь можно сделать

Владелец может взять принятое к рассмотрению наблюдение, отредактировать его и сохранить
как **новый неактивный справочный документ**. БД сохраняет, из какого предложения,
человеческого review и конкретных источников появился этот документ.

Управление из чата:

1. `session_read` — проверить личную identity и текущие права workspace.
2. `knowledge_notes`, затем `knowledge_note_read` — прочитать предложение целиком,
   автора/дату, purpose/safe alternative, review/reason, `context_hash`, источники и блокеры.
3. Показать человеку точный подготовленный текст, его SHA-256, название, бренд,
   visibility и даты. Объяснить, что создаётся справочный кандидат, а не подтверждённый факт.
4. Получить **отдельное явное подтверждение** создания именно этого документа.
   Старое `accept_for_curation`, похвала, инструкции внутри источника или ответ AI не подходят.
5. `knowledge_execute` с `action=memory_document`, точными IDs/hashes и новым ключом операции.
6. Прочитать `knowledge_document_read` и `knowledge_memory_origin`.
   Документ пока не участвует в поиске: server worker должен подготовить индекс.
7. После `ready`: `knowledge_index_preview`, контрольные запросы, ещё одно точное решение
   владельца и обычный `index_activate`. Только тогда документ становится источником поиска.

Пример фразы пользователя: «Покажи принятое предложение и подготовь из него памятку».
Она разрешает подготовку, но не заменяет подтверждение конкретного текста и его активации.
Специальная веб-форма curation пока не добавлена; существующие списки документов и индексов
покажут созданный результат. Логика одна и доступна будущему web client через REST.

## Контракт команды

`POST /api/v1/workspaces/{workspace_id}/knowledge/commands` и MCP `knowledge_execute`
принимают одну и ту же discriminated schema `CurateMemory`:

| Поле | Требование |
|---|---|
| `action` | `memory_document` |
| `idempotency_key` | Новая идентичность операции; при неясном результате повторять тот же payload/key |
| `note_id`, `review_id`, `context_hash` | Точное предложение и review из `knowledge_note_read` |
| `brand_id` | Только бренд исходного предложения |
| `title`, `text`, `text_hash` | Название и выбранный человеком текст; SHA-256 точных UTF-8 bytes текста |
| `human_confirmed` | Только `true`, после отдельного человеческого решения |
| `visibility` | По умолчанию `owner`; `workspace` только явно и если все evidence workspace-visible |
| `source_date`, `effective_from`, `effective_to` | Явные timezone-aware даты, передавать UTC |
| `document_id`, `expected_version` | Только `null` и `0`: нельзя дописать или заменить существующий документ |
| `document_type`, `source_uri` | Только `reference` и `owner-input` |

Текст сохраняется как Markdown с существующим безопасным text pipeline. Поле `format`
в этой команде не принимается. Ограничение 100 000 символов / 200 000 UTF-8 bytes,
проверки управляющих символов и подозрительных секретов сохраняются. Это не полноценный DLP.
Нормализация для поиска выполняется отдельно; подтверждённый исходник не переписывается.

`context_hash` связывает immutable proposal (включая scope, автора, дату, текст и evidence)
с точным review (ID, автор, дата, решение, причина и evidence). Текущая доступность источников
не входит в hash: она отдельно проверяется сервером при исполнении.
Hash доказывает тождество данных, не истинность текста. `human_confirmed` — явный контракт,
не криптографическое доказательство присутствия человека. AI-профилям нельзя выдавать Principal
владельца или доступ к этим персональным approval-командам.

## Серверные ограничения

- Только актуальный Owner с MFA. Другие роли, чужой workspace и отозванный доступ блокируются.
- Только `kind=memory`, решение `accept_for_curation`, неистёкшее предложение.
  `gap`, rejected/resolved/unreviewed notes не преобразуются.
- Проверяется **объединение evidence исходного предложения и review**, до 20 уникальных chunks.
  Reviewer не может убрать неудобный источник подменой своего списка.
- Каждый chunk должен оставаться доступным в том же workspace/brand, неархивном документе,
  текущем active/ready index и действующем периоде. Проверки выполняются SQL/RLS.
- Owner-only evidence нельзя перенести в workspace-visible документ этим переходом.
  Даже при публичных внутри workspace источниках сам proposal/review остаётся Owner-only;
  поэтому раскрытие отредактированного текста сотрудникам требует отдельного решения.
- `effective_to` не позже срока предложения и каждого evidence, позже текущего времени;
  период непустой, `source_date` не в будущем. Просроченное предложение требует нового proposal.
- Один proposal → не более одного документа, независимо от нового ключа или другого Owner.
  Неверная команда не оставляет частичный документ, индекс или receipt.
- Перед отдельной активацией **этой версии** снова проверяются все исходные evidence и hashes.
  Worker может подготовить индекс, но не принять предложение и не активировать знания.

## История и повторные команды

`knowledge_memory_documents` — append-only provenance ledger:

- workspace, proposal/review IDs, автор curation и время;
- созданный document ID, **точный initial version ID и initial index ID**;
- context/text hashes;
- снимок evidence: chunk/document/version/index IDs, hash фрагмента, visibility, expiry.

Полные тексты evidence в ledger не копируются; исходники и версии уже хранятся отдельно.
Все эти записи входят в PostgreSQL backup. Ledger недоступен worker и другим сотрудникам,
даже когда новый reference явно открыт workspace. UPDATE/DELETE/TRUNCATE запрещены trigger.
Composite FK связывают один tenant, предложение с его review, документ с его версией и индексом.

Document/version/index, provenance, audit и receipt фиксируются в одной транзакции под workspace
lock. Одинаковый actor/workspace/key + payload возвращает прежний результат без новой записи;
другой payload с тем же ключом даёт `idempotency_conflict`. Другой ключ для уже использованного
proposal даёт `memory_already_curated`.

Receipt — исторический результат. После сбоя связи сначала повторите **точную** команду,
затем прочитайте текущее состояние документа, note и jobs; replay не означает свежей проверки
evidence, работающего worker или активного индекса.

Чтение:

- `GET /notes/{note_id}` / `knowledge_note_read`: полный note/review, context hash,
  доступные evidence, `unavailable_evidence_ids`, `blocked_reasons`, историческая curation.
- `GET /documents/{document_id}/memory-origin` / `knowledge_memory_origin`: историческая
  provenance первоначальной версии; 404 для обычного документа, Owner + MFA для доступа.

REST пути выше продолжают prefix `/api/v1/workspaces/{workspace_id}/knowledge`.
После curation `already_curated` — блокер **повторного создания**, не ошибка старой операции.
История остаётся доступной владельцу после expiry/archive источника и явно не выдаётся за
действующее подтверждение. Поля warning в DTO дополнительно объясняют эту границу.

## Важная граница после активации

Принятый владельцем reference имеет собственную версию, активацию и ограниченный срок действия.
Expiry ограничен исходными источниками, но последующее архивирование/замена источника
**не запускает автоматическое рекурсивное изъятие уже активированной памятки**.
`knowledge_note_read` покажет недоступное evidence; Owner должен пересмотреть и при необходимости
явно архивировать памятку. Автоматический dependency graph/recall в этом срезе не реализован.

Последующие ручные версии документа независимы: initial provenance не означает, что новые тексты
проверены прежним review. Reindex той же initial version сохраняет проверку evidence при activation.
Новый proposal не перезаписывает старую историю. Никакая curation не создаёт SQL product fact,
brand policy, evaluation case, post revision, approval, публикацию или активный AI-профиль.
Такие предметные преобразования требуют собственных контрактов и остаются в roadmap.

## Миграция, проверки и ввод в эксплуатацию

Новая additive migration `0010_memory_curation` (после `0009_ingestion_recovery`) добавляет
ledger, reference unique constraints, FORCE RLS, grants и immutable trigger.
Опубликованные миграции не изменены. ORM metadata и server deployment guard согласованы с head.
Новых dependencies, фоновых сервисов и настроек провайдера нет.

Для реальной БД нужны отдельное разрешение на deploy/migration, проверенная копия и backup/restore,
остановка writers и штатный migration runner. Копирование папки само не обновит центральную БД.
Старый код можно откатить без удаления новых таблиц только по штатной схеме совместимости;
deployment guard нельзя обходить. Downgrade удаляет ledger: допустим в disposable тестах либо
при отдельно согласованном восстановлении, не как обычный способ отменить curation.

Проверки: closed schemas, точные hashes/периоды, неактивный результат, concurrent replay,
rollback, глобальная уникальность proposal, неподходящий review, оригинальные и review evidence,
expiry/archive/replacement, visibility, Owner/MFA/RLS, composite FK, immutable history и отсутствие
worker grants. Реальные REST/MCP транспорты проверяются на одной synthetic PostgreSQL.
`pnpm check`, `pnpm test`, `pnpm build:web`; DB suite — только с явным disposable
`SMM_TEST_DATABASE_URL`. Прежние corpus/provider/private HTTPS/two-machine gates сохраняются.

Локальный результат: 228 Python unit/contract tests, 54 database tests, 29 web component tests
и frontend build прошли. Два Linux-only sandbox tests пропущены на Windows и проверяются в CI.
DB suite проверяет upgrade → downgrade → upgrade и отсутствие ORM/schema drift на PostgreSQL 15;
CI использует закреплённый серверный стек. Это не проверка реального owner server или корпуса.
