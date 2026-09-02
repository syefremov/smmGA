# Персональный вход и права — фаза 4

## Реализованная граница

Backend умеет аутентифицировать браузер и MCP через OIDC/OAuth. По умолчанию `SMM_AUTH_ENABLED=false`: tenant REST возвращает 503, tenant MCP tools не регистрируются, доступен только прежний синтетический status. Это не режим «доверять любому пользователю». Staging Caddy продолжает блокировать MCP и нестатусный API до отдельного ввода identity в эксплуатацию.

`ops/authentik/provider-contract.example.json` и `identity.env.example` — проверяемые оператором шаблоны настройки, **не импортируемый blueprint и не готовое развёртывание authentik**. Реальные hostname, signing key, отдельная БД IdP, backup, patch version/digest, users и MFA enrollment ещё не созданы. Не включать доступ только потому, что эти файлы существуют.

## Браузер

1. GET `/api/v1/auth/login` сохраняет одноразовый state, nonce, PKCE verifier и привязку к отдельной cookie браузера. Redirect идёт только на проверенный authorization endpoint.
2. GET `/api/v1/auth/callback` сопоставляет state и browser cookie, атомарно расходует flow, выполняет code exchange с PKCE S256 на сервере. Проверяются RS256-подпись, issuer, audience, exp, iat, sub, nonce и azp. Повтор callback не создаёт сессию. При наличии `iss` callback он должен совпасть точно; выбран один фиксированный web issuer и callback.
3. Нужна заранее созданная identity: автоматического создания user или объединения по email нет. Новая cookie заменяет старую сессию. Cookies `__Host-*`, Secure, Path=/, без Domain. Session cookie HttpOnly + SameSite=Lax; CSRF cookie доступна JS и SameSite=Strict. Bearer/refresh tokens не попадают в браузер и не сохраняются сервером.
4. GET `/api/v1/auth/session` сообщает личность и MFA, не секрет. POST требует точного Origin и X-CSRF-Token, совпадающего с хешем в сессии. Session ID также хранится только как SHA-256. Idle TTL — 30 минут, absolute TTL — 8 часов. Login всегда ротирует session ID; endpoint изменения ролей пока отсутствует.
5. POST `/api/v1/auth/logout` отзывает все локальные web-сессии пользователя и очищает cookies. Клиент следующей фазы должен очистить TanStack Query/cache на logout, workspace switch, 401/403. Ответы no-store. Uvicorn access log отключён, чтобы callback query с кодом не попадал в журнал.

Logout в SMM не завершает SSO-сессию authentik. Back-channel logout, периодическая ротация активной сессии и управляемое делегирование ролей ещё не реализованы. До их внедрения экстренный отзыв сотрудника делается операторским `disable-user`: active=false проверяется на следующем запросе и web, и MCP. Не выдавать это ограничение за мгновенную синхронизацию любого logout в IdP.

## MCP

Стандартный MCP SDK защищает `/mcp/`. Метаданные доступны по `/.well-known/oauth-protected-resource` и `/.well-known/oauth-protected-resource/mcp/`; challenge указывает на существующий canonical endpoint. Issuer/resource сравниваются буквально, включая trailing slash. Auth включается только на HTTPS same-origin конфигурации.

JWT принимается только от настроенного MCP issuer, с RS256, ожидаемым resource audience, client_id/azp, сроком и scope `smm:access`. JWKS берётся с проверенного endpoint того же IdP origin, не из jku/x5u токена. Introspection выполняется без positive cache на каждом запросе и при вызове tool: revoked token/недоступный IdP не разрешают действие. Membership остаётся отдельной серверной проверкой, scopes не выдают роли. DNS rebinding guard проверяет Host/Origin; CORS не включён.

Выбран **predefined public OAuth client + PKCE S256**, не shared bearer key, не CIMD и не самодельный authorization server. Для authentik шаблон задаёт client_id равным canonical MCP resource: стандартный audience провайдера тогда совпадает с ресурсом. Это не заменяет проверку RFC8707: на конкретной версии нужно доказать отклонение неверного resource на authorization/token endpoints. Если этого нет, gate остаётся закрытым и нужен отдельно спроектированный совместимый AS/adapter; нельзя просто ослабить проверку audience в приложении.

