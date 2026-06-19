-- Smoke test transaction for PostgreSQL connectivity and write/read health.
-- Run this after postgres_schema.sql in the same database.

BEGIN;

INSERT INTO users (id, first_name, surname, email, role, language, disabled)
VALUES (
    'smoke_user_001',
    'Smoke',
    'Test',
    'smoke.user.001@folio.local',
    'employee',
    'en',
    FALSE
)
ON CONFLICT (id) DO UPDATE
SET
    first_name = EXCLUDED.first_name,
    surname = EXCLUDED.surname,
    email = EXCLUDED.email,
    role = EXCLUDED.role,
    language = EXCLUDED.language,
    disabled = EXCLUDED.disabled;

INSERT INTO audit_logs (user_id, action, details, ip_address, user_agent, session_id)
VALUES (
    'smoke_user_001',
    'user_registered',
    '{"source":"postgres_smoke_test.sql","status":"ok"}'::jsonb,
    '127.0.0.1',
    'pgadmin-query-tool',
    'smoke-session'
);

SELECT id, first_name, surname, email, role, language, disabled, created_at
FROM users
WHERE id = 'smoke_user_001';

SELECT id, user_id, action, timestamp, details
FROM audit_logs
WHERE user_id = 'smoke_user_001'
ORDER BY timestamp DESC
LIMIT 3;

COMMIT;
