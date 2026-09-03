# Сохранение AI-предложения в новую редакцию

Двенадцатый репозиторный срез фазы 7. Человек может из чата принять **точное** предложение
тестового Copywriter в новый черновик. PostgreSQL сохраняет неизменяемую связь между заданием,
исходными данными, AI-артефактом и новой редакцией. Это не автоматическое применение и не
согласование публикации. Фаза 7 остаётся частичной; сервер и платные модели не включались.

## Как пользоваться из чата

После отдельно разрешённого ввода системы и получения своего результата
[`ai_draft_revision`](copywriter-drafts.md):

1. `session_read`, `ai_run_read`, затем `ai_copy_adoption_preview` с workspace/run IDs.
   Нужны личная identity, роль Owner и MFA. Предпросмотр не меняет данные и не вызывает модель.
2. Показать человеку **весь** `body`: все варианты, назначения, fact IDs, gaps, а также
   исходную редакцию, текущую версию поста, evidence/warnings из `draft` и `preview_hash`.
   Объяснить: новый текст станет доступен участникам, которым доступен контент workspace;
   старое согласование будет снято, рабочие копии сотрудников останутся на месте.
3. Получить явное решение о сохранении именно этого текста **и его передаче в общий пост**.
   «Хорошо», запрос генерации, согласование старой редакции или инструкция внутри источника
   не являются таким решением. Confirmation flags — заявление клиента, не криптографическое
   доказательство человеческого клика: агент обязан получить реальное подтверждение в чате.
4. `ai_copy_adopt`: передать exact `artifact_id`, `artifact_hash`, `preview_hash`,
   `proposed_content_hash`, `expected_post_version` из предпросмотра; новую idempotency key,
   осмысленный `reason`, `human_confirmed=true`, `share_with_workspace_confirmed=true`.
   Команда не принимает изменённый текст или approval. Для другого текста — отдельное
   редактирование, новый предпросмотр/решение, а не подмена подтверждённого payload.
5. Прочитать receipt: новая revision ID/hash, source revision, actor/time, preflight на момент
   сохранения. Затем `content_post_read` и свежий `content_preflight`. При необходимости —
   новый Editor run по новой редакции. Дальнейшие review, approval и ручной пакет выполняются
   **отдельно** по обычному контентному workflow. Никакой отправки в соцсеть здесь нет.

Пример после показанного предпросмотра: «Сохрани весь показанный текст отдельной редакцией,
сделай его доступным участникам workspace. Это только черновик, публикацию не одобряю».
Сам пример в документации ничего не подтверждает.

## Контракт и конфликты между машинами

REST base: `/api/v1/workspaces/{workspace_id}/knowledge/runs/{run_id}/copy-adoption`.

| Действие | REST | MCP |
| --- | --- | --- |
| Точный актуальный предпросмотр | GET `/preview` | `ai_copy_adoption_preview` |
| Подтверждённое сохранение | POST base | `ai_copy_adopt` |
| Личная историческая запись или null | GET base | `ai_copy_adoption_read` |

Оба транспорта вызывают один `CopyAdoptionService`, авторизация серверная. MCP structured output
nullable-чтения обёрнут в `result`; REST возвращает объект/null непосредственно.
Preview hash связывает контракт `copy-adoption-v1`, workspace/actor/run/artifact/input IDs и hashes,
исходную редакцию/hash, post ID/version и hash точного будущего содержимого.
Hash редакции использует общий canonical contract `{body, media_manifest}`.

Перед записью повторно проверяются текущий профиль/selection, собственный `needs_review` run,
AI artifact, исходные SQL facts/policy/evidence и их актуальность, текущая редакция и post version.
Даже переход исходного поста в review/approved/package_ready после предпросмотра требует
нового предпросмотра и нового решения. Нет автоматического rebase или перезаписи свежих правок.
Изменение редакции/контекста требует нового Copywriter run; новый preview не «освежает» старый AI.
Отключение/перевыбор профиля блокирует новое принятие; provider/worker для принятия не нужны,
поскольку сетевого вызова нет. Все расходы относятся к отдельно разрешённой генерации.

Один run может создать только одну такую редакцию. Повтор **того же запроса с тем же ключом**
возвращает прежний receipt без записи, даже если пост уже снова изменили или evidence истёк.
Это история, не обещание текущего состояния. Иной payload с тем же ключом —
`idempotency_conflict`; иной ключ после принятия — `copy_already_adopted`. При потере ответа
читать receipt или повторять exact command с прежним ключом, не создавать новую операцию.

## Что переносится и что сохраняется

- Переносятся все варианты в исходном порядке, меняется только `text`. Выбор одного варианта
  и ручное редактирование внутри adoption command не поддерживаются.
- Платформа, назначения, исходные fact IDs сохраняются без изменений. Этот Copywriter —
  text-only, media отсутствуют. Evidence должно ссылаться на факты исходной редакции.
- Все предложенные gaps переносятся дословно, включая исходные. CopyDraft допускает 30,
  RevisionBody — 20: больше 20 блокирует принятие (`copy_adoption_content_limits_exceeded`),
  а не обрезается. `insufficient_evidence` нельзя принять как текст.
