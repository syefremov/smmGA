import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  RouterProvider,
  useRouterState,
} from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, client } from "./api/client";
import { fetchSession, type Session } from "./api/operations";
import { Status } from "./Status";
import { WorkspaceContent } from "./features/WorkspaceContent";
import "./workspace.css";

function Gate() {
  const cache = useQueryClient();
  const [blocked, setBlocked] = useState(false);
  const session = useQuery({
    queryKey: ["session"],
    queryFn: ({ signal }) => fetchSession(signal),
    retry: false,
    refetchInterval: 10_000,
  });
  useEffect(() => {
    const stop = () => {
      setBlocked(true);
      cache.removeQueries({ queryKey: ["session"] });
    };
    window.addEventListener("smm-access-changed", stop);
    return () => window.removeEventListener("smm-access-changed", stop);
  }, [cache]);
  if (blocked)
    return (
      <Notice title="Доступ изменился">
        Приватные данные скрыты.{" "}
        <button onClick={() => window.location.reload()}>
          Проверить доступ
        </button>
      </Notice>
    );
  if (session.isPending)
    return (
      <Notice title="Проверяем доступ…">
        Подключаем рабочее пространство.
      </Notice>
    );
  if (session.error)
    return (
      <Notice title="Вход в рабочее пространство">
        <p>
          {session.error instanceof ApiError && session.error.status === 401
            ? "Войдите под личной учётной записью."
            : "Сервис входа недоступен или ещё не настроен. Данные не загружены."}
        </p>
        <a className="primary" href="/api/v1/auth/login">
          Войти
        </a>{" "}
        <button onClick={() => void session.refetch()}>
          Повторить проверку
        </button>
      </Notice>
    );
  return (
    <Shell
      key={`${session.data.user_id}:${session.data.access_version}`}
      session={session.data}
    />
  );
}

function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="entry">
      <a href="/">SMM GPT · GreenAurum</a>
      <h1>{title}</h1>
      <div>{children}</div>
      <p>
        <Link to="/">Состояние системы</Link>
      </p>
    </main>
  );
}

function PrivateCache({ children }: { children: ReactNode }) {
  const [cache] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            refetchOnWindowFocus: true,
            staleTime: 5000,
          },
          mutations: { retry: false },
        },
      }),
  );
  useEffect(
    () => () => {
      void cache.cancelQueries();
      cache.clear();
    },
    [cache],
  );
  return <QueryClientProvider client={cache}>{children}</QueryClientProvider>;
}

function Shell({ session }: { session: Session }) {
  const location = useRouterState({ select: (s) => s.location });
  const section = location.pathname.split("/")[2] ?? "work";
  const requested = new URLSearchParams(location.searchStr).get("workspace");
  const workspace = requested
    ? session.workspaces.find((w) => w.id === requested)
    : session.workspaces[0];
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);
  const [offline, setOffline] = useState(!navigator.onLine);
  useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  async function logout() {
    setLoggingOut(true);
    setLogoutFailed(false);
    try {
      await client.POST("/api/v1/auth/logout");
      window.location.replace("/app/work");
    } catch {
      setLogoutFailed(true);
    }
  }
  if (loggingOut)
    return (
      <Notice
        title={logoutFailed ? "Выход пока не подтверждён" : "Завершаем сеанс…"}
      >
        {logoutFailed && (
          <>
            <p>Локальные данные скрыты. Сервер не подтвердил отзыв сессии.</p>
            <button onClick={() => void logout()}>Повторить выход</button>
          </>
        )}
      </Notice>
    );
  return (
    <div className="workspace-shell">
      <a className="skip" href="#work-main">
        К содержимому
      </a>
      <aside className="rail">
        <Link to="/" className="wordmark">
          SMM<span>GPT</span>
        </Link>
        <p className="eyebrow">Рабочее пространство</p>
        <label htmlFor="workspace">Компания</label>
        <select
          id="workspace"
          value={workspace?.id ?? ""}
          onChange={(e) =>
            window.location.assign(
              `/app/work?workspace=${encodeURIComponent(e.target.value)}`,
            )
          }
        >
          {!workspace && <option value="">Выберите пространство</option>}
          {session.workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
        <nav aria-label="Рабочие разделы">
          {(
            [
              ["work", "Задачи"],
              ["content", "Контент"],
              ["materials", "Материалы"],
              ["calendar", "Календарь"],
              ["knowledge", "База знаний"],
              ["brands", "Бренды"],
              ["products", "Продукты"],
              ["sources", "Источники"],
              ["audit", "Аудит"],
            ] as const
          ).map(([key, label]) => (
            <a
              key={key}
              aria-current={section === key ? "page" : undefined}
              href={`/app/${key}${workspace ? `?workspace=${workspace.id}` : ""}`}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="rail-bottom">
          <p>{session.display_name}</p>
          <button onClick={() => void logout()}>Выйти</button>
          <p className="muted">Личный доступ · ручной пилот</p>
        </div>
      </aside>
      <div className="work-area">
        <header className="work-header">
          <span>{workspace?.name ?? "Нет пространства"}</span>
          <span className="muted">{workspace?.timezone ?? ""} · чат + веб</span>
        </header>
        {offline && (
          <p role="alert" className="banner">
            Нет связи. Данные могут устареть; изменения недоступны.
          </p>
        )}
        {workspace ? (
          <PrivateCache key={workspace.id}>
            <WorkspaceContent
              key={section}
              workspace={workspace}
              section={section}
              offline={offline}
            />
          </PrivateCache>
        ) : (
          <main id="work-main" className="work-main">
            <h1>Нет доступа к пространству</h1>
            <p>
              Проверьте membership и MFA у администратора. Чужие данные не
              загружены.
            </p>
          </main>
        )}
      </div>
    </div>
  );
}

const root = createRootRoute({ component: Outlet });
const index = createRoute({
  getParentRoute: () => root,
  path: "/",
  component: () => (
    <>
      <div className="open-work">
        <Link to="/app/$section" params={{ section: "work" }}>
          Открыть рабочее пространство →
        </Link>
      </div>
      <Status />
    </>
  ),
});
const app = createRoute({
  getParentRoute: () => root,
  path: "/app/$section",
  component: Gate,
});
const router = createRouter({
  routeTree: root.addChildren([index, app]),
  defaultNotFoundComponent: () => (
    <Notice title="Раздел не найден">Вернитесь к состоянию системы.</Notice>
  ),
});
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
export function App() {
  return <RouterProvider router={router} />;
}
