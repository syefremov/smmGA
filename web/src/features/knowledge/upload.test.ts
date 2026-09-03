// @vitest-environment node
import { expect, test, vi } from "vitest";
import { MAX_FILE_BYTES, prepareFile } from "./upload";

test.each([
  [
    "brief.PDF",
    "%PDF-1.7\nSynthetic only",
    "pdf",
    "adf852561f3e0bcde3e1e97c1e2945b1ef8d1f118b446e05f80b33ac53d4e806",
  ],
  [
    "brief.docx",
    "PK\x03\x04Synthetic only",
    "docx",
    "e0dde90d4a0a848cdcbabf926e066b5169a5c2372588972c751ee9c91c6b9a11",
  ],
])(
  "hashes and encodes exact bytes of %s",
  async (filename, text, format, hash) => {
    const file = new File([text], filename);
    const body = await prepareFile(file, "workspace", "brand");
    expect(body.format).toBe(format);
    expect(body.content_hash).toBe(hash);
    expect(atob(body.content_base64)).toBe(text);
    expect(body.filename).toBe(filename);
    expect(body.idempotency_key).toMatch(/^browser-file-v1:[0-9a-f]{64}$/);
    const reselected = await prepareFile(
      new File([text], filename),
      "workspace",
      "brand",
    );
    expect(reselected).toEqual(body);
    for (const [f, workspace, brand] of [
      [new File([text + "changed"], filename), "workspace", "brand"],
      [new File([text], "renamed." + format), "workspace", "brand"],
      [file, "other-workspace", "brand"],
      [file, "workspace", "other-brand"],
    ] as const)
      expect((await prepareFile(f, workspace, brand)).idempotency_key).not.toBe(
        body.idempotency_key,
      );
  },
);

test.each([
  ["zero.pdf", "", "file_size_invalid"],
  ["file.exe", "%PDF-1.7", "file_type_mismatch"],
  ["fake.pdf", "PK\x03\x04", "file_type_mismatch"],
  ["fake.docx", "%PDF-1.7", "file_type_mismatch"],
  ["bad:filename.pdf", "%PDF-1.7", "invalid_filename"],
  ["bad\nfilename.pdf", "%PDF-1.7", "invalid_filename"],
  ["a".repeat(157) + ".pdf", "%PDF-1.7", "invalid_filename"],
])("rejects invalid input %s", async (name, content, code) => {
  await expect(
    prepareFile(new File([content], name), "workspace", "brand"),
  ).rejects.toThrow(code);
});

test("checks the size before reading bytes and handles the 2 MiB boundary without spread overflow", async () => {
  const file = new File([new Uint8Array(MAX_FILE_BYTES + 1)], "large.pdf");
  const read = vi.spyOn(file, "arrayBuffer");
  await expect(prepareFile(file, "w", "b")).rejects.toThrow(
    "file_size_invalid",
  );
  expect(read).not.toHaveBeenCalled();
  const bytes = new Uint8Array(MAX_FILE_BYTES);
  bytes.set(new TextEncoder().encode("%PDF-"));
  const body = await prepareFile(new File([bytes], "limit.pdf"), "w", "b");
  const decoded = Uint8Array.from(atob(body.content_base64), (char) =>
    char.charCodeAt(0),
  );
  expect(decoded.length).toBe(MAX_FILE_BYTES);
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", decoded)),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  expect(hash).toBe(body.content_hash);
  expect(body.content_base64.length).toBeLessThanOrEqual(2_796_204);
});
