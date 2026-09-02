import { expect, test, type Page } from "@playwright/test";
import type { components } from "../src/api/schema";

const id = "11111111-1111-4111-8111-111111111111";
const source: components["schemas"]["Citation"] = {
  chunk_id: id,
  document_id: id,
  document_version_id: id,
  index_id: id,
  content_hash: "a".repeat(64),
  title: "Правила тестового бренда",
  section: "Голос",
  text: "Точный и спокойный тон. <script>window.shouldNeverRun = true</script>",
  source_uri: "owner-input",
  source_date: "2026-09-01T00:00:00Z",
  effective_to: "2027-09-01T00:00:00Z",
  authority: "owner_reviewed_reference",
};

async function fixture(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        user_id: id,
        display_name: "Тестовый сотрудник",
        mfa: true,
        access_version: "v1",
        workspaces: [
          {
            id,
            name: "GreenAurum · тест",
            timezone: "Europe/Moscow",
            permissions: [
              "workspace.read",
              "content.approve",
              "knowledge.write",
            ],
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/workspaces/*/catalog/brands*", (route) =>
    route.fulfill({
      json: { items: [{ id, name: "Тестовый бренд" }], next_cursor: null },
    }),
  );
  await page.route("**/knowledge/search", (route) =>
    route.fulfill({
      json: {
        run_id: id,
        mode: "fts",
        algorithm: "ru-simple-v1",
        citations: [source],
        warning: "Reference only",
      },
    }),
  );
  await page.route("**/knowledge/documents*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.route("**/knowledge/profiles", (route) =>
    route.fulfill({
      json: [
        {
          name: "product_expert",
          purpose: "Проверка источников",
          version: "reference-assessment-v1",
          status: "testing",
        },
      ],
    }),
  );
  await page.route("**/knowledge/runs*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.route("**/knowledge/notes*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
}

test("search shows verifiable sources as inert text and supports keyboard", async ({
  page,
}, info) => {
  await fixture(page);
  await page.goto("/app/knowledge");
  await expect(
    page.getByRole("heading", { name: "База знаний", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Бренд", exact: true })
    .selectOption(id);
  await page.getByLabel("Запрос").fill("тон");
  await page.getByRole("button", { name: "Найти источники" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Найдено фрагментов: 1")).toBeVisible();
  await expect(page.locator(".knowledge-excerpt")).toContainText("<script>");
  expect(await page.evaluate(() => "shouldNeverRun" in window)).toBe(false);
  await page.getByText("Версия и происхождение", { exact: true }).click();
  await expect(
    page.getByText("SHA-256 фрагмента", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `../output/playwright/knowledge-${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.route("**/knowledge/search", (route) =>
    route.fulfill({
      json: {
        run_id: id,
        mode: "fts",
        algorithm: "ru-simple-v1",
        citations: [],
        warning: "Reference only",
      },
    }),
  );
  await page.getByRole("button", { name: "Найти источники" }).click();
  await expect(page.getByText("Найдено фрагментов: 0")).toBeVisible();
  await expect(page.locator(".knowledge-excerpt")).toHaveCount(0);
});

test("empty views and profile gates do not offer approval or paid execution", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/app/knowledge");
  await page.getByRole("button", { name: "Документы", exact: true }).click();
  await expect(page.getByText("Документов пока нет.")).toBeVisible();
  await page.getByRole("button", { name: "AI-профили", exact: true }).click();
  await expect(page.getByText("Тестирование", { exact: true })).toBeVisible();
  await expect(page.getByText("Запусков пока нет.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Одобрить|Оплатить|Запустить/ }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Пробелы и память", exact: true })
    .click();
  await expect(page.getByText("Предложений пока нет.")).toBeVisible();
});

test("revocation hides private knowledge immediately", async ({ page }) => {
  await fixture(page);
  await page.route("**/knowledge/search", (route) =>
    route.fulfill({ status: 403, json: { error: { code: "access_denied" } } }),
  );
  await page.goto("/app/knowledge");
  await page
    .getByRole("combobox", { name: "Бренд", exact: true })
    .selectOption(id);
  await page.getByLabel("Запрос").fill("тон");
  await page.getByRole("button", { name: "Найти источники" }).click();
  await expect(
    page.getByRole("heading", { name: "Доступ изменился" }),
  ).toBeVisible();
  await expect(page.getByLabel("Запрос")).toHaveCount(0);
});

const definition: components["schemas"]["EvalDefinition"] = {
  title: "Проверка тестового корпуса",
  origin: "owner_curated",
  limit: 5,
  thresholds: { precision: 0.8, recall: 1, max_case_ms: 1000 },
  cases: [
    {
      key: "exact",
      category: "exact",
      audience: "workspace",
      query: "Крем <script>window.evalShouldNeverRun = true</script>",
      expected_document_ids: [id],
      forbidden_document_ids: [],
    },
  ],
};
const evaluation: components["schemas"]["EvalRunDetail"] = {
  id,
  actor_id: id,
  brand_id: id,
  dataset_id: id,
  dataset_hash: "b".repeat(64),
  corpus_hash: "c".repeat(64),
  report_hash: "d".repeat(64),
  created_at: "2026-09-03T10:00:00Z",
  stale: true,
  stale_reasons: ["corpus_changed"],
  acceptance_blockers: ["corpus_changed", "quality_thresholds_failed"],
  baseline_current: false,
  decision: "accept_baseline",
  review_reason: "Ранее проверен владельцем",
  definition,
  corpus: [],
  report: {
    algorithm: "ru-simple-v1",
    metric_version: "source-macro-v1",
    passed: false,
    precision: 0.75,
    recall: 0.5,
    citation_validity: 1,
    negative_pass: true,
    forbidden_pass: true,
    duration_ms: 42,
    cases: [
      {
        key: "exact",
        precision: 0,
        recall: 0,
        citation_validity: 1,
        negative_pass: true,
        forbidden_pass: true,
        latency_ms: 12,
        passed: false,
        missing_document_ids: [id],
        unexpected_document_ids: [],
        hits: [],
      },
    ],
  },
};

async function evaluationFixture(page: Page) {
  await fixture(page);
  await page.route("**/knowledge/evaluations/datasets*", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id,
            brand_id: id,
            family_id: id,
            actor_id: id,
            number: 2,
            content_hash: "b".repeat(64),
            created_at: "2026-09-03T10:00:00Z",
            definition,
          },
        ],
        next_cursor: null,
      },
    }),
  );
  await page.route("**/knowledge/evaluations/runs?*", (route) =>
    route.fulfill({ json: { items: [evaluation], next_cursor: null } }),
  );
  await page.route(`**/knowledge/evaluations/runs/${id}`, (route) =>
    route.fulfill({ json: evaluation }),
  );
  await page.goto("/app/knowledge");
  await page
    .getByRole("button", { name: "Качество поиска", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Проверка тестового корпуса · версия 2" })
    .click();
  await page.getByRole("button", { name: /^Отчёт ·/ }).click();
}

test("evaluation shows historical acceptance separately from freshness and inert questions", async ({
  page,
}, info) => {
  await evaluationFixture(page);
  await expect(
    page.getByText("Исторический отчёт — нужна новая проверка"),
  ).toBeVisible();
  await expect(
    page.getByText("Корпус изменился или источник утратил актуальность"),
  ).toBeVisible();
  await expect(
    page.getByText("Точность источников", { exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => "evalShouldNeverRun" in window)).toBe(false);
  await page
    .getByText("Ожидания и найденные источники · exact", { exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByText(`Пропущены: ${id}`, { exact: true }),
  ).toBeVisible();
  await page
    .getByText("Пороги, решение и точные версии", { exact: true })
    .click();
  await expect(
    page.getByText("Принят как эталон FTS", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Подтвердить|Активировать|Опубликовать/ }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `../output/playwright/evaluation-${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
});

test("failed freshness refresh hides the previous report", async ({ page }) => {
  await evaluationFixture(page);
  await expect(
    page.getByText("Исторический отчёт — нужна новая проверка"),
  ).toBeVisible();
  await page.route(`**/knowledge/evaluations/runs/${id}`, (route) =>
    route.fulfill({ status: 503, json: { error: { code: "unavailable" } } }),
  );
  await page
    .getByRole("button", { name: "Проверить актуальность отчёта" })
    .click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("Результат проверки поиска")).toHaveCount(0);
});

test("evaluation access revocation clears report and private questions", async ({
  page,
}) => {
  await evaluationFixture(page);
  await expect(page.getByLabel("Результат проверки поиска")).toBeVisible();
  await page.route(`**/knowledge/evaluations/runs/${id}`, (route) =>
    route.fulfill({ status: 403, json: { error: { code: "access_denied" } } }),
  );
  await page
    .getByRole("button", { name: "Проверить актуальность отчёта" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Доступ изменился" }),
  ).toBeVisible();
  await expect(
    page.getByText(definition.cases[0]!.query, { exact: true }),
  ).toHaveCount(0);
});

test("empty evaluation workspace explains chat workflow", async ({ page }) => {
  await fixture(page);
  await page.route("**/knowledge/evaluations/datasets*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.goto("/app/knowledge");
  await page
    .getByRole("button", { name: "Качество поиска", exact: true })
    .click();
  await expect(page.getByText(/Тестовых наборов пока нет/)).toBeVisible();
});
