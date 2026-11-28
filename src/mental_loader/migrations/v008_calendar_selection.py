"""Give each imported instance a stable recipient key and a reversible exclusion flag."""

from sqlalchemy import text


def upgrade(connection):
    connection.execute(text("ALTER TABLE birthdays ADD COLUMN calendar_key VARCHAR(64)"))
    connection.execute(
        text("ALTER TABLE birthdays ADD COLUMN sync_excluded BOOLEAN NOT NULL DEFAULT FALSE")
    )
