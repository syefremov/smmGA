import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import * as api from "../../api/content";
import type { Context } from "./ContentWorkspace";
import { Failure } from "./shared";
import { time, useCommand } from "./hooks";
import { findingLabels } from "./findings";

const schema = z.object({
  variants: z
    .array(
      z.object({
        destination: z.string().min(1),
        text: z.string().min(1).max(10000),
      }),
    )
    .min(1)
    .max(3),
  facts: z.string(),
  gaps: z.string(),
});
type Form = z.infer<typeof schema>;
const lines = (value: string) =>
  value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

export function EditorPanel({
  workspace,
  offline,
  postId,
  dirtyRef,
}: Context & { postId: string; dirtyRef: { current: boolean } }) {
  const edit = workspace.permissions.includes("content.edit");
  const post = useQuery({
    queryKey: [workspace.id, "post", postId],
    queryFn: ({ signal }) => api.post(workspace.id, postId, signal),
  });
  const copy = useQuery({
    queryKey: [workspace.id, "working-copy", postId],
    queryFn: ({ signal }) => api.workingCopy(workspace.id, postId, signal),
    enabled: edit,
  });
  const failure = post.error || copy.error;
  if (failure && (!post.data || (edit && copy.data === undefined)))
    return (
      <Failure
        error={(post.error || copy.error)!}
        retry={() => {
          void post.refetch();
          if (edit) void copy.refetch();
        }}
      />
    );
  if (!post.data || (edit && copy.isPending))
    return <p role="status">Читаем редакцию…</p>;
  return (
    <>
      {failure && (
        <Failure
          error={failure}
          retry={() => {
            void post.refetch();
            if (edit) void copy.refetch();
          }}
        />
      )}
      <Editor
        workspace={workspace}
        offline={offline || !!failure}
        post={post.data}
        copy={copy.data ?? null}
        refresh={() => void post.refetch()}
        dirtyRef={dirtyRef}
      />
    </>
  );
}

