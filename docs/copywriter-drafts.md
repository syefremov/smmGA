# Тестовый AI-копирайтер: предложения текста

Одиннадцатый репозиторный срез фазы 7. Система предлагает текст по точной сохранённой редакции,
брифу и подтверждённым SQL-фактам. Управление — из чата; просмотр также в существующей панели.
Предложение хранится отдельно от поста. Оно **не создаёт редакцию, не согласовывает и не публикует**.
Сервер, реальные данные, ключи и платные вызовы в этой итерации не подключались.

Двенадцатый срез добавил **отдельное человеческое** принятие точного предложения с provenance:
[`copywriter-adoption.md`](copywriter-adoption.md). Сама генерация по-прежнему не меняет пост.

## Порядок работы из чата

После отдельно разрешённого ввода серверной системы и платного тестирования:

1. `session_read`, `content_post_read`, `content_preflight`: показать сохранённый пост,
   его revision ID/hash, бриф, подтверждённые факты и ограничения. Рабочая копия не подходит.
2. Подготовить новую версию `copywriter` через registry; показать её целиком и получить
   отдельное решение о testing selection. Старые blocked definitions несовместимы.
3. Получить отдельное разрешение на расходы и передачу текстов провайдеру.
   Выбор профиля сам по себе этого не разрешает.
4. `ai_draft_revision`: workspace/brand/post/revision IDs, `content_hash`, `direction`,
   exact `profile_version_id` и `profile_selection_id`, `testing_only=true`, idempotency key.
5. Прочитать `ai_run_read` и `ai_run_inputs`. Очередь продолжает работать без открытого чата.
   Если исход неизвестен, читать прежний run, а не повторять запрос с новым ключом.
6. Обсудить текст, основания и пробелы. Нельзя автоматически переносить предложение в пост.
   Точный preview и подтверждённая новая редакция с provenance описаны в
   [`copywriter-adoption.md`](copywriter-adoption.md). Deterministic preflight, возможный Editor review и human approval —
   отдельные действия существующего контентного процесса. Согласование исходной редакции
   никогда не распространяется на предложенный текст.

Пример намерения: «Предложи более короткий вариант сохранённого поста по имеющимся фактам.
Ничего не сохраняй в пост и не публикуй». Первый платный вызов этим примером не разрешён.

REST: `POST /api/v1/workspaces/{workspace_id}/knowledge/copywriter-runs`.
MCP и REST вызывают один `AIService.start`. Список/чтение/входы/отмена — прежние `/runs`
и `ai_run_*`. Старый `ai_assess(profile=copywriter)` блокируется:
`copywriter_revision_request_required`. Свободный вопрос не заменяет типизированные входы.

## Входы и факты

`CopywritingContext` версии `copywriting-context-v1` содержит `direction` (1–500 символов)
и `source`: неизменённый контракт `EditorContext` со снимком SQL-редакции, брифа,
подтверждённых evidence records, brand profile, claim policy и исходного preflight.
Это повторное использование read-only snapshot builder, не результат AI-редактора.

- Точные workspace/brand/post/revision/hash, только текущая сохранённая редакция.
- Факты и их источники, продуктовая версия и правила — текущие подтверждённые SQL records
  с hash и сроками. Отсутствующая/просроченная зависимость блокирует вызов.
- Требуется хотя бы один подтверждённый продуктовый факт, связанный с исходной редакцией.
  Без него — `copywriter_confirmed_facts_required`, без обращения к модели.
- Только текст: media attachments/manifest блокируют вызов с `copywriter_text_only_required`.
  Нет генерации с нуля, изображений, media brief, новых destinations или поиска в интернете.
- До 100 SQL records, 250 исходных findings и 100 000 UTF-8 bytes всего контекста.
  При превышении — ошибка без обрезки. Целиком не передаются БД, история чата или credentials.
- Бриф и direction — намерения, не фактические основания. Исходный пост тоже может содержать
  ошибки. Текст источника/направления/правил не становится инструкцией или разрешением.
- RAG не используется. Внутренняя claim policy не является юридическим заключением.

## Выход и пределы проверки

`CopyDraft`: source revision ID/hash, hash всего контекста, `outcome`, варианты, warnings,
knowledge gaps. Все поля обязательны, произвольные дополнительные поля запрещены.

- `draft`: по одному тексту для каждого исходного варианта, до трёх, каждый до 3000 символов.
  Индексы уникальны и полностью соответствуют исходным; destination менять невозможно.
- Каждый вариант содержит 1–10 evidence: `fact_id`, `quote` из предложенного текста,
  `source_quote` из statement подтверждённого факта. Неизвестные IDs и отсутствующие цитаты
  отвергаются. Бриф, policy или source observation не могут выдаваться за product fact.
- `insufficient_evidence`: варианты пусты, пробелы непусты. Это явный отказ от неподкреплённой
  генерации, не ошибка очереди и не разрешение угадать сведения.
- До 10 warnings и 30 gaps. Все исходные knowledge gaps должны сохраниться дословно.
  Модель не может объявить их закрытыми; для исправления нужен отдельный человеческий workflow.

Сервер проверяет форму, IDs, exact bindings и вхождение обеих цитат. Это **не доказывает**
смысловую поддержку утверждения или полноту цитирования: модель может приписать факту
неверный смысл либо пропустить claim. Семантическая точность и соблюдение policy требуют
человеческой проверки. Preflight исходника не является preflight нового текста.
Пустые warnings/gaps не означают, что проверка пройдена. UI сообщает эти ограничения явно.

