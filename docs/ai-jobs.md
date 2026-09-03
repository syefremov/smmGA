# Фоновые AI-задания: четвёртый срез фазы 7

2026-09-03. Реализована очередь существующих **тестовых reference assessments**,
а не готовые Planner/Copywriter/Editor или публикация. Реальные модели, ключи, данные
и сервер в этой итерации не подключались. Фаза 7 остаётся частичной.

**Обновление восьмого среза:** [`ai-profile-registry.md`](ai-profile-registry.md) добавляет
обязательную DB testing selection. В `ai_assess`/POST runs передаются exact `profile_version_id`
и `profile_selection_id` после чтения реестра; без них новый вызов не выполняется.
Смена/отключение selection блокирует queued и отбрасывает in-flight output при checkpoint.
Это не разрешение на расходы. Legacy runs не привязываются к новой версии автоматически.

## Для пользователя

После отдельно разрешённого включения провайдера и worker:

1. «Проведи тестовый разбор этих источников» → `ai_assess`: личный Owner + MFA,
   workspace/brand, question до 500 символов, profile, `testing_only=true`, idempotency key.
2. Чат сразу получает ID и `queued` либо `blocked`. Закрытие чата не отменяет принятую задачу.
3. `ai_run_read` показывает текущий статус; `ai_runs` — личный список. Это запрос состояния,
   не автоматическое уведомление и не необходимость держать клиент открытым.
4. `needs_review` — кандидат с источниками, гипотезами и пробелами. Это не факт и не approval.
5. `ai_run_inputs` показывает сохранённые входы только initiating Owner, пока все sources
   остаются разрешёнными и действующими. Другой Owner этого же workspace их не видит.
6. «Отмени задание» → `ai_run_cancel` с актуальным `version` и новым idempotency key.

Повтор сетевого запроса использует прежний ключ. Иной payload с тем же ключом — conflict.
Повтор прежнего start возвращает тот же run в его текущем состоянии и не вызывает модель снова.
Новый ключ — новый возможный расход; при unknown нельзя предлагать его как безопасный retry.
Оба testing профиля прежние: Product Expert и Research Scout. Остальные заблокированы.

## Статусы и отмена

| Состояние | Значение | Возможность отмены |
|---|---|---|
| queued | Сохранено, dispatch ещё не зарезервирован | cancelled, без вызова модели |
| running | Reservation зафиксирован до внешнего I/O | cancel_requested |
| cancel_requested | Результат нельзя использовать | Завершение → cancelled; неопределённость → unknown |
| needs_review | Валидный кандидат и актуальные источники | Терминальное, только review существующими процессами |
| blocked / failed | Не выполнено либо не принято, безопасный код причины | Терминальное, автоматического retry нет |
| cancelled | Очередь остановлена либо результат отброшен | Терминальное; не означает возврат средств |
| unknown | После dispatch нельзя надёжно установить исход | Только ручная сверка, не повтор |

Cancel receipt — исторический результат команды с version, не обещание текущего состояния:
после него прочитайте run. Stale version возвращает conflict. Повтор той же cancel-команды
возвращает сохранённый receipt, даже если worker уже завершился.

