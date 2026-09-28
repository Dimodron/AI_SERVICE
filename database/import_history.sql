
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
