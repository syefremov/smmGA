# Человеческое сохранение предложения контент-плана

Дата: 2026-09-03. Четырнадцатый репозиторный срез фазы 7, **не закрытие фазы**.
Продолжает [testing Planner](planner-drafts.md). Только личный Owner с MFA может перенести
своё актуальное предложение в общую БД. AI-профиль не получает новых прав или инструментов.
Server/identity/two-machine gates, реальные corpus/profile evals и выключенные paid defaults сохраняются.

## Что получает человек

После проверки whole preview создаются вместе:

- новая неизменяемая версия `ContentPlan` со статусом неподтверждённой записи;
- общие `plan_notes`: темы, ответственный, обоснования, точные цитаты, выбранные fact IDs,
  IDs материалов контекста, все warnings и knowledge gaps;
- evidence links из нового плана к материалам контекста и штатная ссылка на кампанию;
- личная неизменяемая квитанция `plan_adoptions` и минимальное audit event.

Меняются **только темы** заданных слотов. Название, бренд, campaign ID, порядок, даты, назначения,
expiry и исходные версии сохраняются. Campaign owner закреплён в notes, а не назначен новой
операцией. Запись плана не создаёт брифы, посты, work items, расписание отправки или approval.
Новая версия всегда unconfirmed; прежние подтверждения не переносятся. Связанные старые briefs
автоматически не перепривязываются и не переписываются. Создание нового плана не одобряет посты.

## Управление из чата

1. Прочитать собственный `ai_run_read`. Нужен `needs_review`, профиль `content_planner`,
   outcome `draft`; `insufficient_evidence` не принимается.
2. Вызвать `ai_plan_adoption_preview(workspace_id, run_id)`. Показать **полный** proposed body
   и notes, даты/площадки/ответственного, цитаты, warnings, gaps, исходную версию и все hashes.
3. Получить отдельное явное подтверждение человека: сохранить этот точный черновик **и**
   раскрыть всё перечисленное содержимое notes читателям контента workspace. Просмотр, похвала,
   модельный текст и ранее выданное разрешение на генерацию не являются таким согласием.
4. `ai_plan_adopt` принимает `artifact_id/artifact_hash`, `preview_hash`, `proposed_content_hash`,
   `notes_hash`, `expected_plan_number`, короткую private reason, личный idempotency key и два
   поля `human_confirmed=true`, `share_with_workspace_confirmed=true`. Не редактировать preview
   или подменять hashes. API не принимает новый текст, изменение дат или сокращённые gaps.
5. Перечитать `content_record_read` нового `plan_id` и `content_plan_notes_read` перед дальнейшей
   работой. `ai_plan_adoption_read` возвращает только собственную историческую квитанцию.

При неизвестном результате повторить **те же поля и тот же ключ**. Совпавший replay возвращает
старую квитанцию; другой payload с тем же ключом — `idempotency_conflict`. Новый ключ для уже
принятого run — `plan_already_adopted`. Один run создаёт ровно один plan/notes/receipt.
Историческая квитанция доступна даже после изменения профиля, истечения источников или следующих
редакций; это не действующее approval. Отозванный доступ не позволяет читать/повторять операцию.

## Актуальность и конкуренция

Перед preview и начальной записью переиспользуются production Planner checks: актуальные SQL
plan/campaign, текущие confirmed facts/product/source/profile/policy, evidence до последнего
слота, активный ответственный, исходный payload hash и точная registry version/selection.
Порядок блокировок: knowledge → content. Сетевых запросов под locks нет, новых model calls нет.
Параллельное принятие разных предложений по старой версии не перебазируется автоматически:
после первой записи второе отклоняется. Старые inputs/artifact/plan сохраняются неизменно.

Атомарная транзакция включает новый ContentRecord, ContentLinks, notes, receipt и audit.
Ошибка на любом шаге откатывает всю операцию. DB insert guard проверяет связь с собственным
run/artifact/input и registry selection, исходную/новую версию, topics и **всё содержимое notes**.
Deferred constraint требует receipt к моменту commit: нельзя сохранить отдельные публичные notes
без соответствующего подтверждённого переноса. UPDATE/DELETE/TRUNCATE обеих таблиц запрещены.

## Shared notes и приватность

`plan_notes` доступны обычным активным читателям контента только в своём workspace. Они не
запрашивают private AI inputs или artifacts. Run/input IDs, direction, registry purpose, reason
и private AI history не включаются в DTO notes. Значения, которые модель могла повторить внутри
topic/rationale/quotes/warnings/gaps, человек должен заметить при **полном** preview: фильтрация
полей не является DLP и не доказывает отсутствие чувствительного текста в самой генерации.

