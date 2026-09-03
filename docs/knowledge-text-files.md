# Markdown, CSV и HTML: файлы справочных знаний

2026-09-03, пятнадцатый репозиторный срез фазы 7. **Фаза остаётся частичной.**
Расширена существующая закрытая загрузка PDF/DOCX, а не создан новый контур ingestion.
Сервер, реальные документы, провайдеры и аккаунты соцсетей не подключались.

## Что можно сделать

Через `knowledge_file_submit` / тот же REST `POST .../knowledge/files` принять файл;
через `knowledge_file_read` прочитать его состояние и извлечение. В существующей панели
«База знаний → Файлы» сотрудник выбирает локальный документ без установки Python/CLI.
У MCP нет произвольного доступа к локальному диску или вложениям Codex: hash/base64
готовит клиент из **реальных байтов**, не модель по памяти.

В чате можно попросить: «Покажи извлечённый текст этого файла и его ограничения».
После просмотра — отдельно подтвердить Owner `file_import` с точными ID/hash,
метаданными, видимостью и датами; после подготовки индекса — отдельно `index_activate`.
Ни успешная загрузка, ни clean scan, ни `ready` не включают источник в поиск.
Это справочный текст, не подтверждённые SQL-факты о продукте, ценах или продажах.

## Форматы и ограничения

| Формат API | Расширения | Правила извлечения |
| --- | --- | --- |
| `markdown` | `.md`, `.markdown` | UTF-8, CRLF/CR → LF, внешние пробелы удаляются. Разметка, frontmatter и код остаются текстом. |
| `csv` | `.csv` | UTF-8, запятая, двойные кавычки, обязательная строка заголовков; все значения — строки. |
| `html` | `.html`, `.htm` | UTF-8, консервативный пассивный поднабор; только текст без атрибутов/комментариев/изображений. |
| `pdf`, `docx` | `.pdf`, `.docx` | Прежние правила и версии парсера без изменений: [файловый pipeline](knowledge-files.md). |

