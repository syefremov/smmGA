# Тестовый Content Planner: предложения тем

Тринадцатый репозиторный срез фазы 7, 2026-09-03. **Фаза 7 остаётся частичной.**
Планировщик предлагает темы для уже сохранённого каркаса плана. Он не создаёт план,
брифы, посты, рабочие задачи, approvals или расписание отправок. Чат — основной интерфейс;
существующая панель показывает личный результат. Реальные модели и сервер не подключались.

## Как пользоваться после отдельно разрешённого включения

1. Человек задаёт кампанию: цель, KPI, ответственный участник, начало/конец; сохраняет через
   обычные content-команды каркас `ContentPlan` с 1–5 будущими слотами (дата, VK destination,
   исходная тема). Это намерение, не расписание публикации. Модель не выбирает даты/аккаунт.
2. Выбрать 1–10 **точных подтверждённых** `ProductFact` IDs того же бренда. Подтверждение
   создаёт новую immutable версию: ID черновика не подходит. Нужны также текущие подтверждённые
   ProductVersion, SourceItem, BrandProfile и ClaimPolicy, действующие до последнего слота.
3. Прочитать `ai_profile_read` для `content_planner`, создать совместимую definition и отдельно
   выбрать testing version по [registry-контракту](ai-profile-registry.md). В команду передаются
   exact `profile_version_id` **и** `profile_selection_id`. Это не включает provider/расходы.
4. `ai_plan_content` получает plan ID/hash, brand ID, выбранные fact IDs, direction до 500 символов,
   исходные knowledge gaps (до 10 × 200), `testing_only=true` и стабильный idempotency key.
   Команда личная, Owner + MFA. До передачи данных провайдеру нужно отдельное разрешение на тест.
5. `ai_run_read` показывает queued/blocked, затем `PlanDraft`; `ai_run_inputs` — точный вход.
   Сравнить темы, основания, исходные ограничения и gaps. Цитаты и hashes не доказывают
   смысловую точность, полноту фактов или соблюдение политики.
6. Использовать тему можно только после отдельного человеческого решения и обычных content-команд.
   Автоматического принятия Planner proposal, provenance receipt принятия и генерации brief нет.
   Ручное копирование не создаёт доказательство AI adoption. Создание поста, preflight и approval
   точной редакции остаются отдельными этапами. Никогда не запускать их автоматически из результата.

Пример задания в чате: «По сохранённому плану предложи темы на основании выбранных подтверждённых
фактов. Покажи основания и пробелы. Пока ничего не меняй в плане и постах». Агент сначала читает
SQL-записи/registry и уточняет только недостающую внешнюю авторизацию, не выдумывает IDs или факты.
Повтор с тем же ключом возвращает прежний run; новый ключ после unknown — новый потенциальный
расход, не безопасный автоматический retry. Отмена — обычный `ai_run_cancel` по версии run.

## Вход и границы доказательств

`planning-context-v1` хранит immutable снимок плана/кампании и bounded SQL evidence closure:
выбранные факты → их ProductVersion/SourceItem; текущие подтверждённые profile/policy → SourceItem.
Другие факты каталога, RAG, тексты чатов, чужие бренды, URL-fetching, media и metrics не включаются.
Всего до 50 evidence records и 100 000 UTF-8 bytes контекста. Большой план/контекст отклоняется,
не обрезается молча. До 5 слотов; совпадающие время + destination запрещены.

План/кампания могут быть неподтверждёнными: это намерение человека, **не доказательства**.
Они должны быть последними версиями в своих семействах, включая черновики. Selected факты,
product versions, profile/policy/sources — последние **подтверждённые** версии. Новый черновик
факта не отменяет текущую подтверждённую версию. Hypothesis SourceItem не основание для claims.
Вся цепочка evidence и план/кампания должны быть действительны до последнего planned_at.
Ответственный кампании должен оставаться активным участником workspace.

Direction, goal/KPI, исходные темы, документы и политики — недоверенные данные внутри границ
задачи. Ни одно из них не даёт права исполнять инструкции, обещать неподтверждённые эффекты,
акции, цены, согласия или юридическую допустимость. Внутренняя policy не юридическое заключение.

## Выход и детерминированные проверки

Закрытая `PlanDraft` schema содержит plan ID/hash, context hash, outcome, slots, warnings/gaps.
При `draft` требуется ровно один результат на каждый исходный zero-based slot_index:

- exact planned_at/destination/campaign owner_id;
- тема до 200 и rationale до 500 символов;
- 1–3 основания: выбранный fact ID, точная цитата из темы/rationale, точная цитата из fact.statement;
- все исходные gaps без потери или молчаливого «разрешения»; всего до 20 gaps и 10 warnings.

`insufficient_evidence` требует пустых slots и непустых gaps. Нет результата «approved».
Невалидный output, лишние поля/tools, refusal и incomplete не превращаются в готовый план.
Server проверяет DTO, exact bindings, membership цитат и неизменность исходных gaps повторно.
Принадлежность цитаты источнику **не устанавливает** семантическое соответствие или полноту
claim coverage. Для коротких фактов можно непреднамеренно цитировать нерелевантный фрагмент:
до рабочих запусков нужны реальные profile evals и человеческая проверка.

