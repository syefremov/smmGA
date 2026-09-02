# Фаза 4: identity, данные и изоляция

Дата: 2026-09-02. Статус: **backend foundation реализован и локально проверен; полный exit gate не закрыт**.

## Сделано

- Миграция `0002_identity`: 11 таблиц, UTC, constraints/composite FK, FORCE RLS, append-only audit.
- Транзакционный Unit of Work, deny-by-default матрица семи ролей, серверные проверки identity/membership/MFA.
- Раздельные app/worker/migration credentials и grants; worker без доступа к web sessions, app без права выдавать роли.
- OIDC code + PKCE, browser-bound одноразовый state/nonce, непрозрачные хешированные sessions, CSRF/Origin, rotation при входе, TTL и отзыв.
- MCP SDK OAuth resource-server middleware, metadata/challenges, issuer/audience/RS256/scopes, online introspection и повторная проверка перед tool call.
- REST и MCP вызывают один AccessService. Idempotent diagnostic job + outbox + audit фиксируются атомарно. Worker executor повторно проверяет текущие права.
- Operator CLI с dry-run по умолчанию: migration/runtime logins, одноразовый GreenAurum Owner, emergency user disable. Реальные users не созданы.
- Шаблоны authentik configuration contract и runbooks: [`authentication.md`](authentication.md), [`data-model.md`](data-model.md).

## Проверки

80 новых unit cases для ролей, MFA, JWT, issuer discovery, revocation, nonce, payload escalation и CLI plan. Шесть интеграционных сценариев на отдельной временной PostgreSQL 15.7, уже установленной на машине: migration upgrade/downgrade/upgrade и metadata drift, RLS/grants/FK/append-only, конкурентная идемпотентность, worker revocation, bootstrap/rollback, browser PKCE/CSRF/Origin/rotation/expiry, HTTP MCP auth и cross-workspace denial. Тесты создают только собственные случайно названные базы и удаляют их после проверки; пользовательские базы не затрагиваются.

CI дополнен тем же suite на целевой PostgreSQL 17.6. Обычные container/queue/browser и server lifecycle проверки сохранены; backup restore создаёт group roles перед восстановлением ACL. Результаты конкретного CI run проверяются после push, не предполагаются заранее.

Перед commit локально прошли `pnpm check` (включая secret scan и generated contract drift), 122 Python unit tests, 2 React component tests, 6 PostgreSQL integration scenarios, Compose interpolation и `pnpm build:web`. Временный локальный кластер после тестов остановлен. В существующий `.env` добавлены только отсутствующие app/worker credentials без вывода значений; пользовательские настройки сохранены.

## Что ещё не введено в эксплуатацию

- Фазы 1–3: legacy Docker на Windows, SSH, recovery console, Tailscale/HTTPS, reboot и серверный restore всё ещё требуют реальной проверки.
- authentik ещё не установлен; его отдельная БД, версия/digest, backup, users, MFA и реальный OAuth client не настроены. Contract template не является проверенным blueprint.
- Совместимость выбранной версии authentik с resource binding, predefined callback и cross-provider introspection должна пройти реальные negative tests. При несоответствии gate закрыт; JWT audience checks не ослабляются.
- Identity routes в staging Caddy остаются заблокированными. Требуется отдельный согласованный identity rollout; commit не является deploy.
- Нет identity administration UI, back-channel SSO logout, периодической session rotation, role escalation workflow, автоматического outbox dispatcher или готового сотруднического плагина. Эти ограничения и порядок дальнейшей реализации перечислены в auth/data runbooks.

Следующая фаза — единое transport/API ядро, плагин и web foundation. Репозиторную работу можно продолжить по прямой команде владельца, но нельзя объявлять систему готовой для сотрудников до закрытия перечисленных gates.
