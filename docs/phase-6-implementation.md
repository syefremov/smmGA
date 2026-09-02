# Фаза 6: внутренний контентный цикл

## Статус и границы

Реализован репозиторный срез фазы 6 — ручная работа через общие REST/MCP services и минимальный
веб-интерфейс. **Exit gate не закрыт:** Linux-сервер, authentik/private HTTPS, локальный Docker
и работа двух реальных компьютеров остаются непроверенными gates предыдущих фаз.
Outbox dispatcher из фазы 5 также остаётся отдельной незавершённой работой. Этот синхронный
контентный цикл его не использует. Код и тестовые данные не вводят систему в эксплуатацию.

Нет обращения к соцсетям, LLM, embedding API или произвольным URL. Никаких реальных
claims GreenAurum не посеяно. Fixtures только синтетические, создаются в изолированных тестовых БД.
`publication_packages` — неизменяемый ручной манифест, не очередь внешней публикации.

## Физическая модель

Миграция `0004_content` содержит самостоятельный SQL snapshot без импорта ORM.
Миграции `0001–0003` не изменены. `scripts/server.py` ожидает новую schema revision;
это не означает автоматическое применение миграции на сервере.

| Таблицы | Назначение |
|---|---|
| brands/products/sources | Идентификаторы и названия; добавлено scoped создание |
| content_records | Строго типизированные неизменяемые source_item, brand_profile, product_version, product_fact, claim_policy, research, campaign, content_plan, brief, idea |
| content_links | Tenant FK между связанными версиями записей |
| posts / post_revisions | Текущее состояние/версия поста и неизменяемые варианты текста/снимки медиа |
| content_review_runs / content_decisions / content_comments | Отчёты проверки, решения людей, комментарии к точной редакции |
| post_working_copies | Личная серверная рабочая копия с version/base_version/expiry |
| publication_packages / package_cancellations | Точный ручной пакет и отдельная неизменяемая отмена |
| content_receipts | Результат команды для идемпотентного повтора |
| work_assignments / work_item_dependencies | Ответственный, срок, кампания и активные зависимости внутренних задач |

Реестр `content_records` — осознанная физическая реализация модулей, а не свободный JSON:
Pydantic discriminated union запрещает неизвестные поля, требует конкретные ссылки и типы.
Workspace/brand/product/source/family/version/hash/creator/confirmation/expiry вынесены в SQL.
Он не заменяет специализированные таблицы документов, индексов, AI runs или метрик будущих фаз.
Одна семья правил бренда и claim policy на бренд, одна семья product_version на продукт.
Области/юрисдикции нескольких policies пока описываются в правилах этой одной policy.

Исправление создаёт следующую версию семьи с `replaces_id` последней записи.
Подтверждение владельцем тоже создаёт новую неизменяемую запись, возвращает новый UUID;
старый неподтверждённый ID не становится пригодным для ссылок автоматически.
Неподтверждённый draft не заменяет последнюю подтверждённую версию. Использование старой
подтверждённой версии после подтверждения её замены блокируется. Даты — timezone-aware UTC.
Source locator — метаданные (HTTPS или owner-input), без credentials/query/fragment; он не загружается.
Наблюдения, гипотезы и сведения владельца различаются. Гипотеза не подтверждает продуктовый факт.

## Состояния, блокировки и решения

`post_create → draft → revision_save → review_request → in_review → approve/reject`.
Reject даёт `rejected`, approve — `approved`; `package_prepare` — `package_ready`.
Из любого состояния новая редакция возвращает `draft` и обнуляет active approval.
Комментарии и личная копия не меняют утверждённую редакцию.
Отмена пакета сохраняет историю и возвращает текущий одобренный пост в `approved`.

Команды выполняются в одной транзакции: проверка membership/MFA/permission, workspace advisory
lock, receipt, предметные изменения и безопасный audit. Сетевых вызовов внутри нет.
`expected_version` обязателен для изменений состояния. Одновременные правки дают конфликт,
а не last-write-wins. Ключ повтора ограничен workspace + actor, hash включает action и payload.
Повтор того же ключа/тела возвращает старый результат; изменённое тело — idempotency_conflict.
Неизвестный исход в веб-форме сохраняет ключ и блокирует смену payload до повторной сверки.

Approval связывает revision ID, номер, content hash, actor, reason и preflight snapshot.
Hash редакции охватывает ВСЕ destinations/text/fact IDs/gaps/attachments и media manifest.
ReviewRun и preflight никогда не создают Approval. В пилоте только Owner принимает решение;
Publisher с MFA готовит/отменяет пакет уже одобренной редакции, но не редактирует и не одобряет.
Strategist создаёт планы/брифы/идеи; Editor — справочные материалы, посты и редакции.
Owner/Strategist/Editor/Publisher могут комментировать. Administrator не получает контентные права.

Явные `human_confirmed` / `claims_reviewed` — контракт персонального клиента и workflow,
**не криптографическое доказательство человека за клавиатурой**. Сервер гарантирует права,
точную редакцию и запрет автоматического одобрения в review path. Будущим AI worker profiles
нельзя выдавать персональный approval endpoint или доверять boolean модели.

Зависимости задач проверяются на цикл; переход в in_progress/done запрещён, пока зависимости
не done. В пилоте максимум 500 активных рёбер на workspace; удаление ребра остаётся доступным.
Ответственный должен иметь активные User и membership того же workspace; чужой ID недоступен.

## Preflight и ручной пакет

