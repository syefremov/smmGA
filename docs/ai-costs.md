# AI: резерв бюджета и история оценки расходов

2026-09-03, шестнадцатый репозиторный срез фазы 7. **Фаза не завершена.**
Добавлен консервативный денежный контроль существующей testing AI-очереди; это не биллинг
провайдера, не production rollout и не разрешение на платные вызовы. Реальных обращений к модели,
ключей, оплат и изменений сервера в этой итерации нет.

## Для пользователя

Через чат: «Покажи резерв и оценку расходов AI в workspace» → `ai_cost_summary`.
«Покажи финансовую историю этого запуска» → `ai_run_cost` с точным `run_id`.
Обе команды требуют личного Owner + MFA. REST использует тот же сервис:

- `GET /api/v1/workspaces/{workspace_id}/knowledge/costs` — итог workspace;
- `GET /api/v1/workspaces/{workspace_id}/knowledge/runs/{run_id}/cost` — личная квитанция.

Новые API типы сгенерированы в OpenAPI/TypeScript. Отдельного экрана расходов и кнопок оплаты,
возврата, смены тарифа или разблокировки нет. Старые `ai_run_read.usage.cost_usd=null` и
`provider_invoice_required` не заменяются фиктивной точной суммой: расчёт читается отдельно.
Отсутствующая observation — отсутствие достоверной записи usage, **не нулевая стоимость**.

Все суммы — целые **микродоллары USD**, 1 USD = 1 000 000 микродолларов. Нет float/конвертации
валют или скрытого округления до центов. Включение модели и проведение конкретного платного теста
по-прежнему требуют отдельного явного разрешения владельца, profile testing selection,
server provider/model/workspace allowlist и worker flag.

## Резерв и расход — разные величины

1. При enqueue после проверки источников/профиля сервер под workspace lock проверяет тариф,
   общий бюджет и старые неизвестные расходы. Вместе с run/input сохраняет immutable reservation:
   точные actor/run/input hash, полный снимок policy/hash и сумму резерва.
2. Резерв одинаков для одного запуска по текущей policy. Общий **lifetime** лимит workspace
   включает все сохранённые резервы всех его Owner, без сброса в полночь, при смене модели,
   версии policy, identity, бренда или машины. Старый rolling 24h count quota остаётся отдельно.
3. Повтор с тем же idempotency key не резервирует второй раз. Недостаточный бюджет/нет policy →
   `blocked`, без input/reservation/model call. Blocked run всё ещё расходует прежнюю count quota.
4. Перед dispatch worker требует exact policy hash и input hash, свежую policy, отсутствие
   неизвестного расхода/overrun. Без reservation старые queued runs не вызываются автоматически.
   При другом running/cancel_requested run в workspace очередь ждёт; второй вызов не запускается.
5. Полученный валидный usage сохраняется в отдельной immutable observation с lease ID,
   provider model/response ID, input/output counts и расчётом по **сохранённому**, не новому тарифу.
   Результат может быть отброшен из-за отмены/смены источника, но известный usage сохраняется,
   пока личный доступ и lease ещё позволяют финализацию. Late/revoked output не обходит fencing.
6. Если итог неизвестен, модель не соответствует тарифу, usage некорректен или estimate больше
   резерва, новые запросы блокируются до сверки. Нельзя обойти это новым ключом, другой личной
   учёткой, сменой версии policy или повышением лимита. Запущенный call никогда не повторяется.

**Резерв не освобождается автоматически**, даже после отмены queued job, меньшего фактического
usage или блокировки перед dispatch. Это намеренно консервативный первый контур: отрицательных
проводок, автоматических refunds и переиспользования экономии нет. `available_microusd` — только
арифметическая разница лимита и резервов, не подтверждение возможности/разрешения нового вызова.
`estimated_microusd` — сумма известных расчётов; при unresolved это неполный итог, не общий счёт.

Сводка показывает `unresolved_runs`, `overrun_runs`, `in_flight_runs`. Legacy уже вызванные runs
без новой observation также требуют сверки. Даже если старый run имеет успешный artifact,
его историческая стоимость не считается автоматически нулевой.

## Политика тарифа

Новая server-only настройка `SMM_AI_COST_POLICY` по умолчанию `null`. Она не передаётся через
команду модели/браузерный запрос и не включается установкой plugin. API и worker получают
одинаковую защищённую конфигурацию. Зависимости и model selection не менялись.

Поля объекта:

| Поле | Значение |
| --- | --- |
| `version`, `model` | Явная версия политики и точное имя модели; свободный текст/пути запрещены. |
| `currency` | Только `USD`. |
| `input_rate_microusd_per_million`, `output_rate_microusd_per_million` | Положительные целые ставки, до 1 000 000 000 микродолларов за миллион токенов. |
| `reserve_microusd` | Положительный резерв одного запуска, не больше 1 000 000 000 и workspace limit. |
| `workspace_limit_microusd` | Положительный lifetime потолок резервирования, до 1 000 000 000 000. |
| `valid_until` | Обязательная timezone, хранение/сравнение в UTC. Истёкшая policy не допускает dispatch. |

