"""Disposable GitHub-hosted runner only. Never run against a real staging host."""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
from pathlib import Path

from scripts import server


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
        raise server.OperationError(
            "This destructive fixture requires an ephemeral CI sudo session"
        )
    if any(
        path.exists()
        for path in (
            server.LAYOUT.releases.parent,
            server.LAYOUT.config,
            server.LAYOUT.data,
            server.LAYOUT.backups,
        )
    ):
        raise server.OperationError("Refusing to reuse existing SMM operational paths")
    os.umask(0o077)
    server.check_engine()
    server.initialize()
    config_before = server.file_hash(server.LAYOUT.config / "server.env")
    server.initialize()
    assert server.file_hash(server.LAYOUT.config / "server.env") == config_before
    # Two synthetic commits in a disposable fixture repository test an actual rollback.
    with tempfile.TemporaryDirectory(prefix="smm-ci-source-") as temporary:
        source = Path(temporary)
        archive = server.run(
            [
                "git",
                "-c",
                f"safe.directory={server.SOURCE}",
                "-C",
                str(server.SOURCE),
                "archive",
                "HEAD",
            ]
        )
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            server.safe_extract(tar, source)
        server.run(["git", "-C", str(source), "init"])
        server.run(["git", "-C", str(source), "add", "."])
        commit = [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=CI Fixture",
            "-c",
            "user.email=ci@example.invalid",
            "commit",
            "-m",
        ]
        server.run([*commit, "fixture one"])
        first = server.run(["git", "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
        (source / "fixture-version.txt").write_text("second synthetic version\n")
        server.run(["git", "-C", str(source), "add", "fixture-version.txt"])
        server.run([*commit, "fixture two"])
        second = server.run(["git", "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
        server.SOURCE = source
        server.deploy(first)
        assert server.compose(first, ["exec", "-T", "web", "getcap", "/usr/bin/caddy"]) == b""
        server.deploy(first)
        server.run(["bash", str(source / "ops" / "smm-docker-firewall.sh")])
        server.run(["bash", str(source / "ops" / "smm-docker-firewall.sh")])
        server.compose_sql(
            first,
            b"CREATE TABLE restore_fixture (id integer PRIMARY KEY);"
            b"INSERT INTO restore_fixture VALUES (7);",
            server.LAYOUT,
        )
        marker = server.LAYOUT.data / "media" / "fixture.txt"
        marker.write_text("synthetic media for restore drill\n")
        os.chown(marker, 10001, 10001)
        backup_id = server.backup()
        state_before = (server.LAYOUT.state / "current.json").read_bytes()
        server.restore(backup_id)
        assert (server.LAYOUT.state / "current.json").read_bytes() == state_before
        assert marker.read_text() == "synthetic media for restore drill\n"
        assert (
            server.compose_sql(first, b"SELECT id FROM restore_fixture;", server.LAYOUT) == b"7\n"
        )
        server.deploy(second)
        server.deploy(first, rollback=True)
        active = server.current()
        assert active is not None and active["release"] == first
        # Recreating all application containers exercises persistent PostgreSQL/media mounts.
        server.compose(first, ["down"])
        server.start(first)
        assert (
            server.compose_sql(first, b"SELECT id FROM restore_fixture;", server.LAYOUT) == b"7\n"
        )
        server.emit("server_ci_passed", reboot_tested=False, tailscale_tested=False)
        server.compose(first, ["down"])


if __name__ == "__main__":
    try:
        main()
    except server.OperationError as exc:
        if os.environ.get("GITHUB_ACTIONS") == "true" and os.geteuid() == 0:
            diagnostic = exc.captured_output.decode(errors="replace")
            config = server.LAYOUT.config / "server.env"
            if config.exists():
                for value in server.parse_env(config.read_text()).values():
                    if len(value) >= 12:
                        diagnostic = diagnostic.replace(value, "[REDACTED]")
            server.emit("synthetic_ci_failure", detail=diagnostic[-12000:])
        raise
