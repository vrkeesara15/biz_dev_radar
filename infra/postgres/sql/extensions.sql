-- Applied to each application database (bidradar, bidradar_test).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

GRANT USAGE ON SCHEMA public TO bidradar_app;
GRANT CONNECT ON DATABASE bidradar TO bidradar_app;
GRANT CONNECT ON DATABASE bidradar_test TO bidradar_app;
-- Tables created later by the owner (alembic) are automatically DML-granted to the app role.
-- Migrations revoke UPDATE/DELETE again where rows must be append-only (audit_log).
ALTER DEFAULT PRIVILEGES FOR ROLE bidradar IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bidradar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE bidradar IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO bidradar_app;