Здесь **наша серверная очередь**, не OpenAI Background mode. Gateway сохраняет foreground
Responses с `background=false`, `store=false`. По [OpenAI cancel API](https://developers.openai.com/api/reference/python/resources/responses/methods/cancel)
удалённая отмена применима к background responses. Поэтому локальная отмена после отправки
запрещает сохранение кандидата, но не обещает остановку вычисления/списания у провайдера.
Режим хранения и polling у провайдера не включаются автоматически; см.
[Background mode](https://developers.openai.com/api/docs/guides/background).

## Серверный контракт

REST prefix `/api/v1/workspaces/{wid}/knowledge`:

- POST `/runs` — тот же RunAssessment DTO, **теперь enqueue**, не синхронная генерация;
- GET `/runs`, `/runs/{rid}` — текущее состояние, version, timestamps и usage;
- GET `/runs/{rid}/inputs` — private immutable input;
- POST `/runs/{rid}/cancel` — expected_version и idempotency_key.

MCP вызывает тот же AIService; API-процесс не вызывает модель и не создаёт AIArtifact.
Существующий web-экран отображает run/state; создание, отмена и просмотр полного input
в этом срезе доступны через чат/API. Новых web-кнопок и автоматического polling UI нет.

`ai_runs` — единственный источник состояния: identity_id, version, lease/token,
created/started/finished_at. Input и cancel receipts отдельные append-only таблицы.
До enqueue резервируется workspace quota под lock: по умолчанию 5 запросов за rolling 24 часа,
настройка 1–100. Blocked/failed/cancelled также расходуют quota. Это не денежный бюджет.

Input содержит question, до 5 citations, semantic JSON request body и canonical hash.
HTTP authorization headers, ключи, hidden reasoning и история чата не сохраняются.
Profile snapshot хранится в run; retrieval trace остаётся минимальным (query hash и chunk IDs).
Этот input artifact не индексируется как постоянная память/RAG-источник. Действующие политики
backup/доступа должны учитывать, что теперь БД хранит текст вопроса и ограниченный контекст.
Full DLP/retention automation пока отсутствуют; regex-проверка секретов не заменяет их.

## Исполнение и восстановление

Beat каждые 30 секунд отправляет только wake-up `smm_gpt.ai.poll`, без текстов и credentials.
Restricted worker сначала вызывает bounded reconciler, затем читает максимум 5 pending IDs.
Каждый run проверяется в транзакции: текущие user/membership/исходная identity, роль Owner,
TTL очереди 24 часа, sources/lifecycle/visibility/hash, неизменённый profile contract,
точные provider/model/allowlist и совпадение payload с текущим gateway.
Дополнительно проверяются выбранная DB version, selection ID и execution hash; новый draft
не меняет selection. Старый artifact после переключения сохраняется, но скрывается как stale.

Row lock + workspace lock + version переводят **только queued** в running.
Lease 120 секунд и `attempts=1` коммитятся **до** внешнего вызова. Сбой между commit и запросом
может потерять выполнение, но не становится основанием для повторного списания.
Это at-most-one dispatch reservation, не обещание exactly-once исполнения у провайдера.
Worker не забирает running повторно, даже если lease истёк.

HTTP timeout 45 секунд, общий deadline вызова 60 секунд, ответ до 200 KB, output до 2000 tokens.
Tools/произвольный network loop/content commands отсутствуют. Все источники, права и
контракт повторно проверяются перед сохранением. Поздний ответ с истёкшим lease или terminal
state отбрасывается. Изменённый/архивированный источник не создаёт пригодного кандидата.
Запрет/отзыв доступа после отправки не может отменить уже переданные провайдеру данные.

`smm_ai_reconcile()` — SECURITY DEFINER с fixed search_path, только worker EXECUTE:

- максимум 10 записей за вызов, FOR UPDATE SKIP LOCKED;
- просроченные queued или отозванная identity/Owner → blocked;
- running/cancel_requested с истёкшим lease → unknown;
- старые legacy running без lease старше 2 минут → unknown;
- terminal state и audit записываются вместе; функция не возвращает тексты и не вызывает модель.

Reconciler работает при включённом AI worker. При остановленном worker статусы могут оставаться
queued/running; восстановить сам worker, а не менять SQL-state/lease вручную. Нет retry/reset API.
Unknown не разрешается автоматически через provider API: для полной сверки и финансового
учёта потребуется отдельный workflow. До него сохраняйте историю и сверяйте журналы/счета вручную.

Usage: durable attempt count, provider/model, token counts/response ID при валидном ответе,
`cost_usd=null`. Отброшенный после cancel/stale ответ может быть платным; известные tokens
сохраняются. Если worker умер или ответ не прошёл gateway validation, usage может быть
неполным. Ноль вместо неизвестной стоимости не подставляется. Invoice import и price versions
в этом срезе не реализованы.

## Настройка и миграция

`SMM_AI_WORKER_ENABLED=false` по умолчанию; Compose передаёт flag только worker.
Для отдельного разрешённого rollout нужны согласованные API/worker settings:
provider, model, workspace allowlist и server-only key. Полный provider environment не
добавляется в общий Compose anchor и не раздаётся scheduler/сотрудникам. `server.env` server
manager по-прежнему принимает только DB keys: rollout оформляется отдельной защищённой
service configuration после private HTTPS/authentik commissioning.

Feature flag не отменяет ранее отправленный запрос. Выключение worker останавливает новые
poll и reconciliation. Уже начатый вызов может завершиться. Пользовательскую отмену проверять
через команды/состояние; аварийное завершение процесса заканчивается unknown после lease.

`0008_ai_queue` не меняет опубликованные 0001–0007. Новые inputs/receipts FORCE RLS,
app INSERT run/input и узкая отмена; worker не INSERT run, не меняет inputs и не получает
content/approval/knowledge activation. INSERT artifact только worker, с видимым parent run;
needs_review требует уже сохранённого artifact. Terminal history не изменяется/не удаляется.

Перед разрешённой реальной migration: backup/restore rehearsal, проверка копии, остановка
старых API/worker writers. Старый синхронный код несовместим с новыми grants и transitions.
Не использовать rolling old/new writer deployment. Downgrade удаляет private inputs/receipts
и queue metadata, потому только disposable tests либо отдельный restore-backed rollback.
Этот commit не является разрешением на deploy или первую платную операцию.

## Проверки

Fake provider tests без paid calls; реальные disposable PostgreSQL tests покрывают
миграции/metadata drift, private RLS, queue idempotency/concurrent claims, cancel до/во время
dispatch, конфликты versions, immutable inputs/history, source/config/identity changes,
late-result fencing, legacy unknown reconciliation, quota, API/MCP parity и token accounting
отброшенного кандидата. Общий Linux CI также проверяет контейнеры, browser regression и server lifecycle.