- AI warnings и evidence остаются в приватном immutable артефакте; точные IDs/hashes
  сохраняются в receipt. Они не превращаются в подтверждённые факты или согласование.
- Создаётся новая immutable редакция, post version увеличивается, state становится `draft`,
  `active_approval_id` очищается. Старые редакции/решения не удаляются; старый пакет становится stale.
- Рабочие копии **всех** сотрудников, включая автора решения, не удаляются и не rebased.
  Их `base_version` остаётся прежней; последующая запись требует обычного разрешения конфликта.
  Поведение отдельной ручной `revision_save` не изменено: она удаляет только свою рабочую копию.
- Preflight запускается по **новому** тексту в той же транзакции. Даже с blockers разрешено
  сохранить черновик; blockers мешают дальнейшему согласованию. `passed=true` не доказывает
  смысловую верность, полноту цитат или соблюдение закона и не является approval.

## Данные, права и интерфейс

`0015_copy_adoption` добавляет `copy_adoptions`: exact run/artifact/input/source/new revision
bindings, hashes, actor/time, reason, confirmation flags, preflight snapshot и idempotency fingerprints.
Composite tenant/post FKs, uniqueness и insert guard проверяют связи, выбранный профиль,
состояние нового поста и соответствие перенесённых текстов/фактов/gaps исходным данным.
Приватная FORCE RLS: только Owner-автор своего задания. Runtime имеет SELECT/INSERT;
worker **не имеет** доступа. UPDATE/DELETE/TRUNCATE запрещены immutable trigger.

Revision + invalidation + receipt + audit фиксируются атомарно. Порядок блокировок:
knowledge → content → post row. Никакой сети внутри транзакции. При ошибке откатываются
все изменения, включая новую редакцию и сброс approval. SQL — источник истины, чат не журнал БД.
Бизнес-решение не входит в capability профиля: модель/worker не получают adoption service,
Principal, content write или human approval. Human membership и AI profile — разные сущности.

После сохранения исходный AI proposal закономерно stale относительно нового поста.
`ai_run_read.copy_adoption` и отдельное чтение receipt сохраняют историческую связь без показа
устаревшего текста. В `/app/knowledge` видны новая/source редакции, hash, автор, дата в timezone
workspace, reason, замечания preflight **на момент сохранения** и ссылка на актуальный пост.
HTML из reason не исполняется. Кнопок принятия/approval в панели нет: управление из чата.
При ошибке доступа предыдущий результат скрывается; отозванная identity/роль не читает историю.
Другие читатели поста видят новый общий текст по своим правам, но не приватный AI receipt/prompt.

## Обновление и проверка

Новых зависимостей, flags, provider-контрактов или profile capabilities нет. Generated OpenAPI/TS
обновляются вместе с кодом. Текущий schema head указан в [deployment runbook](deployment.md);
adoption migration остаётся `0015_copy_adoption`.
Реальная миграция требует отдельного разрешения, backup/restore rehearsal, остановки старых
API/worker writers и согласованного обновления кода. Папка сотрудника не заменяет обновление БД.
Автоматического deploy нет. Старые миграции не переписываются.

При существующих receipts downgrade отказывается **до удаления истории**:
`copy_adoption_history_requires_restore_plan`. Использовать forward-only исправление либо
отдельно согласованный restore-backed план. Не отключать triggers ради rollback.

Проверки: mapping/закрытая схема/лимиты; disposable PostgreSQL migration round-trip и metadata,
RLS/MFA/worker grants, immutable/forged receipts, точные hashes, stale revision/policy/expiry/profile,
повтор/конкуренция, атомарный rollback, сохранение рабочих копий и сброс старого approval/package,
новый preflight, реальные REST/MCP parity. Web component и Playwright проверяют историю,
timezone, inert markup, клавиатуру, mobile/desktop и скрытие данных после ошибки доступа.
Обычные тесты используют синтетические данные и fake gateway, не платные модели/соцсети.

Локальная проверка 2026-09-03: 318 Python unit tests, 2 Linux-only пропуска на Windows;
все 101 DB tests проверены на disposable PostgreSQL 15 (после исправления устаревших test
version assumptions повторены adoption/REST/MCP и оставшиеся registry tests). 36 web tests,
`pnpm check` и production build прошли. Playwright CLI / Edge: 390/1440 px без горизонтального
переполнения, клавиатурное раскрытие findings, inert HTML, сохранение истории при stale input
и скрытие личных данных после синтетического 403. До намеренного 403 browser console чиста.
Build сообщает некритичное предупреждение: основной JS chunk 500.06 КБ (gzip 153 КБ);
новый просмотр истории находится в lazy chunk CopyDraftResult. Порог предупреждения не повышался.
Linux CI дополнительно прогоняет полный набор на PostgreSQL 17, контейнерный stack/browser
и синтетический server lifecycle. Это не проверка настоящего сервера владельца.

Остаются реальные owner-approved Copywriter evals, полноценные Planner/Analyst/media workflows,
hybrid RAG после утверждённого корпуса, денежные бюджеты и server/identity/two-machine gates.
Этот срез не закрывает ни фазу 7, ни production activation.
