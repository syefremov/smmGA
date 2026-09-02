import { expect, test, type Page } from "@playwright/test";
import type { components } from "../src/api/schema";

type Session = components["schemas"]["SessionView"];
type Item = components["schemas"]["WorkItemView"];
const first = "11111111-1111-4111-8111-111111111111";
const second = "22222222-2222-4222-8222-222222222222";
const session: Session = {
  user_id: first,
  display_name: "Тестовый сотрудник",
  mfa: true,
  access_version: "v1",
  workspaces: [
    {
      id: first,
      name: "GreenAurum · тест",
      timezone: "Europe/Moscow",
      permissions: ["workspace.read", "work_item.write", "audit.read"],
    },
    {
      id: second,
      name: "Вторая компания",
      timezone: "Europe/Moscow",
      permissions: ["workspace.read"],
    },
  ],
};
const example: Item = {
  id: first,
  workspace_id: first,
  created_at: "2026-09-01T12:00:00Z",
  title: "Изучить источники для VK",
  brief: "Только синтетические данные.",
  version: 1,
  state: "open",
  allowed_transitions: ["in_progress", "cancelled"],
};

async function fixture(page: Page, identity: Session = session) {
  let current: Session | null = structuredClone(identity);
  const items: Item[] = [];
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      status: current ? 200 : 401,
      json: current ?? { error: { code: "authentication_required" } },
    }),
  );
  await page.route("**/api/v1/auth/logout", (route) => {
    current = null;
    return route.fulfill({ status: 204 });
  });
  await page.route("**/api/v1/workspaces/*/work-items*", async (route) => {
    const req = route.request();
    if (req.method() === "POST") {
      const body = req.postDataJSON();
      expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8);
      const item = { ...example, title: body.title, brief: body.brief };
      items.push(item);
      return route.fulfill({ status: 201, json: item });
    }
    const wid = new URL(req.url()).pathname.split("/")[4];
    return route.fulfill({
      json: {
        items: items.filter((i) => i.workspace_id === wid),
        next_cursor: null,
      },
    });
  });
  return {
    items,
    setSession: (value: Session | null) => {
      current = value;
    },
  };
}

test("personal login is required on a direct private URL", async ({ page }) => {
  const data = await fixture(page);
  data.setSession(null);
  await page.goto("/app/work");
  await expect(
    page.getByRole("heading", { name: "Вход в рабочее пространство" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Войти", exact: true }),
  ).toHaveAttribute("href", "/api/v1/auth/login");
  await expect(
    page.getByRole("button", { name: "Создать задачу" }),
  ).toHaveCount(0);
});

test("create, inspect, switch workspace and logout without retained private data", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/app/work");
  await expect(page.getByText("Начните с первой задачи")).toBeVisible();
  await page.getByRole("button", { name: "Создать задачу" }).click();
  await page.getByLabel("Название", { exact: true }).fill("План на неделю");
  await page
    .getByLabel("Описание", { exact: true })
    .fill("Подготовить гипотезы, без публикации.");
  await page.getByRole("button", { name: "Сохранить задачу" }).click();
  await expect(
    page.getByRole("heading", { name: "План на неделю" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "План на неделю" }),
  ).toBeVisible();
  await page.getByLabel("Компания").selectOption(second);
  await expect(page.getByText("Начните с первой задачи")).toBeVisible();
  await expect(page.getByText("План на неделю")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Создать задачу" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Вход в рабочее пространство" }),
  ).toBeVisible();
  await expect(page.getByText("Вторая компания", { exact: true })).toHaveCount(
    0,
  );
});

test("stale version conflict requires an explicit refresh", async ({
  page,
}) => {
  const data = await fixture(page);
  data.items.push(example);
  await page.route("**/work-items/*/transition", (route) =>
    route.fulfill({
      status: 409,
      headers: { "X-Request-ID": first },
      json: { error: { code: "version_conflict", correlation_id: first } },
    }),
  );
  await page.goto("/app/work");
  await page.getByRole("button", { name: example.title }).click();
  await page.getByLabel("Новое состояние").selectOption("in_progress");
  await expect(page.getByRole("alert")).toContainText(
    "Коллега уже изменил задачу",
  );
  await expect(page.getByLabel("Новое состояние")).toBeDisabled();
  await page.getByRole("button", { name: "Обновить данные" }).click();
  await expect(page.getByLabel("Новое состояние")).toHaveCount(0);
});

test("a changed permission version discards the inspector and cached task", async ({
  page,
}) => {
  await page.clock.install();
  const data = await fixture(page);
  data.items.push(example);
  await page.goto("/app/work");
  await page.getByRole("button", { name: example.title }).click();
  await expect(page.getByText(example.brief)).toBeVisible();
  data.items.length = 0;
  data.setSession({
    ...session,
    access_version: "v2",
    workspaces: [{ ...session.workspaces[0], permissions: ["workspace.read"] }],
  });
  await page.clock.fastForward(11_000);
  await expect(page.getByText(example.brief)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Создать задачу" }),
  ).toBeDisabled();
  await page.getByRole("link", { name: "Аудит", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Недостаточно прав");
});

test("server permission denial immediately hides private state", async ({
  page,
}) => {
  const data = await fixture(page);
  data.items.push(example);
  await page.route("**/work-items/*/transition", (route) =>
    route.fulfill({ status: 403, json: { error: { code: "access_denied" } } }),
  );
  await page.goto("/app/work");
  await page.getByRole("button", { name: example.title }).click();
  await page.getByLabel("Новое состояние").selectOption("in_progress");
  await expect(
    page.getByRole("heading", { name: "Доступ изменился" }),
  ).toBeVisible();
  await expect(page.getByText(example.brief)).toHaveCount(0);
});

test("offline state and narrow viewport do not hide controls", async ({
  page,
  context,
}) => {
  await fixture(page);
  await page.goto("/app/work");
  await expect(
    page.getByRole("heading", { name: "Задачи", exact: true }),
  ).toBeVisible();
  await context.setOffline(true);
  await expect(page.getByRole("alert")).toContainText("Нет связи");
  await expect(
    page.getByRole("button", { name: "Создать задачу" }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await context.setOffline(false);
});

test("synthetic workspace visual and keyboard check", async ({
  page,
}, info) => {
  const data = await fixture(page);
  data.items.push(example, {
    ...example,
    id: second,
    title: "Подготовить контент-план на сентябрь",
    state: "in_progress",
    allowed_transitions: ["done", "cancelled"],
  });
  await page.goto("/app/work");
  await page.getByRole("button", { name: example.title }).click();
  await expect(page.getByText(example.brief)).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
  await page.screenshot({
    path: `../output/playwright/workspace-${info.project.name}.png`,
    fullPage: true,
  });
});
