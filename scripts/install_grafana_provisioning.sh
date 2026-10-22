#!/usr/bin/env bash
# Installe le provisioning Grafana du Finance Tracker sur une machine Debian
# ou Raspberry Pi OS où Grafana est déjà installé (paquet grafana).
#
#   sudo bash scripts/install_grafana_provisioning.sh
#
# Le script :
#   1. copie la datasource et le provider de dashboards dans /etc/grafana ;
#   2. copie dashboard_finance.json dans /var/lib/grafana/dashboards ;
#   3. crée /etc/grafana/finance.env à partir de l'exemple s'il manque ;
#   4. ajoute un drop-in systemd qui charge ce fichier ;
#   5. redémarre Grafana si le mot de passe a été renseigné.
#
# Idempotent : peut être relancé après une mise à jour du dashboard.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GRAFANA_ETC="${GRAFANA_ETC:-/etc/grafana}"
DASHBOARDS_DIR="${DASHBOARDS_DIR:-/var/lib/grafana/dashboards}"
ENV_FILE="$GRAFANA_ETC/finance.env"
DROPIN_DIR="/etc/systemd/system/grafana-server.service.d"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERREUR : ce script doit être lancé avec sudo." >&2
    exit 1
fi

if [[ ! -d "$GRAFANA_ETC" ]]; then
    echo "ERREUR : $GRAFANA_ETC introuvable. Grafana est-il installé ?" >&2
    echo "  sudo apt install grafana" >&2
    exit 1
fi

if ! systemctl list-unit-files grafana-server.service >/dev/null 2>&1; then
    echo "ERREUR : service grafana-server introuvable." >&2
    exit 1
fi

install -d -m 755 \
    "$GRAFANA_ETC/provisioning/datasources" \
    "$GRAFANA_ETC/provisioning/dashboards" \
    "$DASHBOARDS_DIR"

install -m 644 "$ROOT/grafana/provisioning/datasources/finance-postgres.yaml" \
    "$GRAFANA_ETC/provisioning/datasources/finance-postgres.yaml"
install -m 644 "$ROOT/grafana/provisioning/dashboards/finance.yaml" \
    "$GRAFANA_ETC/provisioning/dashboards/finance.yaml"
install -m 644 "$ROOT/grafana/dashboard_finance.json" \
    "$DASHBOARDS_DIR/dashboard_finance.json"

if [[ ! -f "$ENV_FILE" ]]; then
    install -m 640 "$ROOT/grafana/finance.env.example" "$ENV_FILE"
    if getent group grafana >/dev/null 2>&1; then
        chown root:grafana "$ENV_FILE"
    fi
    echo "Fichier créé : $ENV_FILE"
    echo "Le renseigner (mot de passe du rôle grafana), puis relancer ce script."
fi

install -d -m 755 "$DROPIN_DIR"
cat > "$DROPIN_DIR/finance-env.conf" <<EOF
# Généré par scripts/install_grafana_provisioning.sh
[Service]
EnvironmentFile=-$ENV_FILE
EOF

systemctl daemon-reload
systemctl enable grafana-server >/dev/null 2>&1 || true

if grep -q "change_me" "$ENV_FILE"; then
    echo
    echo "Mot de passe encore à 'change_me' : Grafana n'a pas été redémarré."
    echo "Éditer $ENV_FILE puis relancer ce script."
    exit 0
fi

systemctl restart grafana-server

echo
echo "Attente du démarrage de Grafana…"
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:3000/api/health >/dev/null 2>&1; then
        echo "Grafana répond."
        echo "Datasource : Finance-PostgreSQL (uid finance-postgresql)"
        echo "Dashboard  : /d/finance-correlation"
        exit 0
    fi
    sleep 1
done

echo "ERREUR : Grafana ne répond pas sur http://127.0.0.1:3000." >&2
echo "Consulter : journalctl -u grafana-server -n 100 --no-pager" >&2
exit 1
