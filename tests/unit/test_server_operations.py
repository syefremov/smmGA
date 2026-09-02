from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import server


def valid_environment() -> str:
    return (
        "SMM_POSTGRES_USER=smm\nSMM_POSTGRES_DB=smm\nSMM_POSTGRES_PASSWORD="
        + "a" * 64
        + "\nSMM_APP_PASSWORD="
        + "b" * 64
        + "\nSMM_WORKER_PASSWORD="
        + "c" * 64
        + "\n"
    )


@pytest.mark.parametrize(
    "operation", ["bootstrap", "init", "deploy", "rollback", "backup", "restore"]
)
def test_dry_run_never_touches_host(operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    command = Mock(side_effect=AssertionError("No command may run in a plan"))
    monkeypatch.setattr(server, "run", command)
    monkeypatch.setattr(server, "initialize", command)
    monkeypatch.setattr(server, "operation_lock", command)
    assert (
        server.main([operation, "--release", "a" * 40, "--backup", "20260902T120000Z-1234abcd"])
        == 0
    )
    command.assert_not_called()


@pytest.mark.parametrize(
    "value", ["main", "../etc", "a" * 39, "A" * 40, "a" * 40 + "; echo unsafe"]
)
def test_release_requires_immutable_sha(value: str) -> None:
    with pytest.raises(server.OperationError):
        server.require_sha(value)


def test_bootstrap_requires_recovery_before_any_host_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = Mock(side_effect=AssertionError("No host change before recovery confirmation"))
    monkeypatch.setattr(server, "run", command)
    args = server.parser().parse_args(["bootstrap", "--apply"])
    with pytest.raises(server.OperationError, match="recovery"):
        server.bootstrap(args)
    command.assert_not_called()


def test_ssh_hardening_requires_independent_session(monkeypatch: pytest.MonkeyPatch) -> None:
    command = Mock(side_effect=AssertionError("No SSH change before independent login"))
    monkeypatch.setattr(server, "run", command)
    args = server.parser().parse_args(["bootstrap", "--harden-ssh"])
    with pytest.raises(server.OperationError, match="independent session"):
        server.harden_ssh(args)
    command.assert_not_called()


@pytest.mark.parametrize("line", ["SMM_POSTGRES_DB=two", "EXTRA=value", "export X=value", "broken"])
def test_environment_rejects_unknown_duplicate_and_shell_syntax(line: str) -> None:
    with pytest.raises(server.OperationError):
        server.parse_env(valid_environment() + line)


def test_environment_is_strict_and_redacts_values() -> None:
    assert server.parse_env(valid_environment())["SMM_POSTGRES_DB"] == "smm"
    invalid = valid_environment().replace("a" * 64, "never-echo-this-secret")
    with pytest.raises(server.OperationError) as error:
        server.parse_env(invalid)
    assert "never-echo" not in str(error.value)


def test_subprocess_failure_never_echoes_captured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, b"secret", b"secret")),
    )
    with pytest.raises(server.OperationError, match="exit=1") as error:
        server.run(["docker", "compose"])
    assert "secret" not in str(error.value)
    assert error.value.captured_output == b"secretsecret"


