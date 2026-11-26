"""Database-enforced record ancestry. Equivalent checks on SQLite and PostgreSQL."""

from sqlalchemy import text

# INSERT/UPDATE checks deliberately allow missing optional references only for old
# delivery receipts whose subscription was removed. New receipts must have a recipient.
CHECKS = {
    "runs": "EXISTS (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id "
    "AND c.household_id=NEW.household_id)",
    "messages": "EXISTS (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id "
    "AND c.household_id=NEW.household_id) AND (NEW.run_id IS NULL OR EXISTS "
    "(SELECT 1 FROM runs r WHERE r.id=NEW.run_id AND r.household_id=NEW.household_id "
    "AND r.conversation_id=NEW.conversation_id))",
    "tasks": "EXISTS (SELECT 1 FROM runs r WHERE r.id=NEW.run_id "
    "AND r.household_id=NEW.household_id AND r.conversation_id=NEW.conversation_id)",
    "deliveries": "EXISTS (SELECT 1 FROM tasks t WHERE t.id=NEW.task_id "
    "AND t.household_id=NEW.household_id) AND NOT EXISTS "
    "(SELECT 1 FROM push_subscriptions s WHERE s.id=NEW.subscription_id "
    "AND s.household_id<>NEW.household_id)",
    "runtime_bindings": "NEW.conversation_id IS NULL OR EXISTS (SELECT 1 FROM conversations c "
    "WHERE c.id=NEW.conversation_id AND c.household_id=NEW.household_id "
    "AND c.visibility='private')",
    "ai_usage": "NEW.run_id IS NULL OR EXISTS (SELECT 1 FROM runs r WHERE r.id=NEW.run_id "
    "AND r.household_id=NEW.household_id AND "
    "(r.requested_by=NEW.user_id OR (r.requested_by IS NULL AND NEW.user_id IS NULL)))",
    "login_sessions": "NEW.user_id IS NULL OR EXISTS (SELECT 1 FROM memberships m "
    "WHERE m.household_id=NEW.household_id AND m.user_id=NEW.user_id)",
    "push_subscriptions": "NEW.user_id IS NULL OR EXISTS (SELECT 1 FROM memberships m "
    "WHERE m.household_id=NEW.household_id AND m.user_id=NEW.user_id)",
}


def upgrade(connection):
    # Refuse an upgrade over corrupt pre-existing ancestry rather than silently hiding it.
    for table, condition in CHECKS.items():
        current = condition.replace("NEW.", "record.")
        bad = connection.execute(
            text(f"SELECT 1 FROM {table} record WHERE NOT ({current}) LIMIT 1")
        )
        if bad.first():
            raise RuntimeError(f"Existing {table} records violate household ancestry")
        if connection.dialect.name == "postgresql":
            connection.execute(
                text(
                    f"CREATE FUNCTION validate_{table}_scope() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ "
                    f"BEGIN IF NOT ({condition}) THEN "
                    "RAISE EXCEPTION 'Household ancestry violation' "
                    "USING ERRCODE='23514'; END IF; RETURN NEW; END $$"
                )
            )
            connection.execute(
                text(
                    f"CREATE TRIGGER {table}_scope BEFORE INSERT OR UPDATE ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION validate_{table}_scope()"
                )
            )
        else:
            for action in ("INSERT", "UPDATE"):
                connection.execute(
                    text(
                        f"CREATE TRIGGER {table}_scope_{action.lower()} BEFORE {action} ON {table} "
                        f"WHEN NOT ({condition}) BEGIN "
                        "SELECT RAISE(ABORT, 'Household ancestry violation'); END"
                    )
                )
    # Parent identities are immutable, so a valid parent cannot subsequently move
    # to a new household and strand already-validated children in the old tenant.
    for table, columns in {
        "conversations": ("household_id", "visibility", "owner_id"),
        "runs": ("household_id", "conversation_id", "requested_by"),
        "tasks": ("household_id", "conversation_id", "run_id"),
        "messages": ("household_id", "conversation_id", "run_id"),
        "push_subscriptions": ("household_id",),
    }.items():
        if connection.dialect.name == "postgresql":
            changed = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in columns)
            connection.execute(
                text(
                    f"CREATE FUNCTION immutable_{table}_scope() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ "
                    f"BEGIN IF {changed} THEN RAISE EXCEPTION 'Immutable record scope' "
                    "USING ERRCODE='23514'; END IF; RETURN NEW; END $$"
                )
            )
            connection.execute(
                text(
                    f"CREATE TRIGGER immutable_{table} BEFORE UPDATE ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION immutable_{table}_scope()"
                )
            )
        else:
            changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in columns)
            connection.execute(
                text(
                    f"CREATE TRIGGER immutable_{table} BEFORE UPDATE ON {table} "
                    f"WHEN {changed} BEGIN SELECT RAISE(ABORT, 'Immutable record scope'); END"
                )
            )
