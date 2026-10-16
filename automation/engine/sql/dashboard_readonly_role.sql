-- Read-only Postgres role for the seo-dashboard service.
-- Run once as the database owner (Railway → Postgres → Data → Query, or psql with DATABASE_URL).
-- Replace the password with a generated one and store the resulting URL only in the
-- seo-dashboard service's DATABASE_URL variable.
CREATE ROLE seo_dashboard_ro LOGIN PASSWORD 'replace-with-a-generated-password';
GRANT CONNECT ON DATABASE railway TO seo_dashboard_ro;
GRANT USAGE ON SCHEMA public TO seo_dashboard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO seo_dashboard_ro;
-- Tables created later by the services (owned by the main role) become readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO seo_dashboard_ro;
