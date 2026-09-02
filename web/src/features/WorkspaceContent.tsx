import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import {
  createWork,
  listAudit,
  listCatalog,
  listWork,
  states,
  transitionWork,
  type CatalogKind,
  type WorkItem,
  type Workspace,
  type WorkState,
} from "../api/operations";

function Failure({ error, retry }: { error: Error; retry: () => void }) {
  const code = error instanceof ApiError ? error.code : "offline";
  const messages: Record<string, string> = {
    version_conflict:
      "Коллега уже изменил задачу. Перечитайте её перед новым действием.",
    invalid_transition: "Этот переход состояния недоступен.",
    access_denied: "Недостаточно прав для этого действия.",
    idempotency_conflict: "Этот ключ уже использован для другого запроса.",
    invalid_request: "Проверьте заполненные поля.",
  };
  return (
    <div className="inline-error" role="alert">
      <p>
        {messages[code] ??
          "Не удалось связаться с сервером. Проверьте подключение."}
      </p>
      {error instanceof ApiError && error.correlation && (
        <small>Код обращения: {error.correlation}</small>
      )}
      <button onClick={retry}>Обновить данные</button>
    </div>
  );
}

const titles: Record<string, string> = {
  work: "Задачи",
  brands: "Бренды",
  products: "Продукты",
  sources: "Источники",
  audit: "Аудит",
};
export function WorkspaceContent({
  workspace,
  section,
  offline,
}: {
  workspace: Workspace;
  section: string;
  offline: boolean;
}) {
  if (!titles[section])
    return (
      <main id="work-main" className="work-main">
        <h1>Раздел не найден</h1>
      </main>
    );
  if (section === "work")
    return <WorkQueue workspace={workspace} offline={offline} />;
  if (section === "audit" && !workspace.permissions.includes("audit.read"))
    return (
      <main id="work-main" className="work-main">
        <h1>Аудит</h1>
        <p role="alert">
          Недостаточно прав. Аудит доступен владельцу и администратору.
        </p>
      </main>
    );
  return <ReferenceList workspace={workspace} section={section} />;
}

function ReferenceList({
  workspace,
  section,
}: {
  workspace: Workspace;
  section: string;
}) {
  const [cursor, setCursor] = useState<string>();
  const query = useQuery({
    queryKey: [workspace.id, section, cursor],
    queryFn: async ({ signal }) =>
      section === "audit"
        ? listAudit(workspace.id, cursor, signal)
        : listCatalog(workspace.id, section as CatalogKind, cursor, signal),
  });
  return (
    <main id="work-main" className="work-main">
      <p className="eyebrow">Общая база</p>
      <h1>{titles[section]}</h1>
      <p className="muted">
        {section === "audit"
          ? "Подтверждённые сервером действия. Содержимое задач и секреты сюда не попадают."
          : "Справочные записи. Наполнение и проверка источников — следующий этап."}
      </p>
      {query.isPending ? (
        <p role="status">Загружаем…</p>
      ) : query.error ? (
        <Failure error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          <ul className="reference-list">
            {query.data.items.map((item) => (
              <li key={item.id}>
                {"name" in item ? (
                  item.name
                ) : (
                  <>
                    <strong>{item.action}</strong> · {item.outcome}
                    <small>
                      {new Date(item.created_at).toLocaleString("ru-RU", {
                        timeZone: workspace.timezone,
                      })}
                    </small>
                  </>
                )}
              </li>
            ))}
          </ul>
          {!query.data.items.length && (
            <p className="empty">Записей пока нет.</p>
          )}
          <Pagination
            cursor={cursor}
            next={query.data.next_cursor}
            change={setCursor}
          />
        </>
      )}
    </main>
  );
}

function Pagination({
  cursor,
  next,
  change,
}: {
  cursor?: string;
  next?: string | null;
  change: (value: string | undefined) => void;
}) {
  return (
    <div className="pagination">
      {cursor && <button onClick={() => change(undefined)}>В начало</button>}
      {next && (
        <button onClick={() => change(next)}>Следующая страница →</button>
      )}
    </div>
  );
}

