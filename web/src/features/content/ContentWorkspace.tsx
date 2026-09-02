import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import * as api from "../../api/content";
import type { Workspace } from "../../api/operations";
import { EditorPanel } from "./Editor";
import { Library } from "./Library";
import { Calendar } from "./Calendar";
import { Failure, Paging } from "./shared";
import { useCommand } from "./hooks";
import "./content.css";

export type Context = { workspace: Workspace; offline: boolean };
export function ContentWorkspace({
  workspace,
  offline,
  section,
}: Context & { section: string }) {
  if (section === "materials")
    return <Library workspace={workspace} offline={offline} />;
  if (section === "calendar")
    return <Calendar workspace={workspace} offline={offline} />;
  return <ContentQueue workspace={workspace} offline={offline} />;
}

function ContentQueue({ workspace, offline }: Context) {
  const cache = useQueryClient();
  const dirtyRef = useRef(false);
  function mayLeave() {
    return (
      !dirtyRef.current ||
      window.confirm(
        "Есть несохранённый текст. Перейти и потерять его? Сначала можно сохранить рабочую копию.",
      )
    );
  }
  const [state, setState] = useState<api.PostState>();
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string | null>(
    new URLSearchParams(location.search).get("post"),
  );
  const [creating, setCreating] = useState(false);
  const query = useQuery({
    queryKey: [workspace.id, "posts", state, cursor],
    queryFn: ({ signal }) => api.posts(workspace.id, state, cursor, signal),
  });
  const mutation = useCommand(workspace.id);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!mayLeave()) return;
    const form = new FormData(event.currentTarget);
    const result = await mutation
      .mutateAsync({
        action: "post_create",
        title: String(form.get("title")),
        brief_id: String(form.get("brief")),
        idea_id: String(form.get("idea")) || null,
      })
      .catch(() => null);
    if (result) {
      setSelected(result.entity_id);
      setCreating(false);
    }
  }
  return (
    <main id="work-main" className="work-main content-main">
      <p className="eyebrow">Редакционная работа</p>
      <div className="page-heading">
        <div>
          <h1>Контент</h1>
          <p className="muted">
            От идеи к проверенной редакции. Публикация — только вручную.
          </p>
        </div>
        <button
          className="primary"
          disabled={offline || !workspace.permissions.includes("content.edit")}
          onClick={() => setCreating(!creating)}
        >
          Новый пост
        </button>
      </div>
      {creating && (
        <form
          className="content-form create-form"
          onSubmit={(e) => void create(e)}
        >
          <h2>Пост из брифа</h2>
          <label>
            Название поста
            <input name="title" required maxLength={200} />
          </label>
          <label>
            ID брифа
            <input name="brief" required placeholder="Из раздела «Материалы»" />
          </label>
          <label>
            ID идеи (необязательно)
            <input name="idea" />
          </label>
          <button className="primary" disabled={offline || mutation.isPending}>
            Создать пост
          </button>
          {mutation.error && <Failure error={mutation.error} />}
        </form>
      )}
      <div className="toolbar">
        <label>
          Состояние
          <select
            value={state ?? ""}
            onChange={(e) => {
              setState((e.target.value as api.PostState) || undefined);
              setCursor(undefined);
            }}
          >
            <option value="">Все редакции</option>
            {Object.entries(api.postStates).map(([key, title]) => (
              <option key={key} value={key}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={() =>
            void cache.invalidateQueries({ queryKey: [workspace.id] })
          }
        >
          Обновить
        </button>
      </div>
      {query.isPending && <p role="status">Загружаем контент…</p>}
      {query.error && (
        <Failure error={query.error} retry={() => void query.refetch()} />
      )}
      <div className="content-layout">
        <section className="post-list" aria-label="Очередь контента">
          {query.data?.items.length === 0 && (
            <div className="empty">
              <h2>Пока нет постов</h2>
              <p>
                Сначала сохраните бриф и идею через чат или раздел «Материалы».
              </p>
            </div>
          )}
          {query.data?.items.map((p) => (
            <button
              key={p.id}
              className="post-row"
              aria-pressed={selected === p.id}
              onClick={() => {
                if (selected === p.id || mayLeave()) setSelected(p.id);
              }}
            >
              <strong>{p.title}</strong>
              <span>
                {api.postStates[p.state]} · v{p.version}
              </span>
            </button>
          ))}
          <Paging next={query.data?.next_cursor} set={setCursor} />
        </section>
        {selected ? (
          <EditorPanel
            key={selected}
            workspace={workspace}
            offline={offline}
            postId={selected}
            dirtyRef={dirtyRef}
          />
        ) : (
          <div className="empty">
            <h2>Выберите пост</h2>
            <p>Здесь будут текст, источники, замечания и история решений.</p>
          </div>
        )}
      </div>
    </main>
  );
}
