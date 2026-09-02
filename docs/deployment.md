# Серверный staging: развёртывание

## Статус и границы

Фаза 3 подготовлена в репозитории, но не означает, что сервер настроен. Последняя read-only SSH-проверка 2026-09-02 остановилась по timeout до авторизации. Изменения на удалённом сервере не выполнялись. Локальные exit gates фаз 1–2 также остаются открытыми; см. [`phase-2-implementation.md`](phase-2-implementation.md).

Скрипты рассчитаны на отдельный **Ubuntu 24.04 LTS, amd64/arm64**. Дистрибутив сначала проверяется; неподдерживаемая ОС — причина остановиться и адаптировать bootstrap, а не переустанавливать сервер. Не использовать на общем сервере с чужими контейнерами без отдельного обзора. Рабочая инфраструктура проверяется именно на целевой машине: CI не проверяет её firewall, SSH, tailnet или reboot.

Для первого запуска нужны Bash и системный Python 3.12+ (`python3 --version`). В минимальном образе без Python оператор сначала устанавливает `python3` через apt в разрешённой bootstrap-сессии. Сам bootstrap также устанавливает Git и Python; Git нужен для `git archive` при deploy. Node, pnpm и uv на хост-сервер не устанавливаются: сборка выполняется внутри Docker stages.

В этой фазе допускаются только синтетические данные. Приватный Caddy отдаёт status SPA, `/health/live`, `/health/ready` и `/api/v1/system/status`. `/mcp*`, остальные `/api/*`, Swagger и OpenAPI закрыты кодом 403 до авторизации фазы 4. Tailscale ограничивает сетевой доступ, но не заменяет будущую персональную авторизацию и workspace permissions. authentik ещё не запускается.

## Файлы и роли

| Путь | Назначение |
|---|---|
| `/opt/smm-gpt/releases/<full-SHA>` | Неизменяемый архив committed source, Compose и release manifest |
| `/opt/smm-gpt/state` | Последняя успешно запущенная и предыдущая версии |
| `/etc/smm-gpt/server.env` | Случайный пароль БД, режим 0600, владелец root |
| `/var/lib/smm-gpt/postgres` | PostgreSQL 17; UID/GID 70:70 из выбранного Alpine image |
| `/var/lib/smm-gpt/media` | Медиа; UID/GID 10001:10001 |
| `/var/lib/smm-gpt/redis`, `/var/lib/smm-gpt/authentik` | Резерв под последующие фазы; сейчас Redis не хранит business state |
| `/var/lib/smm-gpt/restore-drills/<ID>` | Изолированные результаты восстановления; сохраняются для проверки |
| `/var/backups/smm-gpt/<ID>` | Локальная staging-копия; не offsite и пока не зашифрована |

`smm` — персонально контролируемая **операционная администраторская учётная запись**, не сотрудник SMM. Она входит в `sudo`, но не в `docker`; пароль для sudo задаётся владельцем интерактивно после bootstrap. Не хранить его в чатах или Git. Членство в docker group тоже давало бы root-подобные полномочия, поэтому не выдаём его сотрудникам. Контейнеры запускаются с фиксированными непривилегированными UID, read-only rootfs, без capabilities и с `no-new-privileges`.

Deployment выполняется из проверенного checkout через краткую авторизованную `sudo`-команду оператора. Это привилегированная операция, а не MCP tool. Не запускать скрипты с непроверенного коммита. Не назначать широкое `NOPASSWD` и не открывать Docker socket приложению.

## 1. До первого изменения сервера

1. Закрыть локальные gates фаз 1–2 или отдельно согласовать изменение порядка roadmap.
2. Получить рабочий SSH и сверить fingerprint ключа хоста с консолью провайдера. После переустановки не применять `StrictHostKeyChecking=no` и не удалять known_hosts вслепую.
3. Убедиться, что работает независимая recovery-консоль провайдера; сохранить текущую сессию открытой. Сделать snapshot средствами провайдера, если доступно.
4. Проверить ОС, диски, RAM, IPv4/IPv6, службы и открытые порты: `sudo bash scripts/doctor-server.sh`. До bootstrap некоторые проверки закономерно вернут ошибку. Вывод может содержать IP/имена узлов — хранить его приватно.
5. Подтвердить, что это staging без важных чужих данных, Docker/containerd и особой сетевой политики. Bootstrap не удаляет конфликтующие пакеты или данные, не сбрасывает UFW и не переписывает чужие конфиги. Но установка пакетов, обновления безопасности и включение firewall всё равно являются изменением сервера.
6. Передать **только публичный** Ed25519-ключ оператора. Приватный ключ остаётся на устройстве владельца. Выданный ранее пароль root следует заменить через консоль как отдельную операцию; скрипт не делает это автоматически.

