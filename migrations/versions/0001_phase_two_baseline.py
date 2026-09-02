"""Create the phase-two migration baseline.

Revision ID: 0001_phase_two
Revises:
Create Date: 2026-09-02
"""

from collections.abc import Sequence

revision: str = "0001_phase_two"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Reserve a reproducible baseline before domain tables are introduced."""


def downgrade() -> None:
    """The empty baseline has no database objects to remove."""