`copy-draft-v1` имеет только `content.snapshot.read`, `copy_draft.propose`.
Модели не выдаются tools, Principal, контентный сервис, registry/approval capabilities.
Post/PostRevision/ContentDecision/WorkingCopy/PublicationPackage не меняются.
Автоматическое принятие предложения не разрешено. Durable связь «AI artifact → новая ручная
редакция» реализована отдельным человеческим workflow двенадцатого среза; обычное ручное
копирование не создаёт эту provenance-запись.

## Очередь, актуальность, приватность и расходы

Используется существующая durable очередь с одной dispatch reservation до сети.
До вызова, перед сохранением и при чтении проверяются источники/редакция/профиль/selection.
Locks: knowledge → content; сеть без транзакционных locks. Старые reference/editor payloads
не изменены. Новая testing selection той же версии не оживляет старые задания.

Изменение текста/правил, истечение evidence или отзыв доступа блокируют queued либо
отбрасывают in-flight результат. При отзыве membership RLS скрывает run от обычного worker;
ограниченный reconciler фиксирует terminal status. Остановленный worker статусы не обновляет.
При чтении stale предложение скрывается с `artifact_copywriter_stale_or_unavailable`
или `artifact_profile_stale_or_unavailable`; immutable история остаётся в БД.
Общие `editor_*` ошибки snapshot означают проблему исходной SQL-редакции и в этом workflow.
Исторические inputs доступны после изменения текста, пока evidence/права ещё актуальны.

Owner + MFA, own-private runs, server provider/model/workspace allowlist, точная registry
selection и отдельное разрешение расходов обязательны. По умолчанию provider/worker выключены.
До 5 reservations на rolling 24 часа по умолчанию (включая blocked), 2000 output tokens,
45 секунд HTTP / 60 секунд worker. Выход может не уместиться: incomplete отвергается,
не обрезается и не повторяется автоматически. Это не денежный бюджет.
`cost_usd=null` означает неизвестную стоимость. Известные usage сохраняются при отмене/stale,
если gateway вернул валидные metadata; отказ/невалидный ответ может оставить usage неизвестным.
Отмена не гарантирует остановки вычислений провайдера или возврата денег.

## БД, интерфейс и обновление

Новая миграция `0014_copywriter` добавляет nullable `ai_inputs.copy_context`.
Старые editor/reference inputs не переписываются. CHECK допускает либо reference без content
IDs, либо exact post/revision с ровно одним типом контекста. Composite FK и новые insert/run
guards проверяют профиль, actor, brand, текущую редакцию и snapshot identity.
Прежние Editor guards сохраняются. Worker получает **никаких новых grants**, только прежний
SELECT под tenant RLS. Inputs/artifacts остаются private и append-only, входят в PostgreSQL backup.

`/app/knowledge` → «AI-профили» → свой run: текст, исходная редакция/hashes, раскрываемые
цитаты/факты, предупреждения/пробелы. HTML показывается как обычный текст. Нет кнопок запуска,
применения или одобрения. Detail перечитывается каждые 10 секунд в активной вкладке;
ошибка доступа скрывает предыдущий результат. Для решения всегда перечитать серверные данные.

Реальный upgrade требует отдельного разрешения, backup/restore rehearsal, остановки старых
API/worker writers и согласованного обновления кода. Текущий deployment guard ожидает более
новую `0016_planner`; её отдельный rollback guard описан в [Planner-контракте](planner-drafts.md).
Никаких автоматических selections, ключей, flags или deploy. Старому Copywriter нужны
новый draft и явный выбор. Downgrade при наличии copy inputs **отказывается до удаления полей**
(`copywriter_history_requires_restore_plan`); это не команда очистки истории. Forward-only
исправление или отдельно согласованный restore-backed план, не обход защитных triggers.
Перенос папки сотруднику не мигрирует сервер и не переносит туда общие секреты.

## Проверка и следующий этап

Синтетические unit/fake HTTP тесты: закрытая схема, цитаты и связи, сохранение пробелов,
refusal/incomplete/timeout, отсутствие tools. PostgreSQL: concurrent replay/claim, отмена,
stale/in-flight, неизвестный исход без retry, RLS/MFA/grants, guards/immutable inputs,
REST/MCP parity и downgrade, не уничтожающий provenance. UI: inert text, отсутствие действий,
мобильный/desktop просмотр и скрытие устаревшего результата.

Локально: 307 Python tests (2 Linux-only пропуска на Windows), 93 DB tests на disposable
PostgreSQL 15, 34 web tests; `pnpm check` и production build прошли. Playwright CLI / Edge:
390 px и 1440 px без горизонтального переполнения, раскрытие оснований с клавиатуры,
inert HTML, скрытие stale кандидата при polling, без browser console errors/warnings.
Синтетические QA-файлы — только в игнорируемом `output/playwright/`, не пользовательские данные.
Linux CI отдельно проверяет PostgreSQL 17, контейнерный stack/browser и synthetic server lifecycle.

Далее нужны реальные owner-approved evals Copywriter: выдуманные/нецитированные claims,
несоответствие цитаты смыслу, prompt injection, требования policy/тона, сохранение gaps,
правильное воздержание. Механизм принятия новой редакции с provenance добавлен двенадцатым срезом;
остаются полные Planner/Analyst/media workflows, финансовые и server/two-machine gates.
Этот срез не закрывает фазу 7 и не разрешает production activation.

Контракт опирается на [официальную документацию Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs):
закрытая JSON-схема проверяется отдельно от содержательной достоверности.
