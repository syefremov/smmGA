import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, expect, test, vi } from "vitest";
import { ApiError } from "../../api/client";
import { time, useCommand } from "./hooks";

const { execute } = vi.hoisted(() => ({ execute: vi.fn() }));
vi.mock("../../api/content", () => ({ execute }));
beforeEach(() => execute.mockReset());
const input = {
  action: "post_create" as const,
  title: "Synthetic",
  brief_id: "test",
};
function hook() {
  const cache = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return renderHook(() => useCommand("workspace"), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={cache}>{children}</QueryClientProvider>
    ),
  });
}
test("uncertain mutations retain a key and reject a different payload", async () => {
  execute.mockRejectedValueOnce(new TypeError("offline")).mockResolvedValue({
    entity_id: "post",
    version: 1,
    action: "post_create",
  });
  const { result } = hook();
  await act(async () => {
    await expect(result.current.mutateAsync(input)).rejects.toThrow("offline");
  });
  const key = execute.mock.calls[0][1].idempotency_key;
  await act(async () => {
    await expect(
      result.current.mutateAsync({ ...input, title: "Different" }),
    ).rejects.toThrow("pending_request_changed");
  });
  expect(execute).toHaveBeenCalledTimes(1);
  await act(async () => {
    await result.current.mutateAsync(input);
  });
  expect(execute.mock.calls[1][1].idempotency_key).toBe(key);
});
test("known version conflict permits an explicitly corrected command", async () => {
  execute
    .mockRejectedValueOnce(new ApiError(409, "version_conflict", "test"))
    .mockResolvedValue({
      entity_id: "post",
      version: 2,
      action: "post_create",
    });
  const { result } = hook();
  await act(async () => {
    await expect(result.current.mutateAsync(input)).rejects.toThrow(
      "version_conflict",
    );
  });
  await act(async () => {
    await result.current.mutateAsync({ ...input, title: "Corrected" });
  });
  expect(execute.mock.calls[0][1].idempotency_key).not.toBe(
    execute.mock.calls[1][1].idempotency_key,
  );
});
test("calendar uses explicit workspace timezone", () => {
  expect(time("2026-09-02T12:00:00Z", "Europe/Moscow")).toContain("15:00");
  expect(time("2026-09-02T12:00:00Z", "UTC")).toContain("12:00");
});
