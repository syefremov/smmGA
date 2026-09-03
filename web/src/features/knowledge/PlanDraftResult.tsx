import type { components } from "../../api/schema";
import { time } from "../content/hooks";

export function PlanDraftResult({
  draft,
  timezone,
}: {
  draft: components["schemas"]["PlanDraft"];
  timezone: string;
}) {
  return (
    <section aria-label="Предложение контент-плана">
      <h3>
        {draft.outcome === "draft"
          ? "Темы для заданных слотов"
          : "Недостаточно оснований"}
      </h3>
      <p>
        Это личное предложение, а не сохранённый или одобренный план. Посты и
        расписание отправок не созданы. Для использования нужны проверка
        человеком и отдельные команды через чат. Цитаты не доказывают смысловую
        точность.
      </p>
      <dl className="file-metadata">
        <dt>Исходный план</dt>
        <dd>{draft.plan_id}</dd>
        <dt>Hash плана</dt>
        <dd>{draft.content_hash}</dd>
        <dt>Hash контекста</dt>
        <dd>{draft.context_hash}</dd>
      </dl>
      <p>
        Часовой пояс: {timezone}. Даты — намерение в плане, не назначенная
        публикация.
      </p>
      {draft.slots.map((slot) => (
        <section
          key={slot.slot_index}
          aria-label={`Слот ${slot.slot_index + 1}`}
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
            <summary>Основания: {slot.evidence.length}</summary>
            {slot.evidence.map((evidence, n) => (
              <div key={n}>
                <p>В предложении: «{evidence.quote}»</p>
                <p>В факте: «{evidence.source_quote}»</p>
                <p>Факт: {evidence.fact_id}</p>
              </div>
            ))}
          </details>
        </section>
      ))}
      <h4>Ограничения</h4>
      {draft.warnings.length ? (
        draft.warnings.map((s, n) => <p key={n}>{s}</p>)
      ) : (
        <p>
          AI не указал дополнительных ограничений; это не результат проверки.
        </p>
      )}
      <h4>Пробелы знаний</h4>
      {draft.knowledge_gaps.length ? (
        draft.knowledge_gaps.map((s, n) => <p key={n}>{s}</p>)
      ) : (
        <p>AI не указал пробелов; требуется человеческая проверка.</p>
      )}
    </section>
  );
}
