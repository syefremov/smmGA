import { useQuery } from "@tanstack/react-query";
import { planNotes } from "../../api/content";
import type { components } from "../../api/schema";
import { time } from "./hooks";
import { Failure } from "./shared";

export function PlanNotes({
  workspaceId,
  planId,
  timezone,
}: {
  workspaceId: string;
  planId: string;
  timezone: string;
}) {
  const query = useQuery({
    queryKey: [workspaceId, "plan-notes", planId],
    queryFn: ({ signal }) => planNotes(workspaceId, planId, signal),
    refetchInterval: 10_000,
  });
  if (query.error)
    return <Failure error={query.error} retry={() => void query.refetch()} />;
  if (query.isPending) return <p role="status">Загружаем основания плана…</p>;
  if (!query.data)
    return (
      <p>
        Для этой версии нет сохранённых AI-оснований. Это не означает, что план
        проверен или не содержит пробелов.
      </p>
    );
  return <PlanNotesResult notes={query.data} timezone={timezone} />;
}

export function PlanNotesResult({
  notes,
  timezone,
}: {
  notes: components["schemas"]["PlanNotesView"];
  timezone: string;
}) {
  return (
    <section className="plan-notes" aria-label="Общие основания плана">
      <h3>Основания и ограничения</h3>
      <p>
        {notes.exact_version
          ? "Заметки относятся к этой точной версии."
          : "Исторические заметки предыдущей версии — не проверка нового текста."}
      </p>
      <p>{notes.warning}</p>
      <p>
        Переданы человеком: {time(notes.created_at, timezone)} · {timezone}.
        Даты слотов — намерение, не назначенная отправка.
      </p>
      <dl className="file-metadata">
        <dt>Версия с основаниями</dt>
        <dd>{notes.plan_id}</dd>
        <dt>Hash той версии</dt>
        <dd>{notes.plan_hash}</dd>
        <dt>Hash заметок</dt>
        <dd>{notes.content_hash}</dd>
      </dl>
      {notes.body.slots.map((slot) => (
        <section
          key={slot.slot_index}
          aria-label={`Основания слота ${slot.slot_index + 1}`}
        >
          <h4>
            Слот {slot.slot_index + 1} · {time(slot.planned_at, timezone)}
          </h4>
          <p>
            {slot.destination} · Ответственный: {slot.owner_id}
          </p>
          <p className="knowledge-excerpt">{slot.topic}</p>
          <p>{slot.rationale}</p>
          <details>
            <summary>Цитаты: {slot.evidence.length}</summary>
            {slot.evidence.map((e, n) => (
              <div key={n}>
                <p>В предложении: «{e.quote}»</p>
                <p>В факте: «{e.source_quote}»</p>
                <p>Факт: {e.fact_id}</p>
              </div>
            ))}
          </details>
        </section>
      ))}
      <h4>Ограничения</h4>
      {notes.body.warnings.length ? (
        notes.body.warnings.map((value, n) => <p key={n}>{value}</p>)
      ) : (
        <p>AI не указал ограничений; это не результат проверки.</p>
      )}
      <h4>Пробелы знаний</h4>
      {notes.body.knowledge_gaps.length ? (
        notes.body.knowledge_gaps.map((value, n) => <p key={n}>{value}</p>)
      ) : (
        <p>AI не указал пробелов; нужна проверка человеком.</p>
      )}
      <details>
        <summary>Связанные факты и материалы</summary>
        <p>Выбранные факты: {notes.body.fact_ids.join(", ")}</p>
        <p>Материалы контекста: {notes.body.evidence_record_ids.join(", ")}</p>
      </details>
    </section>
  );
}