Проверяются scoped references, подтверждение и актуальность источников/фактов/версии продукта,
соответствие фактов продукту брифа, правила бренда/claim policy, required disclaimers,
незакрытые knowledge gaps, опасные ссылки, целостность revision и изменение media metadata.
Rules пока только буквальные case-insensitive совпадения, не смысловая экспертиза.
Всегда есть предупреждение о человеческой проверке утверждений; `ai_review=not_run`.

Внутренние консервативные лимиты пилота: до 3 VK destinations, 4000 символов и 4 вложения
в каждом варианте для успешного preflight. Это **не заявление о лимитах API VK**.
Редакция может хранить до 10 000 символов/10 вложений, но не пройдёт pilot preflight.

Пакет фиксирует exact revision, approval, preflight, schedule UTC и timezone workspace;
`mode=manual`, `external_dispatch=false`, `media_bytes_verified=false`.
Перед созданием заново проверяются permissions/state/version/hash и evidence на дату подготовки.
Набор версий проверенных источников должен совпадать с approval context. При смене правил нужна
новая редакция и решение, даже если новая policy не нашла запрещённых фраз.
Чтение пакета заново рассчитывает active/cancelled/stale/expired. Архивный manifest сохраняется,
но устаревший пакет не пригоден к передаче. API не умеет отправлять его наружу.

Attachments ссылаются на уже существующий `file_metadata` в том же workspace. В hash включены
file ID/SHA/type/size/alt/rights declaration, но не storage_key. Загрузка, антивирус, проверка байтов,
consent evidence и генерация изображений ещё не реализованы — это не готовый media pipeline.

## REST, чат и веб

REST prefix: `/api/v1/workspaces/{workspace_id}/content`.
POST `/commands` — discriminated command DTO, остальные маршруты только читают.
GET `/records`, `/records/{id}`, `/posts`, `/posts/{id}`, `/posts/{id}/preflight`,
`/posts/{id}/working-copy`, `/posts/{id}/history/{kind}`, `/packages`, `/packages/{id}`, `/tasks/{id}`.
MCP: `content_execute`, `content_records`, `content_record_read`, `content_posts`,
`content_post_read`, `content_preflight`, `content_working_copy`, `content_history`,
`content_packages`, `content_package_read`, `content_task_context`.
Сервис один: `ContentService`. Схемы браузера генерируются из OpenAPI.

Списки имеют limit/cursor (до 50 строк), UUID ascending; PostView — последние 10 редакций,
20 решений и 20 комментариев с признаком усечения. Полная история доступна отдельной пагинацией.
Календарь сортирует текущую страницу по времени, явно показывает, что это не весь календарь.
Для крупного пилота понадобятся date-range queries/индексы и более компактные reference summaries.

Веб: `/app/content`, `/app/materials`, `/app/calendar`; direct post link через `?workspace=…&post=…`.
Редактор сохраняет отдельную рабочую копию, показывает варианты, facts/gaps, preflight,
решения, комментарии и side-by-side JSON-сравнение последних редакций (не word-level diff).
Несохранённая/иная восстановленная копия не может быть одобрена как показанная редакция.
Конфликт не стирает поля; нужна явная сверка с сервером. Личная копия имеет логический TTL 7 дней,
после expiry не выдаётся, при новом сохранении заменяется; массовый purge ещё не реализован.
В browser storage текст и токены не пишутся. Кэши очищаются на смене workspace/доступа/logout.

Для сложной редакторской формы подключены закреплённые React Hook Form 7.87.0,
@hookform/resolvers 5.9.1 и Zod 4.5.4. Они проверяют ввод, а не дублируют серверные permissions.
Применены [официальный пример интеграции](https://github.com/react-hook-form/resolvers#zod)
и [контракт Zod](https://zod.dev/basics).
Веб-формы материалов минимальные: одно правило/слот/источник; расширенные массивы и назначения
задач доступны через чат/API. Каталоги пока создаются через чат. Редакции с медиа в вебе только
просматриваются: изменение вложений — через chat/API, чтобы форма не потеряла снимок файлов.

## Проверка и запуск

Локальный результат этой итерации: 147 Python unit tests, 5 frontend tests,
15 PostgreSQL integration tests и 30 browser tests (16 content + 14 workspace,
desktop/mobile в Edge) прошли. `pnpm check`, сборка frontend и валидация skill пройдены.
Настоящий Docker stack и Chromium smoke состояния системы проверяются Linux CI,
а не подменяются локальным preview с mocked business API.

Локально: `pnpm check`, `pnpm test`, `pnpm build:web`, новая миграция и metadata comparison
на изолированном PostgreSQL 15. Upgrade → downgrade до baseline → upgrade проверяется fixture.
Интеграционные тесты: полный lifecycle, RLS/FK, append-only history, roles/MFA, concurrency,
idempotency, stale policy/evidence/media, private working copies, task dependencies, REST/MCP parity.
Browser tests используют synthetic API responses; отдельные HTTP/PG тесты проверяют настоящий backend.
На этой Windows-машине проверяется установленный Edge; CI использует закреплённый Chromium.

Не применять тестовый `SMM_TEST_DATABASE_URL` к серверу. Тесты создают/удаляют только свои
случайно названные БД. Deploy, backup и rollback требуют отдельной авторизации. Downgrade
удаляет историю фазы 6 и пригоден только для disposable tests или отдельно согласованного
restore-backed rollback; после реальных записей предпочитать roll-forward.

До реального командного пилота: закрыть прежние инфраструктурные gates, dispatcher, личный вход
и отзыв доступа двух машин, загрузить подтверждённые правила/источники владельца. Фаза 7 —
документы/ingestion/FTS/RAG/model gateway и ограниченные AI-профили, не автоматическая публикация.
