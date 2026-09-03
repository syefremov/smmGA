import { ApiError } from "../../api/client";

export function Failure({
  error,
  retry,
}: {
  error: Error;
  retry?: () => void;
}) {
  const code = error instanceof ApiError ? error.code : "offline";
  const messages: Record<string, string> = {
    pending_request_changed:
      "Исход предыдущего запроса неизвестен. Верните его поля и повторите без изменений; не создавайте новую операцию до сверки с сервером.",
    version_conflict:
      "Версия изменилась. Текст сохранён в форме: перечитайте серверную редакцию и сверьте изменения.",
    revision_conflict: "Редакция изменилась. Решение не записано.",
    preflight_blocked:
      "Есть блокирующие замечания. Исправьте редакцию или источники.",
    approval_context_changed:
      "Правила или источники изменились после одобрения. Нужна новая проверка.",
    approval_required: "Требуется одобрение владельца для этой редакции.",
    invalid_request: "Проверьте обязательные поля и формат данных.",
    record_family_exists:
      "Такая версия справочника уже есть. Создайте исправление существующей записи через чат.",
    unverified_or_stale_source:
      "Сначала подтвердите актуальную версию источника.",
    access_denied: "Недостаточно прав. Изменение не выполнено.",
    invalid_transition: "Состояние изменилось; перечитайте запись.",
  };
  return (
    <div className="inline-error" role="alert">
      <p>
        {messages[code] ??
          "Сервер не подтвердил действие. Проверьте соединение; повторите тот же запрос без изменений."}
      </p>
      {error instanceof ApiError && (
        <small>
          {code} · {error.correlation}
        </small>
      )}
      {retry && (
        <button type="button" onClick={retry}>
          Перечитать данные
        </button>
      )}
    </div>
  );
}

export function Paging({
  next,
  set,
}: {
  next?: string | null;
  set: (cursor?: string) => void;
}) {
  return (
    <div className="pagination">
      <button type="button" onClick={() => set(undefined)}>
        В начало
      </button>
      <button type="button" disabled={!next} onClick={() => next && set(next)}>
        Далее
      </button>
    </div>
  );
}
