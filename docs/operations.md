# Эксплуатация staging

Это runbook фазы 3, не production SLA. Полное шифрование/offsite/retention, автоматические backups, мониторинг, image digests и disaster recovery относятся к фазе 10. До их реализации запрещено считать этот контур надёжным хранилищем реальных клиентских данных.

## Правила операций

Для optional PDF/DOCX workflow см. [`knowledge-files.md`](knowledge-files.md): следить за
ClamAV/signature freshness, failed jobs, RAM/диском и orphan originals; backup включает
PostgreSQL **и media volume**. Не удалять quarantine вручную и не обходить scanner/sandbox
ради успешного импорта. Реальный scanner smoke остаётся отдельным commissioning gate.

- Команды выполняются администратором с проверенного commit через `sudo`, не сотрудниками и не AI-профилями.
- По умолчанию `bootstrap/deploy/rollback/backup/restore` ничего не меняют; `--apply` означает отдельное явное действие.
- Одновременные операции блокируются `flock` на `/run/lock/smm-gpt-operations.lock`.
- Не запускать `docker compose config` без `--quiet`, `docker inspect` целиком и `env` в чат: они раскрывают credentials. Doctor выбирает только необходимые безопасные поля.
- Ошибки subprocess выводятся без stdout/stderr, где могут быть секреты. При отказе apt/Docker изучать системные логи в приватной админской сессии, не копировать их целиком в Git или чат.
- Scripts никогда автоматически не выполняют prune, очистку backup/data, reboot, reset tailnet или удаление неизвестных releases. Прерванная операция оставляет материалы для диагностики.

## Диагностика

```bash
sudo bash scripts/doctor-server.sh
```

Doctor инвентаризирует ОС, память, диск, listeners, running services, UFW и время, проверяет Docker 28+, Compose, root-only secrets, readiness, 403 для MCP, UID/read-only/health/ports контейнеров, bridge и Tailscale Serve/Funnel. Возвращает ненулевой код при обнаруженной ошибке. Локальная диагностика не доказывает недоступность из интернета, правильность ACL, работоспособность второго SSH и пережитый reboot — это отдельные обязательные проверки.

Нельзя публиковать результат inventory с реальными IP, users или именами узлов в публичном репозитории. В Git хранится только обезличенный итог проверки.

## Резервная копия

```bash
bash scripts/backup.sh
sudo bash scripts/backup.sh --apply
```

Backup временно останавливает web, app, worker и scheduler, оставляя PostgreSQL для `pg_dump -Fc`. Затем сохраняет медиа, server configuration, release manifest, количество строк пользовательских таблиц и schema revision. После этого writers запускаются снова даже при ошибке dump. Нужна доступность предыдущих Docker images. Операция вызывает короткое окно недоступности.

Успех обозначается `backup_ready` и ID вида `YYYYMMDDTHHMMSSZ-<random>`. Только каталог с корректным `complete.json`, контрольными суммами и manifest считается готовой копией. Незавершённые копии не используются для восстановления.

Состав:

| Файл | Содержимое |
|---|---|
| `database.dump` | PostgreSQL logical dump |
| `database-check.txt` | Список/количество строк public tables и migration revision |
| `media.tar` | Только обычные файлы и каталоги медиа, без ссылок и device files |
| `server.env` | Текущие секреты, файл 0600 |
| `release.json` | SHA, image IDs, migration fingerprint |
| `checksums.json`, `complete.json` | Контроль целостности и завершения |

Копия **не зашифрована**, хранится на том же сервере и содержит секрет БД. Она защищает от ошибок обновления, но не от потери сервера или root-компрометации. Не передавать её сотрудникам, не загружать в Git/CI artifacts. Перед реальными данными нужны encryption-at-rest backup с отдельным ключом и проверенная offsite-копия.

Текущая реализация рассчитана на небольшую синтетическую базу: dump и restore читаются в память. Потоковая обработка больших dumps, quotas, автоматический retention и регулярные timer-jobs ещё не реализованы. Следить за свободной RAM/диском; незавершённые копии и drills сохраняются без автоматической очистки.

## Проверка восстановления без воздействия на staging

```bash
bash scripts/restore.sh --backup <backup-ID>
sudo bash scripts/restore.sh --apply --backup <backup-ID>
```

`restore.sh` — **только restore drill**, не команда замены работающей БД. Она:

1. Проверяет ID, права файлов, completion manifest и SHA-256 каждого файла.
2. Распаковывает медиа в новый каталог под `restore-drills`; отказывается от absolute paths, traversal, links и special files.
3. Поднимает временный PostgreSQL без сети (`--network none`), без host ports и с новым data directory.
4. Выполняет `pg_restore --exit-on-error` в новую БД, сверяет все public table counts и migration revision.
5. Сверяет хеши восстановленных медиа и копирует configuration только в drill-каталог.
6. Записывает `result.json` с длительностью и результатом; удаляет только свой временный контейнер. Data/config/media drill сохраняются, активный staging не меняется.

В `result.json` нет паролей и строк постов. Сверка количества строк не заменяет будущую предметную проверку данных и RPO/RTO. Реальная disaster-recovery замена staging требует отдельного разрешения, остановки writers, сохранения повреждённого состояния и новой процедуры cutover; этот скрипт её намеренно не выполняет.

## Code rollback

```bash
bash scripts/rollback.sh --release <previous-full-SHA>
sudo bash scripts/rollback.sh --apply --release <previous-full-SHA>
```

Цель должна иметь сохранённый release manifest и прежние Docker image IDs. Rollback создаёт новый backup текущего состояния, проверяет равенство migration fingerprints и возвращает код предыдущей версии. Он **не откатывает бизнес-данные** и не выполняет `alembic downgrade`. Не удалять данные, чтобы заставить несовместимый rollback пройти.

После отказа deploy `current.json` остаётся у последней успешной версии. Скрипт пытается её запустить; если и это не удалось, состояние считается аварийным. Не повторять развёртывание вслепую, пока не выяснена причина. При failed first deploy остановлены writers, а БД/release сохраняются для диагностики.

## Проверка reboot

В отдельное разрешённое окно: проверить backup, recovery console и второй SSH; выполнить reboot; после возвращения сервера проверить SSH, Docker/systemd guard, private HTTPS, read-only статус MCP, worker smoke и сохранность media/DB. В CI тестируется `compose down` → `up` без удаления bind mounts, но это **не** проверка реальной перезагрузки.

## Минимальный журнал приёмки

Приватный operator report содержит дату, operator identity, server OS, package versions, SHA/image IDs, результаты network/SSH/HTTPS/reboot tests, backup ID, restore drill ID и фактическое время восстановления. Git-отчёт содержит только обезличенный статус: что проверено, что не проверено и почему.