Политика SDK/discovery/PKCE сверена с [официальной документацией OpenAI](https://developers.openai.com/plugins/build/auth). Конкретный redirect URI копируется из используемого OAuth-клиента; wildcard, пустая allowlist и фиктивное объявление CIMD/RFC9207 запрещены. Плагин и удобные команды установки сотрудника будут в фазе 5.

Подписанный действующий bearer token, действительно украденный у пользователя, невозможно отличить от оригинала только проверкой JWT. Сейчас ограничиваются срок, scope, audience, личность и online revocation. После отзыва он отвергается; гарантии «любой украденный токен автоматически распознан» нет. DPoP/token binding не реализованы.

## authentik: порядок ввода

1. Закрыть инфраструктурные gates фаз 1–3. Отдельно согласовать deploy identity, выбрать и закрепить поддерживаемую patch-версию authentik по официальному release, signing key и private HTTPS. БД IdP отдельна от SMM; административные интерфейсы только в приватном контуре. Пока готов только configuration contract.
2. Создать web confidential и MCP public providers из шаблона, per-provider issuer, стабильный user_uuid subject, exact callbacks, code flow; исключить implicit/password/client_credentials для персонального приложения. Не выдавать browser client secret.
3. Создать scope `smm:access`, выдать его MCP provider. Настроить cross-provider introspection: web provider разрешён в Federated OAuth2/OpenID Providers у MCP provider. Ограничить эту связь только нужными двумя providers. [OAuth2 endpoints и federation](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/).
4. Для пилота требовать MFA у всех пользователей; обязательно у Owner/Administrator/Publisher. Authenticator Validation: отсутствие enrolled device → deny, enrollment отдельный. Проверить реальные подписанные amr; приложение принимает `mfa` или `pwd` + `otp`. Не подставлять константу mfa и не считать JWT group подтверждением MFA. [Authenticator Validation stage](https://docs.goauthentik.io/add-secure-apps/flows-stages/stages/authenticator_validate/).
5. Проверить endpoints/issuer/S256, подпись/aud/azp/scopes/amr, wrong resource/redirect/verifier, replay, introspection revocation, MFA bypass. Только после этого подготовить закрытый Compose/Caddy rollout с `/api/*`, `/mcp*` и `/.well-known/*`; текущий default staging не открывается автоматически.
6. Получить подтверждённые issuer/subject первого владельца и выполнить на migration/operator окружении:

```text
python -m smm_gpt.cli bootstrap-owner --issuer <web-issuer> --subject <verified-sub> --mcp-issuer <mcp-issuer> --mcp-subject <verified-sub>
python -m smm_gpt.cli --apply bootstrap-owner --issuer <web-issuer> --subject <verified-sub> --mcp-issuer <mcp-issuer> --mcp-subject <verified-sub>
```

Первая команда только показывает план. Вторая атомарно создаёт GreenAurum, user, две identity links и Owner. Повтор не меняет владельца. Пароль приложения не создаётся. Передавать реальные токены/пароли в аргументах нельзя; `subject` — подтверждённый идентификатор, не credential. Для экстренного отзыва: `python -m smm_gpt.cli --apply disable-user --user-id <UUID>`; это отдельное разрешённое операторское действие, не MCP capability.

## Матрица ролей

| Роль | Права |
|---|---|
| Owner | Все разрешения, включая одобрение точной редакции |
| Administrator | Просмотр, управление участниками, аудит; без одобрения/публикации |
| Strategist | Просмотр, планирование, внутренние задачи, комментарии, диагностические jobs, текстовые знания |
| Editor | Просмотр, редактирование, внутренние задачи, комментарии, диагностические jobs, текстовые знания |
| Publisher | Просмотр, комментарии, подготовка/отмена ручного пакета уже одобренной редакции |
| Analyst | Просмотр, аналитика, диагностические jobs |
| Viewer | Только просмотр workspace |

Срез фазы 7 добавляет `knowledge.write` для Owner/Strategist/Editor: подача workspace text,
reindex и просмотр индексируемого кандидата. Owner-only документы, activation/archive,
gaps/memory review и AI testing доступны только Owner + MFA. AI profiles не являются ролями,
не получают Principal/approval tools. Runs и artifacts actor-private; источники повторно
проверяются при чтении. Details: [phase-7-implementation.md](phase-7-implementation.md).

Retrieval eval datasets/runs/review также требуют Owner + MFA, включая read. Они видимы всем
текущим Owners данного workspace, receipts только инициатору; worker доступа не имеет.
`audience=workspace` сужает owner query до общих документов, не входит под сотрудником и не
заменяет employee RLS/OAuth checks. `accept_baseline` не выдаёт новых capabilities и не активирует AI.

В фазе 6 реализованы внутренние задачи и ручной контентный цикл, но не внешняя публикация и не управление ролями через веб. `content.publish` пока означает ручную подготовку/отмену, не вызов VK. Только Owner подтверждает факты/правила и одобряет редакции; `content.comment` не даёт approval. Одна роль на membership; deny-by-default. Расширение/совмещение ролей и право назначить Owner потребуют отдельного дизайна, migration и тестов. Повышенные роли без подтверждённого MFA не получают даже workspace read. Подробности и граница человеческого подтверждения — [phase-6-implementation.md](phase-6-implementation.md).
