import { useQuery } from "@tanstack/react-query";

import { fetchSystemStatus } from "./api/client";

const navigation = [
  { label: "Состояние", active: true },
  { label: "Контент", active: false },
  { label: "Календарь", active: false },
  { label: "Аналитика", active: false },
];

function formatUpdatedAt(timestamp: number): string {
  if (timestamp === 0) return "ещё не обновлялось";
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(timestamp);
}

export function App() {
  const statusQuery = useQuery({
    queryKey: ["system-status"],
    queryFn: ({ signal }) => fetchSystemStatus(signal),
    refetchInterval: 15_000,
  });
  const status = statusQuery.data;
  const isReady = status?.state === "ready";

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Основная навигация">
        <a
          className="brand"
          href="#status"
          aria-label="SMM GPT, начало страницы"
        >
          <span className="brand-mark" aria-hidden="true">
            GA
          </span>
          <span>
            <strong>SMM GPT</strong>
            <small>GreenAurum workspace</small>
          </span>
        </a>

        <nav className="nav-list" aria-label="Разделы системы">
          {navigation.map((item) =>
            item.active ? (
              <a
                className="nav-item active"
                href="#status"
                aria-current="page"
                key={item.label}
              >
                <span aria-hidden="true">01</span>
                {item.label}
              </a>
            ) : (
              <span
                className="nav-item disabled"
                aria-disabled="true"
                key={item.label}
              >
                <span aria-hidden="true">—</span>
                {item.label}
              </span>
            ),
          )}
        </nav>

        <p className="phase-note">
          Фаза 2<span>Исполняемый каркас</span>
        </p>
      </aside>

      <main id="status" className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Операционный контур</p>
            <h1>Состояние системы</h1>
          </div>
          <div
            className={`overall-status ${isReady ? "ready" : "degraded"}`}
            role="status"
          >
            <span aria-hidden="true" />
            {statusQuery.isPending
              ? "Проверяем"
              : isReady
                ? "Система готова"
                : "Требует внимания"}
          </div>
        </header>

        <div className="workspace-grid">
          <section className="primary-panel" aria-labelledby="services-heading">
            <div className="section-intro">
              <div>
                <p className="eyebrow">Живой сигнал</p>
                <h2 id="services-heading">Сервисы и зависимости</h2>
              </div>
              <button
                className="refresh-button"
                type="button"
                onClick={() => void statusQuery.refetch()}
                disabled={statusQuery.isFetching}
              >
                {statusQuery.isFetching ? "Обновление…" : "Обновить"}
              </button>
            </div>

            {statusQuery.isPending && (
              <div className="message-state" role="status">
                Проверяем API, PostgreSQL и Redis…
              </div>
            )}

            {statusQuery.isError && (
              <div className="message-state error" role="alert">
                <strong>API пока недоступен.</strong>
                <span>Проверьте локальный стек и повторите запрос.</span>
              </div>
            )}

            {status && (
              <div className="status-table-wrap">
                <table className="status-table">
                  <caption className="sr-only">
                    Состояние зависимостей приложения
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Компонент</th>
                      <th scope="col">Состояние</th>
                      <th scope="col">Отклик</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.dependencies.map((dependency) => (
                      <tr key={dependency.name}>
                        <th scope="row">{dependency.name}</th>
                        <td>
                          <span className={`state-label ${dependency.state}`}>
                            <span aria-hidden="true" />
                            {dependency.state === "ready"
                              ? "Готов"
                              : "Недоступен"}
                          </span>
                        </td>
                        <td className="metric">
                          {dependency.latency_ms.toFixed(1)} мс
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="activity-line">
              <span>Последняя проверка</span>
              <time>{formatUpdatedAt(statusQuery.dataUpdatedAt)}</time>
            </div>
          </section>

          <aside className="context-panel" aria-labelledby="context-heading">
            <p className="eyebrow">Контекст</p>
            <h2 id="context-heading">Что доступно сейчас</h2>

            <dl className="facts-list">
              <div>
                <dt>Среда</dt>
                <dd>{status?.environment ?? "—"}</dd>
              </div>
              <div>
                <dt>Версия API</dt>
                <dd>{status?.version ?? "—"}</dd>
              </div>
              <div>
                <dt>MCP</dt>
                <dd>только чтение</dd>
              </div>
            </dl>

            <div className="connector-block">
              <p>Коннекторы</p>
              {status?.connectors.map((connector) => (
                <div className="connector-row" key={connector.name}>
                  <span>{connector.name}</span>
                  <small>
                    {connector.can_publish
                      ? "запись доступна"
                      : "без публикации"}
                  </small>
                </div>
              )) ?? <span className="muted">Ожидаем данные API</span>}
            </div>

            <p className="safety-note">
              На этом этапе система не выполняет внешние публикации и не хранит
              реальные токены соцсетей.
            </p>
          </aside>
        </div>
      </main>
    </div>
  );
}