def archive_with(name: str, kind: bytes = tarfile.REGTYPE) -> tarfile.TarFile:
    memory = io.BytesIO()
    with tarfile.open(fileobj=memory, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            member.linkname = "/etc/passwd"
        archive.addfile(member)
    memory.seek(0)
    return tarfile.open(fileobj=memory)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("dir\\escape", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("hardlink", tarfile.LNKTYPE),
        ("device", tarfile.CHRTYPE),
    ],
)
def test_restore_rejects_unsafe_archive_before_extraction(
    tmp_path: Path, name: str, kind: bytes
) -> None:
    with archive_with(name, kind) as archive, pytest.raises(server.OperationError):
        server.safe_extract(archive, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_restore_accepts_regular_archive(tmp_path: Path) -> None:
    with archive_with("safe.txt") as archive:
        server.safe_extract(archive, tmp_path)
    assert (tmp_path / "safe.txt").is_file()


def test_managed_configuration_does_not_replace_unknown_file(tmp_path: Path) -> None:
    path = tmp_path / "ssh.conf"
    path.write_text("owned by somebody else")
    with pytest.raises(server.OperationError):
        server.managed_file(path, "PasswordAuthentication no\n")
    assert path.read_text() == "owned by somebody else"


def test_managed_configuration_is_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "managed.conf"
    server.managed_file(path, "value=one\n")
    before = path.stat().st_mtime_ns
    server.managed_file(path, "value=one\n")
    assert path.stat().st_mtime_ns == before
    with pytest.raises(server.OperationError):
        server.managed_file(path, "value=two\n")


def test_initialization_refuses_new_credentials_for_existing_database(tmp_path: Path) -> None:
    data = tmp_path / "data" / "postgres"
    data.mkdir(parents=True)
    (data / "PG_VERSION").write_text("17")
    layout = server.Layout(config=tmp_path / "config", data=tmp_path / "data")
    with pytest.raises(server.OperationError, match="recover secrets"):
        server.initialize(layout)
    assert not layout.config.exists()


def test_corrupted_backup_is_rejected_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = server.Layout(backups=tmp_path)
    name = "20260902T120000Z-1234abcd"
    destination = tmp_path / name
    destination.mkdir()
    files = ["database.dump", "database-check.txt", "media.tar", "server.env", "release.json"]
    for file in files:
        (destination / file).write_text("synthetic fixture")
    (destination / "checksums.json").write_text(json.dumps(dict.fromkeys(files, "wrong-checksum")))
    (destination / "complete.json").write_text("{}")
    monkeypatch.setattr(server, "private_file", Mock())
    with pytest.raises(server.OperationError, match="checksum"):
        server.validate_backup(name, layout)


def test_backup_id_cannot_select_an_external_path(tmp_path: Path) -> None:
    with pytest.raises(server.OperationError, match="Invalid backup ID"):
        server.validate_backup("../../etc", server.Layout(backups=tmp_path))


def test_schema_fingerprint_detects_migration_changes(tmp_path: Path) -> None:
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    migration = versions / "0001.py"
    migration.write_text("old version")
    before = server.schema_fingerprint(tmp_path)
    migration.write_text("new version")
    assert before != server.schema_fingerprint(tmp_path)


def test_rollback_refuses_missing_release_before_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "check_engine", Mock())
    monkeypatch.setattr(server, "current", Mock(return_value=None))
    mutate = Mock(side_effect=AssertionError("Must not build a new rollback target"))
    monkeypatch.setattr(server, "stage_release", mutate)
    with pytest.raises(server.OperationError, match="previously built"):
        server.deploy("a" * 40, rollback=True, layout=server.Layout(releases=tmp_path))
    mutate.assert_not_called()


def test_failed_backup_always_resumes_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "current", Mock(return_value={"release": "a" * 40}))
    command = Mock(side_effect=[b"", server.OperationError("dump failed")])
    monkeypatch.setattr(server, "compose", command)
    resume = Mock()
    monkeypatch.setattr(server, "start", resume)
    layout = server.Layout(backups=tmp_path)
    with pytest.raises(server.OperationError, match="dump failed"):
        server.backup(layout)
    resume.assert_called_once_with("a" * 40, layout)
    assert not list(tmp_path.glob("*/complete.json"))


def test_partial_quiesce_failure_also_resumes_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "current", Mock(return_value={"release": "a" * 40}))
    monkeypatch.setattr(server, "compose", Mock(side_effect=server.OperationError("stop failed")))
    resume = Mock()
    monkeypatch.setattr(server, "start", resume)
    layout = server.Layout(backups=tmp_path)
    with pytest.raises(server.OperationError, match="stop failed"):
        server.backup(layout)
    resume.assert_called_once_with("a" * 40, layout)


def test_failed_deploy_resumes_previous_release_without_updating_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = {"release": "a" * 40, "schema_fingerprint": "same"}
    new = {"release": "b" * 40, "schema_fingerprint": "same"}
    for name in ("check_engine", "backup", "compose"):
        monkeypatch.setattr(server, name, Mock())
    monkeypatch.setattr(server, "current", Mock(return_value=old))
    monkeypatch.setattr(server, "stage_release", Mock(return_value=new))
    start = Mock(side_effect=[server.OperationError("readiness failed"), None])
    monkeypatch.setattr(server, "start", start)
    state = Mock()
    monkeypatch.setattr(server, "write_state", state)
    layout = server.Layout(state=tmp_path)
    with pytest.raises(server.OperationError, match="readiness failed"):
        server.deploy(new["release"], layout=layout)
    assert start.call_args_list[1].args == (old["release"], layout)
    state.assert_not_called()
