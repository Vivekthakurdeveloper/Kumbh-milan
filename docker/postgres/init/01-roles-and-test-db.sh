#!/bin/sh
# Runs once, on first initialisation of the Postgres volume.
# The owner role (POSTGRES_USER) runs migrations; the app role only receives grants from the migration.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE ROLE "${KUMBH_APP_DB_USER}" LOGIN PASSWORD '${KUMBH_APP_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO "${KUMBH_APP_DB_USER}";
  GRANT USAGE ON SCHEMA public TO "${KUMBH_APP_DB_USER}";
  CREATE DATABASE kumbh_setu_test OWNER "${POSTGRES_USER}";
SQL
