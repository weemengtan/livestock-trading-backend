-- Production database roles for the application (run once, as the database
-- owner / a superuser; NOT run by migrations).
--
-- Why: the audit log is append-only. A trigger already rejects UPDATE,
-- DELETE and TRUNCATE on audit_log, but the trigger's owner could disable
-- it. Running the application as a role that does NOT own the tables closes
-- that: it cannot disable triggers, and it is explicitly denied UPDATE,
-- DELETE and TRUNCATE on audit_log. Migrations keep running as the owner.
--
-- Usage:
--   psql "$OWNER_DATABASE_URL" -v app_password="'change-me'" -f scripts/create_app_role.sql
-- then point the application's DATABASE_URL at livestock_app.

CREATE ROLE livestock_app LOGIN PASSWORD :app_password;

GRANT USAGE ON SCHEMA public TO livestock_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO livestock_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO livestock_app;

-- Append-only: the app may add and read audit entries, nothing else.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM livestock_app;

-- Tables and sequences created by future migrations get the same grants
-- (audit_log's restrictions above are per-table and are not undone by this).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO livestock_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO livestock_app;
