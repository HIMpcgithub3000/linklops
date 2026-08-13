"""Alembic environment.

The connection URL comes from MIGRATION_DATABASE_URL, never DATABASE_URL.
That split is the enforcement of "migrations run as the owner, the app never
does" -- because a table owner bypasses row-level security silently, an app
sharing the migration credential would have policies that exist and do
nothing. One credential per privilege level beats one shared connection with
a promise attached.

Importing app.config here is deliberate: alembic inherits the same fail-fast
validation and key-parity checks as the API and the worker, because they all
enter through the same import.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import settings
from app.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", str(settings.MIGRATION_DATABASE_URL))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
