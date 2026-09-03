# Управление обработкой знаний — пятый срез фазы 7

2026-09-03. Закрыт пункт abandoned ingestion reconciliation для текстовых индексов и PDF/DOCX.
Это управление уже существующей обработкой, не новые источники, гибридный поиск, AI-профили
или автоматическая публикация. Сервер, реальные файлы и внешние провайдеры не подключались.

## Что доступно из чата

- «Покажи задания обработки текстов/файлов» → `knowledge_jobs`, kind `index` / `file`.
- «Покажи историю этого задания» → `knowledge_job_history`.
- «Отмени обработку» → `knowledge_job_cancel`: kind, job_id, точный expected_version,
  idempotency_key. Используется **версия задания**, не версия документа.
- «Повтори прерванную обработку файла» → прежний `knowledge_file_retry` с expected_attempts.
- Для текста — прежний `document_reindex`: новый index_id, точный document version и source version.
- Для нового сканирования оригинала / новой identity — прежний `knowledge_file_rescan`:
  новый job и расход квоты, полный scan/sandbox заново.

Сначала `session_read`. Owner + MFA видит и отменяет доступные задания workspace.
Editor/Strategist управляют только своими заданиями. Видимость общего документа сама по себе
не разрешает отменять чужую обработку; Administrator/Viewer этих команд не получают.
Обычный документный read/search сохраняет прежние права. Чужой workspace не раскрывается.

Cancel receipt исторический: повтор с тем же ключом возвращает его, другой payload — conflict.
После команды перечитайте jobs. Неверная версия — conflict; ready/failed/cancelled отменять нельзя.
Проверка исходной identity перед выполнением не означает, что для cancel нужна та же identity:
авторизованный Owner может остановить старое задание сотрудника.

## Состояния, сроки и отмена

| Состояние | Действие |
|---|---|
| queued | Worker может зарезервировать обработку; пользователь может отменить |
| processing | Только текущая попытка с lease; отмена сразу переводит в cancelled |
| ready | Результат подготовлен; история неизменяема, отдельное Owner-подтверждение всё ещё нужно |
| failed | Безопасный код ошибки; для текста новый reindex, для файла только разрешённый retry |
| cancelled | Терминально; исходник сохранён, старый результат не может завершить задание |

Cancel **не удаляет файл, chunks или документ**, не деактивирует готовый индекс и не завершает
насильно поток/процесс parser или уже начатую проверку scanner. Работа может физически закончиться,
но state/token/version fence запрещает сохранение её результата. Побеждает первая транзакция:
если ready уже записан, отмена возвращает conflict/недопустимое состояние.

Worker теперь забирает **только queued**. Сохраняются started_at, version, attempts,
lease 120 секунд и случайный token. Парсинг идёт вне транзакции. Ready допустим только с
действующим lease и уже записанными chunks/extraction в той же транзакции.
Сбой не приводит к скрытому повторному захвату processing.

TTL — 24 часа от создания задания, включая повторные попытки одного file_id.
Истёкший текстовый job требует нового reindex; файл — нового rescan/upload.
Срок источника проверяется до и после индексирования; будущий effective_from не мешает подготовке,
но поиск по-прежнему исключает ещё не действующий источник.

## Reconciler

Перед обычным poll каждый включённый worker вызывает `smm_ingestion_reconcile(kind)`.
SECURITY DEFINER с фиксированным search_path, только worker EXECUTE, без внешнего I/O:

- максимум 10 изменений за вызов, FOR UPDATE SKIP LOCKED;
- отозванные user/membership/исходная identity/роль → failed / authorization_changed;
- архивированный/просроченный текстовый источник → failed / document_unavailable;
- queued/processing старше 24 часов → failed / queue_expired;
- processing с истёкшим lease → failed / processing_interrupted;
- legacy processing без lease считается прерванным через 2 минуты от created_at.

При нескольких причинах приоритет: права, документ, TTL, прерванная обработка.
Ready/failed/cancelled не изменяются; готовые активные знания не снимаются.
Состояние, системная история и audit фиксируются атомарно. Функция возвращает только число записей.
Содержимое файлов, вопросы, тексты и credentials в Redis/audit/history не отправляются.

Ограничение 10 относится к одному вызову, не к workspace: следующий tick продолжает очередь.
Полноценный мониторинг очереди/alerts и настройка SLA относятся к последующим операционным задачам.

## Повторная обработка

