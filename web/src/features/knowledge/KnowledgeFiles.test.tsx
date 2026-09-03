import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import type { components } from "../../api/schema";
import { ApiError } from "../../api/client";
import { KnowledgeFiles } from "./KnowledgeFiles";
import { FileInputError } from "./upload";

const api = vi.hoisted(() => ({
  files: vi.fn(),
  file: vi.fn(),
  submit: vi.fn(),
  cancel: vi.fn(),
  history: vi.fn(),
  listCatalog: vi.fn(),
  prepareFile: vi.fn(),
}));
vi.mock("../../api/knowledgeFiles", () => api);
vi.mock("../../api/operations", () => ({ listCatalog: api.listCatalog }));
vi.mock("./upload", async (original) => ({
  ...(await original<typeof import("./upload")>()),
  prepareFile: api.prepareFile,
}));
const workspace: components["schemas"]["WorkspaceView"] = {
  id: "workspace",
  name: "Synthetic",
  timezone: "Europe/Moscow",
  permissions: ["knowledge.write"],
};
const file: components["schemas"]["FileDetail"] = {
  id: "file-id",
  brand_id: "brand",
  actor_id: "actor",
  filename: "Synthetic.pdf",
  format: "pdf",
  byte_size: 9,
  content_hash: "a".repeat(64),
  state: "queued",
  attempts: 0,
  error_code: null,
  created_at: "2026-09-03T10:00:00Z",
  version: 1,
  extraction: null,
  warning: "Synthetic unreviewed extraction",
};
function document(name = "Synthetic.pdf", text = "%PDF-1.7\nSynthetic only") {
  const file = new File([text], name, { type: "application/pdf" });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode(text).buffer,
  });
  return file;
}
function mount(w = workspace, offline = false) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <KnowledgeFiles workspace={w} offline={offline} />
    </QueryClientProvider>,
  );
  return {
    ...view,
    cache,
    change: (next = w, offline = false) =>
      view.rerender(
        <QueryClientProvider client={cache}>
          <KnowledgeFiles workspace={next} offline={offline} />
        </QueryClientProvider>,
      ),
  };
}
async function selectDocument(input = document()) {
  const user = userEvent.setup();
  await screen.findByRole("option", { name: "Synthetic brand" });
  await user.selectOptions(screen.getByLabelText("Бренд"), "brand");
  await user.upload(screen.getByLabelText("Документ"), input);
  return user;
}
function sendFile() {
  // jsdom does not synchronize its native file validity with user-event's FileList.
  const button = screen.getByRole("button", { name: "Загрузить файл" });
  expect(button).toBeEnabled();
  fireEvent.submit(button.closest("form")!);
}
beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.files.mockResolvedValue({ items: [], next_cursor: null });
  api.file.mockResolvedValue(file);
  api.listCatalog.mockResolvedValue({
    items: [{ id: "brand", name: "Synthetic brand" }],
    next_cursor: null,
  });
  api.history.mockResolvedValue({
    kind: "file",
    job_id: file.id,
    events: [],
    truncated: false,
  });
  api.submit.mockResolvedValue({ file_id: file.id });
  api.prepareFile.mockImplementation(
    async (input: File, wid: string, brand: string) => ({
      idempotency_key: `synthetic:${wid}:${brand}:${input.name}`,
      brand_id: brand,
      filename: input.name,
      format: "pdf",
      content_hash: "a".repeat(64),
      content_base64: "JVBERi0xLjc=",
    }),
  );
});