function WorkQueue({
  workspace,
  offline,
}: {
  workspace: Workspace;
  offline: boolean;
}) {
  const cache = useQueryClient();
  const [filter, setFilter] = useState<WorkState>();
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<WorkItem | null>(null);
  const [creating, setCreating] = useState(false);
  const canWrite = workspace.permissions.includes("work_item.write");
  const query = useQuery({
    queryKey: [workspace.id, "work", filter, cursor],
    queryFn: ({ signal }) => listWork(workspace.id, filter, cursor, signal),
    refetchInterval: 10_000,
  });
  async function refresh() {
    setSelected(null);
    await cache.invalidateQueries({ queryKey: [workspace.id, "work"] });
  }
  return (
    <main id="work-main" className="work-main">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Операционная очередь</p>
          <h1>Задачи</h1>
          <p className="muted">Общие с чатом. Сохранены на сервере.</p>
        </div>
        <button
          className="primary"
          disabled={!canWrite || offline}
          onClick={() => {
            setSelected(null);
            setCreating(true);
          }}
        >
          Создать задачу
        </button>
      </div>
      {!canWrite && (
        <p className="muted">У вас доступ только для чтения задач.</p>
      )}
      <div className="toolbar">
        <label>
          Состояние{" "}
          <select
            value={filter ?? ""}
            onChange={(e) => {
              setFilter((e.target.value || undefined) as WorkState | undefined);
              setCursor(undefined);
              setSelected(null);
            }}
          >
            <option value="">Все задачи</option>
            {Object.entries(states).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button disabled={query.isFetching} onClick={() => void refresh()}>
          {query.isFetching ? "Обновляем…" : "Обновить"}
        </button>
      </div>
      <div className="queue-layout">
        <section aria-label="Список задач">
          {query.isPending ? (
            <p role="status">Загружаем задачи…</p>
          ) : query.error ? (
            <Failure error={query.error} retry={() => void refresh()} />
          ) : (
            <>
              {!query.data.items.length ? (
                <div className="empty">
                  <h2>
                    {filter
                      ? "Нет задач в этом состоянии"
                      : "Начните с первой задачи"}
                  </h2>
                  <p>
                    Например, изучить источники или подготовить план контента.
                    Задача не публикует посты.
                  </p>
                </div>
              ) : (
                <table className="work-table">
                  <thead>
                    <tr>
                      <th>Задача</th>
                      <th>Состояние</th>
                      <th>Версия</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.data.items.map((item) => (
                      <tr
                        key={item.id}
                        data-selected={selected?.id === item.id}
                      >
                        <td>
                          <button
                            onClick={() => {
                              setCreating(false);
                              setSelected(item);
                            }}
                          >
                            {item.title}
                          </button>
                        </td>
                        <td>
                          <span className={`work-state ${item.state}`}>
                            {states[item.state]}
                          </span>
                        </td>
                        <td>{item.version}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <Pagination
                cursor={cursor}
                next={query.data.next_cursor}
                change={(value) => {
                  setCursor(value);
                  setSelected(null);
                }}
              />
            </>
          )}
        </section>
        <aside className="inspector" aria-label="Детали задачи">
          {creating ? (
            <CreateForm
              key="create"
              wid={workspace.id}
              offline={offline}
              close={() => setCreating(false)}
              done={(item) => {
                setCreating(false);
                setSelected(item);
                void cache.invalidateQueries({
                  queryKey: [workspace.id, "work"],
                });
              }}
            />
          ) : selected ? (
            <Detail
              key={`${selected.id}:${selected.version}`}
              item={selected}
              canWrite={canWrite && !offline}
              close={() => setSelected(null)}
              refresh={() => void refresh()}
              done={(item) => {
                setSelected(item);
                void cache.invalidateQueries({
                  queryKey: [workspace.id, "work"],
                });
              }}
            />
          ) : (
            <>
              <p className="eyebrow">Контекст</p>
              <h2>Одна очередь для команды</h2>
              <p>
                Выберите задачу, чтобы увидеть описание и изменить состояние.
              </p>
              <hr />
              <p className="muted">
                В чате можно попросить: «Покажи открытые задачи». Здесь появятся
                те же записи.
              </p>
            </>
          )}
        </aside>
      </div>
    </main>
  );
}

function CreateForm({
  wid,
  offline,
  close,
  done,
}: {
  wid: string;
  offline: boolean;
  close: () => void;
  done: (item: WorkItem) => void;
}) {
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const mutation = useMutation({
    mutationFn: () => createWork(wid, { title, brief, idempotency_key: key }),
    onSuccess: done,
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (title.trim()) mutation.mutate();
  }
  // Preserve an uncertain request's exact payload and key until it is reconciled.
  const rejected =
    mutation.error instanceof ApiError &&
    [400, 422].includes(mutation.error.status);
  const frozen = mutation.isPending || (mutation.isError && !rejected);
  useEffect(() => {
    if (!frozen) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [frozen]);
  return (
    <form onSubmit={submit}>
      <div className="inspector-heading">
        <h2>Новая задача</h2>
        <button type="button" disabled={frozen} onClick={close}>
          Закрыть
        </button>
      </div>
      <label htmlFor="task-title">Название</label>
      <input
        id="task-title"
        required
        maxLength={200}
        value={title}
        disabled={frozen}
        onChange={(e) => {
          setTitle(e.target.value);
          setKey(crypto.randomUUID());
          mutation.reset();
        }}
      />
      <label htmlFor="task-brief">Описание</label>
      <textarea
        id="task-brief"
        rows={6}
        maxLength={2000}
        value={brief}
        disabled={frozen}
        onChange={(e) => {
          setBrief(e.target.value);
          setKey(crypto.randomUUID());
          mutation.reset();
        }}
      />
      <p className="muted">Не указывайте пароли и ключи доступа.</p>
      {mutation.error && (
        <p role="alert">
          {rejected
            ? "Проверьте поля: сервер отклонил запрос."
            : "Ответ не подтверждён. Повторите тот же запрос: дубль не создастся."}
        </p>
      )}
      <button
        className="primary"
        disabled={offline || mutation.isPending || !title.trim()}
      >
        {mutation.isPending
          ? "Сохраняем…"
          : mutation.isError
            ? "Повторить сохранение"
            : "Сохранить задачу"}
      </button>
    </form>
  );
}

function Detail({
  item,
  canWrite,
  close,
  refresh,
  done,
}: {
  item: WorkItem;
  canWrite: boolean;
  close: () => void;
  refresh: () => void;
  done: (item: WorkItem) => void;
}) {
  const mutation = useMutation({
    mutationFn: (state: WorkState) =>
      transitionWork(item.workspace_id, item, state),
    onSuccess: done,
  });
  return (
    <>
      <div className="inspector-heading">
        <p className="eyebrow">Версия {item.version}</p>
        <button onClick={close}>Закрыть</button>
      </div>
      <h2>{item.title}</h2>
      <p className="brief">{item.brief || "Описание не добавлено."}</p>
      <p>{states[item.state]}</p>
      {mutation.error && <Failure error={mutation.error} retry={refresh} />}
      {canWrite && (
        <div className="actions">
          <label htmlFor="new-state">Новое состояние</label>
          <select
            id="new-state"
            defaultValue=""
            disabled={mutation.isPending || mutation.isError}
            onChange={(e) => {
              if (e.target.value) mutation.mutate(e.target.value as WorkState);
            }}
          >
            <option value="" disabled>
              Выберите действие
            </option>
            {item.allowed_transitions.map((key) => (
              <option key={key} value={key}>
                {states[key]}
              </option>
            ))}
          </select>
          <small>Переход проверяет сервер. Публикаций не будет.</small>
        </div>
      )}
    </>
  );
}