function Editor({
  workspace,
  offline,
  post,
  copy,
  refresh,
  dirtyRef,
}: Context & {
  post: api.Post;
  copy: api.Copy | null;
  refresh: () => void;
  dirtyRef: { current: boolean };
}) {
  const current = post.revisions[0];
  const [initial] = useState(copy?.body ?? current?.body);
  const [base, setBase] = useState(copy?.base_version ?? post.version);
  const [copyVersion, setCopyVersion] = useState(copy?.version ?? 0);
  const [notice, setNotice] = useState(
    copy ? "Загружена ваша рабочая копия с сервера." : "",
  );
  const [compare, setCompare] = useState(1);
  const [confirmedFor, setConfirmedFor] = useState("");
  const [reason, setReason] = useState("");
  const [schedule, setSchedule] = useState("");
  const confirmationKey = `${post.version}:${current?.content_hash}:${schedule}`;
  const confirmation = confirmedFor === confirmationKey;
  const setConfirmation = (checked: boolean) =>
    setConfirmedFor(checked ? confirmationKey : "");
  const mutation = useCommand(workspace.id);
  const edit =
    workspace.permissions.includes("content.edit") &&
    !initial?.variants.some((v) => v.media?.length);
  const disabled = offline || mutation.isPending;
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: {
      variants: initial?.variants.map((v) => ({
        destination: v.destination,
        text: v.text,
      })) ?? [{ destination: "", text: "" }],
      facts: initial?.fact_ids?.join("\n") ?? "",
      gaps: initial?.knowledge_gaps?.join("\n") ?? "",
    },
  });
  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "variants",
  });
  const shown = useWatch({ control: form.control });
  useEffect(() => {
    dirtyRef.current = form.formState.isDirty;
    if (!form.formState.isDirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      dirtyRef.current = false;
      window.removeEventListener("beforeunload", warn);
    };
  }, [dirtyRef, form.formState.isDirty]);
  const check = useQuery({
    queryKey: [workspace.id, "preflight", post.id, post.version],
    queryFn: ({ signal }) => api.preflight(workspace.id, post.id, signal),
    enabled: !!current,
  });
  const contextChanged = base !== post.version;
  function body(value: Form): api.Revision {
    // Retain media on unchanged destinations; text-only editor cannot silently discard attachments.
    return {
      variants: value.variants.map((v) => ({
        platform: "vk",
        ...v,
        media:
          initial?.variants.find((old) => old.destination === v.destination)
            ?.media ?? [],
      })),
      fact_ids: lines(value.facts),
      knowledge_gaps: lines(value.gaps),
    };
  }
  async function save(value: Form, working: boolean) {
    const revision = body(value);
    const result = await mutation
      .mutateAsync(
        working
          ? {
              action: "working_copy_save",
              post_id: post.id,
              expected_copy_version: copyVersion,
              base_version: base,
              body: revision,
            }
          : {
              action: "revision_save",
              post_id: post.id,
              expected_version: base,
              body: revision,
            },
      )
      .catch(() => null);
    if (!result) return;
    if (working) {
      setCopyVersion(result.version);
      setNotice("Рабочая копия сохранена на сервере на 7 дней.");
    } else {
      setBase(result.version);
      setCopyVersion(0);
      setNotice("Новая редакция сохранена. Предыдущее одобрение снято.");
    }
    form.reset(value);
    setConfirmation(false);
  }
  async function action(input: api.Input) {
    const result = await mutation.mutateAsync(input).catch(() => null);
    if (result) {
      setNotice("Действие подтверждено сервером.");
      setBase(result.version);
      setConfirmation(false);
    }
  }
  const matchesStored =
    JSON.stringify(shown.variants) ===
      JSON.stringify(
        current?.body.variants.map((v) => ({
          destination: v.destination,
          text: v.text,
        })),
      ) &&
    JSON.stringify(lines(shown.facts ?? "")) ===
      JSON.stringify(current?.body.fact_ids ?? []) &&
    JSON.stringify(lines(shown.gaps ?? "")) ===
      JSON.stringify(current?.body.knowledge_gaps ?? []);
  const decisionReady =
    !!current && matchesStored && !form.formState.isDirty && !disabled;
  return (
    <section className="editor" aria-label="Редактор поста">
      <div className="page-heading">
        <h2>{post.title}</h2>
        <span className="work-state">
          {api.postStates[post.state]} · v{post.version}
        </span>
      </div>
      <p className="muted">
        Редакция {current?.number ?? "ещё не создана"} ·{" "}
        {current && time(current.created_at, workspace.timezone)}
      </p>
      {notice && (
        <p role="status" className="save-status">
          {notice}
        </p>
      )}
      {!matchesStored && current && (
        <p className="banner">
          На экране рабочий текст, отличный от сохранённой редакции. Сохраните
          редакцию перед проверкой или решением.
        </p>
      )}
      {mutation.error && <Failure error={mutation.error} retry={refresh} />}
      {contextChanged && edit && (
        <div className="banner">
          <p>
            Серверная версия v{post.version}, ваша основа v{base}. Сверьте текст
            ниже. Рабочую копию сохранить можно; редакцию — после сверки.
          </p>
          <details>
            <summary>Текущий текст сервера</summary>
            {current?.body.variants.map((v) => (
              <pre key={v.destination}>
                {v.destination}
                {"\n"}
                {v.text}
              </pre>
            ))}
          </details>
          <button
            onClick={() => {
              setBase(post.version);
              setConfirmation(false);
            }}
          >
            Сверено: использовать v{post.version} как основу
          </button>
        </div>
      )}
      <form
        className="content-form"
        onSubmit={form.handleSubmit((value) => save(value, false))}
      >
        <fieldset disabled={!edit || disabled}>
          {fields.map((field, i) => (
            <div className="variant" key={field.id}>
              <label>
                Назначение {i + 1}
                <input
                  {...form.register(`variants.${i}.destination`)}
                  placeholder="vk:group:123"
                  readOnly={!!initial?.variants[i]?.media?.length}
                />
              </label>
              <label>
                Текст {i + 1}
                <textarea rows={8} {...form.register(`variants.${i}.text`)} />
              </label>
              {!!initial?.variants[i]?.media?.length && (
                <p className="muted">
                  Вложения сохранены в снимке. Изменяйте их через чат после
                  проверки прав.
                </p>
              )}
              {fields.length > 1 && (
                <button
                  type="button"
                  disabled={!!initial?.variants[i]?.media?.length}
                  onClick={() => remove(i)}
                >
                  Убрать вариант {i + 1}
                </button>
              )}
            </div>
          ))}
          {fields.length < 3 && (
            <button
              type="button"
              onClick={() => append({ destination: "", text: "" })}
            >
              Добавить назначение
            </button>
          )}
          <details>
            <summary>Факты и пробелы в данных</summary>
            <label>
              ID подтверждённых фактов, по одному на строку
              <textarea rows={2} {...form.register("facts")} />
            </label>
            <label>
              Что ещё нужно выяснить, по одному пункту на строку
              <textarea rows={2} {...form.register("gaps")} />
            </label>
          </details>
        </fieldset>
        {Object.keys(form.formState.errors).length > 0 && (
          <p role="alert">
            Заполните назначения и текст каждого варианта (до 10 000 символов).
          </p>
        )}
        {edit && (
          <div className="actions">
            <button className="primary" disabled={disabled || contextChanged}>
              Сохранить редакцию
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={form.handleSubmit((value) => save(value, true))}
            >
              Сохранить рабочую копию
            </button>
          </div>
        )}
      </form>
      <section className="review-section">
        <h3>Проверка редакции</h3>
        <p className="muted">
          Проверки правил и источников. ИИ-проверка не запускалась; содержание
          проверяет человек.
        </p>
        {check.isFetching && current && (
          <p role="status">Проверяем источники…</p>
        )}
        {check.error && (
          <Failure error={check.error} retry={() => void check.refetch()} />
        )}
        {check.data && (
          <>
            <p>
              {check.data.passed
                ? "Блокирующих замечаний нет"
                : "Есть блокирующие замечания"}
            </p>
            <ul className="findings">
              {check.data.findings.map((f, i) => (
                <li key={i}>
                  <span>
                    {f.severity === "blocker" ? "Блокер" : "Внимание"}
                  </span>{" "}
                  · {findingLabels[f.code] ?? f.code}{" "}
                  <small>{f.location}</small>
                </li>
              ))}
            </ul>
          </>
        )}
        {edit && (post.state === "draft" || post.state === "rejected") && (
          <button
            disabled={!decisionReady}
            onClick={() =>
              void action({
                action: "review_request",
                post_id: post.id,
                expected_version: post.version,
              })
            }
          >
            Передать на проверку
          </button>
        )}
        {current && (
          <p className="content-hash">SHA-256: {current.content_hash}</p>
        )}
        {current &&
          post.state === "in_review" &&
          workspace.permissions.includes("content.approve") && (
            <div className="content-form">
              <label>
                Решение владельца: основание
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={200}
                />
              </label>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={confirmation}
                  onChange={(e) => setConfirmation(e.target.checked)}
                />
                Я проверил(а) сохранённую редакцию №{current.number}, все
                назначения, факты и предупреждения.
              </label>
              <div className="actions">
                <button
                  className="primary"
                  disabled={
                    !decisionReady ||
                    !confirmation ||
                    !reason.trim() ||
                    !check.data?.passed ||
                    check.isFetching ||
                    !!check.error
                  }
                  onClick={() =>
                    void action({
                      action: "post_decide",
                      post_id: post.id,
                      expected_version: post.version,
                      revision_id: current.id,
                      content_hash: current.content_hash,
                      decision: "approve",
                      reason,
                      human_confirmed: true,
                      claims_reviewed: true,
                    })
                  }
                >
                  Одобрить редакцию №{current.number}
                </button>
                <button
                  disabled={!decisionReady || !confirmation || !reason.trim()}
                  onClick={() =>
                    void action({
                      action: "post_decide",
                      post_id: post.id,
                      expected_version: post.version,
                      revision_id: current.id,
                      content_hash: current.content_hash,
                      decision: "reject",
                      reason,
                      human_confirmed: true,
                      claims_reviewed: true,
                    })
                  }
                >
                  На доработку
                </button>
              </div>
            </div>
          )}
        {current &&
          post.state === "approved" &&
          workspace.permissions.includes("content.publish") && (
            <div className="content-form">
              <label>
                Время ручной подготовки (UTC)
                <input
                  type="datetime-local"
                  value={schedule}
                  onChange={(e) => setSchedule(e.target.value)}
                />
              </label>
              <p className="muted">
                В пространстве:{" "}
                {schedule && Number.isFinite(Date.parse(schedule + "Z"))
                  ? time(schedule + "Z", workspace.timezone)
                  : "—"}
                . Отправки в VK не будет.
              </p>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={confirmation}
                  onChange={(e) => setConfirmation(e.target.checked)}
                />
                Подтверждаю время и точную одобренную редакцию.
              </label>
              <button
                className="primary"
                disabled={!decisionReady || !confirmation || !schedule}
                onClick={() =>
                  void action({
                    action: "package_prepare",
                    post_id: post.id,
                    expected_version: post.version,
                    revision_id: current.id,
                    content_hash: current.content_hash,
                    scheduled_at: new Date(schedule + "Z").toISOString(),
                    human_confirmed: true,
                  })
                }
              >
                Подготовить ручной пакет
              </button>
            </div>
          )}
      </section>
      {current && (
        <section>
          <h3>Комментарии</h3>
          {post.comments.map((c) => (
            <div className="history-item" key={c.id}>
              <p>{c.text}</p>
              <small>
                {time(c.created_at, workspace.timezone)} · редакция{" "}
                {c.revision_id.slice(0, 8)}
              </small>
            </div>
          ))}
          {workspace.permissions.includes("content.comment") && (
            <form
              className="content-form"
              onSubmit={(e) => {
                e.preventDefault();
                const target = e.currentTarget;
                const value = String(new FormData(target).get("comment"));
                void mutation
                  .mutateAsync({
                    action: "comment_add",
                    post_id: post.id,
                    revision_id: current.id,
                    text: value,
                  })
                  .then(() => target.reset())
                  .catch(() => undefined);
              }}
            >
              <label>
                Новый комментарий
                <textarea name="comment" required maxLength={2000} rows={2} />
              </label>
              <button disabled={disabled}>Добавить комментарий</button>
            </form>
          )}
        </section>
      )}
      <details className="revision-history">
        <summary>Редакции и решения ({post.revisions.length})</summary>
        {post.revisions.length > 1 && (
          <>
            <label>
              Сравнить с редакцией
              <select
                value={compare}
                onChange={(e) => setCompare(Number(e.target.value))}
              >
                {post.revisions.slice(1).map((r, i) => (
                  <option value={i + 1} key={r.id}>
                    №{r.number}
                  </option>
                ))}
              </select>
            </label>
            <div className="revision-compare">
              <section>
                <h4>Было</h4>
                <pre>
                  {JSON.stringify(post.revisions[compare]?.body, null, 2)}
                </pre>
              </section>
              <section>
                <h4>Сейчас · №{current?.number}</h4>
                <pre>{JSON.stringify(current?.body, null, 2)}</pre>
              </section>
            </div>
          </>
        )}
        {post.decisions.map((d) => (
          <p key={d.id}>
            {d.decision === "approve" ? "Одобрено" : "На доработку"} ·{" "}
            {d.reason} · {time(d.created_at, workspace.timezone)}
          </p>
        ))}
        {post.history_truncated && (
          <p>
            Показаны последние 10 редакций и 20 решений/комментариев. Полная
            история доступна через чат, командой content_history.
          </p>
        )}
      </details>
    </section>
  );
}
