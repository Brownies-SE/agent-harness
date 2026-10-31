ALTER TABLE agent_chat.tool_calls
    ADD COLUMN user_id UUID REFERENCES agent_chat.users(id) ON DELETE CASCADE,
    ADD COLUMN conversation_id UUID REFERENCES agent_chat.conversations(id) ON DELETE CASCADE,
    ADD COLUMN run_id UUID,
    ALTER COLUMN message_id DROP NOT NULL;

UPDATE agent_chat.tool_calls tc
    SET conversation_id=m.conversation_id,user_id=c.user_id
    FROM agent_chat.messages m
    JOIN agent_chat.conversations c ON c.id=m.conversation_id
    WHERE tc.message_id=m.id;

ALTER TABLE agent_chat.tool_calls
    ALTER COLUMN user_id SET NOT NULL,
    ALTER COLUMN conversation_id SET NOT NULL,
    DROP CONSTRAINT tool_calls_message_id_fkey,
    ADD CONSTRAINT tool_call_message_scope FOREIGN KEY (conversation_id,message_id)
        REFERENCES agent_chat.messages(conversation_id,id) ON DELETE SET NULL (message_id);

ALTER TABLE agent_chat.mcp_connections ADD CONSTRAINT connection_owner UNIQUE(id,user_id);
ALTER TABLE agent_chat.tool_calls
    DROP CONSTRAINT tool_calls_connection_id_fkey,
    ADD CONSTRAINT tool_call_connection_scope FOREIGN KEY(connection_id,user_id)
        REFERENCES agent_chat.mcp_connections(id,user_id) ON DELETE SET NULL(connection_id);

ALTER TABLE agent_chat.memories ADD COLUMN embedding_model TEXT;
CREATE INDEX tool_calls_owner_run ON agent_chat.tool_calls(user_id,run_id);
CREATE INDEX tool_calls_conversation ON agent_chat.tool_calls(conversation_id,started_at DESC);
