-- Owner role is POSTGRES_USER (bidradar): runs migrations and owns every table.
-- Application role bidradar_app: NOT owner, NOBYPASSRLS, so RLS is always enforced.
SELECT format('CREATE ROLE bidradar_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidradar_app')\gexec

SELECT 'CREATE DATABASE bidradar OWNER bidradar'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'bidradar')\gexec
SELECT 'CREATE DATABASE bidradar_test OWNER bidradar'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'bidradar_test')\gexec