test("waits for the upload receipt, clears input and never places bytes in mutation cache or storage", async () => {
  let complete!: (value: { file_id: string }) => void;
  api.submit.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const storage = vi.spyOn(Storage.prototype, "setItem");
  const { cache } = mount();
  await selectDocument();
  sendFile();
  await waitFor(() => expect(api.submit).toHaveBeenCalledTimes(1));
  expect(
    screen.getByText("Отправляем файл. Это ещё не подтверждение обработки."),
  ).toBeVisible();
  expect(
    screen.queryByText(/Сервер подтвердил загрузку/),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText("Документ")).toBeDisabled();
  await act(async () => complete({ file_id: file.id }));
  expect(
    await screen.findByText(/Сервер подтвердил загрузку/),
  ).toHaveTextContent(file.id);
  expect(screen.getByLabelText("Документ")).toHaveValue("");
  expect(screen.getByLabelText("Бренд")).toHaveValue("brand");
  expect(cache.getMutationCache().getAll()).toEqual([]);
  expect(storage).not.toHaveBeenCalled();
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data),
    ),
  ).not.toContain("content_base64");
});

test("lost upload response preserves exact identity and rejects a changed file before reselecting and replaying", async () => {
  api.submit
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValue({ file_id: file.id });
  mount();
  const user = await selectDocument();
  sendFile();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Сервер не подтвердил результат",
  );
  const body = api.submit.mock.calls[0][1];
  await user.upload(screen.getByLabelText("Документ"), document("Changed.pdf"));
  sendFile();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Исход предыдущей загрузки неизвестен",
  );
  expect(api.submit).toHaveBeenCalledTimes(1);
  await user.upload(screen.getByLabelText("Документ"), document());
  sendFile();
  await screen.findByText(/Сервер подтвердил загрузку/);
  expect(api.submit.mock.calls[1][1]).toEqual(body);
});

test("disabled ingestion and local validation do not claim success", async () => {
  api.prepareFile.mockRejectedValueOnce(
    new FileInputError("file_type_mismatch"),
  );
  api.submit.mockRejectedValue(
    new ApiError(503, "binary_ingestion_disabled", "synthetic-request"),
  );
  mount();
  const user = await selectDocument(document("Bad.pdf", "not a pdf"));
  sendFile();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Расширение должно соответствовать содержимому",
  );
  expect(api.submit).not.toHaveBeenCalled();
  await user.upload(screen.getByLabelText("Документ"), document());
  sendFile();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Загрузка на сервере ещё не включена",
  );
  expect(
    screen.queryByText(/Сервер подтвердил загрузку/),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText("Документ")).not.toHaveValue("");
});

test("permissions and offline mode prevent upload and private reads", async () => {
  const view = mount({ ...workspace, permissions: [] });
  expect(screen.getByText("Нет доступа к загрузке файлов.")).toBeVisible();
  expect(api.files).not.toHaveBeenCalled();
  view.change(workspace, true);
  expect(screen.getByLabelText("Документ")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Загрузить файл" })).toBeDisabled();
  expect(api.listCatalog).not.toHaveBeenCalled();
  expect(api.submit).not.toHaveBeenCalled();
});

test("workspace change aborts a pending upload, clears selection and ignores late receipts", async () => {
  let complete!: (value: { file_id: string }) => void;
  api.submit.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const view = mount();
  await selectDocument();
  sendFile();
  await waitFor(() => expect(api.submit).toHaveBeenCalledTimes(1));
  const signal: AbortSignal = api.submit.mock.calls[0][2];
  view.change({ ...workspace, id: "other-workspace" });
  expect(signal.aborted).toBe(true);
  expect(screen.getByLabelText("Документ")).toHaveValue("");
  await act(async () => complete({ file_id: file.id }));
  expect(
    screen.queryByText(/Сервер подтвердил загрузку/),
  ).not.toBeInTheDocument();
  expect(api.file).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(api.files).toHaveBeenCalledWith(
      "other-workspace",
      undefined,
      expect.any(AbortSignal),
    ),
  );
});

