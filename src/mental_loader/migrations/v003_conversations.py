"""Explicit conversation visibility and participants, preserving old shared chats."""

from sqlalchemy import text


def upgrade(connection):
    for statement in (
        "ALTER TABLE conversations ADD COLUMN visibility VARCHAR(20) NOT NULL "
        "DEFAULT 'household' CHECK (visibility IN ('household', 'private'))",
        "ALTER TABLE conversations ADD COLUMN owner_id VARCHAR(36) REFERENCES users(id) "
        "CHECK (visibility = 'household' OR owner_id IS NOT NULL)",
        "CREATE UNIQUE INDEX conversations_id_household ON conversations (id, household_id)",
        "CREATE TABLE conversation_participants ("
        "conversation_id VARCHAR(36) NOT NULL, household_id VARCHAR(36) NOT NULL, "
        "user_id VARCHAR(36) NOT NULL, PRIMARY KEY (conversation_id, user_id), "
        "FOREIGN KEY (conversation_id, household_id) REFERENCES conversations(id, household_id), "
        "FOREIGN KEY (household_id, user_id) REFERENCES memberships(household_id, user_id) "
        "ON DELETE CASCADE)",
        "ALTER TABLE runs ADD COLUMN requested_by VARCHAR(36) REFERENCES users(id)",
    ):
        connection.execute(text(statement))
