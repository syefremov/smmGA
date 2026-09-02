import { expect, test, type Page } from "@playwright/test";
import type { components } from "../src/api/schema";
import type { Command } from "../src/api/content";

const wid = "11111111-1111-4111-8111-111111111111";
const pid = "22222222-2222-4222-8222-222222222222";
type Post = components["schemas"]["PostView"];
type Copy = components["schemas"]["WorkingCopyView"];
const original: Post = {
  id: pid,
  brand_id: wid,
  title: "Знакомство с GreenAurum",
  state: "draft",
  version: 2,
  current_revision_id: wid,
  brief_id: wid,
  idea_id: null,
  active_approval_id: null,
  history_truncated: false,
  comments: [],
  decisions: [],
  revisions: [
    {
      id: wid,
      number: 1,
      actor_id: wid,
      created_at: "2026-09-02T12:00:00Z",
      content_hash: "a".repeat(64),
      media_manifest: [],
      body: {
        variants: [
          {
            platform: "vk",
            destination: "vk:group:123",
            text: "Синтетический текст для проверки редактора.",
            media: [],
          },
        ],
        fact_ids: [],
        knowledge_gaps: [],
      },
    },
  ],
};

async function fixture(page: Page, readonly = false) {
  const post = structuredClone(original);
  const commands: Command[] = [];
  const records: components["schemas"]["RecordView"][] = [];
  const extraPosts: Post[] = [];
  let copy: Copy | null = null;
  let conflict = false;
  let activePackage: components["schemas"]["PackageView"] | null = null;
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        user_id: wid,
        display_name: "Тестовый владелец",
        mfa: true,
        access_version: "content-v1",
        workspaces: [
          {
            id: wid,
            name: "GreenAurum · тест",
            timezone: "Europe/Moscow",
            permissions: readonly
              ? ["workspace.read"]
              : [
                  "workspace.read",
                  "content.edit",
                  "content.plan",
                  "content.approve",
                  "content.publish",
                  "content.comment",
                ],
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/workspaces/*/content/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/commands")) {
      const c = route.request().postDataJSON() as Command;
      commands.push(c);
      expect(c.idempotency_key).toBeTruthy();
      if (c.action === "record_create") {
        records.push({
          id: wid,
          family_id: wid,
          number: 1,
          body: c.body,
          confirmed_by: null,
          content_hash: "a".repeat(64),
          created_at: "2026-09-02T12:00:00Z",
          expires_at: c.expires_at,
        });
        return route.fulfill({
          json: { entity_id: wid, version: 1, action: c.action },
        });
      }
      if (c.action === "post_create") {
        post.title = c.title;
        post.brief_id = c.brief_id;
        post.idea_id = c.idea_id ?? null;
        post.version = 1;
        post.revisions = [];
        post.current_revision_id = null;
      }
      if (conflict && c.action === "revision_save") {
        conflict = false;
        post.version++;
        post.revisions[0].body.variants[0].text = "Правка коллеги";
        return route.fulfill({
          status: 409,
          json: { error: { code: "version_conflict" } },
        });
      }
      if ("expected_version" in c)
        expect(c.expected_version).toBe(post.version);
      if (c.action === "working_copy_save")
        copy = {
          post_id: pid,
          version: (copy?.version ?? 0) + 1,
          base_version: c.base_version,
          expires_at: "2099-09-10T12:00:00Z",
          body: c.body,
        };
      if (c.action === "revision_save") {
        post.version++;
        const r = {
          ...(post.revisions[0] ?? original.revisions[0]),
          id: crypto.randomUUID(),
          number: (post.revisions[0]?.number ?? 0) + 1,
          body: c.body,
          content_hash: "b".repeat(64),
        };
        post.revisions.unshift(r);
        post.current_revision_id = r.id;
        post.state = "draft";
        post.active_approval_id = null;
        copy = null;
      }
      if (c.action === "review_request") {
        post.version++;
        post.state = "in_review";
      }
      if (c.action === "post_decide") {
        expect(c.content_hash).toBe(post.revisions[0].content_hash);
        expect(c.revision_id).toBe(post.current_revision_id);
        expect(c.human_confirmed).toBe(true);
        post.version++;
        post.state = c.decision === "approve" ? "approved" : "rejected";
        post.active_approval_id = crypto.randomUUID();
        post.decisions.push({
          id: post.active_approval_id,
          revision_id: c.revision_id,
          actor_id: wid,
          created_at: "2026-09-02T12:00:00Z",
          decision: c.decision,
          reason: c.reason,
          content_hash: c.content_hash,
        });
      }
      if (c.action === "package_prepare") {
        post.version++;
        post.state = "package_ready";
        activePackage = {
          id: wid,
          post_id: pid,
          revision_id: c.revision_id,
          content_hash: c.content_hash,
          mode: "manual",
          status: "active",
          timezone: "Europe/Moscow",
          created_at: "2026-09-02T12:00:00Z",
          scheduled_at: c.scheduled_at,
          manifest: {
            external_dispatch: false,
            revision: structuredClone(post.revisions[0]),
          },
        };
      }
      if (c.action === "package_cancel" && activePackage) {
        post.version++;
        post.state = "approved";
        activePackage.status = "cancelled";
      }
      if (c.action === "comment_add")
        post.comments.push({
          id: crypto.randomUUID(),
          revision_id: c.revision_id,
          actor_id: wid,
          created_at: "2026-09-02T12:00:00Z",
          text: c.text,
        });
      return route.fulfill({
        json: {
          entity_id: c.action === "package_prepare" ? wid : pid,
          version:
            c.action === "working_copy_save" ? copy?.version : post.version,
          action: c.action,
        },
      });
    }
    if (path.endsWith("/working-copy")) return route.fulfill({ json: copy });
    if (path.endsWith("/preflight"))
      return route.fulfill({
        json: {
          revision_id: post.current_revision_id,
          content_hash: post.revisions[0].content_hash,
          checked_at: "2026-09-02T12:00:00Z",
          findings: [
            {
              code: "human_claims_review_required",
              severity: "warning",
              location: "review",
              record_id: null,
            },
          ],
          checked_record_ids: [],
          passed: true,
          ai_review: "not_run",
        },
      });
    if (path.endsWith("/posts"))
      return route.fulfill({
        json: { items: [post, ...extraPosts], next_cursor: null },
      });
    if (path.endsWith(`/posts/${pid}`)) return route.fulfill({ json: post });
    if (path.endsWith("/packages"))
      return route.fulfill({
        json: {
          items: activePackage ? [activePackage] : [],
          next_cursor: null,
        },
      });
    if (path.endsWith(`/packages/${wid}`))
      return route.fulfill({ json: activePackage });
    if (path.endsWith("/records"))
      return route.fulfill({ json: { items: records, next_cursor: null } });
    return route.fulfill({
      status: 404,
      json: { error: { code: "not_found" } },
    });
  });
  return {
    post,
    extraPosts,
    commands,
    setCopy: (value: Copy) => {
      copy = value;
    },
    conflict: () => {
      conflict = true;
    },
  };
}

async function open(page: Page) {
  await page.goto("/app/content");
  await page.getByRole("button", { name: /Знакомство с GreenAurum/ }).click();
  await expect(page.getByLabel("Текст 1", { exact: true })).toBeVisible();
}

test("create a typed brief and the first revision of a new post", async ({
  page,
}) => {
  const data = await fixture(page);
  await page.goto("/app/materials");
  await page.getByRole("button", { name: "Добавить материал" }).click();
  await page.getByLabel("Название материала").fill("Бриф для теста");
  await page.getByLabel("ID бренда", { exact: true }).fill(wid);
  await page
    .getByLabel("Задача брифа")
    .fill("Проверить процесс без внешней публикации");
  await page.getByLabel("Аудитория", { exact: true }).fill("Тестовая команда");
  await page.getByLabel("Актуально до (UTC)").fill("2099-09-10T12:00");
  await page.getByRole("button", { name: "Сохранить материал" }).click();
  await expect(
    page.getByRole("button", { name: "Бриф для теста · версия 1" }),
  ).toBeVisible();
  expect(data.commands[0]).toMatchObject({
    action: "record_create",
    body: { kind: "brief", brand_id: wid },
  });
  await page.getByRole("link", { name: "Контент", exact: true }).click();
  await page.getByRole("button", { name: "Новый пост" }).click();
  await page.getByLabel("Название поста").fill("Пост с нуля");
  await page.getByLabel("ID брифа", { exact: true }).fill(wid);
  await page.getByRole("button", { name: "Создать пост", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Пост с нуля" }),
  ).toBeVisible();
  await page.getByLabel("Назначение 1", { exact: true }).fill("vk:group:123");
  await page
    .getByLabel("Текст 1", { exact: true })
    .fill("Первая синтетическая редакция");
  await page
    .getByRole("button", { name: "Сохранить редакцию", exact: true })
    .click();
  await expect(page.locator(".save-status")).toContainText(
    "Новая редакция сохранена",
  );
  expect(data.post.revisions).toHaveLength(1);
});

test("switching posts warns before losing unsaved text", async ({ page }) => {
  const data = await fixture(page);
  data.extraPosts.push({
    ...structuredClone(original),
    id: "33333333-3333-4333-8333-333333333333",
    title: "Другой пост",
  });
  await open(page);
  await page
    .getByLabel("Текст 1", { exact: true })
    .fill("Не терять эту правку");
  let warned = false;
  page.once("dialog", async (dialog) => {
    warned = true;
    await dialog.dismiss();
  });
  await page.getByRole("button", { name: /Другой пост/ }).click();
  expect(warned).toBeTruthy();
  await expect(page.getByLabel("Текст 1", { exact: true })).toHaveValue(
    "Не терять эту правку",
  );
});

test("failed refresh keeps unsaved input and disables writes until recovery", async ({
  page,
}) => {
  await fixture(page);
  await open(page);
  await page
    .getByLabel("Текст 1", { exact: true })
    .fill("Локальная правка при потере связи");
  const pattern = `**/content/posts/${pid}`;
  await page.route(pattern, (route) =>
    route.fulfill({
      status: 503,
      json: { error: { code: "service_unavailable" } },
    }),
  );
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("Текст 1", { exact: true })).toHaveValue(
    "Локальная правка при потере связи",
  );
  await expect(
    page.getByRole("button", { name: "Сохранить редакцию", exact: true }),
  ).toBeDisabled();
  await page.unroute(pattern);
  await page.getByRole("button", { name: "Перечитать данные" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByLabel("Текст 1", { exact: true })).toHaveValue(
    "Локальная правка при потере связи",
  );
});

test("edit, review, exact owner approval, manual package and mobile cancellation", async ({
  page,
}, info) => {
  const data = await fixture(page);
  await open(page);
  await page
    .getByLabel("Текст 1", { exact: true })
    .fill("Обновлённый синтетический текст. Без публикации.");
  await page
    .getByRole("button", { name: "Сохранить редакцию", exact: true })
    .click();
  await expect(page.locator(".save-status")).toContainText(
    "Новая редакция сохранена",
  );
  await page.getByRole("button", { name: "Передать на проверку" }).click();
  await page
    .getByLabel("Решение владельца: основание")
    .fill("Текст и источники проверены");
  const approve = page.getByRole("button", { name: "Одобрить редакцию №2" });
  await expect(approve).toBeDisabled();
  await page.getByLabel(/Я проверил/).check();
  await expect(approve).toBeEnabled();
  await page.screenshot({
    path: `../output/playwright/content-${info.project.name}.png`,
    fullPage: true,
  });
  await approve.click();
  await page
    .getByLabel("Время ручной подготовки (UTC)")
    .fill("2099-09-03T12:00");
  await page.getByLabel(/Подтверждаю время/).check();
  await page.getByRole("button", { name: "Подготовить ручной пакет" }).click();
  await expect(
    page
      .getByRole("region", { name: "Редактор поста" })
      .getByText("Пакет готов · v6"),
  ).toBeVisible();
  expect(data.commands.some((c) => c.action === "post_decide")).toBeTruthy();
  await page.getByRole("link", { name: "Календарь", exact: true }).click();
  await page.getByRole("button", { name: "Открыть пакет" }).click();
  await page.getByLabel(/Отменить этот внутренний пакет/).check();
  await page
    .getByRole("button", { name: "Отменить пакет", exact: true })
    .click();
  await expect(
    page.getByText("Отменён", { exact: true }).first(),
  ).toBeVisible();
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("concurrent edit preserves input and requires explicit reconciliation", async ({
  page,
}) => {
  const data = await fixture(page);
  data.conflict();
  await open(page);
  await page.getByLabel("Текст 1", { exact: true }).fill("Моя правка");
  await page
    .getByRole("button", { name: "Сохранить редакцию", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Версия изменилась");
  await page.getByRole("button", { name: "Перечитать данные" }).click();
  await expect(page.getByLabel("Текст 1", { exact: true })).toHaveValue(
    "Моя правка",
  );
  await expect(
    page.getByRole("button", { name: "Сохранить редакцию", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Сверено: использовать v3 как основу" })
    .click();
  await page
    .getByRole("button", { name: "Сохранить редакцию", exact: true })
    .click();
  await expect(page.locator(".save-status")).toContainText(
    "Новая редакция сохранена",
  );
});

test("a restored different working copy can never approve the stored revision", async ({
  page,
}) => {
  const data = await fixture(page);
  data.post.state = "in_review";
  const body = structuredClone(original.revisions[0].body);
  body.variants[0].text = "Личная неподтверждённая копия";
  data.setCopy({
    post_id: pid,
    version: 1,
    base_version: 2,
    expires_at: "2099-09-10T12:00:00Z",
    body,
  });
  await open(page);
  await expect(page.getByLabel("Текст 1", { exact: true })).toHaveValue(
    body.variants[0].text,
  );
  await page.getByLabel("Решение владельца: основание").fill("Просмотрено");
  await page.getByLabel(/Я проверил/).check();
  await expect(
    page.getByRole("button", { name: "Одобрить редакцию №1" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Сохранить рабочую копию" }).click();
  await expect(page.locator(".save-status")).toContainText(
    "Рабочая копия сохранена",
  );
  await expect(
    page.getByRole("button", { name: "Одобрить редакцию №1" }),
  ).toBeDisabled();
  expect(data.commands.some((c) => c.action === "post_decide")).toBeFalsy();
});

test("viewer has no mutation controls and source text is not executable", async ({
  page,
}) => {
  const data = await fixture(page, true);
  data.post.state = "in_review";
  data.post.revisions[0].body.variants[0].text =
    '<img src=x onerror="window.injected=true">';
  await open(page);
  await expect(page.getByLabel("Текст 1", { exact: true })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: /Одобрить редакцию/ }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Сохранить редакцию", exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => Object.hasOwn(window, "injected")),
  ).toBeFalsy();
});

test("review comments and history are visible without changing approval", async ({
  page,
}) => {
  const data = await fixture(page);
  data.post.state = "approved";
  data.post.active_approval_id = wid;
  await open(page);
  await page
    .getByLabel("Новый комментарий")
    .fill("Нужен источник для следующей редакции.");
  await page.getByRole("button", { name: "Добавить комментарий" }).click();
  await expect(
    page.getByText("Нужен источник для следующей редакции.", { exact: true }),
  ).toBeVisible();
  expect(data.post.state).toBe("approved");
  expect(data.post.active_approval_id).toBe(wid);
});