Файловый retry допускает только прежние временные scanner/sandbox/timeout/resource ошибки
и новый processing_interrupted, максимум 3 попытки на file_id, в пределах TTL.
Исходные user/identity/role должны оставаться активными. Owner не подменяет автора старого job:
при смене identity создаётся отдельный rescan с текущей личной identity.

Каждая попытка читает и сверяет immutable original, повторяет ClamAV и sandbox.
Malware, неверный формат, отозванный доступ, cancellation и expired queue не становятся retryable.
Rescan также не обход защиты: создаёт новую запись с полной проверкой и прежней storage quota.
Отмена не возвращает квоту хранения; оригиналы сохраняются.

Для текста failed/cancelled индекс не переписывается: reindex создаёт новую запись.
Действующий индекс сохраняется до отдельного точного Owner index_activate. Ни retry, ни отмена,
ни восстановление worker не являются подтверждением фактов или разрешением публикации.

## История и контракты

REST prefix `/api/v1/workspaces/{workspace_id}/knowledge`:

- GET `/jobs?kind=index|file`: cursor/limit, по умолчанию 25;
- POST `/jobs/cancel`: CancelIngestion;
- GET `/jobs/{kind}/{job_id}/history`: последние 50 событий, по возрастанию version,
  явный truncated. Предыдущую историю при необходимости смотрит администратор БД по отдельной процедуре.

API/MCP вызывают один IngestionService. Списки не возвращают оригиналы/фрагменты/имена файлов,
только IDs, actor_id, state, version, attempts, безопасную ошибку и timestamps.
Имена/тексты доступны через прежние отдельные разрешённые команды.
IndexView/FileView также получили version/started_at/finished_at.
Новых web-кнопок нет; текущий просмотр знаний и сгенерированные типы совместимы.

`knowledge_job_receipts` хранит actor-private отмены с scoped FK и уникальным ключом.
`knowledge_job_events` — append-only журнал каждого INSERT/UPDATE после миграции:
job version, state, attempts, code, actor_id, timestamp. Запись только DB trigger,
прямой INSERT/UPDATE/DELETE для API/worker запрещён. Reconciler записывает actor_id=null
(системный переход), а ручная отмена — ID текущего пользователя.
Причина старой ошибки сохраняется после retry. История старых jobs до этой миграции
не восстанавливается задним числом; их timestamps могут быть null.

RLS истории наследует видимый parent и границу Owner/автор. Read истории не выдаёт права на original.
История ingestion не управляет AI-заданиями: у них отдельные команды и запрет автоматического
повтора неопределённых платных вызовов — [ai-jobs.md](ai-jobs.md).

## Настройка и миграция

Новых dependencies, credentials, фоновых сервисов или разрешений провайдеров нет.
Существующие flags остаются false: SMM_KNOWLEDGE_WORKER_ENABLED для текста,
SMM_KNOWLEDGE_FILES_ENABLED для файлов. При выключенном worker автоматический reconciler
этого типа тоже не работает; ручные read/history/cancel остаются доступны.
Само изменение flag не является отменой уже принятого job.

`0009_ingestion_recovery` не меняет опубликованные 0001–0008. Добавляет version/timestamps,
cancelled state, receipts/events, ограниченные функции, переходы и grants. Старые API/worker
не совместимы с новой схемой: до отдельно разрешённого deploy нужен backup/restore rehearsal,
проверка копии и остановка старых writers, без смешанного rolling old/new запуска.
Нельзя обходить schema guard или вручную сбрасывать state/attempts/lease.

Downgrade разрушителен: удаляет новые receipts/events и queue metadata, переводит cancelled
в failed для старой схемы. Это не безопасная отмена deploy; только disposable tests
или отдельно разрешённый restore-backed rollback. Commit/push не разрешает deploy.

## Проверки и оставшиеся ограничения

Тесты на disposable PostgreSQL: миграции/metadata drift, отмена queued/processing, конфликт версий,
concurrent idempotency/reconciliation, late output, приватность, immutable history,
TTL/batch bound, отзыв identity, безопасный retry со свежим scanner, REST/MCP parity.
Прежние text/binary/AI/retrieval/content тесты остаются обязательными.

Реальный ClamAV smoke, обновление сигнатур, RAM/диск, private HTTPS/authentik, backup media+DB
и работа с двух машин требуют отдельного rollout. Orphan originals автоматически не удаляются.
Полноценные DLP/OCR, гибридный RAG, реальный eval-корпус, specialist workflows и денежный
accounting этим срезом не закрыты. Фаза 7 остаётся частичной.
