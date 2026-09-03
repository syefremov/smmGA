# PDF/DOCX: закрытая загрузка в базу знаний

Пятый срез добавляет управление jobs, отмену, историю и восстановление статусов после сбоев —
[`ingestion-jobs.md`](ingestion-jobs.md), migration `0009_ingestion_recovery`.

2026-09-03, третий репозиторный срез фазы 7. Фаза не завершена. Реальные документы,
антивирус на сервере и production ingestion в этой итерации не подключались.

## Что реализовано

Чат и REST используют один сервис. Последовательность:

1. Owner/Editor/Strategist загружает файл: filename, format, SHA-256, base64 и idempotency key.
2. Сервер проверяет размер/расширение/magic/hash, сохраняет неизменяемый оригинал в private volume
   и метаданные/queued job в PostgreSQL. Файл видят только автор и Owner workspace.
3. Worker читает оригинал с проверкой hash, отправляет байты в private ClamAV INSTREAM,
   требует **точный clean ответ** и сигнатуры не старше 48 часов, проверяет версию до/после.
4. Linux subprocess извлекает текст. До чтения входа включаются seccomp default-deny,
   ограничения CPU/RAM/FD, запрещены открытие файлов, сеть и создание процессов.
   Дочернему процессу не передаются DB/API secrets или личная identity.
5. `ready` содержит неизменяемый текст, его hash, parser version и scan evidence. Это ещё
   не источник поиска, не проверенный факт и не гарантия отсутствия всех вредоносных конструкций.
6. Owner просматривает текст и отправляет `knowledge_execute` / `file_import` с точным
   `file_id`, `text_hash`, метаданными/visibility/effective dates и `human_confirmed=true`.
   Создаются новая текстовая версия и index job. После подготовки требуется отдельный
   `index_activate` с обычными проверками. Файл не может сам попросить своё одобрение.

Извлечённый текст остаётся недоверенными данными. Инструкции внутри файла не дают прав
выполнять команды. `human_confirmed` — протокольное решение личного Owner + MFA,
не криптографическое доказательство присутствия человека; AI-профилю эти права не выдаются.

## Интерфейс

MCP: `knowledge_file_submit`, `knowledge_files`, `knowledge_file_read`,
`knowledge_file_retry`, `knowledge_file_rescan`; импорт — `knowledge_execute`.
Перед работой `session_read`; повтор сетевого запроса использует тот же idempotency key.
Другой payload с тем же ключом — conflict. Rescan требует **новый** ключ.

REST prefix `/api/v1/workspaces/{workspace_id}/knowledge/files`:

- POST `/`: загрузка; GET `/`: cursor/limit список (25 по умолчанию).
- GET `/{file_id}`: метаданные, состояние, безопасный код ошибки, extraction для ready.
- POST `/retry`: file_id, expected_attempts, idempotency_key.
- POST `/rescan`: file_id, новый idempotency_key; новая копия/job, старая история сохранена.
- GET `/{file_id}/original`: только fresh ready, authenticated attachment, opaque filename,
  octet-stream, CSP sandbox, no-store, nosniff. Нет публичных ссылок/inline viewer.
- POST `/api/v1/workspaces/{workspace_id}/knowledge/commands`: `file_import`.

Примеры запросов человека: «покажи мои файлы и ошибки», «покажи извлечённый текст»,
«повтори временно неудачную обработку», «пересканируй этот оригинал».
Перед импортом чат показывает конкретный файл, hash, видимость и срок действия для подтверждения.
Base64 и SHA-256 должен вычислять клиент из реальных байтов, **не модель вручную**.
Автоматического доступа MCP к вложениям этого чата или локальным файлам сотрудника нет.
Удобный attachment uploader/CLI ещё требуется; готовый REST-клиент может передать payload сейчас.
Новых элементов web UI в этом срезе нет. Citation теперь содержит nullable `source_file_id`;
сам ID не даёт разрешения скачать приватный оригинал.

## Ограничения и отказоустойчивость

- Файл до 2 MiB; HTTP body до 3 MiB, включая chunked REST/MCP, до JSON/base64 parsing.
  Lifetime квота на сотрудника/workspace: 200 файлов и 100 MiB. Rescan расходует квоту.
- PDF: до 50 страниц, object graph до 20 000 объектов, поток страницы до 4 MiB после распаковки;
  allocation до проверки также ограничена памятью subprocess. Зашифрованные и активные/embedded
  PDF отклоняются. Скан без текстового слоя → `ocr_required`, OCR не выполняется.
- DOCX: до 200 ZIP entries, 8 MiB суммарно распакованных данных, 2 MiB на entry, ratio до 100.
  Нет disk extraction. Запрещены traversal/symlinks/encrypted entries, DTD/entities,
  макросы, вложенные объекты, внешние relationships и поля. Консервативная политика может
  отклонять обычный документ с внешней гиперссылкой — подготовьте пассивную копию.
- Текст до 100 000 символов / 200 000 UTF-8 bytes. Regex-фильтр секретов не заменяет полноценный DLP.
- Sandbox: Linux + libseccomp2, 256 MiB address space, 5 CPU seconds, 15 seconds wall timeout,
  bounded stdout, stderr не журналируется. Windows / ошибка sandbox → отказ, не fallback.
- Scanner timeout 30 секунд; недоступность/невалидный ответ/старая база → отказ. Чистый ответ
  не исключает неизвестные угрозы. ClamAV требует обслуживаемых актуальных сигнатур.
- PostgreSQL queue, lease 120 секунд + fencing token, max 3 attempts. Beat каждые 30 секунд,
  максимум 5 IDs за poll. Redis не хранит бинарные файлы/текст/личные credentials.
