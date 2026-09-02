# Проверка качества поиска — второй срез фазы 7

Дата: 2026-09-03. Реализован **воспроизводимый FTS benchmark**, а не весь RAG.
Контур доступен через чат/MCP, REST и read-only web. Платные провайдеры не вызываются.
Реальный GreenAurum corpus/eval ещё не загружен и не утверждён: тестовые fixtures не заменяют его.

## Что теперь можно сделать из чата

1. Выбрать workspace и бренд; найти точные document IDs через `knowledge_documents`.
2. Подготовить набор реальных вопросов с ожидаемыми и запрещёнными документами.
3. Сохранить `dataset_submit` через `knowledge_eval_execute`. Результат: immutable dataset ID/hash.
4. Запустить `evaluation_run` с этим ID/hash и новым idempotency key.
5. Прочитать `knowledge_eval_run_read`: все вопросы, пропуски, лишние источники, метрики,
   версии корпуса, blockers. Посмотреть отчёт также можно в «База знаний → Качество поиска».
6. После **явного человеческого рассмотрения** записать `evaluation_review` с exact run ID,
   report hash, решением, причиной и `human_confirmed=true`.

Примеры обычных запросов в подключённом чате:

```text
Подготовь тестовый набор поиска для бренда из наших подтверждённых документов.
Включи точные названия, перефразированные вопросы, отсутствие ответа, устаревшие
источники, конфликтующие документы и проверку закрытых материалов. Покажи ожидания.

Запусти сохранённый набор. Покажи каждый непройденный вопрос и пропущенные документы.

Покажи точные версии отчёта и корпуса перед моим решением. Ничего не активируй.
```

Сам факт просьбы создать/запустить набор не является подтверждением его качества.
Постоянное разрешение commit/push тоже не является разрешением на утверждение рабочего корпуса.

## Контракт набора

`definition`: title, origin (`synthetic` / `owner_curated`), limit, thresholds, cases.
До 25 вопросов по 500 символов; top-k 1–10 **fragments**, по умолчанию 5.
В каждом case:

- уникальный `key`, `query`, `category`;
- `audience=workspace` ограничивает SQL только общими документами;
- `audience=owner` использует текущую разрешённую владельцу область, включая owner-only;
- `expected_document_ids` — до 10 ожидаемых документов;
- `forbidden_document_ids` — до 10 документов, которые не должны попасть в результат.

UUID должны принадлежать тому же workspace/brand. Можно указать archived/unactivated документ
как отрицательный пример; чужие или неизвестные IDs отвергаются без раскрытия метаданных.
Повторы ключей и пары normalized query/audience запрещены; expected/forbidden не пересекаются.
Для `no_answer` ожидание пустое; для exact/paraphrase нужен источник; conflict требует минимум два.
Категория — **метка теста человека**, не утверждение, что программа понимает смысл конфликта.

Новая редакция передаёт `previous_dataset_id` последней версии. Старые записи не редактируются.
Конкурентное создание от уже заменённой версии получает `dataset_conflict`.
Пороги являются частью неизменяемой версии и hash; их нельзя снизить задним числом.

## Метрики и условия подтверждения

Для вопроса сравниваются множества **уникальных document IDs** в top-k fragments:

- precision = найденные ожидаемые / все найденные;
- recall = найденные ожидаемые / все ожидаемые;
- пустое ожидание + пустой результат дают 1; пустое ожидание + найденный ответ — 0;
- citation validity проверяет workspace/audience, exact version/index и существование
  chunk ID с совпадающим content hash; это не проверка смысловой поддержки утверждения;
- negative pass требует пустого результата при пустом ожидании;
- forbidden pass требует отсутствия запрещённых IDs.

Defaults для **каждого** вопроса: precision ≥ 0.8, recall = 1, latency ≤ 1000 мс,
все ссылки корректны, все negative/forbidden проверки успешны. Настройка precision/recall
в диапазоне 0.8–1; latency 1–2000 мс. Средние macro-метрики показаны для обзора, но не скрывают
непройденный вопрос. Latency измеряет ranked SQL retrieval, не network/UI/полный ответ модели;
duration всего прогона включает проверку ссылок и корпуса. Нагрузочный тест это не заменяет.

`accept_baseline` дополнительно требует:

- owner_curated origin; synthetic всегда заблокирован;
- минимум 8 вопросов, все шесть категорий: exact/paraphrase/no_answer/freshness/conflict/injection;
- обе области видимости и хотя бы один case с запрещённым источником;
- непустой корпус, последнюю версию набора и неизменившийся корпус;
- Owner + MFA, exact report hash, причину и human confirmation.

`reject` допустим и для плохого/stale отчёта. На run записывается одно окончательное решение.
Исправление: новая dataset version или новый run, не перезапись решения.
Галочка `human_confirmed` сама по себе не доказывает присутствие человека: личная способность
review не должна передаваться AI-профилям. Owner отвечает за правдивую маркировку origin
и смысловую полезность вопросов; система не может доказать их происхождение по тексту.

## Снимки, актуальность и повторы

Search и eval используют **один `retrieve` SQL helper** с одинаковым ранжированием, RLS,
brand/lifecycle/freshness фильтрами. Дополнительный workspace-only predicate только сужает доступ;
eval не входит под сотрудником и не доказывает работоспособность employee RLS/OAuth.
Cross-workspace/employee isolation проверяется отдельными настоящими DB/auth tests.

