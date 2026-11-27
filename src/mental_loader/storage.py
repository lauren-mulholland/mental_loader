import hashlib
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(UTC)


def identifier() -> str:
    return str(uuid.uuid4())


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Base(DeclarativeBase):
    pass


class Household(Base):
    __tablename__ = "households"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(120))
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    timezone: Mapped[str] = mapped_column(String(60), default="Australia/Sydney")


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(120))
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    recovery_hash: Mapped[str | None] = mapped_column(String(64), unique=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint("role IN ('owner', 'member')"),)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "household_id"),
        CheckConstraint("visibility IN ('household', 'private')"),
        CheckConstraint("visibility = 'household' OR owner_id IS NOT NULL"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    visibility: Mapped[str] = mapped_column(String(20), default="household")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "household_id"], ["conversations.id", "conversations.household_id"]
        ),
        ForeignKeyConstraint(
            ["household_id", "user_id"],
            ["memberships.household_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
    )
    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("household_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    input: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    title: Mapped[str] = mapped_column(String(240))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Birthday(Base):
    __tablename__ = "birthdays"
    __table_args__ = (UniqueConstraint("household_id", "calendar_id", "event_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    recipient: Mapped[str] = mapped_column(String(100))
    occasion_date: Mapped[date] = mapped_column(Date)
    preferences: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[str | None] = mapped_column(String(30))
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    lead_days: Mapped[int] = mapped_column(Integer, default=14)
    gift_status: Mapped[str] = mapped_column(String(20), default="planning")
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), unique=True)
    calendar_id: Mapped[str | None] = mapped_column(String(1024))
    event_id: Mapped[str | None] = mapped_column(String(1024))


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    session_hash: Mapped[str | None] = mapped_column(
        ForeignKey("login_sessions.token_hash", ondelete="CASCADE")
    )
    device_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True)
    subscription: Mapped[dict] = mapped_column(JSON)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("task_id", "subscription_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    subscription_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "user_id"],
            ["memberships.household_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
    )
    household_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    encrypted_credentials: Mapped[str] = mapped_column(Text)


class OAuthAttempt(Base):
    __tablename__ = "oauth_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_hash: Mapped[str] = mapped_column(String(64))
    encrypted_verifier: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class RuntimeBinding(Base):
    __tablename__ = "runtime_bindings"
    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id"))
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), unique=True)
    container_id: Mapped[str] = mapped_column(String(64), unique=True)
    endpoint: Mapped[str] = mapped_column(String(240), unique=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    tool_hash: Mapped[str] = mapped_column(String(64), unique=True)


def database(url: str):
    from mental_loader.migrations import upgrade

    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    upgrade(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
