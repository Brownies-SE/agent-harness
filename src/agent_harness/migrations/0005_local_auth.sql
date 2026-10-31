CREATE TABLE agent_chat.local_account (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    user_id UUID UNIQUE NOT NULL REFERENCES agent_chat.users(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL
);
CREATE TABLE agent_chat.auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES agent_chat.users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX auth_sessions_owner ON agent_chat.auth_sessions(user_id);