Реального тарифа по умолчанию нет. Перед отдельно разрешённой настройкой владелец проверяет
действующие условия выбранной модели и фиксирует консервативные ставки, срок и достаточный резерв.
У OpenAI ставки отличаются для input/cache/cache writes/output и режимов контекста/обслуживания:
[официальные цены](https://developers.openai.com/api/docs/pricing). Не копировать synthetic fixtures
в production и не считать pricing version ссылкой на автоматически обновляемую цену.

Расчёт: `ceil((input_tokens × input_rate + output_tokens × output_rate) / 1_000_000)`.
Принимаются только целые неотрицательные counts ≤ 1 млрд. Input считается по полной заданной
ставке без автоматической cache-скидки; ставка должна учитывать применимые надбавки. Output уже
содержит reported reasoning usage, он не прибавляется повторно. Основа — поля
[Responses usage](https://developers.openai.com/api/reference/python/resources/responses/methods/create).
Детализация тарифов, фактические invoice credits/taxes/discounts и сверка provider billing сюда
не входят. Fixed reserve — внутренний предел допуска, **не гарантированный потолок внешнего
счёта**: неверная ставка/недостаточный резерв обнаруживаются только после вызова. Нужны также
внешние лимиты/мониторинг аккаунта провайдера и проверенный commissioning.

Оценка сохраняется только если gateway вернул пригодные метаданные. Например, после timeout,
обрыва, refusal/malformed response или потери процесса usage может быть неизвестен, хотя вызов
уже оплачен. Нет запроса восстановления по provider response ID, retry или предположения о refund.

## Права, БД и миграция

`0019_ai_costs`: append-only `ai_cost_reservations` и `ai_cost_observations`, tenant FKs,
unique workspace/run, amount checks, immutable triggers. RLS — initiating actor + Owner.
Runtime INSERT только reservations; worker INSERT только observations, без новых content writes.
DB guards требуют matching queued input для reservation и matching live lease для observation;
dispatch без reservation запрещён. SQL перепроверяет расчёт по снимку тарифа.

Единственное расширение видимости — SECURITY DEFINER `smm_ai_cost_totals(workspace_id)`:
fixed search_path, exact current workspace + Owner, разрешены только пять агрегатов. Она нужна,
чтобы два разных Owner не обошли общий лимит. Чужие run IDs, тексты, response IDs, actor IDs и
детальные receipts через неё не раскрываются. Историческая личная квитанция доступна без
повторного раскрытия контента/источников; не даёт доступа к закрытому AI artifact.

Перед отдельно разрешённым upgrade: DB backup/isolated restore rehearsal, остановить старые
API/worker writers, применить migration privileged ролью, согласованно обновить код и конфигурацию.
Пустая схема обратима. Downgrade с любым финансовым ledger отказывается
`ai_cost_history_requires_restore_plan` **до удаления данных**; этот guard может сработать раньше
старого feature-specific rollback guard. Не удалять историю/manifest ради обхода.

## Проверки и незакрытые gates

Unit: integer rounding, malformed counts, currency/timezone/limits, disabled/missing/expired policy.
PostgreSQL: shared budget concurrency, private RLS/MFA, bounded aggregate, неизменяемость,
no-refund cancel, idempotency, exact config fencing, in-flight serialization, unknown/overrun/model
mismatch stop, snapshot pricing после отмены/смены policy, rollback guard, REST/MCP parity.
Прямой SQL отвергает подмену суммы/lease и dispatch без reservation. Отзыв identity во время
вызова не разрешает финализацию observation; резерв остаётся unresolved, без повторного dispatch.
Регрессии старой очереди/profile/copywriter/planner остаются обязательными.

Команды: `pnpm check`, `pnpm test`, `pnpm build:web`; database tests —
`uv run pytest tests/database -m integration` с явно указанным `SMM_TEST_DATABASE_URL` на disposable
инфраструктуре. Нормальные тесты не обращаются к провайдеру.

Локальная проверка 2026-09-03: `pnpm check`, 452 Python + 59 web tests, `pnpm build:web`.
143 PostgreSQL cases проверены последовательными прогонами на disposable PostgreSQL 15:
после обновления старой transport fixture повторены последние 45 тестов, после финальной
проверки identity — все 17 cost/queue тестов. Два Linux-only unit checks пропущены на Windows;
они исполняются в Linux CI. Прежний warning основного JS bundle 502.16 kB не изменился.

**До production ещё нужны:** явный human invoice reconciliation/immutable adjustments и
разблокировка после сверки (сейчас таких команд нет), получение usage при отвергнутых ответах,
полная детализация тарифов/проверка верхней оценки, внешние spending controls и реальные authorized
provider/corpus/server/identity/two-machine gates. При неизвестном расходе текущий контур остаётся
закрытым; менять SQL вручную, удалять ledger или повторять run для разблокировки нельзя.