OpenAI Docs использован для проверки закрытой Structured Outputs схемы: обязательные поля,
`additionalProperties=false`, отдельная обработка refusal/incomplete. Это транспортная гарантия
формы, не фактической точности. [Официальный контракт Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Существующий adapter: Responses text, без tools/history, store/background false,
2000 output tokens, bounded HTTP response/timeout; большой ответ может честно закончиться incomplete.
Строгого денежного бюджета/полного provider accounting этот срез не добавляет.

## Очередь, актуальность и личный доступ

Используется [общая очередь](ai-jobs.md): durable reservation до I/O, одна попытка, fencing,
отмена, reconciliation и immutable artifacts. Lock order knowledge → content; network вне locks.
Общие `AIService`/worker recheck входы до dispatch, после ответа и при чтении artifact.
Изменение плана/кампании, подтверждённых оснований/policy, отзыв ответственного, истечение
слота или несовместимая registry selection блокируют выдачу. Старый результат скрывается,
но AIInput/AIArtifact не удаляются. Для Planner даже historical `ai_run_inputs` требует актуальных
плана/кампании/evidence: после изменений доступна метаинформация запуска, не stale snapshot через API.
Снимок сохраняется в БД/backup; обходной historical download не добавлен.

Provider и worker по умолчанию disabled. Нужны прежние server key/model/workspace allowlist,
Owner/MFA и отдельная авторизация на платный тест. Run/input/artifact доступны только инициатору
при действующих правах; второй Owner не получает автоматически личные результаты первого.
Модель не получает Principal, content service или registry/adoption/approval tools.
Отмена in-flight отбрасывает результат, но не гарантирует остановку вычислений/списаний провайдера.

## MCP, REST и панель

- MCP `ai_plan_content`; POST `/api/v1/workspaces/{wid}/knowledge/planner-runs` — один сервис.
- Чтение/inputs/cancel — существующие run endpoints/tools; `AIRunView.plan_draft`,
  `AIInputView.planner_context`. Общая квота учитывает также blocked runs.
- `/app/knowledge` → «AI-профили» → личный запуск: readonly темы/обоснования, exact IDs/hashes,
  даты в явной workspace timezone, ответственный, цитаты, gaps и limitations.
- Компонент загружается отдельно; кнопок save/approve/schedule нет. HTML выводится текстом.
  При stale/ошибке/отзыве доступа предложение скрывается; cache остаётся workspace-scoped.
  Панель не запускает платные запросы и не дублирует серверные бизнес-правила.

## Миграция и обратная совместимость

`0016_planner` после `0015_copy_adoption`: nullable `ai_inputs.plan_id/planner_context`,
composite workspace/plan FK, взаимное исключение editor/copy/planner contexts и input/run guards.
DB guards связывают actor/brand/current plan/campaign, приложение — полную evidence closure.
Прежние context и payload contracts Editor/Copywriter/reference не изменены.
Новый profile contract `plan-draft-v1`: старые blocked Planner definitions несовместимы,
не получают selection автоматически. Новая definition/выбор нужны явно.

Worker получает только EXECUTE существующей boolean `smm_assignable_member(wid, uid)`:
проверка активного участника в текущем workspace при действующей membership инициатора.
Нет широкого SELECT сотрудников, INSERT/UPDATE контента, approvals или личных receipts.
Прежний private AIInput RLS/append-only остаётся. Новых зависимостей/flags нет.

Перед отдельно разрешённым deploy: backup + isolated restore rehearsal, остановить старые writers,
upgrade до `0016_planner`, согласованно обновить API/worker, проверить restricted grants и smoke.
При planner history downgrade **отказывается до изменений**, не удаляет provenance. Нужен
отдельный restore-backed план, не прямой schema rollback. Старые migrations не редактируются.
Копирование employee plugin не мигрирует сервер. В рамках этого среза remote deploy не выполнялся.

## Проверки и оставшаяся работа

Unit/HTTP fake tests проверяют closed schema, отсутствие tools, exact time/target/owner/evidence,
gaps, abstention, invalid/refusal/incomplete/timeout. Disposable PostgreSQL проверяет очередь,
параллельную идемпотентность, неизменяемость, права/изоляцию, stale intent/evidence/owner,
in-flight changes/cancel/profile drift, no-retry unknown и безопасный отказ downgrade.
Контрактный HTTP тест сравнивает реальные MCP/REST с одной БД. Web component и browser QA —
readonly/inert output, timezone, desktop/mobile, клавиатурные citations и скрытие stale/error.

Локальная проверка: `pnpm check`, 351 Python unit (2 Linux-only проверки остаются для CI),
39 web tests и production web build. Полный disposable DB-прогон: 118 passed; после добавления
трёх крайних случаев повторены все 20 Planner и 3 transport/operations теста — 23 passed.
Playwright Edge: 1440×1080 и 390×844, без горизонтального overflow/исполнения HTML,
keyboard evidence, stale и 403 скрывают приватный результат. Новые UI assets lazy-loaded;
прежнее предупреждение основного JS chunk >500 kB остаётся (500.78 kB), порог не повышался.

Это технические синтетические проверки, не оценка качества модели. Последующий срез добавил
[отдельное человеческое принятие с provenance](planner-adoption.md); права профиля не расширены.
Остаются реальные corpus/profile evals, генерация brief/ограничения
производства, полноценные остальные специалисты, hybrid/RAG и прежние server/identity/two-machine
gates. Полная дорожная карта — [фаза 7](phase-7-implementation.md), не переход к фазе 8.
