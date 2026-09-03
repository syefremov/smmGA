import type { components } from "../../api/schema";

export function CopyDraftResult({
  draft,
}: {
  draft: components["schemas"]["CopyDraft"];
}) {
  return (
    <section aria-label="Предложение копирайтера">
      <h3>
        {draft.outcome === "draft"
          ? "Предложение текста"
          : "Недостаточно оснований"}
      </h3>
      <p>
        Текст не сохранён в пост. Нужны проверка человеком, отдельная новая
        редакция и preflight. Цитаты не доказывают смысловую точность или
        соблюдение правил.
      </p>
      <dl className="file-metadata">
        <dt>Исходная редакция</dt>
        <dd>{draft.revision_id}</dd>
        <dt>Hash редакции</dt>
        <dd>{draft.content_hash}</dd>
        <dt>Hash контекста</dt>
        <dd>{draft.context_hash}</dd>
      </dl>
      {draft.variants.map((variant) => (
        <section
          key={variant.variant_index}
          aria-label={`Вариант ${variant.variant_index + 1}`}
        >
          <h4>Вариант {variant.variant_index + 1}</h4>
          <p className="knowledge-excerpt">{variant.text}</p>
          <details>
            <summary>Основания: {variant.evidence.length}</summary>
            {variant.evidence.map((evidence, n) => (
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
