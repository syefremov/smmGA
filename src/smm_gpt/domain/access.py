"""Transport-independent permissions. Token claims never assign workspace roles."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    STRATEGIST = "strategist"
    EDITOR = "editor"
    PUBLISHER = "publisher"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Permission(StrEnum):
    READ = "workspace.read"
    MANAGE = "members.manage"
    PLAN = "content.plan"
    EDIT = "content.edit"
    APPROVE = "content.approve"
    PUBLISH = "content.publish"
    ANALYZE = "analytics.read"
    AUDIT = "audit.read"
    RUN_JOB = "system.job.run"
    WORK_ITEM = "work_item.write"
    COMMENT = "content.comment"
    KNOWLEDGE = "knowledge.write"


GRANTS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.ADMINISTRATOR: frozenset({Permission.READ, Permission.MANAGE, Permission.AUDIT}),
    Role.STRATEGIST: frozenset(
        {
            Permission.READ,
            Permission.PLAN,
            Permission.RUN_JOB,
            Permission.WORK_ITEM,
            Permission.COMMENT,
            Permission.KNOWLEDGE,
        }
    ),
    Role.EDITOR: frozenset(
        {
            Permission.READ,
            Permission.EDIT,
            Permission.RUN_JOB,
            Permission.WORK_ITEM,
            Permission.COMMENT,
            Permission.KNOWLEDGE,
        }
    ),
    Role.PUBLISHER: frozenset({Permission.READ, Permission.PUBLISH, Permission.COMMENT}),
    Role.ANALYST: frozenset(
        {Permission.READ, Permission.ANALYZE, Permission.RUN_JOB, Permission.WORK_ITEM}
    ),
    Role.VIEWER: frozenset({Permission.READ}),
}
MFA_ROLES = {Role.OWNER, Role.ADMINISTRATOR, Role.PUBLISHER}


class AccessDenied(Exception):
    """Safe error shared by all transports; never includes submitted values."""


class Conflict(Exception):
    """A request conflicts with an already recorded operation."""


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    identity_id: UUID
    mfa: bool


def authorize(role: str, permission: Permission, *, mfa: bool) -> None:
    try:
        parsed = Role(role)
    except ValueError:
        raise AccessDenied("access_denied") from None
    if permission not in GRANTS[parsed] or (parsed in MFA_ROLES and not mfa):
        raise AccessDenied("access_denied")