- Явный retry только для scanner unavailable/stale, sandbox unavailable, timeout/resource limit
  и processing_interrupted, до 3 попыток в пределах 24 часов от создания job.
  Malware/invalid format не обходятся retry. Rescan создаёт новый scan, не меняет старый verdict.
- До и после работы проверяются действующие user/membership/identity. Reconciler закрывает
  прерванные/просроченные/отозванные jobs как failed. Автоматического повторного захвата
  processing больше нет; ручной retry сохраняет историю и снова выполняет scan/sandbox.
- После 48 часов от даты сигнатур импорт/скачивание блокируются; просмотр истории разрешён.
  Нужен rescan. Уже активированный **текстовый** индекс автоматически не снимается; это не
  механизм retroactive malware recall. При выявлении проблемы Owner архивирует документ.

## Хранение, миграция и backup

Решение D-009: `/var/lib/smm-gpt/media/knowledge-originals/<UUID>.blob` на сервере,
в контейнере `/app/.data/media`. API RW, worker **RO**, scanner не получает media mount/DB credentials.
Пути пользователя не принимаются. Original создаётся O_EXCL, не перезаписывается; проверка hash
при чтении/import. Raw bytes не в PostgreSQL и не в Git. Extraction/version/chunks разделены.
`knowledge_document_versions.original` для binary-origin версии — извлечённый текст;
его source_file_id ведёт к отдельному binary hash и scan evidence.

Write-before-DB-commit может оставить недоступный orphan после отказа транзакции/диска.
Автоматического удаления нет. Следить за свободным диском; очистка только отдельной,
проверенной процедурой с разрешением и сверкой БД/backup. Квота считает DB records, не orphans.
Backup/restore должны согласованно охватывать **PostgreSQL и media volume**, включая quarantine.
Scanner signatures можно скачать заново, они не заменяют сохранённую scan evidence.

Новая migration `0007_knowledge_files`: FORCE RLS, private files/extractions/retry receipts,
immutable terminal history и scoped FK source_file_id. Старые 0001–0006 не изменялись.
Runtime не получает migration credentials; worker не получает file_import/index_activate.
Downgrade удаляет данные/связи, допустим только disposable tests либо отдельно разрешённый
restore-backed rollback. Deployment schema guard обновлён, его обходить нельзя.

## Включение — отдельная эксплуатационная работа

Defaults: `SMM_KNOWLEDGE_FILES_ENABLED=false`, text worker независимо
`SMM_KNOWLEDGE_WORKER_ENABLED=false`. API/worker читают первый flag; text indexing требует второй
в worker. Scheduler обязателен. Выключение первого прекращает новые uploads/retries/poll,
но не отменяет уже выполняющийся job и не запрещает импорт ранее готового fresh extraction.

`ops/compose.knowledge-files.yaml` — optional ClamAV overlay, без опубликованного TCP port,
private backend network, signatures volume, TZ=UTC; image 1.4.3 закреплён digest.
Не подключается deployment scripts автоматически и не включает flags/authentication.
Для разработки объединять с `compose.yaml`, для staging **только** с `ops/compose.server.yaml`,
никогда с development ports. `SMM_CLAMAV_HOST/PORT` — server configuration, defaults clamav:3310;
Compose использует эти defaults, переопределение требует явного environment overlay.
`server.env` существующего server manager принимает только DB keys: feature flags не дописывать
туда; при отдельном commissioning оформить защищённую воспроизводимую service configuration.

Перед разрешённым rollout проверить RAM/диск: scanner имеет mem_limit 1536m, ему и приложению/БД
нужен запас хоста; маленький VPS может быть недостаточен. Это лимит, не гарантия достаточности.
Freshclam требует разрешённого исходящего доступа к обновлениям, первая загрузка может быть долгой.
clamd TCP не имеет собственной аутентификации/TLS — не публиковать наружу, не подключать
недоверенные контейнеры в его сеть. Сервис запускается штатным entrypoint образа; его privileges
и изоляция от соседних workloads требуют проверки при вводе в эксплуатацию.

Порядок commissioning: отдельное разрешение → backup/restore drill → проверка копии миграции →
Linux images/resources → private scanner + актуальные signatures → synthetic clean/rejected smoke →
auth/private HTTPS → feature flags → upload/import/activation с двух личных машин.
Никаких реальных файлов/платных вызовов/публикаций для обычных тестов не требуется.

## Проверки и первичные источники

Unit: пассивные и активные PDF/DOCX, zip expansion, XML entities, storage integrity,
framed ClamAV clean/found/error, freshness, bounded HTTP. Linux tests запускают настоящий
subprocess, проверяют запрет file/network/fork и лимиты RAM/CPU; Windows не подменяет их mock-success.
CI дополнительно извлекает оба synthetic файла в собранном worker image (`scripts.parser_smoke`).
PostgreSQL: upgrade/downgrade/upgrade, RLS/grants/history, concurrent idempotency/claim,
retry limit, revocation, Owner import/activation, source_file_id, REST/MCP parity.
Реальный ClamAV engine/signature download smoke пока не выполнен; protocol tests используют fake daemon.

[ClamD protocol](https://docs.clamav.net/manual/Usage/ClamdProtocol.html),
[официальный Docker image](https://docs.clamav.net/manual/Installing/Docker.html),
[pypdf extraction limitations](https://pypdf.readthedocs.io/en/stable/user/extract-text.html),
[seccomp rules](https://man7.org/linux/man-pages/man3/seccomp_rule_add.3.html).