## 2. Bootstrap в два этапа

Все изменяющие entry points без `--apply` показывают план и не выполняют даже preflight-команды на хосте. `doctor-server.sh` всегда read-only, но требует Linux/sudo для полной диагностики.

```bash
bash scripts/bootstrap-server.sh --public-key /root/operator.pub
sudo bash scripts/bootstrap-server.sh --apply --recovery-confirmed --public-key /root/operator.pub
```

Первый bootstrap:

- настраивает официальные apt repositories Docker и Tailscale, устанавливает Engine, CLI, buildx и **Compose plugin**;
- сохраняет используемые пакетные версии в системной apt/dpkg history; конкретные installed versions надо приложить к приватному commissioning report;
- включает UTC, chrony, persistent journald с лимитом 100 MB и Ubuntu unattended security updates без автоматического reboot;
- создаёт `smm`, добавляет один публичный ключ без удаления существующих ключей;
- создаёт каталоги и `.env` только при отсутствии, не меняет существующий пароль БД;
- включает UFW с incoming deny, SSH rate limit на TCP 22 и UDP 41641 для Tailscale;
- добавляет собственное ingress-правило в `DOCKER-USER` для bridge `smmbr0` и systemd unit для повторного применения при запуске Docker;
- **не отключает** root/password SSH, не выполняет Tailscale login, reboot или deploy.

У UFW существующее широкое allow-правило перед rate-limit может сохранить прежний доступ. После bootstrap вручную проверить effective rules: скрипт намеренно не удаляет чужие правила. Docker nftables backend не поддерживается этим bootstrap; отсутствие `DOCKER-USER` останавливает его. Не отключать Docker iptables и не сбрасывать цепочки Tailscale.

После первой команды владелец интерактивно задаёт пароль для sudo (`sudo passwd smm`). Затем из **второй независимой сессии** проверяет вход ключом как `smm`, выполнение `sudo -v`, доступ к recovery-консоли. Только после этого:

```bash
sudo bash scripts/bootstrap-server.sh --apply --recovery-confirmed --harden-ssh --second-session-confirmed
```

Эта команда проверяет наличие ключа/пароля sudo, устанавливает SSH drop-in, запускает `sshd -t` и проверяет effective settings до reload. Если новый конфиг не проходит проверку, удаляется только созданный этой командой drop-in; чужие файлы не затрагиваются. Флаги подтверждения — запись реальной ручной проверки, не способ её пропустить. Проверить третью новую сессию перед закрытием старых. При `Match`-блоках дополнительно проверить `sshd -T -C user=smm,host=<host>,addr=<client-IP>` и root для реального адреса.

## 3. Tailscale: решение владельца аккаунта

1. Войти интерактивно: `sudo tailscale up --hostname=ops-staging --advertise-tags=tag:smm-staging`. Не передавать auth key в командной строке или чат.
2. Проверить и адаптировать [`ops/tailscale-policy.example.json`](../ops/tailscale-policy.example.json) с реальной персональной identity. Это **пример**, а не команда перезаписи всего tailnet policy. Удалить конфликтующие широкие grants/ACL только после анализа их влияния на остальные устройства.
3. Предоставить группе операторов только TCP 22/443 до подключения сотрудников в следующих фазах. Проверить policy tests и реальный доступ с разрешённого и запрещённого устройств.
4. Включить MagicDNS и HTTPS certificates в админке. Нейтральное имя узла не должно содержать клиента или секрет: DNS name сертификата попадает в публичные CT logs.
5. Не включать Funnel. Проверить `sudo tailscale serve status --json`: `AllowFunnel` отсутствует/false. Удаление существующей чужой Funnel-конфигурации требует отдельного решения, скрипты её не сбрасывают.

