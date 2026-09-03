import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SubmitFile, CancelFile } from "./knowledgeFiles";

let api: typeof import("./knowledgeFiles");

const NativeRequest = Request;
const fetchMock = vi.fn();
beforeEach(async () => {
  // Node's fetch requires absolute URLs; browsers resolve the same-origin client paths.
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(
          typeof input === "string"
            ? new URL(input, "https://synthetic.invalid")
            : input,
          init,
        );
      }
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  fetchMock
    .mockReset()
    .mockImplementation(async () =>
      Response.json({ file_id: "synthetic-file" }),
    );
  vi.spyOn(document, "cookie", "get").mockReturnValue(
    "__Host-smm-csrf=synthetic-csrf-fixture",
  );
  vi.resetModules();
  api = await import("./knowledgeFiles");
});
afterEach(() => vi.unstubAllGlobals());

const body: SubmitFile = {
  idempotency_key: "synthetic-test-upload",
  brand_id: "brand",
  filename: "Synthetic.pdf",
  format: "pdf",
  content_hash: "a".repeat(64),
  content_base64: "JVBERi0xLjc=",
};
test("generated upload transport uses same-origin session/CSRF and exact payload, without bearer auth", async () => {
  expect(await api.submit("workspace", body)).toEqual({
    file_id: "synthetic-file",
  });
  const request: Request = fetchMock.mock.calls[0][0];
  expect(request.url).toBe(
    "https://synthetic.invalid/api/v1/workspaces/workspace/knowledge/files",
  );
  expect(request.method).toBe("POST");
  expect(request.credentials).toBe("same-origin");
  expect(request.headers.get("X-CSRF-Token")).toBe("synthetic-csrf-fixture");
  expect(request.headers.has("Authorization")).toBe(false);
  expect(await request.json()).toEqual(body);
});

test("private reads preserve workspace, cursor, job kind and abort signal", async () => {
  const controller = new AbortController();
  await api.files("other", "next", controller.signal);
  await api.file("other", "id", controller.signal);
  await api.history("other", "id", controller.signal);
  const requests: Request[] = fetchMock.mock.calls.map(([request]) => request);
  expect(requests.map((request) => request.url)).toEqual([
    "https://synthetic.invalid/api/v1/workspaces/other/knowledge/files?cursor=next",
    "https://synthetic.invalid/api/v1/workspaces/other/knowledge/files/id",
    "https://synthetic.invalid/api/v1/workspaces/other/knowledge/jobs/file/id/history",
  ]);
  expect(
    requests.every(
      (request) =>
        request.method === "GET" && !request.headers.has("X-CSRF-Token"),
    ),
  ).toBe(true);
  controller.abort();
  expect(requests.every((request) => request.signal.aborted)).toBe(true);
});

test("cancel passes the exact version and idempotency key with CSRF", async () => {
  const command: CancelFile = {
    idempotency_key: "synthetic-test-cancel",
    kind: "file",
    job_id: "id",
    expected_version: 4,
  };
  await api.cancel("workspace", command);
  const request: Request = fetchMock.mock.calls[0][0];
  expect(request.url).toContain("/knowledge/jobs/cancel");
  expect(request.headers.get("X-CSRF-Token")).toBe("synthetic-csrf-fixture");
  expect(await request.json()).toEqual(command);
});

test.each([401, 403])(
  "%i triggers the existing session gate without retrying writes",
  async (status) => {
    const accessChanged = vi.fn();
    window.addEventListener("smm-access-changed", accessChanged);
    fetchMock.mockResolvedValue(
      Response.json(
        { error: { code: "access_denied" } },
        { status, headers: { "X-Request-ID": "synthetic-request" } },
      ),
    );
    try {
      await expect(api.submit("workspace", body)).rejects.toMatchObject({
        status,
        code: "access_denied",
        correlation: "synthetic-request",
      });
      expect(accessChanged).toHaveBeenCalledTimes(1);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("smm-access-changed", accessChanged);
    }
  },
);
