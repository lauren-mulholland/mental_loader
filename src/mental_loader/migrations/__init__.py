"""Ordered, forward-only local migrations, serialized across application starts."""

from sqlalchemy import Column, MetaData, String, Table, select

from mental_loader.migrations import (
    v001_baseline,
    v002_accounts,
    v003_conversations,
    v004_security,
    v005_integrity,
    v006_birthdays,
    v007_planning,
    v008_calendar_selection,
)

REVISIONS = (
    ("001", v001_baseline.upgrade),
    ("002", v002_accounts.upgrade),
    ("003", v003_conversations.upgrade),
    ("004", v004_security.upgrade),
    ("005", v005_integrity.upgrade),
    ("006", v006_birthdays.upgrade),
    ("007", v007_planning.upgrade),
    ("008", v008_calendar_selection.upgrade),
)


def upgrade(engine):
    metadata = MetaData()
    revisions = Table("schema_revisions", metadata, Column("version", String(20), primary_key=True))
    with engine.connect() as connection:
        # Transactional DDL and a database-wide migration lock. PostgreSQL is the
        # deployment database; SQLite supports disposable tests/local experiments.
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(482370125)")
        else:
            raise RuntimeError("Migrations support PostgreSQL and SQLite only")
        try:
            metadata.create_all(connection)
            applied = list(
                connection.scalars(select(revisions.c.version).order_by(revisions.c.version))
            )
            expected = [version for version, _ in REVISIONS]
            if applied != expected[: len(applied)]:
                raise RuntimeError(
                    "Unknown or out-of-order schema revision; upgrade the application"
                )
            for version, migrate in REVISIONS[len(applied) :]:
                migrate(connection)
                connection.execute(revisions.insert().values(version=version))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
