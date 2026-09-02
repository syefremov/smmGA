import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import * as api from "../../api/content";
import type { Context } from "./ContentWorkspace";
import { Failure, Paging } from "./shared";
import { time, useCommand } from "./hooks";

const statuses: Record<api.Package["status"], string> = {
  active: "Готов к ручной передаче",
  cancelled: "Отменён",
  stale: "Устарел — не использовать",
  expired: "Время прошло — перепроверьте",
};
export function Calendar({ workspace, offline }: Context) {
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const query = useQuery({
    queryKey: [workspace.id, "packages", cursor],
    queryFn: ({ signal }) => api.packages(workspace.id, cursor, signal),
  });
  const packages = [...(query.data?.items ?? [])].sort((a, b) =>
    a.scheduled_at.localeCompare(b.scheduled_at),
  );
  return (
    <main id="work-main" className="work-main">
      <p className="eyebrow">Ручная подготовка</p>
      <h1>Календарь</h1>
      <p className="muted">
        Время показано в {workspace.timezone}. Эти пакеты не отправляются в
        соцсеть. Страница — часть списка; остальные пакеты доступны по кнопке
        «Далее».
      </p>
      <button onClick={() => void query.refetch()}>Обновить календарь</button>
      {query.isPending && <p role="status">Загружаем пакеты…</p>}
      {query.error && (
        <Failure error={query.error} retry={() => void query.refetch()} />
      )}
      {query.data?.items.length === 0 && (
        <div className="empty">
          <h2>Пакетов пока нет</h2>
          <p>
            После одобрения редакции задайте время ручной подготовки в
            редакторе.
          </p>
        </div>
      )}
      <ol className="calendar-list">
        {packages.map((p) => (
          <li key={p.id}>
            <time dateTime={p.scheduled_at}>
              {time(p.scheduled_at, workspace.timezone)}
            </time>
            <div>
              <strong>{statuses[p.status]}</strong>
              <p className="muted">
                Пост {p.post_id.slice(0, 8)} · SHA {p.content_hash.slice(0, 12)}
              </p>
            </div>
            <button onClick={() => setSelected(p.id)}>Открыть пакет</button>
          </li>
        ))}
      </ol>
      <Paging next={query.data?.next_cursor} set={setCursor} />
      {selected && (
        <PackageDetail
          key={selected}
          workspace={workspace}
          offline={offline}
          id={selected}
        />
      )}
    </main>
  );
}
function PackageDetail({ workspace, offline, id }: Context & { id: string }) {
  const query = useQuery({
    queryKey: [workspace.id, "package", id],
    queryFn: ({ signal }) => api.packageRead(workspace.id, id, signal),
  });
  const post = useQuery({
    queryKey: [workspace.id, "post", query.data?.post_id],
    queryFn: ({ signal }) =>
      api.post(workspace.id, query.data!.post_id, signal),
    enabled: !!query.data,
  });
  const mutation = useCommand(workspace.id);
  const [confirmed, setConfirmed] = useState(false);
  if (query.error)
    return <Failure error={query.error} retry={() => void query.refetch()} />;
  if (!query.data) return <p role="status">Перепроверяем пакет…</p>;
  const p = query.data;
  return (
    <section className="package-detail">
      <h2>Публикационный пакет</h2>
      <p>{statuses[p.status]}</p>
      <p>
        Это снимок текста и метаданных, а не подтверждение публикации. Файлы и
        права на них проверяются отдельно перед ручной отправкой.
      </p>
      <a href={`/app/content?workspace=${workspace.id}&post=${p.post_id}`}>
        Открыть пост
      </a>
      <details>
        <summary>Точный манифест</summary>
        <pre>{JSON.stringify(p.manifest, null, 2)}</pre>
      </details>
      {post.error && (
        <Failure error={post.error} retry={() => void post.refetch()} />
      )}
      {workspace.permissions.includes("content.publish") &&
        p.status !== "cancelled" && (
          <>
            <label className="check-label">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              Отменить этот внутренний пакет (не удаляет посты в VK).
            </label>
            <button
              disabled={
                offline ||
                !confirmed ||
                !post.data ||
                !!post.error ||
                mutation.isPending
              }
              onClick={() =>
                post.data &&
                mutation.mutate({
                  action: "package_cancel",
                  package_id: p.id,
                  expected_version: post.data.version,
                })
              }
            >
              Отменить пакет
            </button>
          </>
        )}
      {mutation.error && (
        <Failure error={mutation.error} retry={() => void post.refetch()} />
      )}
    </section>
  );
}
