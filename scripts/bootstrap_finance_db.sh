#!/usr/bin/env bash
# Bootstrap idempotent de la base Finance Tracker sur le Raspberry Pi.
#
# Utilisation, depuis la racine du dépôt :
#   bash scripts/bootstrap_finance_db.sh
#
# Le script :
#   1. vérifie l'accès à PostgreSQL en tant que postgres ;
#   2. crée finance_db si elle n'existe pas ;
#   3. demande le mot de passe du rôle finance ;
#   4. exécute sql/00_bootstrap.sql (rôle, schéma, droits Grafana).
#
# Aucun mot de passe n'est stocké sur disque.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_FILE="$ROOT/sql/00_bootstrap.sql"
DB_NAME="${1:-finance_db}"

if [[ ! "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]]; then
    echo "ERREUR : nom de base invalide : $DB_NAME" >&2
    exit 1
fi

if [[ ! -f "$SQL_FILE" ]]; then
    echo "ERREUR : fichier introuvable : $SQL_FILE" >&2
    exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
    echo "ERREUR : psql introuvable. Installer postgresql-client." >&2
    exit 1
fi

# Test explicite avec -U postgres : cela évite qu'un PGUSER résiduel
# (parfois une chaîne vide ou un espace) prenne le dessus.
if ! sudo -u postgres psql -U postgres -d postgres -c "SELECT 1" >/dev/null 2>&1; then
    echo "ERREUR : impossible de se connecter en tant que postgres." >&2
    echo "Tester : sudo -u postgres psql -U postgres -d postgres -c 'SELECT current_user;'" >&2
    exit 1
fi

exists="$(sudo -u postgres psql -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")"

if [[ "$exists" != "1" ]]; then
    echo "Création de la base '$DB_NAME'…"
    sudo -u postgres psql -U postgres -d postgres \
        -c "CREATE DATABASE \"$DB_NAME\" OWNER postgres;"
else
    echo "La base '$DB_NAME' existe déjà."
fi

read -rsp "Mot de passe du rôle finance : " FINANCE_PASSWORD; echo
if [[ -z "$FINANCE_PASSWORD" ]]; then
    echo "ERREUR : mot de passe vide." >&2
    exit 1
fi

trap 'unset FINANCE_PASSWORD' EXIT

sudo -u postgres psql -U postgres -d postgres \
    -v finance_password="$FINANCE_PASSWORD" \
    -v dbname="$DB_NAME" \
    < "$SQL_FILE"

echo
echo "Bootstrap terminé."
echo "Vérification :"
sudo -u postgres psql -U postgres -d "$DB_NAME" -c \
    "SELECT nspname, pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname = 'finance';"
