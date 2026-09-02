import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import * as api from "../api/evaluation";
import type { Workspace } from "../api/operations";
import { time } from "./content/hooks";
import { Failure, Paging } from "./content/shared";

const reasons: Record<string, string> = {
  synthetic_dataset: "Синтетический набор — не рабочий эталон",
  at_least_eight_cases_required: "Нужно минимум восемь разных вопросов",
  category_coverage_incomplete: "Не покрыты все категории проверок",
  audience_coverage_incomplete: "Не проверены обе области видимости",
  forbidden_source_case_required: "Нужен пример с запрещённым источником",
  corpus_changed: "Корпус изменился или источник утратил актуальность",
  dataset_superseded: "Есть новая версия тестового набора",
  quality_thresholds_failed: "Не все вопросы прошли заданные пороги",
  empty_corpus: "Нет действующих источников",
};
const percent = (value: number) =>
  new Intl.NumberFormat("ru", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);

export function RetrievalEvaluations({
  workspace: w,
}: {
  workspace: Workspace;
}) {
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const data = useQuery({
    queryKey: [w.id, "eval-datasets", cursor],
    queryFn: ({ signal }) => api.datasets(w.id, cursor, signal),
  });
  return (
    <section aria-label="Качество поиска">
      <h2>Проверки поиска</h2>
      <p>
        Наборы вопросов и отчёты FTS. Создание, запуск и точное подтверждение —
        через чат.
      </p>
      {data.isPending && <p role="status">Загрузка наборов…</p>}
      {data.error ? (
        <Failure error={data.error} retry={() => void data.refetch()} />
      ) : (
        data.data && (
          <>
            {!data.data.items.length && (
              <p>
                Тестовых наборов пока нет. Попросите в чате подготовить вопросы
                и ожидаемые источники для бренда.
              </p>
            )}
            <ul className="knowledge-list">
              {data.data.items.map((item) => (
                <li key={item.id}>
                  <div>
                    <button
                      aria-pressed={selected === item.id}
                      onClick={() => setSelected(item.id)}
                    >
                      {item.definition.title} · версия {item.number}
                    </button>
                    <p>
                      {item.definition.origin === "synthetic"
                        ? "Синтетический набор"
                        : "Набор владельца"}{" "}
                      · Вопросов: {item.definition.cases.length}
                    </p>
                    <small>{time(item.created_at, w.timezone)}</small>
                  </div>
                </li>
              ))}
            </ul>
            <Paging
              next={data.data.next_cursor}
              set={(next) => {
                setCursor(next);
                setSelected(undefined);
              }}
            />
            {selected && (
              <Runs key={selected} workspace={w} dataset={selected} />
            )}
          </>
        )
      )}
    </section>
  );
}

function Runs({
  workspace: w,
  dataset,
}: {
  workspace: Workspace;
  dataset: string;
}) {
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const data = useQuery({
    queryKey: [w.id, "eval-runs", dataset, cursor],
    queryFn: ({ signal }) => api.runs(w.id, dataset, cursor, signal),
  });
  return (
    <section className="knowledge-detail" aria-label="Прогоны тестового набора">
      <h3>Прогоны</h3>
      {data.isPending && <p role="status">Загрузка отчётов…</p>}
      {data.error ? (
        <Failure error={data.error} retry={() => void data.refetch()} />
      ) : (
        data.data && (
          <>
            {!data.data.items.length && <p>Прогонов пока нет.</p>}
            <ul className="knowledge-list">
              {data.data.items.map((item) => (
                <li key={item.id}>
                  <button
                    aria-pressed={selected === item.id}
                    onClick={() => setSelected(item.id)}
                  >
                    Отчёт · {time(item.created_at, w.timezone)}
                  </button>
                  <span>
                    {item.report.passed ? "Пороги пройдены" : "Есть ошибки"}
                    {item.stale ? " · Устарел" : ""}
                    {item.baseline_current ? " · Эталон подтверждён" : ""}
                  </span>
                </li>
              ))}
            </ul>
            <Paging
              next={data.data.next_cursor}
              set={(next) => {
                setCursor(next);
                setSelected(undefined);
              }}
            />
            {selected && <Report key={selected} workspace={w} id={selected} />}
          </>
        )
      )}
    </section>
  );
}

