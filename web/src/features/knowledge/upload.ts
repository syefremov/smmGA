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
  if (extension !== "pdf" && extension !== "docx")
    throw new FileInputError("file_type_mismatch");
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  if (bytes.length !== file.size) throw new FileInputError("file_size_invalid");
  const magic = extension === "pdf" ? [37, 80, 68, 70, 45] : [80, 75, 3, 4];
  if (!magic.every((byte, i) => bytes[i] === byte))
    throw new FileInputError("file_type_mismatch");
  const content_hash = await sha256(buffer);
  // Stable across lost responses, re-selection and reload, without persisting private bytes.
  // The server additionally scopes keys to the authenticated actor and workspace.
  const identity = JSON.stringify([
    "browser-file-v1",
    workspaceId,
    brandId,
    file.name,
    extension,
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
    format: extension,
    content_hash,
    content_base64: btoa(binary),
  };
}
