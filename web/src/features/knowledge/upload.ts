import type { SubmitFile } from "../../api/knowledgeFiles";

export const MAX_FILE_BYTES = 2 * 1024 * 1024;

export class FileInputError extends Error {}

async function sha256(bytes: ArrayBuffer) {
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

// Convenience checks only. Server validation, quarantine and scanning remain authoritative.
export async function prepareFile(
  file: File,
  workspaceId: string,
  brandId: string,
): Promise<SubmitFile> {
  if (!crypto.subtle) throw new FileInputError("secure_context_required");
  if (!file.size || file.size > MAX_FILE_BYTES)
    throw new FileInputError("file_size_invalid");
  if (
    !file.name ||
    Array.from(file.name).length > 160 ||
    Array.from(file.name).some(
      (c) => c.charCodeAt(0) < 32 || '/\\:"<>|?*'.includes(c),
    )
  )
    throw new FileInputError("invalid_filename");
  const extension = file.name.split(".").at(-1)?.toLowerCase();
  const formats: Record<string, SubmitFile["format"]> = {
    pdf: "pdf",
    docx: "docx",
    md: "markdown",
    markdown: "markdown",
    csv: "csv",
    html: "html",
    htm: "html",
  };
  if (!extension || !Object.hasOwn(formats, extension))
    throw new FileInputError("file_type_mismatch");
  const format = formats[extension];
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  if (bytes.length !== file.size) throw new FileInputError("file_size_invalid");
  if (format === "pdf" || format === "docx") {
    const magic = format === "pdf" ? [37, 80, 68, 70, 45] : [80, 75, 3, 4];
    if (!magic.every((byte, i) => bytes[i] === byte))
      throw new FileInputError("file_type_mismatch");
  } else {
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      throw new FileInputError("text_encoding_invalid");
    }
    if (
      ["%PDF-", "PK\u0003\u0004", "MZ", "\u007fELF"].some((magic) =>
        text.startsWith(magic),
      )
    )
      throw new FileInputError("file_type_mismatch");
    if (
      Array.from(text).some(
        (c) =>
          (c.charCodeAt(0) < 32 && !"\n\r\t".includes(c)) ||
          c.charCodeAt(0) === 127,
      )
    )
      throw new FileInputError("text_controls_rejected");
    if (!text.trim()) throw new FileInputError("extracted_text_empty");
  }
  const content_hash = await sha256(buffer);
  // Stable across lost responses, re-selection and reload, without persisting private bytes.
  // The server additionally scopes keys to the authenticated actor and workspace.
  const identity = JSON.stringify([
    "browser-file-v1",
    workspaceId,
    brandId,
    file.name,
    format,
    content_hash,
  ]);
  const key = await sha256(new TextEncoder().encode(identity).buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return {
    idempotency_key: `browser-file-v1:${key}`,
    brand_id: brandId,
    filename: file.name,
    format,
    content_hash,
    content_base64: btoa(binary),
  };
}
