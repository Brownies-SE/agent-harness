-- Host connection lifecycle events have no chat conversation; API tool requests still do.
ALTER TABLE agent_chat.tool_calls ALTER COLUMN conversation_id DROP NOT NULL;
