"""Birthday actions and conversation-scoped planning settings."""

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, Text, text


def upgrade(connection):
    metadata = MetaData()
    Table("conversations", metadata, Column("id", String(36), primary_key=True))
    Table(
        "occasion_settings",
        metadata,
        Column("conversation_id", String(36), ForeignKey("conversations.id"), primary_key=True),
        Column("revision", Integer, nullable=False),
        Column("document", Text, nullable=False),
    ).create(connection)
    Table(
        "gift_edit_previews",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("conversation_id", String(36), ForeignKey("conversations.id"), nullable=False),
        Column("document", Text, nullable=False),
        Column("created", String(40), nullable=False),
        Column("request_id", String(36)),
    ).create(connection)
    for column in ("person_id VARCHAR(36)", "actions TEXT"):
        connection.execute(text(f"ALTER TABLE birthdays ADD COLUMN {column}"))
