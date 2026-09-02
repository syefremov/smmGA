"""Linux staging operations. Dry-run by default; no third-party Python dependencies.

Run the checked-out script explicitly through sudo. This is an operator tool, never
an MCP tool or employee capability. Errors intentionally omit subprocess output.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SOURCE = Path(__file__).resolve().parent.parent
SHA = re.compile(r"[0-9a-f]{40}")
BACKUP_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}")
MANAGED = "# Managed by SMM GPT\n"
WRITERS = ["web", "scheduler", "worker", "app"]
ENV_KEYS = {
    "SMM_POSTGRES_USER",
    "SMM_POSTGRES_DB",
    "SMM_POSTGRES_PASSWORD",
    "SMM_APP_PASSWORD",
    "SMM_WORKER_PASSWORD",
}
SCHEMA = "0005_knowledge"


class OperationError(Exception):
    """A safe, operator-facing error that never contains captured credentials."""

    def __init__(self, message: str, *, captured_output: bytes = b"") -> None:
        super().__init__(message)
        # Kept in memory for the synthetic CI harness only; never part of str(exc).
        self.captured_output = captured_output


@dataclass(frozen=True)
class Layout:
    releases: Path = Path("/opt/smm-gpt/releases")
    state: Path = Path("/opt/smm-gpt/state")
    config: Path = Path("/etc/smm-gpt")
    data: Path = Path("/var/lib/smm-gpt")
    backups: Path = Path("/var/backups/smm-gpt")


LAYOUT = Layout()


def emit(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def run(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 600,
) -> bytes:
    try:
        result = subprocess.run(
            args, input=data, capture_output=True, check=False, env=env, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperationError(f"Step could not finish: {Path(args[0]).name}") from exc
    if result.returncode:
        # Apt, Docker and database errors can echo environment/connection strings.
        raise OperationError(
            f"Step failed: {Path(args[0]).name}; exit={result.returncode}",
            captured_output=result.stdout + result.stderr,
        )
    return result.stdout


def no_symlinks(path: Path) -> None:
    if not path.is_absolute():
        raise OperationError("An absolute operational path is required")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise OperationError("Symbolic links are not allowed in operational paths")


def private_file(path: Path) -> None:
    no_symlinks(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise OperationError("Configuration/backup must be a root-owned private regular file")


def write_new(path: Path, content: bytes, mode: int = 0o600) -> None:
    no_symlinks(path)
    with path.open("xb") as target:
        os.chmod(path, mode)
        target.write(content)


def write_state(path: Path, value: object) -> None:
    no_symlinks(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(4))
    write_new(temporary, (json.dumps(value, sort_keys=True) + "\n").encode())
    temporary.replace(path)


def managed_file(path: Path, content: str, mode: int = 0o644) -> None:
    no_symlinks(path)
    expected = MANAGED + content
    if path.exists():
        old = path.read_text()
        if old == expected:
            return
        if not old.startswith(MANAGED):
            raise OperationError("Refusing to replace an unmanaged system configuration")
        raise OperationError("Managed configuration differs; review it before replacement")
    write_new(path, expected.encode(), mode)


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name not in ENV_KEYS or name in values:
            raise OperationError("Invalid or duplicate configuration key")
        if name.endswith("PASSWORD"):
            valid = re.fullmatch(r"[0-9a-f]{64}", value)
        else:
            valid = re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value)
        if not valid:
            raise OperationError("Configuration contains an invalid value")
        values[name] = value
    if values.keys() != ENV_KEYS:
        raise OperationError("Required server configuration is missing")
    return values


def configuration(layout: Layout = LAYOUT) -> dict[str, str]:
    path = layout.config / "server.env"
    private_file(path)
    return parse_env(path.read_text())


def initialize(layout: Layout = LAYOUT) -> None:
    if not (layout.config / "server.env").exists():
        for name in ("postgres", "media"):
            existing_data = layout.data / name
            no_symlinks(existing_data)
            if existing_data.exists() and any(existing_data.iterdir()):
                raise OperationError("Existing data has no configuration; recover secrets first")
    for path in (layout.releases, layout.state, layout.config, layout.data, layout.backups):
        no_symlinks(path)
        if any(parent.exists() and parent.stat().st_uid != 0 for parent in (path, *path.parents)):
            raise OperationError("Operational directories must have root-owned ancestors")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    for name, uid in (("postgres", 70), ("media", 10001), ("redis", 999), ("authentik", 0)):
        path = layout.data / name
        no_symlinks(path)
        if not path.exists():
            path.mkdir(mode=0o700)
            os.chown(path, uid, uid)
        elif path.stat().st_uid != uid:
            raise OperationError("Existing data directory has an unexpected owner; not changing it")
    path = layout.config / "server.env"
    if not path.exists():
        write_new(
            path,
            (
                "SMM_POSTGRES_USER=smm\nSMM_POSTGRES_DB=smm\n"
                f"SMM_POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
                f"SMM_APP_PASSWORD={secrets.token_hex(32)}\n"
                f"SMM_WORKER_PASSWORD={secrets.token_hex(32)}\n"
            ).encode(),
        )
    configuration(layout)


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    """Accept only regular files/directories, with no absolute paths or traversal."""
    no_symlinks(destination)
    members = archive.getmembers()
    for member in members:
        parts = PurePosixPath(member.name).parts
        if (
            not parts
            or PurePosixPath(member.name).is_absolute()
            or ".." in parts
            or "\\" in member.name
            or ":" in member.name
            or not (member.isfile() or member.isdir())
        ):
            raise OperationError("Archive contains an unsafe member")
        no_symlinks(destination / member.name)
    archive.extractall(destination, members=members, filter="data")


def require_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise OperationError("Release must be a full lowercase 40-character commit SHA")
    return value


def schema_fingerprint(source: Path) -> str:
    files = sorted((source / "migrations" / "versions").glob("*.py"))
    if not files:
        raise OperationError("Release has no migrations")
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def current(layout: Layout = LAYOUT) -> dict[str, Any] | None:
    path = layout.state / "current.json"
    if not path.exists():
        return None
    private_file(path)
    result: dict[str, Any] = json.loads(path.read_text())
    require_sha(result["release"])
    return result


def compose(sha: str, args: Sequence[str], layout: Layout = LAYOUT) -> bytes:
    env = {**os.environ, **configuration(layout), "SMM_RELEASE": require_sha(sha)}
    return run(
        [
            "docker",
            "compose",
            "--project-name",
            "smm-gpt-staging",
            "--env-file",
            str(layout.config / "server.env"),
            "--file",
            str(layout.releases / sha / "ops" / "compose.server.yaml"),
            *args,
        ],
        env=env,
        timeout=1800,
    )


def check_engine() -> None:
    version = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=15)
    # Older Docker releases had a localhost-published port reachability issue.
    if int(version.decode().split(".")[0]) < 28:
        raise OperationError("Docker Engine 28 or newer is required")
    run(["docker", "compose", "version"], timeout=15)


def check_http() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health/ready", timeout=10) as response:
            if response.status != 200:
                raise OperationError("Staging readiness failed")
        try:
            urllib.request.urlopen("http://127.0.0.1:8080/mcp/", timeout=10).close()
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                raise OperationError("Remote MCP must remain blocked in phase 3") from exc
        else:
            raise OperationError("Remote MCP must remain blocked in phase 3")
    except (OSError, urllib.error.URLError) as exc:
        raise OperationError("Staging endpoint check failed") from exc


def check_runtime() -> None:
    ids = (
        run(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.project=smm-gpt-staging",
                "--format",
                "{{.ID}}",
            ]
        )
        .decode()
        .split()
    )
    if len(ids) != 6:
        raise OperationError("Expected six running staging services")
    for container in ids:
        fields = run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Config.User}}|{{.HostConfig.Privileged}}|{{.HostConfig.ReadonlyRootfs}}|"
                "{{.State.Health.Status}}|{{json .HostConfig.PortBindings}}",
                container,
            ]
        )
        user, privileged, readonly, health, bindings = fields.decode().strip().split("|")
        if user not in ("70:70", "999:999", "10001:10001") or (privileged, readonly, health) != (
            "false",
            "true",
            "healthy",
        ):
            raise OperationError("Container identity, confinement or health check failed")
        for port, hosts in (json.loads(bindings) or {}).items():
            if port != "8080/tcp" or hosts != [{"HostIp": "127.0.0.1", "HostPort": "8080"}]:
                raise OperationError("Unexpected published infrastructure port")
    network = json.loads(run(["docker", "network", "inspect", "smm-gpt-staging_backend"]))[0]
    if (
        network["EnableIPv6"]
        or network["Options"].get("com.docker.network.bridge.name") != "smmbr0"
    ):
        raise OperationError("Staging bridge differs from the firewall policy")


def image_id(name: str) -> str:
    return run(["docker", "image", "inspect", "--format", "{{.Id}}", name]).decode().strip()


def stage_release(sha: str, layout: Layout = LAYOUT) -> dict[str, Any]:
    destination = layout.releases / sha
    no_symlinks(destination)
    manifest = destination / "release.json"
    if manifest.exists():
        value: dict[str, Any] = json.loads(manifest.read_text())
        for service in ("app", "web"):
            if image_id(f"smm-gpt-{service}:{sha}") != value[f"{service}_image"]:
                raise OperationError("An immutable release image was changed or removed")
        return value
    if destination.exists():
        raise OperationError("An incomplete release exists; inspect it before retrying")
    archive = run(
        ["git", "-c", f"safe.directory={SOURCE}", "-C", str(SOURCE), "archive", "--format=tar", sha]
    )
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source_archive:
        safe_extract(source_archive, destination)
    # A release is always the committed tree, not uncommitted local changes.
    fingerprint = schema_fingerprint(destination)
    previous = current(layout)
    if previous and fingerprint != previous["schema_fingerprint"]:
        raise OperationError("Schema change requires a separately reviewed migration/rollback plan")
    for service, dockerfile in (("app", "Dockerfile"), ("web", "Dockerfile.web")):
        run(
            [
                "docker",
                "build",
                "--pull",
                "-f",
                str(destination / dockerfile),
                "-t",
                f"smm-gpt-{service}:{sha}",
                str(destination),
            ],
            timeout=1800,
        )
    value = {
        "release": sha,
        "schema_fingerprint": fingerprint,
        "schema_revision": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "app_image": image_id(f"smm-gpt-app:{sha}"),
        "web_image": image_id(f"smm-gpt-web:{sha}"),
    }
    write_state(manifest, value)
    return value


def start(sha: str, layout: Layout = LAYOUT) -> None:
    compose(sha, ["config", "--quiet"], layout)
    compose(sha, ["up", "-d", "--wait", "--wait-timeout", "240"], layout)
    compose(sha, ["exec", "-T", "app", "python", "-m", "smm_gpt.workers.smoke"], layout)
    check_http()
    check_runtime()


def deploy(sha: str, *, rollback: bool = False, layout: Layout = LAYOUT) -> None:
    check_engine()
    previous = current(layout)
    if rollback and not (layout.releases / sha / "release.json").is_file():
        raise OperationError("Rollback requires a previously built immutable release")
    target = stage_release(sha, layout)
    if previous and target["schema_fingerprint"] != previous["schema_fingerprint"]:
        raise OperationError("Rollback across schema versions is not supported")
    if previous and previous["release"] == sha:
        start(sha, layout)
        emit("release_already_active", release=sha)
        return
    if previous:
        backup(layout)
    try:
        if previous:
            compose(previous["release"], ["stop", *WRITERS], layout)
        start(sha, layout)
    except OperationError:
        # Only code with the exact same migration fingerprint can be resumed.
        compose(sha, ["stop", *WRITERS], layout)
        if previous:
            start(previous["release"], layout)
            emit("previous_release_resumed", release=previous["release"])
        raise
    if previous:
        write_state(layout.state / "previous.json", previous)
    write_state(layout.state / "current.json", target)
    emit("release_ready", release=sha, mode="rollback" if rollback else "deploy")


def identifier() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def media_archive(source: Path, target: Path) -> None:
    no_symlinks(source)
    with tarfile.open(target, "x") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise OperationError("Media contains a link or special file; backup stopped")
            archive.add(path, arcname=path.relative_to(source), recursive=False)


def database_snapshot(sha: str, layout: Layout) -> bytes:
    # Row counts for all user tables, plus migration revision. No content is logged.
    sql = (
        "SELECT format('SELECT %L, count(*) FROM %I.%I;', schemaname || '.' || tablename, "
        "schemaname, tablename) FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
        "\n\\gexec\nSELECT version_num FROM alembic_version;\n"
    )
    return compose_sql(sha, sql.encode(), layout)


def compose_sql(sha: str, sql: bytes, layout: Layout) -> bytes:
    env = {**os.environ, **configuration(layout), "SMM_RELEASE": sha}
    return run(
        [
            "docker",
            "compose",
            "-p",
            "smm-gpt-staging",
            "--env-file",
            str(layout.config / "server.env"),
            "-f",
            str(layout.releases / sha / "ops" / "compose.server.yaml"),
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
        ],
        env=env,
        data=sql,
    )


def backup(layout: Layout = LAYOUT) -> str:
    active = current(layout)
    if not active:
        raise OperationError("No active release to back up")
    sha = active["release"]
    name = identifier()
    destination = layout.backups / name
    no_symlinks(destination)
    destination.mkdir(mode=0o700)
    # All writes are stopped across PostgreSQL dump and media/config snapshot.
    try:
        compose(sha, ["stop", *WRITERS], layout)
        dump = compose(
            sha,
            [
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ],
            layout,
        )
        write_new(destination / "database.dump", dump)
        write_new(destination / "database-check.txt", database_snapshot(sha, layout))
        media_archive(layout.data / "media", destination / "media.tar")
        write_new(destination / "server.env", (layout.config / "server.env").read_bytes())
        write_state(destination / "release.json", active)
        files = sorted(destination.iterdir())
        for path in files:
            os.chmod(path, 0o600)
        write_state(destination / "checksums.json", {path.name: file_hash(path) for path in files})
        write_state(destination / "complete.json", {"backup": name, "release": sha, "version": 1})
    finally:
        start(sha, layout)
    emit("backup_ready", backup=name, encrypted=False)
    return name


def validate_backup(name: str, layout: Layout = LAYOUT) -> Path:
    if not BACKUP_ID.fullmatch(name):
        raise OperationError("Invalid backup ID")
    path = layout.backups / name
    no_symlinks(path)
    expected = {"database.dump", "database-check.txt", "media.tar", "server.env", "release.json"}
    for filename in (*expected, "checksums.json", "complete.json"):
        private_file(path / filename)
    checksums = json.loads((path / "checksums.json").read_text())
    if checksums.keys() != expected:
        raise OperationError("Unexpected backup manifest")
    for filename in expected:
        if file_hash(path / filename) != checksums[filename]:
            raise OperationError("Backup checksum verification failed")
    complete = json.loads((path / "complete.json").read_text())
    manifest = json.loads((path / "release.json").read_text())
    if complete != {"backup": name, "release": manifest["release"], "version": 1}:
        raise OperationError("Incomplete backup or mismatched release manifest")
    parse_env((path / "server.env").read_text())
    return path


def restore(name: str, layout: Layout = LAYOUT) -> None:
    """A drill only: never replace the running database, media or configuration."""
    check_engine()
    source = validate_backup(name, layout)
    drill_id = identifier()
    destination = layout.data / "restore-drills" / drill_id
    no_symlinks(destination)
    destination.mkdir(parents=True, mode=0o700)
    media = destination / "media"
    media.mkdir(mode=0o700)
    with tarfile.open(source / "media.tar") as archive:
        safe_extract(archive, media)
    # Recreate the archive inventory and compare content after extraction.
    with tarfile.open(source / "media.tar") as archive:
        for member in archive.getmembers():
            if member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                if hashlib.sha256(stream.read()).hexdigest() != file_hash(media / member.name):
                    raise OperationError("Restored media hash mismatch")
    values = parse_env((source / "server.env").read_text())
    db_env = destination / "postgres.env"
    write_new(
        db_env,
        "".join(f"{key.removeprefix('SMM_')}={value}\n" for key, value in values.items()).encode(),
    )
    pgdata = destination / "postgres"
    pgdata.mkdir(mode=0o700)
    os.chown(pgdata, 70, 70)
    container = "smm-restore-" + drill_id.lower()
    created = False
    started = time.monotonic()
    try:
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container,
                "--network",
                "none",
                "--user",
                "70:70",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--env-file",
                str(db_env),
                "--mount",
                f"type=bind,source={pgdata},target=/var/lib/postgresql/data",
                "--tmpfs",
                "/var/run/postgresql:uid=70,gid=70,mode=0770",
                "--tmpfs",
                "/tmp:uid=70,gid=70,mode=1770",
                "postgres:17.6-alpine",
            ]
        )
        created = True
        for attempt in range(60):
            try:
                # Query, not pg_isready alone: wait for final TCP server after initdb.
                run(
                    [
                        "docker",
                        "exec",
                        container,
                        "sh",
                        "-c",
                        'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -h 127.0.0.1 '
                        '-U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1"',
                    ],
                    timeout=5,
                )
                break
            except OperationError:
                if attempt == 59:
                    raise
                time.sleep(1)
        # pg_dump retains ACLs/functions/policies, but cluster roles are not in the dump.
        run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "sh",
                "-c",
                'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ],
            data=b"CREATE ROLE smm_app NOLOGIN NOSUPERUSER NOBYPASSRLS;"
            b"CREATE ROLE smm_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;",
        )
        run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "sh",
                "-c",
                'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ],
            data=(source / "database.dump").read_bytes(),
        )
        sql = (
            "SELECT format('SELECT %L, count(*) FROM %I.%I;', schemaname || '.' || tablename, "
            "schemaname, tablename) FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
            "\n\\gexec\nSELECT version_num FROM alembic_version;\n"
        )
        actual = run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "sh",
                "-c",
                'psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ],
            data=sql.encode(),
        )
        if actual != (source / "database-check.txt").read_bytes():
            raise OperationError("Restored database table counts/revision differ")
        write_new(destination / "server.env", (source / "server.env").read_bytes())
        write_state(
            destination / "result.json",
            {
                "backup": name,
                "drill": drill_id,
                "status": "passed",
                "duration_seconds": round(time.monotonic() - started, 2),
                "database": "all user table counts and migration revision match",
                "media": "all file hashes match",
                "active_state_changed": False,
            },
        )
    finally:
        if created:
            run(["docker", "rm", "-f", container])
    emit("restore_drill_passed", drill=drill_id, backup=name, retained=True)


def bootstrap(args: argparse.Namespace) -> None:
    if not args.recovery_confirmed:
        raise OperationError("Confirm working provider recovery console before bootstrap")
    release = dict(
        line.split("=", 1)
        for line in Path("/etc/os-release").read_text().splitlines()
        if "=" in line
    )
    if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID") != '"24.04"':
        raise OperationError("This bootstrap supports Ubuntu 24.04 only; do not reinstall blindly")
    if args.harden_ssh:
        harden_ssh(args)
        return
    if not args.public_key or not args.public_key.is_file():
        raise OperationError("A local OpenSSH public key file is required")
    key = args.public_key.read_text().strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,3}(?: [^\r\n]+)?", key):
        raise OperationError("Supply one Ed25519 public key, never a private key")
    run(["ssh-keygen", "-lf", str(args.public_key)])
    # Never remove another Docker installation or silently reuse its data.
    for package in ("docker.io", "docker-compose", "podman-docker", "containerd", "runc"):
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${db:Status-Status}", package],
            capture_output=True,
            check=False,
        )
        if result.stdout == b"installed":
            raise OperationError("Conflicting container packages exist; inspect before bootstrap")
    ssh_state = run(["/usr/sbin/sshd", "-T"]).decode()
    if "port 22\n" not in ssh_state:
        raise OperationError("Nonstandard SSH port requires a separately reviewed firewall plan")
    emit("bootstrap_packages_started")
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    run(["apt-get", "update"], env=env)
    run(
        [
            "apt-get",
            "install",
            "-y",
            "ca-certificates",
            "curl",
            "gnupg",
            "git",
            "python3",
            "sudo",
            "ufw",
            "chrony",
            "unattended-upgrades",
            "openssh-server",
        ],
        env=env,
    )
    for directory in ("/etc/apt/keyrings", "/etc/systemd/journald.conf.d"):
        Path(directory).mkdir(exist_ok=True, mode=0o755)
    docker_key = Path("/etc/apt/keyrings/smm-docker.asc")
    tailscale_key = Path("/etc/apt/keyrings/smm-tailscale.gpg")
    for path, url in (
        (docker_key, "https://download.docker.com/linux/ubuntu/gpg"),
        (tailscale_key, "https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg"),
    ):
        if not path.exists():
            write_new(
                path, run(["curl", "--fail", "--silent", "--show-error", "--location", url]), 0o644
            )
        else:
            no_symlinks(path)
    arch = run(["dpkg", "--print-architecture"]).decode().strip()
    if arch not in ("amd64", "arm64"):
        raise OperationError("Unsupported architecture")
    managed_file(
        Path("/etc/apt/sources.list.d/smm-docker.sources"),
        "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\n"
        f"Components: stable\nArchitectures: {arch}\nSigned-By: {docker_key}\n",
    )
    managed_file(
        Path("/etc/apt/sources.list.d/smm-tailscale.list"),
        f"deb [signed-by={tailscale_key}] https://pkgs.tailscale.com/stable/ubuntu noble main\n",
    )
    run(["apt-get", "update"], env=env)
    run(
        [
            "apt-get",
            "install",
            "-y",
            "docker-ce",
            "docker-ce-cli",
            "containerd.io",
            "docker-buildx-plugin",
            "docker-compose-plugin",
            "tailscale",
        ],
        env=env,
    )
    managed_file(
        Path("/etc/apt/apt.conf.d/90smm-security-updates"),
        'APT::Periodic::Update-Package-Lists "1";\n'
        'APT::Periodic::Unattended-Upgrade "1";\n'
        'Unattended-Upgrade::Automatic-Reboot "false";\n',
    )
    managed_file(
        Path("/etc/systemd/journald.conf.d/90-smm.conf"),
        "[Journal]\nStorage=persistent\nSystemMaxUse=100M\nMaxRetentionSec=14day\n",
    )
    run(["timedatectl", "set-timezone", "UTC"])
    run(["systemctl", "enable", "--now", "chrony", "docker", "tailscaled"])
    run(["systemctl", "restart", "systemd-journald"])
    run(["unattended-upgrade"], env=env, timeout=1800)
    try:
        import pwd

        account = pwd.getpwnam("smm")
    except KeyError:
        run(["useradd", "--create-home", "--shell", "/bin/bash", "--groups", "sudo", "smm"])
        account = pwd.getpwnam("smm")
    if account.pw_dir != "/home/smm" or account.pw_uid == 0:
        raise OperationError("Existing smm account needs manual review")
    ssh_dir = Path(account.pw_dir) / ".ssh"
    no_symlinks(ssh_dir)
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ssh_dir, 0o700)
    os.chown(ssh_dir, account.pw_uid, account.pw_gid)
    authorized = ssh_dir / "authorized_keys"
    no_symlinks(authorized)
    existing = authorized.read_text().splitlines() if authorized.exists() else []
    if key not in existing:
        with authorized.open("a") as key_file:
            key_file.write(("\n" if existing else "") + key + "\n")
    os.chmod(authorized, 0o600)
    os.chown(authorized, account.pw_uid, account.pw_gid)
    initialize()
    for source, target, mode in (
        ("smm-docker-firewall.sh", "/usr/local/sbin/smm-docker-firewall", 0o755),
        ("smm-docker-firewall.service", "/etc/systemd/system/smm-docker-firewall.service", 0o644),
    ):
        path = Path(target)
        no_symlinks(path)
        content = (SOURCE / "ops" / source).read_bytes()
        if path.exists() and path.read_bytes() != content:
            raise OperationError("Existing firewall configuration differs; review required")
        if not path.exists():
            write_new(path, content, mode)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "smm-docker-firewall.service"])
    run(["ufw", "limit", "22/tcp"])
    run(["ufw", "allow", "41641/udp"])
    run(["ufw", "default", "deny", "incoming"])
    run(["ufw", "default", "allow", "outgoing"])
    run(["ufw", "--force", "enable"])
    check_engine()
    emit(
        "bootstrap_ready",
        ssh_hardened=False,
        tailscale_login_required=True,
        sudo_password_setup_required=True,
        reboot_required=Path("/var/run/reboot-required").exists(),
    )


def harden_ssh(args: argparse.Namespace) -> None:
    if not args.second_session_confirmed:
        raise OperationError("Verify smm key login AND sudo in an independent session first")
    path = Path("/etc/ssh/sshd_config.d/00-smm-hardening.conf")
    if not Path("/home/smm/.ssh/authorized_keys").is_file():
        raise OperationError("Operator key is not installed")
    password_state = run(["passwd", "-S", "smm"]).decode().split()
    if len(password_state) < 2 or password_state[1] != "P":
        raise OperationError("Set the operator sudo password locally before disabling root SSH")
    content = (
        "PubkeyAuthentication yes\nPasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\nPermitRootLogin no\n"
    )
    existed = path.exists()
    managed_file(path, content)
    try:
        run(["/usr/sbin/sshd", "-t"])
        effective = run(["/usr/sbin/sshd", "-T"]).decode()
        for setting in (
            "passwordauthentication no",
            "kbdinteractiveauthentication no",
            "permitrootlogin no",
            "pubkeyauthentication yes",
        ):
            if setting + "\n" not in effective:
                raise OperationError("SSH include precedence prevented the requested policy")
    except OperationError:
        if not existed:
            path.unlink()  # Only the exact new file created by this invocation.
        raise
    run(["systemctl", "reload", "ssh"])
    emit("ssh_hardened", verify_new_session_before_closing_current=True)


def doctor(layout: Layout = LAYOUT) -> bool:
    passed = True
    checks: list[tuple[str, Sequence[str]]] = [
        ("os", ["uname", "-sm"]),
        ("memory", ["free", "-m"]),
        ("disk", ["df", "-h", "/"]),
        ("listeners", ["ss", "-lntu"]),
        (
            "running_services",
            [
                "systemctl",
                "list-units",
                "--type=service",
                "--state=running",
                "--no-pager",
                "--no-legend",
            ],
        ),
        ("time", ["timedatectl", "show", "-p", "Timezone", "-p", "NTPSynchronized"]),
        ("firewall", ["ufw", "status", "verbose"]),
        (
            "docker_ingress_guard",
            [
                "iptables",
                "-w",
                "-C",
                "DOCKER-USER",
                "!",
                "-i",
                "smmbr0",
                "-o",
                "smmbr0",
                "-m",
                "conntrack",
                "--ctstate",
                "NEW",
                "-j",
                "DROP",
            ],
        ),
    ]
    for name, command in checks:
        try:
            output = run(command, timeout=15).decode().strip()
            if name == "firewall" and "Status: active" not in output:
                raise OperationError("Firewall is inactive")
            if name == "time" and not {"Timezone=UTC", "NTPSynchronized=yes"}.issubset(
                output.splitlines()
            ):
                raise OperationError("Time is not synchronized to UTC")
            emit("inventory", check=name, output=output)
        except OperationError:
            passed = False
            emit("check_failed", check=name)
    try:
        check_engine()
        configuration(layout)
        active = current(layout)
        if not active:
            raise OperationError("No active release")
        check_http()
        check_runtime()
        state = json.loads(run(["tailscale", "status", "--json"]))
        if state.get("BackendState") != "Running":
            raise OperationError("Tailscale is not connected")
        serve = json.loads(run(["tailscale", "serve", "status", "--json"]))
        if any(serve.get("AllowFunnel", {}).values()):
            raise OperationError("Funnel is enabled")
        handlers = [
            handler
            for web in serve.get("Web", {}).values()
            for handler in web.get("Handlers", {}).values()
        ]
        if not any(handler.get("Proxy") == "http://127.0.0.1:8080" for handler in handlers):
            raise OperationError("Private HTTPS proxy is not configured")
        emit("private_stack_ready", release=active["release"])
    except OperationError as exc:
        passed = False
        emit("check_failed", check="private_stack", reason=str(exc))
    emit(
        "external_checks_required",
        checks=[
            "public IPv4/IPv6 port probe",
            "allowed and denied tailnet devices",
            "fresh SSH session",
            "reboot",
            "restore drill",
        ],
    )
    return passed


@contextlib.contextmanager
def operation_lock() -> Iterator[None]:
    import fcntl

    path = Path("/run/lock/smm-gpt-operations.lock")
    no_symlinks(path)
    with path.open("a") as lock:
        private_file(path)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OperationError("Another SMM operation is running") from exc
        yield


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "operation",
        choices=["bootstrap", "init", "deploy", "rollback", "backup", "restore", "doctor"],
    )
    result.add_argument(
        "--apply", action="store_true", help="Execute changes; otherwise print plan"
    )
    result.add_argument("--release", help="Full committed SHA for deploy/rollback")
    result.add_argument("--backup", help="Backup ID for isolated restore drill")
    result.add_argument("--public-key", type=Path)
    result.add_argument("--recovery-confirmed", action="store_true")
    result.add_argument("--second-session-confirmed", action="store_true")
    result.add_argument("--harden-ssh", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.operation in ("deploy", "rollback"):
            require_sha(args.release or "")
        if args.operation == "restore" and not BACKUP_ID.fullmatch(args.backup or ""):
            raise OperationError("Restore requires a valid backup ID")
        if args.operation != "doctor" and not args.apply:
            emit(
                "plan",
                operation=args.operation,
                changes_applied=False,
                release=args.release,
                isolated_restore=args.operation == "restore",
                prerequisites="Read docs/deployment.md; authorize this exact server operation",
            )
            return 0
        if sys.platform != "linux" or os.geteuid() != 0:
            raise OperationError("Execution requires Linux and an authorized sudo session")
        os.umask(0o077)
        with operation_lock():
            if args.operation == "doctor":
                return 0 if doctor() else 1
            if args.operation == "bootstrap":
                bootstrap(args)
            elif args.operation == "init":
                initialize()
                emit("configuration_initialized", existing_secrets_preserved=True)
            elif args.operation in ("deploy", "rollback"):
                deploy(args.release, rollback=args.operation == "rollback")
            elif args.operation == "backup":
                backup()
            elif args.operation == "restore":
                restore(args.backup)
    except (OperationError, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        # Never stringify arbitrary OS/parser/subprocess exceptions: values can be secret.
        message = str(exc) if isinstance(exc, OperationError) else type(exc).__name__
        emit("operation_stopped", reason=message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