`plan_adoptions` — только автору-run Owner с MFA; даже другой Owner того же workspace не читает
личную квитанцию. `FORCE RLS`, composite tenant FKs, runtime SELECT/INSERT и никаких worker grants.
Платный gateway не получает Principal, notes/receipt/content services или personal tools.

Notes закреплены за точным immutable `plan_id/hash`. При чтении последующей версии этой же
семьи сервер возвращает ближайшие notes предка с `requested_plan_id` и `exact_version=false`.
Панель явно пишет: история предыдущей версии, **не проверка нового текста**. Исходная версия до
принятия не получает notes будущей версии. Время, подтверждение или ручная правка не снимают gaps.
Свежесть процитированных источников при историческом чтении не гарантируется; перед новым
решением нужны актуальные SQL records. Цитаты/ID membership не доказывают семантическую истинность.

Перед повторным Planner run приложение требует все inherited gaps в новом запросе. Пропуск
блокирует run с `planner_inherited_gaps_required`; worker работает с уже сохранённым контекстом,
не получает доступа к notes. Contract запросов остаётся до 10 gaps, outputs/notes — до 20.
Если все ограничения не помещаются, **не сокращать и не создавать обходную семью**: нужен
отдельный будущий workflow человеческого разрешения gaps/версионирования контракта. В этом срезе
такого workflow нет. Notes не переносятся автоматически в brief/post и не являются SQL facts.

## REST и существующая панель

- GET `/api/v1/workspaces/{wid}/knowledge/runs/{rid}/plan-adoption/preview`;
- POST/GET `/api/v1/workspaces/{wid}/knowledge/runs/{rid}/plan-adoption`;
- GET `/api/v1/workspaces/{wid}/content/records/{pid}/plan-notes`;
- `AIRunView.plan_adoption` содержит historical receipt даже когда stale `plan_draft` скрыт.

REST и MCP вызывают одинаковые domain services. DTO/OpenAPI/TS генерируются вместе. Browser
использует личную same-origin session; POST под существующими CSRF/Origin guards. В панели нет
кнопки adopt: управление остаётся в чате. AI-раздел показывает private receipt и ссылку на
точный `/app/materials?workspace=…&record=…`. Материалы загружают immutable record и общие notes
отдельно; cache keys включают workspace/record. Ошибка повторного чтения скрывает прежний текст,
loading/empty/failure показаны явно. HTML только escaped text, без preview исполнения.
Дата отображается в явной workspace timezone; мобильный просмотр/клавиатурные citations доступны.

## Миграция, проверка и границы

`0017_plan_adoption` после `0016_planner`: две новые таблицы/политики/guards, без dependencies
и flags. `ContentPlan`, `RecordView` и прежние model payload contracts не изменялись: это важно
для canonical hashes исторических inputs. Deployment guard ожидает новый schema head.

Реальный deploy только по отдельному разрешению: backup и isolated restore rehearsal,
остановить старые writers, применить новую migration, согласованно обновить API/worker/web,
проверить restricted grants и smoke. Downgrade с notes **или** receipts отказывается до любых
удалений; требуется отдельно согласованный restore-backed план. Старый код не должен писать
поверх нового workflow и терять provenance/gaps. Копирование plugin не обновляет сервер.

Проверки: exact bindings/consent, atomic rollback, concurrency/replay, competing proposals,
workspace/actor isolation, worker denial, immutable guards, paired notes/receipt, stale
plan/policy/selection/expiry, inherited gaps и безопасный отказ downgrade. HTTP MCP/REST parity,
generated contract, read-only/inert UI, deep links, cache isolation и denied refetch.
Это синтетические технические тесты, не реальная модельная оценка. Полная фаза 7, hybrid RAG,
оставшиеся специалисты и production commissioning ещё не завершены. Сервер и реальные данные
не менялись; расходов на модели/публикаций нет.

Локально проверены `pnpm check`, 356 Python unit (2 Linux-only проверки — в CI), все 131
disposable PostgreSQL tests, 43 web component tests и `pnpm build:web`. Playwright Edge:
desktop 1440×1080 и mobile 390×844, переход к точной версии, keyboard citations, inert markup,
отсутствие horizontal overflow, скрытие notes после 403. Личные причины не появляются в
общих notes. Панель материалов расширена для чтения оснований; raw JSON доступен отдельно.
Новые компоненты lazy-loaded; прежнее предупреждение основного JS chunk >500 kB сохраняется
(502.16 kB, gzip 153.46 kB), порог не повышался.
