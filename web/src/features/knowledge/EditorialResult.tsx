import type { components } from "../../api/schema";

type Review = components["schemas"]["EditorialReview"];
const labels = {
  facts: "Факты",
  claims: "Утверждения",
  tone: "Тон",
  format: "Формат",
  accessibility: "Доступность",
  privacy: "Персональные данные",
  revision: "Редакция",
  variant: "Текст варианта",
  brief: "Бриф",
  evidence: "Основания",
};
const recommendations = {
  pass: "Замечаний, блокирующих проверку, не найдено",
  needs_changes: "Нужны изменения",
  needs_human_decision: "Нужно решение человека",
};
const severities = {
  info: "Информация",
  warning: "Замечание",
  blocking: "Блокирующее замечание",
};

export function EditorialResult({ review }: { review: Review }) {
  return (
    <section aria-label="Проверка редактора">
      <h3>{recommendations[review.recommendation]}</h3>
      <p>
        Тестовая рекомендация AI, не одобрение публикации и не юридическое
        заключение. Изображения не проверялись.
      </p>
      <p>{review.summary}</p>
      <p>
        Редакция: <code>{review.revision_id}</code>
      </p>
      <details>
        <summary>Точная версия проверки</summary>
        <p>
          Текст: <code>{review.content_hash}</code>
        </p>
        <p>
          Входные данные: <code>{review.context_hash}</code>
        </p>
      </details>
      {!review.findings.length && (
        <p>AI не вернул замечаний. Проверка человеком всё равно обязательна.</p>
      )}
      <ol>
        {review.findings.map((finding, index) => (
          <li key={index}>
            <p>
              {severities[finding.severity]} · {labels[finding.category]} ·{" "}
              {labels[finding.location]}
              {finding.variant_index !== null
                ? ` · Вариант ${finding.variant_index + 1}`
                : ""}
            </p>
            {finding.quote && <blockquote>{finding.quote}</blockquote>}
            <p>{finding.description}</p>
            <p>Предложение: {finding.suggestion}</p>
            {!!finding.record_ids.length && (
              <p>Записи-основания: {finding.record_ids.join(", ")}</p>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
