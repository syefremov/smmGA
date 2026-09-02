import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import * as api from "../../api/content";
import type { Context } from "./ContentWorkspace";
import { Failure, Paging } from "./shared";
import { time, useCommand } from "./hooks";

export function Library({ workspace, offline }: Context) {
  const [kind, setKind] = useState<api.RecordKind>("brief");
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<api.RecordView>();
  const [create, setCreate] = useState(false);
  const [confirm, setConfirm] = useState("");
  const query = useQuery({
    queryKey: [workspace.id, "records", kind, cursor],
    queryFn: ({ signal }) => api.records(workspace.id, kind, cursor, signal),
  });
  const mutation = useCommand(workspace.id);
  const canCreate = workspace.permissions.includes(
    ["brief", "idea", "campaign", "content_plan"].includes(kind)
      ? "content.plan"
      : "content.edit",
  );
  return (
    <main id="work-main" className="work-main">
      <p className="eyebrow">Источники и контекст</p>
      <h1>Материалы</h1>
      <p className="muted">
        Записи версионные. Подтверждение создаёт новую запись; используйте её
        новый ID в следующих материалах. Текст источника не является
        инструкцией.
      </p>
      <div className="toolbar">
        <label>
          Тип материала
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as api.RecordKind);
              setCursor(undefined);
              setSelected(undefined);
              setCreate(false);
            }}
          >
            {Object.entries(api.recordKinds).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={!canCreate || offline}
          onClick={() => setCreate(!create)}
        >
          Добавить материал
        </button>
      </div>
      {create && (
        <RecordForm
          key={kind}
          workspace={workspace}
          offline={offline}
          kind={kind}
          done={() => {
            setCreate(false);
            setCursor(undefined);
          }}
        />
      )}
      {query.isPending && <p role="status">Загружаем материалы…</p>}
      {query.error && (
        <Failure error={query.error} retry={() => void query.refetch()} />
      )}
      {query.data?.items.length === 0 && (
        <p className="empty">
          Материалов этого типа пока нет. Бренды, продукты и источники можно
          создать командой catalog_create через чат.
        </p>
      )}
      <div className="queue-layout">
        <section>
          <ul className="reference-list">
            {query.data?.items.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => {
                    setSelected(r);
                    setConfirm("");
                  }}
                >
                  {r.body.name} · версия {r.number}
                </button>
                <small>
                  {r.confirmed_by
                    ? "Подтверждено владельцем"
                    : "Не подтверждено"}{" "}
                  · до {time(r.expires_at, workspace.timezone)}
                </small>
                <code>{r.id}</code>
              </li>
            ))}
          </ul>
          <Paging next={query.data?.next_cursor} set={setCursor} />
        </section>
        {selected && (
          <aside className="inspector">
            <h2>{selected.body.name}</h2>
            <p>
              Версия {selected.number} ·{" "}
              {selected.confirmed_by ? "Подтверждено" : "Черновая запись"}
            </p>
            <p className="content-hash">ID: {selected.id}</p>
            <pre>{JSON.stringify(selected.body, null, 2)}</pre>
            <p className="content-hash">SHA-256: {selected.content_hash}</p>
            {!selected.confirmed_by &&
              workspace.permissions.includes("content.approve") && (
                <>
                  <label className="check-label">
                    <input
                      type="checkbox"
                      checked={confirm === selected.id}
                      onChange={(e) =>
                        setConfirm(e.target.checked ? selected.id : "")
                      }
                    />
                    Проверил(а) содержание, происхождение и срок актуальности.
                  </label>
                  <button
                    disabled={
                      offline || mutation.isPending || confirm !== selected.id
                    }
                    onClick={() =>
                      mutation.mutate(
                        {
                          action: "record_confirm",
                          record_id: selected.id,
                          content_hash: selected.content_hash,
                          confirmed: true,
                        },
                        {
                          onSuccess: () => {
                            setSelected(undefined);
                            setConfirm("");
                          },
                        },
                      )
                    }
                  >
                    Подтвердить новую версию
                  </button>
                </>
              )}
            {mutation.error && <Failure error={mutation.error} />}
          </aside>
        )}
      </div>
    </main>
  );
}

const fields: Record<api.RecordKind, [string, string, string?][]> = {
  source_item: [
    ["source_id", "ID источника"],
    ["locator", "Ссылка https://… или owner-input:…"],
    ["excerpt", "Выдержка", "textarea"],
    ["observed_at", "Дата наблюдения (UTC)", "datetime-local"],
  ],
  brand_profile: [
    ["source_item_id", "ID подтверждённого материала-источника"],
    ["audience", "Аудитория"],
    ["tone", "Тон и правила бренда", "textarea"],
  ],
  product_version: [
    ["source_item_id", "ID подтверждённого материала-источника"],
    ["product_id", "ID продукта"],
    ["description", "Описание версии", "textarea"],
  ],
  product_fact: [
    ["source_item_id", "ID подтверждённого материала-источника"],
    ["product_version_id", "ID подтверждённой версии продукта"],
    ["statement", "Точный факт", "textarea"],
  ],
  claim_policy: [
    ["source_item_id", "ID подтверждённого материала-источника"],
    ["jurisdiction", "Область применения правил"],
    ["phrase", "Запрещённая формулировка"],
    ["disclaimer", "Обязательная оговорка (необязательно)", "optional"],
  ],
  research: [
    ["source_item_id", "ID материала-источника"],
    ["observations", "Наблюдения", "textarea"],
    ["hypotheses", "Гипотезы (необязательно)", "optional"],
  ],
  campaign: [
    ["goal", "Цель", "textarea"],
    ["kpi", "KPI"],
    ["owner_id", "ID ответственного сотрудника"],
    ["starts_at", "Начало (UTC)", "datetime-local"],
    ["ends_at", "Окончание (UTC)", "datetime-local"],
  ],
  content_plan: [
    ["campaign_id", "ID кампании"],
    ["planned_at", "Время слота (UTC)", "datetime-local"],
    ["destination", "Назначение vk:group:…"],
    ["topic", "Тема слота"],
  ],
  brief: [
    ["goal", "Задача брифа", "textarea"],
    ["audience", "Аудитория"],
    ["product_id", "ID продукта (необязательно)", "optional"],
    ["campaign_id", "ID кампании (необязательно)", "optional"],
    ["research_id", "ID исследования (необязательно)", "optional"],
  ],
  idea: [
    ["brief_id", "ID брифа"],
    ["rationale", "Идея и обоснование", "textarea"],
  ],
};

