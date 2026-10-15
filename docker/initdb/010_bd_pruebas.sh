#!/bin/sh
# Solo se ejecuta la primera vez que se crea el volumen de la base local.
# Crea una base separada para las pruebas automáticas (nunca se usa la base de trabajo).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE DATABASE territorial_pruebas;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname territorial_pruebas -c "CREATE EXTENSION IF NOT EXISTS postgis;"
