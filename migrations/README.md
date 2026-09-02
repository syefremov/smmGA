# Database migrations

Alembic owns every PostgreSQL schema change. Run `pnpm db:migrate` against the local Compose
stack. Never edit a shared database schema by hand.
