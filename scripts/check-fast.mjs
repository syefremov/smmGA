import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

function run(command, args, capture = false) {
  const useCommandShell = process.platform === "win32" && command === "pnpm";
  const executable = useCommandShell
    ? (process.env.ComSpec ?? "cmd.exe")
    : command;
  const commandArgs = useCommandShell
    ? ["/d", "/s", "/c", command, ...args]
    : args;
  const result = spawnSync(executable, commandArgs, {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: capture ? "pipe" : "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
  return result.stdout ?? "";
}

run("git", ["diff", "--cached", "--check"]);

const stagedNames = run(
  "git",
  ["diff", "--cached", "--name-only", "--diff-filter=ACMR"],
  true,
)
  .split(/\r?\n/u)
  .filter(Boolean);
const forbiddenNames = [
  /(^|\/)\.env($|\.)/u,
  /\.(key|pem|p12|pfx)$/u,
  /\.(dump|sql\.gz)$/u,
  /(^|\/)(backups?|\.data)\//u,
];

for (const name of stagedNames) {
  if (name === ".env.example" || /\.env\.[^/]+\.example$/u.test(name)) continue;
  if (forbiddenNames.some((pattern) => pattern.test(name))) {
    throw new Error(
      `Запрещённый локальный или чувствительный файл подготовлен к commit: ${name}`,
    );
  }
}

const stagedDiff = run(
  "git",
  ["diff", "--cached", "--unified=0", "--no-color"],
  true,
);
const privateKeyPattern = /BEGIN (RSA|OPENSSH|EC) PRIVATE KEY/u;
const credentialPattern =
  /(password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*["']?[A-Za-z0-9+/_.-]{12,}/iu;
if (privateKeyPattern.test(stagedDiff) || credentialPattern.test(stagedDiff)) {
  throw new Error(
    `В staged diff найден возможный секрет. Проверьте: ${stagedNames.join(", ")}`,
  );
}

JSON.parse(readFileSync("package.json", "utf8"));
JSON.parse(readFileSync("web/package.json", "utf8"));
for (const script of [
  "scripts/check-fast.mjs",
  "scripts/init-dev-env.mjs",
  "scripts/project-command.mjs",
]) {
  run(process.execPath, ["--check", script]);
}
run("uv", ["lock", "--check"]);
run("pnpm", [
  "install",
  "--lockfile-only",
  "--offline",
  "--frozen-lockfile",
  "--ignore-scripts",
]);

console.log("Быстрые проверки пройдены.");