После deploy и проверки localhost:

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:8080
sudo tailscale serve status
```

Это явное действие публикации **внутри tailnet**, выполняемое только после проверки ACL. Скрипты deploy не включают Serve автоматически. Запрет remote MCP остаётся даже внутри tailnet до фазы 4.

## 4. Deploy точного коммита

Использовать полный SHA коммита, чей CI прошёл. Пример `<full-commit-SHA>` нужно заменить; сокращённые SHA, имена веток и теги скрипт не принимает.

```bash
bash scripts/deploy.sh --release <full-commit-SHA>
sudo bash scripts/deploy.sh --apply --release <full-commit-SHA>
sudo bash scripts/doctor-server.sh
```

Deploy извлекает `git archive` точного коммита, а не dirty working tree, собирает два образа с SHA-tag и фиксирует их image IDs. Повторный deploy проверяет те же image IDs; подмена tag приводит к остановке. Секреты не копируются в release и build context. `ops/compose.server.yaml` является самостоятельным server manifest — его нельзя объединять с development Compose, который публикует локальные DB/API ports.

Перед заменой уже работающей версии создаётся backup. Сервисные writers останавливаются, затем новая версия запускается и проходит health, worker smoke, blocked-MCP и container confinement/port checks. Только после успеха переключается `current.json`. При отказе возвращается прежняя версия с тем же migration fingerprint. Первый неудачный deploy не считается активным.

Ограничение: автоматический deploy/rollback поддерживает **одинаковый набор migrations**. Изменение схемы требует следующей реализации migration preflight и отдельного обзора; скрипт отказывается выполнять его поверх текущей базы. Не удалять manifest, чтобы обойти защиту. Незавершённый release-каталог сохраняется для диагностики; скрипт не очищает его вслепую.

Сборка при первом deploy использует pinned image tags, но не registry digests; release IDs фиксируют фактически полученные app/web images. Полная supply-chain фиксация, image scanning и registry promotion — фаза 10. Обновление host-пакетов идёт через apt, а не standalone Compose.

## 5. Приёмка на реальном сервере

- [ ] Ubuntu, RAM, диск, package versions и active services записаны в приватный inventory.
- [ ] Оператор входит ключом из новой сессии; password/root login отклоняется; sudo работает.
- [ ] UTC/chrony, security updates и журналы работают; reboot-required обработан в согласованное окно.
- [ ] UFW active, `DOCKER-USER` содержит guard, прочие rules разобраны; IPv6 проверен отдельно.
- [ ] Из публичного интернета недоступны 5432, 6379, 8000, 8080 и identity admin; IPv4 **и IPv6**.
- [ ] Private HTTPS работает с разрешённого устройства и недоступен запрещённому; Funnel выключен.
- [ ] `/mcp/` и неразрешённые REST routes возвращают 403, страница состояния работает.
- [ ] Повторный deploy, code rollback и isolated restore подтверждены.
- [ ] После согласованного reboot Docker, guard, контейнеры и Serve восстановились; данные сохранились.

Только этот checklist закрывает серверный exit gate. CI с временными контейнерами не равен reboot/SSH/Tailscale-проверке на сервере.

## Официальные источники

- [Docker Engine для Ubuntu и официальный apt repository](https://docs.docker.com/engine/install/ubuntu/).
- [Docker Compose plugin для Linux](https://docs.docker.com/compose/install/linux/).
- [Docker, UFW и firewall backend](https://docs.docker.com/engine/network/packet-filtering-firewalls/); [цепочка DOCKER-USER](https://docs.docker.com/engine/network/firewall-iptables/).
- [Ограничение localhost port publishing в Docker старше 28](https://docs.docker.com/engine/network/port-publishing/) — поэтому серверный preflight требует Engine 28+.
- [Официальные apt packages Tailscale](https://pkgs.tailscale.com/stable/) и [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).
- [Синтаксис Tailscale grants](https://tailscale.com/docs/reference/syntax/grants) — основа policy template; реальную политику проверяют в tailnet до включения Serve.