Регистр расширения не важен; MIME от браузера не считается доказательством типа.
UTF-8 может иметь BOM, который убирается только при декодировании. Исходные bytes/hash
сохраняются, включая BOM и переводы строк. Неверная кодировка, запрещённые C0 controls
(кроме CR/LF/tab), DEL, известные бинарные префиксы и пустой текст отклоняются.
Это ограниченная проверка конверта, не универсальное распознавание polyglot и не антивирус.
Кодировка не угадывается: [Python UTF-8-sig](https://docs.python.org/3/library/codecs.html#encodings-and-unicode).

Общие лимиты прежние: оригинал 2 МиБ, HTTP 3 МиБ, извлечение 100 000 символов / 200 000
UTF-8 bytes, lifetime квота 200 файлов / 100 МиБ на человека/workspace. Размер результата
проверяется при накоплении, весь парсер дополнительно ограничен ресурсами Linux child.

CSV: до 30 колонок, 1000 записей данных, 6000 символов на поле; заголовки не пустые
и без точных дубликатов, ширина каждой записи совпадает. Многострочные quoted поля допустимы.
Не угадываем разделитель, наличие header, числа и даты; файл с `;` может стать одной колонкой.
Извлечение — `# Record N` и JSON-quoted пары `"заголовок": "значение"`, сохраняющие границы
ячеек, кавычки и embedded newlines. Формулы (`=`, `+` и т.п.) не вычисляются.
Основа — строковый [Python csv.reader](https://docs.python.org/3/library/csv.html#csv.reader),
без `Sniffer` и `QUOTE_NONNUMERIC`. Это **не импорт Wildberries, метрик или прайс-листов**.
Скачанный оригинал остаётся исходным: сторонний Excel/другой табличный редактор может
интерпретировать формулы. Безопасный текстовый предпросмотр не гарантирует безопасность
открытия оригинала во внешнем приложении.

HTML: до 64 уровней вложенности, 5000 элементов, 30 атрибутов элемента, 20 000 событий.
Разрешены обычные структурные/текстовые теги, списки, таблицы, `a`, `img`, `meta charset=utf-8`;
точный allowlist — `src/smm_gpt/parsers/text_files.py`. `script`, `style`, формы, iframe,
SVG/MathML, неизвестные теги, event handlers, `style/srcdoc/hidden/http-equiv`, активные
`javascript/vbscript/data/file` URL и нестандартные declarations отклоняются.
Разрешён только простой `<!doctype html>`. Требуем явное совпадение закрывающих тегов;
обычный браузерный HTML с опущенными окончаниями может не пройти. Это не валидатор всего HTML,
не полноценный sanitizer и не воспроизведение браузерного отображения. Мы добавляем свои
ограничения поверх невалидационного [HTMLParser](https://docs.python.org/3/library/html.parser.html).
Обычные URL никогда не загружаются; атрибуты, `alt` и изображения не становятся текстом.
Нет OCR, JS/CSS execution, inline rendering или сохранения extraction как исполняемой разметки.
Entity-encoded `<script>` может стать буквальным текстом — UI обязан показать его как text node.

## Безопасность, provenance и повторы

- API/браузер проверяют envelope/UTF-8, **не разбирают CSV/HTML**. В worker: fresh ClamAV,
  затем child с default-deny seccomp, CPU/RAM/FD/time limits, без сети/open/fork и credentials.
  Нет Windows/in-process production fallback. Недоступный scanner/sandbox означает отказ.
- Парсер preload включает UTF-8-sig до lockdown. Версии новых извлечений:
  `markdown-utf8-v1`, `csv-utf8-rows-v1`, `html-passive-utf8-v1`. PDF/DOCX version неизменна.
- Оригинал лежит в immutable private volume; PostgreSQL хранит его исходный format/hash,
  scan evidence, extraction text/hash/parser version, job/history. До/после обработки
  перепроверяются actor/identity/lease fencing. Только автор и Owner читают private files.
- `file_import` сохраняет **готовый текст** в новую `KnowledgeVersion` с format `markdown`
  и `source_file_id`. CSV/HTML не парсятся повторно внутри API/text index worker.
  Исходный формат остаётся у файла; нормализованные chunks/index отдельно. Retrieval algorithm
  `ru-simple-v1` и прежние content/AI hashes не меняются; новая копия требует обычной проверки.
- Browser identity остаётся `browser-file-v1` с canonical format, именем, brand/workspace
  и raw hash. Повтор неизвестного исхода использует тот же запрос. Новый UUID/имя не способ
  безопасного retry; пользовательский rescan — отдельное явное действие.
- Терминальные ошибки парсинга не retryable. Исправленный оригинал — новая загрузка;
  transient retry снова проходит scanner и sandbox. Нет удаления истории/активного индекса.
- [Клиентские ограничения](knowledge-file-client.md), [jobs](ingestion-jobs.md), личные
  permissions/MFA, CSRF, RLS, limits, no-store download и secret redaction сохраняются.
  Regex-фильтр не заменяет DLP, ClamAV не гарантирует отсутствие неизвестных угроз,
  текст и найденные в нём инструкции остаются недоверенными данными.

## Миграция и включение

`0018_text_files` добавляет allowlisted CHECK для `knowledge_files.format`; новые таблицы,
grants, зависимости и роли не нужны. Generated OpenAPI/TypeScript перечисляют пять форматов.
Перед отдельно разрешённым обновлением остановить несовместимые старые API/worker, проверить
backup DB+originals и выполнить privileged migration по deployment runbook. Runtime не мигрирует.
После появления **любого** нового оригинала (включая queued/failed/cancelled) downgrade до
`0017_plan_adoption` отказывается с `text_file_history_requires_restore_plan`. Не удалять
документы ради обхода; нужен проверенный restore-backed план, не rolling rollback.

`SMM_KNOWLEDGE_FILES_ENABLED=false` и выключенный worker остаются default. Push не включает
ClamAV overlay и не развёртывает сервер. До реального использования нужны прежние
SSH/Tailscale/HTTPS/identity/two-machine gates, scan/signatures/sandbox/RAM/disk/backup drill
на целевой машине, разрешённый корпус и ручная проверка извлечений для каждого формата.
Ни synthetic score, ни эти parser tests не заменяют реальные retrieval/model evals.

## Проверки

Unit: UTF-8/BOM/raw hash, controls/binary mismatch, CSV quotes/width/limits/formulas,
HTML active content/entities/structure/resource limits, неизменность PDF/DOCX и codec preload
после запрета file opens. Linux CI дополнительно выполняет реальные seccomp/resource tests
и `scripts/parser_smoke.py` по всем пяти форматам внутри worker image.
PostgreSQL: очередь/скан до парсера, idempotency, приватность/RLS, исходные bytes/provenance,
отдельный exact import/activation, fail-closed retry и downgrade, общие REST/MCP DTO.
Клиент: hash/base64, aliases, stable identity, encoding errors, native file selection,
прежние cache/session/CSRF/conflict protections. Ручной Playwright smoke использует только
синтетический mock API; он не является malware verdict или проверкой рабочего сервера.

Команды: `pnpm check`, `uv run pytest -m 'not integration'`, `pnpm test`, `pnpm build:web`;
DB — `uv run pytest tests/database -m integration` с явно заданным `SMM_TEST_DATABASE_URL`
на disposable infrastructure. Серверные/Compose проверки — в Linux CI, не на сервере владельца.

Локально проверено: `pnpm check`, 433 Python unit-теста (два Linux-only skip), 135 PostgreSQL
тестов на одноразовых БД, 59 frontend tests, `pnpm build:web`. Существующее предупреждение
основного bundle 502,16 kB не изменилось; лимит не повышался. Playwright/Edge на 1280 и 390 px:
нативный выбор всех трёх новых форматов, проверка raw hash/base64 тестовым сервером,
receipt/список/preview, открытие текста клавиатурой, отсутствие исполнения script и горизонтальной
прокрутки. До fault injection консоль без ошибок; искусственный 403 скрывает приватные данные.