В одной транзакции сохраняются report, corpus snapshot, receipt и audit. Все вопросы используют
один UTC момент. Advisory lock общий с activation/archive; перед commit корпус проверяется снова.
Если источник истёк во время прогона, `evaluation_corpus_changed` откатывает транзакцию.
Лимиты: 500 действующих документов бренда, 5 секунд на SQL statement, 10 секунд на case loop.
Это bounded synchronous SQL-операция, **не новый background dispatcher**. Большие наборы нужно
разделять; расширение требует durable jobs и отдельного измеренного бюджета.

Hash корпуса включает algorithm, отсортированные document/version/index IDs, hashes оригиналов,
parser/chunking versions, visibility и effective dates. Неподтверждённый новый индекс не меняет
корпус. Активация/архивация/замена/expiry или вступление будущего источника в силу меняет его.
При изменении ранжирования необходимо обновить идентификатор алгоритма и снова выполнить eval.

При чтении всегда рассчитываются `stale`, `stale_reasons`, `acceptance_blockers`,
`baseline_current`. Историческое решение остаётся `accept_baseline`, но **не является актуальным**,
если корпус/набор изменился. Исходный отчёт не переписывается. Возврат к точно тому же корпусу
может снова сделать отчёт применимым; это сравнение снимков, не журнал всех промежуточных изменений.

Повтор команды с тем же actor/workspace/key/payload возвращает прежний receipt, даже если отчёт
уже stale; актуальность узнаётся отдельным read. Другой payload с тем же ключом — conflict.
Параллельные повторы сериализуются. При rollback до commit receipt не существует и тот же ключ
можно повторить: внешних/платных вызовов здесь нет.

## Данные и доступ

Миграция `0006_retrieval_eval` добавляет:

| Таблица | Содержание |
|---|---|
| retrieval_eval_datasets | immutable версии, thresholds, вопросы и ожидания, автор |
| retrieval_eval_runs | dataset hash, corpus snapshot/hash, report/hash, автор, UTC время |
| retrieval_eval_reviews | exact run/hash, одно решение, причина, автор и UTC время |
| retrieval_eval_receipts | actor-private ключ/hash/result для безопасных повторов |

Все таблицы FORCE RLS, owner-only + текущий workspace/membership. INSERT требует actor=current
user. Composite FKs связывают dataset family/brand и точные hashes run/review. UPDATE/DELETE/
TRUNCATE запрещены immutable triggers; app имеет только SELECT/INSERT, worker не имеет grants.
Тексты вопросов хранятся как **явно сохранённый набор**, не как история чата. Source text в отчёт
не копируется: только IDs/hashes и результаты. Есть базовая secret-signature проверка входов,
не полноценный DLP. Реальные corpora/evals не коммитятся; восстановление — вместе с PostgreSQL.

REST prefix: `/api/v1/workspaces/{wid}/knowledge/evaluations`:

- POST `/commands`: dataset_submit/evaluation_run/evaluation_review;
- GET `/datasets`, `/datasets/{id}`;
- GET `/runs` (optional dataset_id), `/runs/{id}`.

MCP: `knowledge_eval_execute`, `knowledge_eval_datasets`, `knowledge_eval_dataset_read`,
`knowledge_eval_runs`, `knowledge_eval_run_read`. Все операции, включая чтение: Owner + MFA.
Наборы и отчёты видят текущие Owners workspace; receipts только инициатор.
Браузер использует generated contract, очищаемый workspace cache, lazy-loaded screen и text-only
rendering. Сбой refresh скрывает прежний detail; 403 очищает приватную оболочку. Решения — в чате/API.

## Проверки и rollout

Unit: строгие схемы, дубли, contradictory expectations, thresholds/NaN, synthetic gate.
PostgreSQL: migration upgrade/downgrade/upgrade, schema drift, RLS/grants/immutable triggers,
одновременный replay, source parity с обычным поиском, exact hash review, stale corpus/dataset,
expiry во время транзакции, corrupted citation, чужой бренд, role/MFA revocation, REST/MCP parity.
Playwright: desktop/mobile, inert query text, provenance, history vs freshness, failed refresh,
revocation и empty state; регрессия старого knowledge/content/workspace UI.

Локально пройдены 184 Python unit tests, 5 frontend tests, 26 PostgreSQL tests, 44 Edge
desktop/mobile сценария; после финального изменения metadata повторены 14 knowledge/eval
browser tests. Форматирование, lint, mypy/TypeScript, generated contract и production build
проходят. Основной JS 495.25 KB, eval screen загружается отдельно (9.88 KB); это размер сборки,
не результат измерения скорости на машине сотрудника. Compose/Linux проверяются отдельным CI.

Новых зависимостей/env/secrets нет. Проверки: `pnpm check`, `pnpm test`, `pnpm build:web`,
`uv run pytest tests/database` только с disposable `SMM_TEST_DATABASE_URL`; E2E — по правилам проекта.
Схема deployment guard обновлена; published migrations 0001–0005 не изменены.
На реальном сервере ничего не применялось. Для rollout по-прежнему нужны отдельное разрешение,
backup/restore rehearsal, migration role, авторизованный HTTPS/OIDC и две реальные машины.

Подтверждённый FTS baseline — **одно входное свидетельство** для следующего этапа. Он не включает
pgvector, model provider, specialist profile или публикацию. Binary ingestion, hybrid retrieval,
полные AI workflows/accounting/reconciliation и реальные corpus/provider/server gates остаются
в фазе 7; следующая итерация должна продолжить её, а не считать фазу 8 автоматически разрешённой.
