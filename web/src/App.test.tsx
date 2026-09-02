import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "./App";

const { fetchSystemStatusMock } = vi.hoisted(() => ({
  fetchSystemStatusMock: vi.fn(),
}));

vi.mock("./api/client", () => ({
  fetchSystemStatus: fetchSystemStatusMock,
}));

const readyResponse = {
  application: "smm-gpt",
  version: "0.1.0",
  environment: "test",
  state: "ready",
  dependencies: [
    { name: "postgresql", state: "ready", latency_ms: 2.4 },
    { name: "redis", state: "ready", latency_ms: 1.1 },
  ],
  connectors: [
    {
      name: "fake-social",
      state: "ready",
      can_publish: false,
      mode: "fake-read-only",
    },
  ],
};

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchSystemStatusMock.mockReset();
});

test("renders live service and connector status from the API", async () => {
  fetchSystemStatusMock.mockResolvedValue(readyResponse);

  renderApp();

  expect(await screen.findByText("Система готова")).toBeInTheDocument();
  expect(
    screen.getByRole("rowheader", { name: "postgresql" }),
  ).toBeInTheDocument();
  expect(screen.getByText("fake-social")).toBeInTheDocument();
  expect(screen.getByText("без публикации")).toBeInTheDocument();
});

test("shows a recoverable error state when the API is unavailable", async () => {
  fetchSystemStatusMock.mockRejectedValue(new TypeError("offline"));

  renderApp();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "API пока недоступен",
  );
  expect(screen.getByRole("button", { name: "Обновить" })).toBeEnabled();
});
