// @vitest-environment node
import { expect, test, vi } from "vitest";
import { MAX_FILE_BYTES, prepareFile } from "./upload";

async function hashBytes(bytes: ArrayBuffer) {
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

test.each([
  ["brief.MD", "markdown", "\ufeff# Крем\r\nИсходный текст"],
  ["brief.markdown", "markdown", "<script>not executable</script>"],
  ["table.csv", "csv", '\ufeffname,note\r\nКрем,"=1+1"\r\n'],
  ["reference.html", "html", "<p>Крем &amp; уход</p>"],
  ["reference.HTM", "html", "<script>parser must reject on server</script>"],
])(
  "preserves UTF-8 originals and exact upload identity for %s",
  async (name, format, source) => {
    const bytes = new TextEncoder().encode(source);
    const file = new File([bytes], name, { type: "application/octet-stream" });
    const result = await prepareFile(file, "workspace", "brand");
    const hash = await hashBytes(bytes.buffer);
    expect(result.format).toBe(format);
    expect(result.content_hash).toBe(hash);
    expect(
      Uint8Array.from(atob(result.content_base64), (c) => c.charCodeAt(0)),
    ).toEqual(bytes);
    expect(result.idempotency_key).toBe(
      "browser-file-v1:" +
        (await hashBytes(
          new TextEncoder().encode(
            JSON.stringify([
              "browser-file-v1",
              "workspace",
              "brand",
              name,
              format,
              hash,
            ]),
          ).buffer,
        )),
    );
    expect(
      await prepareFile(new File([bytes], name), "workspace", "brand"),
    ).toEqual(result);
  },
);

test.each([
  ["bad.md", new Uint8Array([0xff]), "text_encoding_invalid"],
  ["bad.csv", new Uint8Array([0xff, 0xfe, 65, 0]), "text_encoding_invalid"],
  [
    "bad.html",
    new TextEncoder().encode("text\u0000"),
    "text_controls_rejected",
  ],
  ["bad.md", new TextEncoder().encode("text\u007f"), "text_controls_rejected"],
  ["bad.csv", new TextEncoder().encode("\ufeff%PDF-1.7"), "file_type_mismatch"],
  ["bad.html", new TextEncoder().encode("\ufeff \n"), "extracted_text_empty"],
  ["bad.constructor", new TextEncoder().encode("text"), "file_type_mismatch"],
])("rejects invalid text envelope %s (%s)", async (name, bytes, code) => {
  await expect(
    prepareFile(new File([bytes], name), "workspace", "brand"),
  ).rejects.toThrow(code);
});

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
