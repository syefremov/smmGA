"""Operator-only migrations and first-owner enrollment; never exposed over REST/MCP."""

import argparse
import asyncio
import os
import re
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text, update

from smm_gpt.core.config import get_settings
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.models import Identity, Membership, User, WebSession, Workspace, utcnow
from smm_gpt.services.access import audit


async def provision_logins(database: Database) -> None:
    """Only the migration process receives these credentials; SQL output is never logged."""
    credentials = [
        ("smm_api_login", "smm_app", os.environ.get("SMM_APP_PASSWORD", "")),
        ("smm_worker_login", "smm_worker", os.environ.get("SMM_WORKER_PASSWORD", "")),
    ]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", password) for _, _, password in credentials):
        raise ValueError("Runtime credentials must be generated before migration")
    async with database.transaction() as s:
        for name, group, password in credentials:
            unsafe = await s.scalar(
                text(
                    "SELECT rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole "
                    "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid) "
                    "FROM pg_roles WHERE rolname=:name"
                ),
                {"name": group},
            )
            if unsafe is not False:
                raise ValueError("Unsafe runtime group role")
            if not await s.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname=:name"), {"name": name}
            ):
                await s.execute(
                    text(
                        f"CREATE ROLE {name} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                    )
                )
            unsafe_login = await s.scalar(
                text(
                    "SELECT rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole "
                    "OR EXISTS (SELECT 1 FROM pg_auth_members am JOIN pg_roles parent "
                    "ON parent.oid=am.roleid WHERE am.member=pg_roles.oid "
                    "AND parent.rolname<>:group) "
                    "FROM pg_roles WHERE rolname=:name"
                ),
                {"name": name, "group": group},
            )
            if unsafe_login is not False:
                raise ValueError("Unsafe existing runtime login")
            # format(%L) runs on PostgreSQL; never interpolate unquoted secrets or identifiers.
            statement = await s.scalar(
                text(
                    "SELECT format('ALTER ROLE %I PASSWORD %L', "
                    "CAST(:name AS text), CAST(:password AS text))"
                ),
                {"name": name, "password": password},
            )
            await s.execute(text(str(statement)))
            await s.execute(text(f"GRANT {group} TO {name}"))


async def bootstrap_owner(
    database: Database, issuer: str, subject: str, mcp_issuer: str, mcp_subject: str
) -> tuple[UUID, UUID]:
    if not all(
        (issuer.startswith("https://"), mcp_issuer.startswith("https://"), subject, mcp_subject)
    ):
        raise ValueError("Explicit verified issuer and subject identifiers are required")
    async with database.transaction() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(470001)"))
        if await s.scalar(select(Workspace.id).where(Workspace.slug == "greenaurum")):
            raise ValueError("Owner already bootstrapped; refusing to replace membership")
        user_id, workspace_id = uuid4(), uuid4()
        s.add(User(id=user_id, display_name="GreenAurum Owner"))
        s.add(Workspace(id=workspace_id, name="GreenAurum", slug="greenaurum"))
        await s.flush()
        for identity_issuer, identity_subject in {(issuer, subject), (mcp_issuer, mcp_subject)}:
            s.add(Identity(user_id=user_id, issuer=identity_issuer, subject=identity_subject))
        s.add(Membership(workspace_id=workspace_id, user_id=user_id, role="owner"))
        audit(s, user_id, workspace_id, uuid4(), "owner.bootstrap", "allowed", user_id)
        return user_id, workspace_id


async def operate(args: argparse.Namespace) -> None:
    db = Database(get_settings().database_url.get_secret_value(), 5)
    try:
        if args.operation == "migrate":
            await provision_logins(db)
        elif args.operation == "bootstrap-owner":
            user, workspace = await bootstrap_owner(
                db, args.issuer, args.subject, args.mcp_issuer, args.mcp_subject
            )
            print(f"owner_id={user} workspace_id={workspace}")
        else:
            user_id = UUID(args.user_id)
            async with db.transaction() as s:
                if await s.get(User, user_id) is None:
                    raise ValueError("Unknown user")
                await s.execute(update(User).where(User.id == user_id).values(active=False))
                await s.execute(
                    update(WebSession)
                    .where(
                        WebSession.identity_id.in_(
                            select(Identity.id).where(Identity.user_id == user_id)
                        )
                    )
                    .values(revoked_at=utcnow())
                )
                audit(s, None, None, uuid4(), "operator.user_disable", "allowed", user_id)
    finally:
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("migrate")
    bootstrap = sub.add_parser("bootstrap-owner")
    for argument in ("issuer", "subject", "mcp-issuer", "mcp-subject"):
        bootstrap.add_argument("--" + argument, required=True)
    disable = sub.add_parser("disable-user")
    disable.add_argument("--user-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Plan only; pass --apply before the subcommand after reviewing the operation.")
        return 0
    try:
        if args.operation == "migrate":
            command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(operate(args))
    except Exception:
        # Do not expose SQL, connection URLs, submitted identifiers or IdP claims.
        print("Operation failed; credentials and database diagnostics withheld.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
