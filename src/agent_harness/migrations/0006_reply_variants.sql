ALTER TABLE agent_chat.messages ADD COLUMN selected_variant INTEGER NOT NULL DEFAULT 0 CHECK(selected_variant >= 0);
CREATE TABLE agent_chat.message_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES agent_chat.messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    content JSONB NOT NULL CHECK(jsonb_typeof(content)='array'),
    rating TEXT CHECK(rating IN ('up','down')),
    input_tokens BIGINT NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
    output_tokens BIGINT NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
    cache_read_tokens BIGINT NOT NULL DEFAULT 0 CHECK(cache_read_tokens >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(message_id,ordinal)
);
INSERT INTO agent_chat.message_variants(id,message_id,ordinal,content,input_tokens,output_tokens,cache_read_tokens,created_at)
SELECT id,id,0,content,input_tokens,output_tokens,cache_read_tokens,created_at FROM agent_chat.messages WHERE role='assistant';
