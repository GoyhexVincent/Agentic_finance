#!/usr/bin/env bash
# Lance une instance Grafana locale (sans root) pour tester le provisioning.
#
# Prérequis : avoir téléchargé Grafana dans .cache/grafana-<version> et
# disposer d'un rôle PostgreSQL de lecture (voir sql/20_grafana_readonly.sql).
#
#   GF_PG_PASSWORD='...' bash scripts/run_grafana_local.sh
#
# Variables surchargeables : GF_PG_HOST, GF_PG_PORT, GF_PG_DATABASE,
# GF_PG_USER, GF_PG_PASSWORD, GF_ADMIN_PASSWORD, GF_HTTP_PORT.
#
# En production, Grafana est lancé par systemd et lit /etc/grafana/finance.env :
# ce script ne sert qu'au développement.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GRAFANA_HOME="${GRAFANA_HOME:-$ROOT/.cache/grafana-12.2.0}"
STATE="${GF_STATE_DIR:-$ROOT/.cache/gf-finance}"

if [[ ! -x "$GRAFANA_HOME/bin/grafana" ]]; then
    echo "ERREUR : Grafana introuvable dans $GRAFANA_HOME" >&2
    echo "Télécharger l'archive : https://grafana.com/grafana/download" >&2
    exit 1
fi

mkdir -p "$STATE"/{data,logs,plugins}

export GF_PATHS_PROVISIONING="$ROOT/grafana/provisioning"
export GF_PATHS_DATA="$STATE/data"
export GF_PATHS_LOGS="$STATE/logs"
export GF_PATHS_PLUGINS="$STATE/plugins"

export GF_PG_HOST="${GF_PG_HOST:-localhost}"
export GF_PG_PORT="${GF_PG_PORT:-5432}"
export GF_PG_DATABASE="${GF_PG_DATABASE:-finance_db}"
export GF_PG_USER="${GF_PG_USER:-grafana}"
export GF_PG_PASSWORD="${GF_PG_PASSWORD:-}"

export GF_FINANCE_DASHBOARDS_PATH="$ROOT/grafana"
export GF_SERVER_HTTP_ADDR="${GF_HTTP_ADDR:-127.0.0.1}"
export GF_SERVER_HTTP_PORT="${GF_HTTP_PORT:-3000}"
export GF_AUTH_ANONYMOUS_ENABLED=true
export GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
export GF_SECURITY_ADMIN_USER=admin
export GF_SECURITY_ADMIN_PASSWORD="${GF_ADMIN_PASSWORD:-admin}"
export GF_ANALYTICS_REPORTING_ENABLED=false
export GF_ANALYTICS_CHECK_FOR_UPDATES=false
export GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false
export GF_PLUGINS_PREINSTALL_DISABLED=true

exec "$GRAFANA_HOME/bin/grafana" server --homepath "$GRAFANA_HOME"
