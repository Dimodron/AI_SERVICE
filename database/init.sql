CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    login TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Also applies to an existing installation without resetting data.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_uuid UUID REFERENCES users(id),
    model TEXT NOT NULL,
    title TEXT,
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_conversation_id_id_idx ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS files (
    id UUID PRIMARY KEY,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    extracted_text TEXT NOT NULL,
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_files (
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES files(id),
    PRIMARY KEY (conversation_id, file_id)
);

CREATE TABLE IF NOT EXISTS message_files (
    message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES files(id),
    PRIMARY KEY (message_id, file_id)
);

CREATE TABLE IF NOT EXISTS scenarios (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    table_name TEXT,
    columns_description JSONB NOT NULL DEFAULT '{}'::jsonb,
    tables JSONB NOT NULL DEFAULT '[]'::jsonb,
    scenario TEXT NOT NULL,
    visible_jurpers BIGINT[] NOT NULL DEFAULT '{}',
    groups TEXT[] NOT NULL DEFAULT '{}',
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    create_user UUID REFERENCES users(id),
    edit_user UUID REFERENCES users(id),
    create_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    edit_time TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_prompt (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_num INTEGER NOT NULL DEFAULT 0,
    prompt TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    create_user UUID REFERENCES users(id),
    edit_user UUID REFERENCES users(id),
    create_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    edit_time TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Existing scenarios retain their previous visibility and have no groups.
ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS groups TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS tables JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Safe to apply to an existing database; existing data is preserved.
CREATE TABLE IF NOT EXISTS public.data_import_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('replace', 'append')),
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
    row_count BIGINT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS data_import_history_table_time_idx
    ON public.data_import_history (table_name, finished_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS data_import_history_time_idx
    ON public.data_import_history (finished_at DESC, id DESC);


-- Idempotency keys for saved questions and their answers; old rows remain valid.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS request_id UUID;
CREATE UNIQUE INDEX IF NOT EXISTS messages_request_role_idx
    ON messages (conversation_id, request_id, role) WHERE request_id IS NOT NULL;

ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS report_template JSONB;
