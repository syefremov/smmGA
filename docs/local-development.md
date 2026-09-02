# Локальная разработка на Windows

## Назначение

Этот документ описывает воспроизводимую подготовку основной Windows-машины разработчика. Серверные зависимости здесь не устанавливаются: PostgreSQL, Redis, authentik и остальные сервисы будут запускаться в Docker Compose начиная с фазы 2.

Обычному сотруднику этот набор не нужен. Для него в фазе 9 будет создан отдельный установщик Codex-плагина; на рабочей машине сотрудника останутся только Codex, Tailscale, браузер и connection doctor.

## Поддерживаемая база

Основная целевая система — Windows 11. Текущая машина с Windows 10 Pro 22H2 build 19045 допускается для локального пилота: этот build входит в актуальные требования Docker Desktop. До production рекомендуется перейти на поддерживаемую Windows 11, а полный Playwright-набор в любом случае выполняется в Linux CI.

Минимальные ресурсы:

- 64-bit CPU и включённая аппаратная виртуализация;
- 8 GB RAM;
- 10 GB свободного места только для bootstrap;
- 30 GB свободного места рекомендуется перед загрузкой Docker images и Playwright browsers.

Версии инструментов находятся в [`../scripts/tool-versions.psd1`](../scripts/tool-versions.psd1). Python, Node.js и pnpm фиксируются точно; Git, Docker, WSL, PowerShell, Tailscale и `uv` должны быть не ниже безопасной поддерживаемой базы.

Полезные официальные источники:

- [Docker Desktop for Windows: требования и установка](https://docs.docker.com/desktop/setup/install/windows-install/);
- [Microsoft: установка и обновление WSL](https://learn.microsoft.com/windows/wsl/install);
- [uv: установка](https://docs.astral.sh/uv/getting-started/installation/);
- [Node.js: поддерживаемые LTS-релизы](https://nodejs.org/en/about/previous-releases/).

Условия лицензии Docker Desktop нужно проверить до коммерческого развёртывания в крупной организации. Это не влияет на сервер: там используется Docker Engine на Linux.

## Первичная подготовка

Откройте PowerShell 7 в корне репозитория. Первый запуск на чистой машине:

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\bootstrap-dev.ps1 -InstallMissing -UpgradeTools -UpdateLocks
```

Скрипт:

1. Устанавливает отсутствующие PowerShell, Git, `uv`, Node.js, Docker Desktop и Tailscale через `winget`.
2. Обновляет WSL при `-UpgradeTools`.
3. Устанавливает изолированный Python из `.python-version` через `uv`.
4. Активирует точную версию `pnpm` из `package.json` через Corepack.
5. Создаёт `.venv`, синхронизирует lockfiles и включает repository-owned Git hook.
6. Запускает безопасную диагностику.

Установка WSL, Docker Desktop, Git или Node.js может показать стандартный запрос UAC. После установки WSL или Docker может потребоваться перезагрузка Windows и повторный запуск bootstrap.

Обычный повторный запуск не обновляет системные пакеты и не переписывает lockfiles:

```powershell
pnpm bootstrap
```

Осознанное обновление системных инструментов:

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\bootstrap-dev.ps1 -UpgradeTools
```

Осознанное пересоздание lockfiles выполняется только в отдельной dependency-итерации:

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\bootstrap-dev.ps1 -UpdateLocks
```

## Tailscale

Bootstrap устанавливает клиент, но намеренно не входит в чужую или общую учётную запись. Персональный вход выполняется человеком:

```powershell
tailscale up
```

Фаза 1 допускает состояние `NeedsLogin` как предупреждение. Перед проверкой удалённого MCP в фазе 5 используется строгая проверка:

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\doctor.ps1 -RequireTailscaleLogin
```

## Диагностика

Обычная команда:

```powershell
pnpm run doctor
```

Doctor не читает `.env`, токены, SSH private keys или содержимое пользовательских документов. Он показывает только:

- версию Windows, RAM и свободное место;
- наличие и версии обязательных CLI;
- доступность Docker daemon и Compose;
- наличие закреплённых Python, Node.js и pnpm;
- обобщённое состояние Tailscale без имени пользователя, IP и ключей;
- наличие lockfiles и настройку Git hooks.

Код возврата `0` означает, что обязательные проверки прошли. Предупреждения не блокируют текущую фазу, но должны быть устранены до указанной в сообщении фазы.

## Команды проекта

Команды ниже одинаковы в Windows, Linux и CI, кроме Windows-специфичных `bootstrap` и `doctor`:

| Команда | Назначение |
|---|---|
| `pnpm bootstrap` | Устанавливает Windows runtimes, синхронизирует зависимости и Git hook |
| `pnpm run doctor` | Выполняет read-only диагностику Windows-станции; без `run` вызывается встроенный pnpm doctor |
| `pnpm env:init` | Один раз создаёт локальный `.env` со случайными значениями |
| `pnpm check` | Проверяет locks, Python, frontend и актуальность generated OpenAPI client |
| `pnpm test` | Запускает Python unit и React component tests без внешних сервисов |
| `pnpm build:web` | Выполняет TypeScript check и production-сборку SPA |
| `pnpm infra:up` | Запускает только локальные PostgreSQL и Redis |
| `pnpm api:dev` | Запускает FastAPI с reload вне контейнера после `infra:up` |
| `pnpm dev` | Собирает и запускает полный Compose stack в foreground |
| `pnpm dev:down` | Останавливает Compose stack без удаления постоянных volumes |
| `pnpm build` | Собирает application и web container images |
| `pnpm db:migrate` | Применяет Alembic migrations к настроенной локальной PostgreSQL |
| `pnpm worker:smoke` | Отправляет безопасную задачу и ждёт ответ worker |
| `pnpm test:integration` | Проверяет health API и MCP через reverse proxy |
| `pnpm test:e2e` | Проверяет status UI установленным Playwright browser |

`pnpm dev` автоматически вызывает безопасный initializer `.env`. После запуска откройте `http://127.0.0.1:8080`. Для проверки полного gate последовательно выполните `pnpm worker:smoke`, `pnpm test:integration` и `pnpm test:e2e`.

## Локальная конфигурация и секреты

В репозитории хранится только `.env.example` с placeholders. Локальный файл создаётся командой `pnpm env:init`: генератор использует криптографически случайные значения, не печатает их и не перезаписывает существующий `.env`. Файл уже исключён из Git.

Локальный `.env` предназначен только для development Compose. Реальные server secrets, OAuth credentials, ключи моделей и VK-токены никогда не копируются на компьютер сотрудника и не передаются через Git. Server environment будет создаваться отдельно в фазе 3.

## Частые проблемы

### Команда появилась только после установки

Закройте и снова откройте PowerShell либо повторно запустите bootstrap: он перечитывает machine/user PATH внутри процесса.

### `wsl --version` не показывает современную версию

Запустите административный PowerShell:

```powershell
wsl --update --web-download
```

После обновления перезагрузите Windows. Docker Desktop требует WSL 2.1.5 или новее.

### Docker CLI есть, но daemon не отвечает

Запустите Docker Desktop, дождитесь состояния Engine running и повторите `pnpm run doctor`. Виртуализация должна быть включена в BIOS/UEFI.

Если обновляется legacy Docker Desktop 3.x и новый установщик сообщает `Path contains symlink` внутри `C:\ProgramData\DockerDesktop\version-bin`, не удаляйте `docker-desktop-data` и VHDX вслепую. Сначала проверьте наличие старых volumes/images и экспортируйте WSL-диск на другой носитель. Штатный uninstall может удалить локальные Docker-данные; текущий результат аудита описан в [`phase-1-audit.md`](phase-1-audit.md).

### Недостаточно места

Doctor блокирует работу ниже 10 GB и предупреждает ниже 30 GB. Освобождение пользовательских файлов или очистка Docker выполняются только вручную после просмотра точных целей; bootstrap ничего не удаляет.

## Критерий завершения фазы 1

Фаза закрывается, когда:

- bootstrap повторяем и завершается успешно;
- `pnpm run doctor` не показывает обязательных ошибок;
- существуют `uv.lock` и `pnpm-lock.yaml`;
- `pnpm check` проходит;
- Git hook использует `.githooks`;
- другой чистый Windows-профиль может повторить настройку по этому документу.
