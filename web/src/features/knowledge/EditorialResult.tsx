import type { components } from "../../api/schema";

type Review = components["schemas"]["EditorialReview"];
type Triage = components["schemas"]["EditorialTriageView"];
const states = {
  open: "В работе",
  needs_changes: "Нужно исправить",
  dismissed: "Отклонено человеком",
};
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

export function EditorialResult({
  review,
  triage,
  timezone = "UTC",
}: {
  review: Review;
  triage?: Triage | null;
  timezone?: string;
}) {
  return (
    <section aria-label="Проверка редактора">
      <h3>{recommendations[review.recommendation]}</h3>
      <p>
        Тестовая рекомендация AI, не одобрение публикации и не юридическое
        заключение. Изображения не проверялись.
      </p>
      <p>{review.summary}</p>
      {triage && <p>{triage.warning} Изменение статуса — через чат.</p>}
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
        {triage && (
          <p>
            Отчёт: <code>{triage.artifact_id}</code> · Версия решений:{" "}
            {triage.version}
          </p>
        )}
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
            {triage?.findings[index] && (
              <p>
                Решение человека: {states[triage.findings[index].status]}.
                {triage.findings[index].latest_decision && (
                  <>
                    {" "}
                    Основание: {triage.findings[index].latest_decision.reason}
                  </>
                )}
              </p>
            )}
            {!!finding.record_ids.length && (
              <p>Записи-основания: {finding.record_ids.join(", ")}</p>
            )}
          </li>
        ))}
      </ol>
      {triage && (
        <details>
          <summary>История решений по замечаниям</summary>
          <p>
            Исторические записи не подтверждают исправление текста или
            разрешение публикации.
          </p>
          <p>Часовой пояс: {timezone}</p>
          {!triage.recent_history.length && <p>Решений ещё нет.</p>}
          <ol>
            {triage.recent_history.map((decision) => (
              <li key={decision.id}>
                <p>
                  № {decision.sequence} · Замечание {decision.finding_index + 1}{" "}
                  · {states[decision.status]}
                </p>
                <p>{decision.reason}</p>
                <p>
                  Автор: <code>{decision.actor_id}</code> ·{" "}
                  <time dateTime={decision.created_at}>
                    {new Date(decision.created_at).toLocaleString("ru-RU", {
                      timeZone: timezone,
                    })}
                  </time>
                </p>
              </li>
            ))}
          </ol>
          {triage.next_before !== null && (
            <p>
              Показаны последние 25 решений. Более ранняя история доступна через
              чат.
            </p>
          )}
        </details>
      )}
    </section>
  );
}
