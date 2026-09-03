import type { components } from "../../api/schema";
import { time } from "../content/hooks";

export function CopywriterResult({
  draft,
  adoption,
  workspaceId,
  timezone,
}: {
  draft?: components["schemas"]["CopyDraft"] | null;
  adoption?: components["schemas"]["CopyAdoptionView"] | null;
  workspaceId: string;
  timezone: string;
}) {
  return (
    <>
      {draft && <CopyDraftResult draft={draft} />}
      {adoption && (
        <section aria-label="История сохранения AI-текста">
          <h3>Предложение сохранено человеком</h3>
          <p>{adoption.warning}</p>
          <p>
            Создана отдельная редакция со статусом «Черновик»; старое
            согласование снято. Рабочие копии сохранены.
          </p>
          <p>
            Автор решения: {adoption.actor_id} ·{" "}
            {time(adoption.created_at, timezone)} · {timezone}
          </p>
          <p>Основание: {adoption.reason}</p>
          <dl className="file-metadata">
            <dt>Новая редакция</dt>
            <dd>{adoption.revision_id}</dd>
            <dt>Hash текста</dt>
            <dd>{adoption.content_hash}</dd>
            <dt>Исходная редакция</dt>
            <dd>{adoption.source_revision_id}</dd>
          </dl>
          <p>
            Проверка на момент сохранения:{" "}
            {adoption.preflight.passed
              ? "без детерминированных блокеров, не одобрение"
              : "есть блокирующие замечания"}
            .
          </p>
          <details>
            <summary>
              Замечания при сохранении: {adoption.preflight.findings.length}
            </summary>
            {adoption.preflight.findings.map((finding, n) => (
              <p key={n}>
                {finding.severity} · {finding.code} · {finding.location}
              </p>
            ))}
          </details>
          <a
            href={`/app/content?workspace=${encodeURIComponent(workspaceId)}&post=${encodeURIComponent(adoption.post_id)}`}
          >
            Открыть пост для актуальной проверки
          </a>
        </section>
      )}
    </>
  );
}

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
