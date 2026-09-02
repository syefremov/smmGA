import { randomBytes } from "node:crypto";
import { chmod, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const envPath = resolve(projectRoot, ".env");

if (existsSync(envPath)) {
  console.log("Локальный .env уже существует; файл не изменён.");
  process.exit(0);
}

const databasePassword = randomBytes(32).toString("base64url");
const sessionSecret = randomBytes(48).toString("base64url");
const databaseUrl = `postgresql+asyncpg://smm_gpt:${databasePassword}@127.0.0.1:5432/smm_gpt`;
const lines = [
  "# Generated locally. Never commit or reuse this file on a server.",
  "SMM_ENV=development",
  "SMM_LOG_LEVEL=INFO",
  "SMM_TIMEZONE=Europe/Moscow",
  "SMM_API_BIND=0.0.0.0",
  "SMM_API_PORT=8000",
  "SMM_WEB_ORIGIN=http://127.0.0.1:8080",
  "SMM_POSTGRES_DB=smm_gpt",
  "SMM_POSTGRES_USER=smm_gpt",
  `SMM_POSTGRES_PASSWORD=${databasePassword}`,
  "SMM_POSTGRES_PORT=5432",
  "SMM_REDIS_PORT=6379",
  `SMM_DATABASE_URL=${databaseUrl}`,
  "SMM_REDIS_URL=redis://127.0.0.1:6379/0",
  "SMM_MEDIA_ROOT=.data/media",
  `SMM_SESSION_SECRET=${sessionSecret}`,
  "SMM_MCP_RESOURCE_URL=http://127.0.0.1:8080/mcp/",
  "SMM_AI_PROVIDER=disabled",
  "SMM_VK_ENABLED=false",
  "",
];

await writeFile(envPath, lines.join("\n"), {
  encoding: "utf8",
  mode: 0o600,
  flag: "wx",
});
if (process.platform !== "win32") await chmod(envPath, 0o600);
console.log("Создан локальный .env с новыми случайными значениями.");
