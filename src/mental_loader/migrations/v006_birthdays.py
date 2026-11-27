"""Dated preparation records with the same immutable conversation boundary as tasks."""

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)


def upgrade(connection):
    # Frozen definition: later models must not alter an already released migration.
    metadata = MetaData()
    for parent in ("households", "conversations", "tasks"):
        Table(parent, metadata, Column("id", String(36), primary_key=True))
    table = Table(
        "birthdays", metadata,
        Column("id", String(36), primary_key=True),
        Column("household_id", String(36), ForeignKey("households.id"), nullable=False, index=True),
        Column("conversation_id", String(36), ForeignKey("conversations.id"), nullable=False),
        Column("recipient", String(100), nullable=False),
        Column("occasion_date", Date, nullable=False),
        Column("preferences", Text, nullable=False),
        Column("budget", String(30)), Column("currency", String(3), nullable=False),
        Column("lead_days", Integer, nullable=False),
        Column("gift_status", String(20), nullable=False),
        Column("task_id", String(36), ForeignKey("tasks.id"), unique=True),
        Column("calendar_id", String(1024)), Column("event_id", String(1024)),
        UniqueConstraint("household_id", "calendar_id", "event_id"),
    )
    table.create(connection)
    valid = (
        "EXISTS (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id "
        "AND c.household_id=NEW.household_id) AND (NEW.task_id IS NULL OR EXISTS "
        "(SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.household_id=NEW.household_id "
        "AND t.conversation_id=NEW.conversation_id))"
    )
    if connection.dialect.name == "postgresql":
        connection.execute(text(
            "CREATE FUNCTION validate_birthdays_scope() RETURNS trigger LANGUAGE plpgsql AS $$ "
            f"BEGIN IF NOT ({valid}) THEN RAISE EXCEPTION 'Birthday ancestry violation' "
            "USING ERRCODE='23514'; END IF; RETURN NEW; END $$"
        ))
        connection.execute(text(
            "CREATE TRIGGER birthdays_scope BEFORE INSERT OR UPDATE ON birthdays "
            "FOR EACH ROW EXECUTE FUNCTION validate_birthdays_scope()"
        ))
        connection.execute(text(
            "CREATE FUNCTION immutable_birthdays_scope() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.household_id IS DISTINCT FROM OLD.household_id OR "
            "NEW.conversation_id IS DISTINCT FROM OLD.conversation_id THEN "
            "RAISE EXCEPTION 'Immutable birthday scope' USING ERRCODE='23514'; "
            "END IF; RETURN NEW; END $$"
        ))
        connection.execute(text(
            "CREATE TRIGGER immutable_birthdays BEFORE UPDATE ON birthdays "
            "FOR EACH ROW EXECUTE FUNCTION immutable_birthdays_scope()"
        ))
    else:
        for action in ("INSERT", "UPDATE"):
            connection.execute(text(
                f"CREATE TRIGGER birthdays_scope_{action.lower()} BEFORE {action} ON birthdays "
                f"WHEN NOT ({valid}) BEGIN SELECT RAISE(ABORT, 'Birthday ancestry violation'); END"
            ))
        connection.execute(text(
            "CREATE TRIGGER immutable_birthdays BEFORE UPDATE ON birthdays "
            "WHEN NEW.household_id IS NOT OLD.household_id OR "
            "NEW.conversation_id IS NOT OLD.conversation_id BEGIN "
            "SELECT RAISE(ABORT, 'Immutable birthday scope'); END"
        ))
