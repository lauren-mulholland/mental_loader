"""Account recovery, encrypted integrations, runtime scopes and job leases."""

from sqlalchemy import text


def upgrade(connection):
    statements = [
        "ALTER TABLE users ADD COLUMN recovery_hash VARCHAR(64)",
        "CREATE UNIQUE INDEX users_recovery_hash ON users(recovery_hash)",
        "ALTER TABLE push_subscriptions ADD COLUMN session_hash VARCHAR(64) "
        "REFERENCES login_sessions(token_hash) ON DELETE CASCADE",
        "ALTER TABLE push_subscriptions ADD COLUMN device_hash VARCHAR(64)",
        "CREATE INDEX push_device ON push_subscriptions(device_hash)",
        "ALTER TABLE runs ADD COLUMN claim_token VARCHAR(36)",
        "ALTER TABLE runs ADD COLUMN lease_until TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE deliveries ADD COLUMN claim_token VARCHAR(36)",
        "ALTER TABLE deliveries ADD COLUMN lease_until TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE ai_usage ADD COLUMN run_id VARCHAR(36) REFERENCES runs(id)",
        "ALTER TABLE ai_usage ADD COLUMN user_id VARCHAR(36) REFERENCES users(id)",
        "CREATE UNIQUE INDEX ai_usage_run ON ai_usage(run_id)",
        "CREATE UNIQUE INDEX one_running_conversation ON runs(conversation_id) "
        "WHERE status = 'running'",
        "CREATE TABLE integration_connections (household_id VARCHAR(36) NOT NULL, "
        "user_id VARCHAR(36) NOT NULL, provider VARCHAR(40) NOT NULL, "
        "encrypted_credentials TEXT NOT NULL, PRIMARY KEY(household_id, user_id, provider), "
        "FOREIGN KEY(household_id, user_id) REFERENCES memberships(household_id, user_id) "
        "ON DELETE CASCADE)",
        "CREATE TABLE oauth_attempts (state_hash VARCHAR(64) PRIMARY KEY, "
        "household_id VARCHAR(36) NOT NULL REFERENCES households(id), "
        "user_id VARCHAR(36) NOT NULL REFERENCES users(id), session_hash VARCHAR(64) NOT NULL, "
        "encrypted_verifier TEXT NOT NULL, status VARCHAR(20) NOT NULL, "
        "expires_at TIMESTAMP WITH TIME ZONE NOT NULL)",
        "CREATE TABLE rate_buckets (key VARCHAR(64) NOT NULL, bucket_start INTEGER NOT NULL, "
        "count INTEGER NOT NULL, PRIMARY KEY(key, bucket_start))",
        "CREATE TABLE runtime_bindings (scope VARCHAR(80) PRIMARY KEY, "
        "household_id VARCHAR(36) NOT NULL REFERENCES households(id), "
        "conversation_id VARCHAR(36) UNIQUE REFERENCES conversations(id), "
        "container_id VARCHAR(64) NOT NULL UNIQUE, endpoint VARCHAR(240) NOT NULL UNIQUE, "
        "encrypted_api_key TEXT NOT NULL, tool_hash VARCHAR(64) NOT NULL UNIQUE)",
    ]
    for statement in statements:
        connection.execute(text(statement))
