CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

ALTER TABLE agent_chat.conversations
    ADD COLUMN summary TEXT,
    ADD COLUMN compacted_through_message_id UUID,
    ADD COLUMN memory_processed_at TIMESTAMPTZ,
    ADD CONSTRAINT conversations_id_owner UNIQUE (id, user_id);

ALTER TABLE agent_chat.messages
    ADD COLUMN cache_read_tokens BIGINT NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    ADD CONSTRAINT messages_conversation_id UNIQUE (conversation_id, id);

ALTER TABLE agent_chat.conversations
    ADD CONSTRAINT compaction_message_scope
    FOREIGN KEY (id, compacted_through_message_id)
    REFERENCES agent_chat.messages(conversation_id, id)
    ON DELETE SET NULL (compacted_through_message_id);

CREATE TABLE agent_chat.mcp_servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    transport TEXT NOT NULL CHECK (transport IN ('streamable_http', 'sse', 'stdio')),
    auth_type TEXT NOT NULL CHECK (auth_type IN ('none', 'oauth', 'api_key')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_chat.mcp_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES agent_chat.users(id) ON DELETE CASCADE,
    server_id UUID NOT NULL REFERENCES agent_chat.mcp_servers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'needs_reauth', 'disabled')),
    access_token_enc BYTEA,
    refresh_token_enc BYTEA,
    token_expires_at TIMESTAMPTZ,
    scopes TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, server_id)
);

CREATE TABLE agent_chat.mcp_tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    server_id UUID NOT NULL REFERENCES agent_chat.mcp_servers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    input_schema JSONB NOT NULL CHECK (jsonb_typeof(input_schema) = 'object'),
    deferred BOOLEAN NOT NULL DEFAULT true,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (server_id, name)
);

CREATE TABLE agent_chat.tool_permissions (
    user_id UUID NOT NULL REFERENCES agent_chat.users(id) ON DELETE CASCADE,
    tool_key TEXT NOT NULL,
    policy TEXT NOT NULL CHECK (policy IN ('allow', 'ask', 'deny')),
    PRIMARY KEY (user_id, tool_key)
);

CREATE TABLE agent_chat.tool_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES agent_chat.messages(id) ON DELETE CASCADE,
    tool_use_id TEXT NOT NULL,
    tool_key TEXT NOT NULL,
    connection_id UUID REFERENCES agent_chat.mcp_connections(id) ON DELETE SET NULL,
    input JSONB NOT NULL,
    output JSONB,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'awaiting_approval', 'running', 'succeeded', 'failed', 'denied')),
    error TEXT,
    approved_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (message_id, tool_use_id)
);

CREATE TABLE agent_chat.files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES agent_chat.users(id) ON DELETE CASCADE,
    conversation_id UUID,
    storage_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT,
    size_bytes BIGINT CHECK (size_bytes >= 0),
    origin TEXT NOT NULL CHECK (origin IN ('upload', 'generated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (conversation_id, user_id)
        REFERENCES agent_chat.conversations(id, user_id)
        ON DELETE SET NULL (conversation_id)
);

CREATE TABLE agent_chat.memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES agent_chat.users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('stated', 'inferred')),
    source_conversation_id UUID,
    embedding public.vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (source_conversation_id, user_id)
        REFERENCES agent_chat.conversations(id, user_id)
        ON DELETE SET NULL (source_conversation_id)
);

CREATE INDEX tool_calls_message ON agent_chat.tool_calls(message_id);
CREATE INDEX memories_owner ON agent_chat.memories(user_id);
