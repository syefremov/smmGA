import { expect, test } from "@playwright/test";

test("shows the shared system status", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Состояние системы" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Система готова");
  await expect(
    page.getByRole("rowheader", { name: "postgresql" }),
  ).toBeVisible();
});
