"""Individual accounts; additive upgrade with no credential or history changes."""

from sqlalchemy import Column, MetaData, String, Table, text


def upgrade(connection):
    metadata = MetaData()
    Table(
        "users", metadata,
        Column("id", String(36), primary_key=True),
        Column("name", String(120), nullable=False),
        Column("access_hash", String(64), nullable=False, unique=True),
    )
    # References use SQL here so this frozen revision does not import current models.
    metadata.create_all(connection)
    connection.execute(text(
        "CREATE TABLE memberships ("
        "household_id VARCHAR(36) NOT NULL REFERENCES households(id), "
        "user_id VARCHAR(36) NOT NULL REFERENCES users(id), "
        "role VARCHAR(20) NOT NULL CHECK (role IN ('owner', 'member')), "
        "PRIMARY KEY (household_id, user_id))"
    ))
    connection.execute(text(
        "ALTER TABLE login_sessions ADD COLUMN user_id VARCHAR(36) REFERENCES users(id)"
    ))
    connection.execute(text(
        "ALTER TABLE push_subscriptions ADD COLUMN user_id VARCHAR(36) REFERENCES users(id)"
    ))