function Report({ workspace: w, id }: { workspace: Workspace; id: string }) {
  const data = useQuery({
    queryKey: [w.id, "eval-report", id],
    queryFn: ({ signal }) => api.run(w.id, id, signal),
    retry: false,
  });
  if (data.error)
    return <Failure error={data.error} retry={() => void data.refetch()} />;
  if (!data.data) return <p role="status">Загрузка результата…</p>;
  const report = data.data;
  return (
    <section
      className="knowledge-detail"
      aria-label="Результат проверки поиска"
    >
      <h3>Результат проверки</h3>
      <p>
        {report.baseline_current
          ? "Действующий подтверждённый эталон FTS"
          : report.stale
            ? "Исторический отчёт — нужна новая проверка"
            : "Эталон не подтверждён"}
      </p>
      <p>
        Средние показатели по вопросам. Успех требует прохождения каждого
        вопроса, а не только среднего.
      </p>
      <dl className="eval-metrics">
        <div>
          <dt>Точность источников</dt>
          <dd>{percent(report.report.precision)}</dd>
        </div>
        <div>
          <dt>Полнота источников</dt>
          <dd>{percent(report.report.recall)}</dd>
        </div>
        <div>
          <dt>Корректность ссылок</dt>
          <dd>{percent(report.report.citation_validity)}</dd>
        </div>
      </dl>
      <p>
        Top-{report.definition.limit} · {report.report.algorithm} ·{" "}
        {report.report.duration_ms.toFixed(0)} мс
      </p>
      {report.acceptance_blockers.length > 0 && (
        <div className="eval-notice" role="status">
          <p>Подтверждение недоступно:</p>
          <ul>
            {report.acceptance_blockers.map((reason) => (
              <li key={reason}>{reasons[reason] ?? reason}</li>
            ))}
          </ul>
        </div>
      )}
      <ol className="knowledge-sources">
        {report.report.cases.map((item) => {
          const test = report.definition.cases.find((c) => c.key === item.key);
          return (
            <li key={item.key}>
              <h4>{test?.query ?? item.key}</h4>
              <p>
                {item.passed ? "Пройден" : "Не пройден"} ·{" "}
                {test?.audience === "owner"
                  ? "Область владельца"
                  : "Общие документы"}{" "}
                · {item.latency_ms.toFixed(0)} мс
              </p>
              <p>
                Точность {percent(item.precision)} · полнота{" "}
                {percent(item.recall)} · {item.hits.length} фрагментов
              </p>
              {!item.forbidden_pass && <p>Обнаружен запрещённый источник.</p>}
              {!item.negative_pass && (
                <p>Найден ответ там, где ожидалось отсутствие источника.</p>
              )}
              <details>
                <summary>Ожидания и найденные источники · {item.key}</summary>
                <p>
                  Ожидались:{" "}
                  {test?.expected_document_ids.join(", ") || "нет источников"}
                </p>
                <p>
                  Запрещены:{" "}
                  {test?.forbidden_document_ids?.join(", ") || "не заданы"}
                </p>
                <p>
                  Пропущены: {item.missing_document_ids.join(", ") || "нет"}
                </p>
                <p>
                  Лишние: {item.unexpected_document_ids.join(", ") || "нет"}
                </p>
                {item.hits.map((hit) => (
                  <dl key={hit.chunk_id}>
                    <dt>Документ / версия / индекс</dt>
                    <dd>
                      {hit.document_id} / {hit.document_version_id} /{" "}
                      {hit.index_id}
                    </dd>
                    <dt>Фрагмент / SHA-256</dt>
                    <dd>
                      {hit.chunk_id} / {hit.content_hash}
                    </dd>
                  </dl>
                ))}
              </details>
            </li>
          );
        })}
      </ol>
      <details>
        <summary>Пороги, решение и точные версии</summary>
        <p>
          Для каждого вопроса: точность ≥{" "}
          {percent(report.definition.thresholds?.precision ?? 0.8)}, полнота ≥{" "}
          {percent(report.definition.thresholds?.recall ?? 1)}, время ≤{" "}
          {report.definition.thresholds?.max_case_ms ?? 1000} мс. Все ссылки и
          запреты должны пройти проверку.
        </p>
        <dl>
          <dt>Отчёт</dt>
          <dd>{report.id}</dd>
          <dt>Автор запуска</dt>
          <dd>{report.actor_id}</dd>
          <dt>SHA-256 отчёта</dt>
          <dd>{report.report_hash}</dd>
          <dt>SHA-256 набора</dt>
          <dd>{report.dataset_hash}</dd>
          <dt>SHA-256 корпуса</dt>
          <dd>{report.corpus_hash}</dd>
          <dt>Решение в истории</dt>
          <dd>
            {report.decision === "accept_baseline"
              ? "Принят как эталон FTS"
              : report.decision === "reject"
                ? "Отклонён"
                : "Не записано"}
          </dd>
        </dl>
        {report.review_reason && <p>{report.review_reason}</p>}
        {report.reviewed_by && (
          <p>
            Решение: {report.reviewed_by}
            {report.reviewed_at
              ? ` · ${time(report.reviewed_at, w.timezone)}`
              : ""}
          </p>
        )}
      </details>
      <p>
        Этот тест не проверяет истинность утверждений и не включает рабочий RAG,
        AI-профили или публикацию. Области видимости не заменяют тест входа
        сотрудника.
      </p>
      <button onClick={() => void data.refetch()} disabled={data.isFetching}>
        Проверить актуальность отчёта
      </button>
      {data.isFetching && <p role="status">Проверка актуальности…</p>}
    </section>
  );
}
