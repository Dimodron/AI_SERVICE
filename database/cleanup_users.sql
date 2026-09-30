-- Apply after deploying the updated API. Does not recreate any tables.
-- No CASCADE: dependent views must be reviewed instead of silently deleted.
BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE public.users
    DROP COLUMN IF EXISTS name,
    DROP COLUMN IF EXISTS jurpers,
    DROP COLUMN IF EXISTS organization;
COMMIT;
