import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useState } from "react";
import * as api from "../api/knowledge";
import { listCatalog, type Workspace } from "../api/operations";
import { Failure, Paging } from "./content/shared";
import { time } from "./content/hooks";
import "./knowledge.css";

const RetrievalEvaluations = lazy(() =>
  import("./RetrievalEvaluations").then((module) => ({
    default: module.RetrievalEvaluations,
  })),
);
const KnowledgeFiles = lazy(() =>
  import("./knowledge/KnowledgeFiles").then((module) => ({
    default: module.KnowledgeFiles,
  })),
);

const states: Record<string, string> = {
  queued: "В очереди",
  processing: "Обработка",
  ready: "Готов к проверке",
  failed: "Ошибка",
  blocked: "Не подключён",
  testing: "Тестирование",
  running: "Выполняется",
  unknown: "Результат неизвестен",
  needs_review: "Нужна проверка",
};

export function KnowledgeWorkspace({
  workspace,
  offline,
}: {
  workspace: Workspace;
  offline: boolean;
}) {
  const [tab, setTab] = useState<
    "search" | "documents" | "files" | "profiles" | "notes" | "evaluations"
  >("search");
  return (
    <main id="work-main" className="work-main knowledge-workspace">
      <header>
        <p className="eyebrow">Источники и проверка</p>
        <h1>База знаний</h1>
        <p>
          Поиск по действующим источникам. Факты продукта и согласования
          хранятся отдельно.
        </p>
      </header>
      <nav className="knowledge-tabs" aria-label="Разделы базы знаний">
        {(
          [
            ["search", "Поиск"],
            ["documents", "Документы"],
            ...(workspace.permissions.includes("knowledge.write")
              ? [["files", "Файлы"]]
              : []),
            ["profiles", "AI-профили"],
            ...(workspace.permissions.includes("content.approve")
              ? [
                  ["notes", "Пробелы и память"],
                  ["evaluations", "Качество поиска"],
                ]
              : []),
          ] as [typeof tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "search" && (
        <Search key={workspace.id} workspace={workspace} offline={offline} />
      )}
      {tab === "documents" && <Documents workspace={workspace} />}
      {tab === "files" && workspace.permissions.includes("knowledge.write") && (
        <Suspense fallback={<p role="status">Загрузка файлов…</p>}>
          <KnowledgeFiles
            key={workspace.id}
            workspace={workspace}
            offline={offline}
          />
        </Suspense>
      )}
      {tab === "profiles" && <Profiles workspace={workspace} />}
      {tab === "notes" && <Notes workspace={workspace} />}
      {tab === "evaluations" &&
        workspace.permissions.includes("content.approve") && (
          <Suspense fallback={<p role="status">Загрузка проверок поиска…</p>}>
            <RetrievalEvaluations key={workspace.id} workspace={workspace} />
          </Suspense>
        )}
    </main>
  );
}

function Sources({
  citations,
  zone,
}: {
  citations: api.Citation[];
  zone: string;
}) {
  return (
    <ol className="knowledge-sources">
      {citations.map((c) => (
        <li key={c.chunk_id}>
          <h3>{c.title}</h3>
          <p className="knowledge-excerpt">{c.text}</p>
          <p>
            {c.source_uri === "owner-input" ? (
              "Материал владельца"
            ) : (
              <a href={c.source_uri} target="_blank" rel="noreferrer">
                Первоисточник
              </a>
            )}
            {" · "}Актуален до {time(c.effective_to, zone)}
          </p>
          <details>
            <summary>Версия и происхождение</summary>
            <dl>
              <dt>Документ</dt>
              <dd>{c.document_id}</dd>
              <dt>Версия</dt>
              <dd>{c.document_version_id}</dd>
              <dt>Фрагмент</dt>
              <dd>{c.chunk_id}</dd>
              <dt>SHA-256 фрагмента</dt>
              <dd>{c.content_hash}</dd>
            </dl>
            <p>Проверенный справочный материал, не подтверждённый SQL-факт.</p>
          </details>
        </li>
      ))}
    </ol>
  );
}

function Search({
  workspace: w,
  offline,
}: {
  workspace: Workspace;
  offline: boolean;
}) {
  const [brand, setBrand] = useState("");
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState<{
    brand: string;
    query: string;
    sequence: number;
  } | null>(null);
  const brands = useQuery({
    queryKey: [w.id, "knowledge-brands"],
    queryFn: ({ signal }) => listCatalog(w.id, "brands", undefined, signal),
  });
  const found = useQuery({
    queryKey: [w.id, "knowledge-search", submitted],
    enabled: !!submitted,
    queryFn: ({ signal }) =>
      api.search(w.id, submitted!.brand, submitted!.query, signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
  return (
    <section aria-label="Поиск источников">
      <form
        className="knowledge-search"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted((previous) => ({
            brand,
            query: query.trim(),
            sequence: (previous?.sequence ?? 0) + 1,
          }));
        }}
      >
        <label>
          Бренд
          <select
            required
            value={brand}
            onChange={(e) => {
              setBrand(e.target.value);
              setSubmitted(null);
            }}
          >
            <option value="">Выберите бренд</option>
            {brands.data?.items.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Запрос
          <input
            required
            maxLength={500}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Правила бренда или название продукта"
          />
        </label>
        <button
          className="primary"
          disabled={offline || found.isFetching || !brand || !query.trim()}
        >
          Найти источники
        </button>
      </form>
      {brands.error && (
        <Failure error={brands.error} retry={() => void brands.refetch()} />
      )}
      {brands.data?.next_cursor && (
        <p>Показаны первые 25 брендов; остальные доступны через чат.</p>
      )}
      {!submitted && (
        <p>Работает полнотекстовый поиск. Смысловой поиск ещё не включён.</p>
      )}
      {submitted && found.isPending && <p role="status">Ищем источники…</p>}
      {found.error && (
        <Failure error={found.error} retry={() => void found.refetch()} />
      )}
      {found.data && !found.error && (
        <>
          <p role="status">Найдено фрагментов: {found.data.citations.length}</p>
          <p>Запрос: «{submitted?.query}». Для актуализации повторите поиск.</p>
          {!found.data.citations.length && (
            <p>
              Подтверждённых действующих источников не найдено. Это пробел
              знаний, а не основание придумывать ответ.
            </p>
          )}
          <Sources citations={found.data.citations} zone={w.timezone} />
          <small>
            Поиск {found.data.algorithm} · {found.data.run_id}
          </small>
        </>
      )}
    </section>
  );
}

function Documents({ workspace: w }: { workspace: Workspace }) {
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const docs = useQuery({
    queryKey: [w.id, "knowledge-documents", cursor],
    queryFn: ({ signal }) => api.documents(w.id, cursor, signal),
  });
  const detail = useQuery({
    queryKey: [w.id, "knowledge-document", selected],
    enabled: !!selected,
    queryFn: ({ signal }) => api.document(w.id, selected!, signal),
    refetchInterval: 10_000,
  });
  return (
    <section aria-label="Документы">
      <p>
        Добавление текста, просмотр кандидата и подтверждение версии — через
        чат. PDF/DOCX пока не принимаются.
      </p>
      {docs.isPending && <p role="status">Загружаем документы…</p>}
      {docs.error && (
        <Failure error={docs.error} retry={() => void docs.refetch()} />
      )}
      {docs.data && !docs.error && (
        <>
          <ul className="knowledge-list">
            {docs.data.items.map((d) => (
              <li key={d.id}>
                <button
                  onClick={() => setSelected(d.id)}
                  aria-pressed={selected === d.id}
                >
                  {d.title}
                </button>
                <span>
                  {d.archived
                    ? "Архив"
                    : d.active_index_id
                      ? "Индекс выбран"
                      : "Не включён в поиск"}{" "}
                  · версия {d.version}
                </span>
              </li>
            ))}
          </ul>
          {!docs.data.items.length && <p>Документов пока нет.</p>}
          <Paging next={docs.data.next_cursor} set={setCursor} />
        </>
      )}
      {detail.isFetching && <p role="status">Проверяем версию…</p>}
      {detail.error && (
        <Failure error={detail.error} retry={() => void detail.refetch()} />
      )}
      {detail.data && !detail.error && (
        <section className="knowledge-detail" aria-label="Версии документа">
          <h2>{detail.data.title}</h2>
          <p>
            {detail.data.visibility === "owner"
              ? "Только владелец"
              : "Рабочее пространство"}{" "}
            · {detail.data.id}
          </p>
          {detail.data.indexes.map((i) => (
            <div key={i.id}>
              <h3>{states[i.state] ?? i.state}</h3>
              <p>{i.error_code}</p>
              <dl>
                <dt>Индекс</dt>
                <dd>{i.id}</dd>
                <dt>Версия источника</dt>
                <dd>{i.document_version_id}</dd>
                <dt>SHA-256 оригинала</dt>
                <dd>{i.content_hash}</dd>
              </dl>
              <p>
                {i.parser_version} · {i.chunking_version} · попыток:{" "}
                {i.attempts}
              </p>
            </div>
          ))}
          {detail.data.indexes_truncated && (
            <p>Показаны 20 последних индексов.</p>
          )}
        </section>
      )}
    </section>
  );
}

function Profiles({ workspace: w }: { workspace: Workspace }) {
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const profiles = useQuery({
    queryKey: [w.id, "ai-profiles"],
    queryFn: ({ signal }) => api.profiles(w.id, signal),
  });
  const runs = useQuery({
    queryKey: [w.id, "ai-runs", cursor],
    queryFn: ({ signal }) => api.runs(w.id, cursor, signal),
    refetchInterval: 10_000,
  });
  const run = useQuery({
    queryKey: [w.id, "ai-run", selected],
    enabled: !!selected,
    queryFn: ({ signal }) => api.run(w.id, selected!, signal),
  });
  return (
    <section aria-label="AI-профили">
      <p>
        Профили не имеют инструментов публикации или одобрения. Платный тест
        запускает только владелец через чат после настройки сервера.
      </p>
      {profiles.isPending && <p role="status">Загружаем профили…</p>}
      {profiles.error && (
        <Failure error={profiles.error} retry={() => void profiles.refetch()} />
      )}
      {profiles.data && !profiles.error && (
        <ul className="knowledge-list">
          {profiles.data.map((p) => (
            <li key={p.name}>
              <div>
                <strong>{p.name}</strong>
                <p>{p.purpose}</p>
                <small>{p.blocked_reason ?? p.version}</small>
              </div>
              <span>{states[p.status]}</span>
            </li>
          ))}
        </ul>
      )}
      <h2>Ваши запуски</h2>
      {runs.error && (
        <Failure error={runs.error} retry={() => void runs.refetch()} />
      )}
      {runs.data && !runs.error && (
        <>
          <ul className="knowledge-list">
            {runs.data.items.map((r) => (
              <li key={r.id}>
                <button onClick={() => setSelected(r.id)}>
                  {r.profile} · {states[r.state] ?? r.state}
                </button>
                <span>{r.error_code}</span>
              </li>
            ))}
          </ul>
          {!runs.data.items.length && <p>Запусков пока нет.</p>}
          <Paging next={runs.data.next_cursor} set={setCursor} />
        </>
      )}
      {run.error && (
        <Failure error={run.error} retry={() => void run.refetch()} />
      )}
      {run.data && !run.error && (
        <section className="knowledge-detail" aria-label="Результат AI">
          <h2>{states[run.data.state] ?? run.data.state}</h2>
          <p>{run.data.warning}</p>
          <p>{run.data.error_code}</p>
          <p>
            {run.data.provider} · {run.data.model || "Модель не выбрана"}
          </p>
          {run.data.assessment && (
            <>
              <h3>Наблюдения источников</h3>
              {run.data.assessment.statements.map((s, n) => (
                <p key={n}>
                  {s.text} · {s.evidence}
                  <small> · Источники: {s.citation_ids.join(", ")}</small>
                </p>
              ))}
              <h3>Гипотезы AI</h3>
              {run.data.assessment.hypotheses.map((s, n) => (
                <p key={n}>{s}</p>
              ))}
              <h3>Пробелы знаний</h3>
              {run.data.assessment.knowledge_gaps.map((s, n) => (
                <p key={n}>{s}</p>
              ))}
            </>
          )}
          <Sources citations={run.data.citations ?? []} zone={w.timezone} />
          <details>
            <summary>Расход и параметры</summary>
            <pre>{JSON.stringify(run.data.usage, null, 2)}</pre>
          </details>
        </section>
      )}
    </section>
  );
}

function Notes({ workspace: w }: { workspace: Workspace }) {
  const [cursor, setCursor] = useState<string>();
  const notes = useQuery({
    queryKey: [w.id, "knowledge-notes", cursor],
    queryFn: ({ signal }) => api.notes(w.id, cursor, signal),
  });
  return (
    <section aria-label="Пробелы и память">
      <p>
        Предложения не меняют постоянные правила. Принятое предложение ещё нужно
        оформить и подтвердить как отдельный источник.
      </p>
      {notes.isPending && <p role="status">Загружаем предложения…</p>}
      {notes.error && (
        <Failure error={notes.error} retry={() => void notes.refetch()} />
      )}
      {notes.data && !notes.error && (
        <>
          <ul className="knowledge-list">
            {notes.data.items.map((n) => (
              <li key={n.id}>
                <div>
                  <h3>
                    {n.kind === "gap" ? "Пробел знаний" : "Предложение памяти"}
                  </h3>
                  <p>{n.text}</p>
                  <p>{n.purpose}</p>
                  <p>Безопасный вариант: {n.safe_alternative}</p>
                  <small>
                    {n.id} · до {time(n.effective_to, w.timezone)}
                  </small>
                </div>
                <span>{n.decision ?? "Ожидает решения"}</span>
              </li>
            ))}
          </ul>
          {!notes.data.items.length && <p>Предложений пока нет.</p>}
          <Paging next={notes.data.next_cursor} set={setCursor} />
        </>
      )}
    </section>
  );
}