test("untrusted extraction is inert, provenance is visible and no import or activation is offered", async () => {
  const text =
    '<script>window.shouldNeverRun=true</script><img src=x onerror="alert(1)">';
  api.files.mockResolvedValue({
    items: [{ ...file, state: "ready" }],
    next_cursor: null,
  });
  api.file.mockResolvedValue({
    ...file,
    state: "ready",
    extraction: {
      text,
      text_hash: "b".repeat(64),
      parser_version: "synthetic-parser",
      scan_engine: "synthetic-scanner",
      signature_version: "fixture-only",
      signatures_updated_at: file.created_at,
      scanned_at: file.created_at,
    },
  });
  api.history.mockResolvedValue({
    kind: "file",
    job_id: file.id,
    truncated: true,
    events: [
      {
        version: 1,
        state: "ready",
        attempts: 1,
        error_code: null,
        actor_id: null,
        created_at: file.created_at,
      },
    ],
  });
  const view = mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: file.filename }));
  await user.click(await screen.findByText("Прочитать извлечённый текст"));
  expect(screen.getByText(text)).toBeVisible();
  expect(view.container.querySelector("script, iframe, img")).toBeNull();
  expect(screen.getByText("b".repeat(64))).toBeVisible();
  expect(screen.getByText(/Показаны последние 50 событий/)).toBeVisible();
  expect(
    screen.queryByRole("button", {
      name: /Импорт|Активировать|Одобрить|Отменить/,
    }),
  ).not.toBeInTheDocument();
  view.change({ ...workspace, id: "other" });
  expect(screen.queryByText(text)).not.toBeInTheDocument();
  await waitFor(() =>
    expect(
      view.cache.getQueryData([workspace.id, "file", file.id]),
    ).toBeUndefined(),
  );
});

test("cancel retains the exact version and key after a lost reply despite subsequent reads", async () => {
  api.files.mockResolvedValue({ items: [file], next_cursor: null });
  api.cancel.mockRejectedValueOnce(new TypeError("offline")).mockResolvedValue({
    kind: "file",
    job_id: file.id,
    state: "cancelled",
    version: 3,
  });
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: file.filename }));
  await user.click(
    await screen.findByRole("button", { name: "Отменить обработку" }),
  );
  await screen.findByRole("alert");
  const body = api.cancel.mock.calls[0][1];
  expect(body).toMatchObject({
    job_id: file.id,
    kind: "file",
    expected_version: 1,
  });
  api.file.mockResolvedValue({ ...file, state: "processing", version: 2 });
  await user.click(screen.getByRole("button", { name: "Обновить файл" }));
  await screen.findByText(/Проверка и извлечение · версия 2/);
  expect(api.cancel).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Повторить отмену" }));
  await screen.findByText("Сервер подтвердил отмену, версия 3.");
  expect(api.cancel.mock.calls[1][1]).toEqual(body);
});

test("version conflict requires an explicit fresh action; refreshing never sends another cancel", async () => {
  api.files.mockResolvedValue({ items: [file], next_cursor: null });
  api.cancel.mockRejectedValue(
    new ApiError(409, "ingestion_conflict", "synthetic-request"),
  );
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: file.filename }));
  await user.click(
    await screen.findByRole("button", { name: "Отменить обработку" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Версия задания изменилась",
  );
  api.file.mockResolvedValue({ ...file, version: 2 });
  await user.click(screen.getByRole("button", { name: "Обновить файл" }));
  await screen.findByText(/В очереди · версия 2/);
  expect(api.cancel).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Отменить обработку" }));
  await waitFor(() => expect(api.cancel).toHaveBeenCalledTimes(2));
  expect(api.cancel.mock.calls[1][1].expected_version).toBe(2);
  expect(api.cancel.mock.calls[1][1].idempotency_key).not.toBe(
    api.cancel.mock.calls[0][1].idempotency_key,
  );
});

test("brand pagination cannot submit a selected document", async () => {
  api.listCatalog.mockResolvedValue({
    items: [{ id: "brand", name: "Synthetic brand" }],
    next_cursor: "next-brand",
  });
  mount();
  await selectDocument();
  const fieldset = screen.getByRole("group", { name: "Загрузить документ" });
  fireEvent.click(within(fieldset).getByRole("button", { name: "Далее" }));
  await waitFor(() =>
    expect(api.listCatalog).toHaveBeenCalledWith(
      workspace.id,
      "brands",
      "next-brand",
      expect.any(AbortSignal),
    ),
  );
  expect(api.submit).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Бренд")).toHaveValue("");
});
