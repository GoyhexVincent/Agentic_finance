#!/usr/bin/env bash
# Crée (ou répare) le rôle PostgreSQL « grafana » en lecture seule.
#
# Utilisation, depuis la racine du dépôt, avec un accès sudo à postgres :
#   bash scripts/bootstrap_grafana.sh
#   bash scripts/bootstrap_grafana.sh finance_db
#
# Le script demande le mot de passe du rôle grafana, puis exécute
# sql/20_grafana_readonly.sql. Aucun mot de passe n'est écrit sur disque.
#
# À relancer après un changement de mot de passe ou pour réparer des droits.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_FILE="$ROOT/sql/20_grafana_readonly.sql"
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

if ! sudo -u postgres psql -U postgres -d postgres -c "SELECT 1" >/dev/null 2>&1; then
    echo "ERREUR : impossible de se connecter en tant que postgres." >&2
    echo "Tester : sudo -u postgres psql -U postgres -d postgres -c 'SELECT current_user;'" >&2
    exit 1
fi

exists="$(sudo -u postgres psql -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")"
if [[ "$exists" != "1" ]]; then
    echo "ERREUR : la base '$DB_NAME' n'existe pas." >&2
    echo "Lancer d'abord : bash scripts/bootstrap_finance_db.sh $DB_NAME" >&2
    exit 1
fi

read -rsp "Mot de passe du rôle grafana : " GRAFANA_PASSWORD; echo
read -rsp "Confirmer le mot de passe : " GRAFANA_PASSWORD_CONFIRM; echo

if [[ -z "$GRAFANA_PASSWORD" ]]; then
    echo "ERREUR : mot de passe vide." >&2
    exit 1
fi

if [[ "$GRAFANA_PASSWORD" != "$GRAFANA_PASSWORD_CONFIRM" ]]; then
    echo "ERREUR : les mots de passe ne correspondent pas." >&2
    unset GRAFANA_PASSWORD GRAFANA_PASSWORD_CONFIRM
    exit 1
fi

unset GRAFANA_PASSWORD_CONFIRM
trap 'unset GRAFANA_PASSWORD' EXIT

sudo -u postgres psql -U postgres -d postgres \
    -v grafana_password="$GRAFANA_PASSWORD" \
    -v dbname="$DB_NAME" \
    < "$SQL_FILE"

echo
echo "Rôle grafana prêt sur la base '$DB_NAME'."
echo "Renseigner GF_PG_PASSWORD dans /etc/grafana/finance.env, puis :"
echo "  sudo systemctl restart grafana-server"