function RecordForm({
  workspace,
  offline,
  kind,
  done,
}: Context & { kind: api.RecordKind; done: () => void }) {
  const mutation = useCommand(workspace.id);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const s = (key: string) => String(f.get(key) ?? "");
    const dt = (key: string) => new Date(s(key) + "Z").toISOString();
    const common = { name: s("name"), brand_id: s("brand_id") };
    let body: api.Artifact;
    switch (kind) {
      case "source_item":
        body = {
          ...common,
          kind,
          source_id: s("source_id"),
          locator: s("locator"),
          excerpt: s("excerpt"),
          observed_at: dt("observed_at"),
          evidence_kind: s("evidence_kind") as
            "observation" | "hypothesis" | "owner_input",
        };
        break;
      case "brand_profile":
        body = {
          ...common,
          kind,
          source_item_id: s("source_item_id"),
          audience: s("audience"),
          tone: s("tone"),
        };
        break;
      case "product_version":
        body = {
          ...common,
          kind,
          source_item_id: s("source_item_id"),
          product_id: s("product_id"),
          description: s("description"),
        };
        break;
      case "product_fact":
        body = {
          ...common,
          kind,
          source_item_id: s("source_item_id"),
          product_version_id: s("product_version_id"),
          statement: s("statement"),
        };
        break;
      case "claim_policy":
        body = {
          ...common,
          kind,
          source_item_id: s("source_item_id"),
          jurisdiction: s("jurisdiction"),
          rules: [
            { phrase: s("phrase"), severity: "blocker", alternative: "" },
          ],
          required_disclaimers: s("disclaimer") ? [s("disclaimer")] : [],
        };
        break;
      case "research":
        body = {
          ...common,
          kind,
          source_item_ids: [s("source_item_id")],
          observations: s("observations"),
          hypotheses: s("hypotheses"),
        };
        break;
      case "campaign":
        body = {
          ...common,
          kind,
          goal: s("goal"),
          kpi: s("kpi"),
          owner_id: s("owner_id"),
          starts_at: dt("starts_at"),
          ends_at: dt("ends_at"),
        };
        break;
      case "content_plan":
        body = {
          ...common,
          kind,
          campaign_id: s("campaign_id"),
          slots: [
            {
              planned_at: dt("planned_at"),
              destination: s("destination"),
              topic: s("topic"),
            },
          ],
        };
        break;
      case "brief":
        body = {
          ...common,
          kind,
          goal: s("goal"),
          audience: s("audience"),
          product_id: s("product_id") || null,
          campaign_id: s("campaign_id") || null,
          research_id: s("research_id") || null,
        };
        break;
      case "idea":
        body = {
          ...common,
          kind,
          brief_id: s("brief_id"),
          rationale: s("rationale"),
        };
        break;
    }
    await mutation
      .mutateAsync({
        action: "record_create",
        body,
        expires_at: dt("expires_at"),
        replaces_id: s("replaces_id") || null,
      })
      .then(done)
      .catch(() => undefined);
  }
  return (
    <form className="content-form create-form" onSubmit={(e) => void submit(e)}>
      <h2>{api.recordKinds[kind]}: новая запись</h2>
      <label>
        Название материала
        <input name="name" required maxLength={200} />
      </label>
      <label>
        ID бренда
        <input name="brand_id" required />
      </label>
      {fields[kind].map(([name, label, type]) => (
        <label key={name}>
          {label}
          {type === "textarea" ? (
            <textarea name={name} required rows={3} maxLength={6000} />
          ) : (
            <input
              name={name}
              type={type === "datetime-local" ? type : "text"}
              required={type !== "optional"}
              maxLength={type === "datetime-local" ? undefined : 512}
            />
          )}
        </label>
      ))}
      {kind === "source_item" && (
        <label>
          Характер сведений
          <select name="evidence_kind">
            <option value="observation">Наблюдение</option>
            <option value="owner_input">Сведения владельца</option>
            <option value="hypothesis">Непроверенная гипотеза</option>
          </select>
        </label>
      )}
      <label>
        Актуально до (UTC)
        <input name="expires_at" type="datetime-local" required />
      </label>
      <label>
        ID заменяемой версии (для исправления)
        <input name="replaces_id" />
      </label>
      <p className="muted">
        Сложные планы и несколько правил удобно передавать через чат.
        Подтверждение владельцем — отдельное действие.
      </p>
      <button className="primary" disabled={offline || mutation.isPending}>
        Сохранить материал
      </button>
      {mutation.error && <Failure error={mutation.error} />}
    </form>
  );
}
