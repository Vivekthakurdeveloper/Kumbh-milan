from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

import app.models  # noqa: F401  registers every table on Base.metadata
from app.config import get_settings
from app.database.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


# Tables owned by Postgres extensions (PostGIS, tiger geocoder, topology), not by this application.
# Filled from the catalog when connected, so add-ons installed by the PostGIS image are never reported as drift.
EXTENSION_TABLES: set[str] = {"spatial_ref_sys"}
_EXTENSION_TABLES_SQL = text(
    "SELECT c.relname FROM pg_depend d "
    "JOIN pg_class c ON c.oid = d.objid AND d.classid = 'pg_class'::regclass "
    "WHERE d.deptype = 'e' AND c.relkind IN ('r', 'p')"
)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "table" and name in EXTENSION_TABLES)


def _database_url() -> str:
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        EXTENSION_TABLES.update(connection.execute(_EXTENSION_TABLES_SQL).scalars())
        connection.commit()
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
