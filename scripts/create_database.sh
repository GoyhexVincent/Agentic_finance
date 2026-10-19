#!/usr/bin/env bash
# Crée la base dédiée du Finance Tracker si elle n'existe pas.
#
# En production (rôle postgres) :
#   sudo -u postgres bash scripts/create_database.sh finance_db
#
# En développement local, si ton rôle possède CREATEDB :
#   bash scripts/create_database.sh finance_db "$USER"
#
# Le second argument est le propriétaire de la base (défaut : postgres).
# Le script est idempotent : si la base existe déjà, il ne fait rien.

set -euo pipefail

DB_NAME="${1:-finance_db}"
DB_OWNER="${2:-postgres}"
ADMIN_USER="${PGUSER_ADMIN:-postgres}"

if ! [[ "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]]; then
    echo "ERREUR : nom de base invalide : $DB_NAME" >&2
    echo "         utiliser [a-z_][a-z0-9_]* (ex. finance_db)" >&2
    exit 1
fi

if ! command -v psql >/dev/null 2>&1 || ! command -v createdb >/dev/null 2>&1; then
    echo "ERREUR : psql/createdb introuvables. Installer postgresql-client." >&2
    exit 1
fi

exists="$(psql -U "$ADMIN_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")"

if [[ "$exists" == "1" ]]; then
    echo "La base '$DB_NAME' existe déjà — rien à faire."
    exit 0
fi

createdb -U "$ADMIN_USER" -O "$DB_OWNER" "$DB_NAME"
echo "Base '$DB_NAME' créée (propriétaire : $DB_OWNER)."
