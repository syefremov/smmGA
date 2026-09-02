import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
process.chdir(projectRoot);

function composeCommand(args) {
  const standalone = resolve(
    process.env.LOCALAPPDATA ?? "",
    "Microsoft",
    "WinGet",
    "Links",
    "docker-compose.exe",
  );
  if (process.platform === "win32" && existsSync(standalone)) {
    return [standalone, args];
  }
  return ["docker", ["compose", ...args]];
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
  return result.status === 0;
}

const commandName = process.argv[2];
const commands = new Map([
  ["dev", composeCommand(["up", "--build", "--remove-orphans"])],
  ["down", composeCommand(["down"])],
  ["infra-up", composeCommand(["up", "-d", "postgres", "redis"])],
  ["build", composeCommand(["build"])],
  ["db-migrate", ["uv", ["run", "alembic", "upgrade", "head"]]],
  [
    "api-dev",
    [
      "uv",
      [
        "run",
        "uvicorn",
        "smm_gpt.application:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--reload",
      ],
    ],
  ],
  [
    "worker-dev",
    [
      "uv",
      [
        "run",
        "celery",
        "-A",
        "smm_gpt.workers.celery_app:celery_app",
        "worker",
        "-l",
        "INFO",
      ],
    ],
  ],
  [
    "scheduler-dev",
    [
      "uv",
      [
        "run",
        "celery",
        "-A",
        "smm_gpt.workers.celery_app:celery_app",
        "beat",
        "-l",
        "INFO",
      ],
    ],
  ],
  [
    "worker-smoke",
    composeCommand([
      "exec",
      "-T",
      "app",
      "python",
      "-m",
      "smm_gpt.workers.smoke",
    ]),
  ],
]);

if (!commands.has(commandName)) {
  console.error(`Неизвестная project command: ${commandName ?? "<пусто>"}`);
  process.exit(2);
}

if (
  commandName !== "down" &&
  !run(process.execPath, ["scripts/init-dev-env.mjs"])
)
  process.exit(1);
const [executableName, args] = commands.get(commandName);
run(executableName, args);
