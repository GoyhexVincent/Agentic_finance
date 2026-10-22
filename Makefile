DB_NAME ?= finance_db

.PHONY: db-create db-bootstrap grafana-role grafana-install grafana-check \
        etl verify test dashboard

# À exécuter avec un rôle qui a CREATEDB, généralement postgres :
#   sudo -u postgres make db-create
db-create:
	bash scripts/create_database.sh $(DB_NAME)

# À exécuter avec un rôle superutilisateur. FINANCE_PASSWORD doit être défini.
#   sudo -u postgres env FINANCE_PASSWORD='...' make db-bootstrap
db-bootstrap:
	@test -n "$(FINANCE_PASSWORD)" || (echo "ERREUR : FINANCE_PASSWORD non défini" >&2; exit 1)
	psql -U postgres -d postgres \
		-v finance_password="$(FINANCE_PASSWORD)" \
		-v dbname=$(DB_NAME) \
		-f sql/00_bootstrap.sql

# Crée le rôle grafana en lecture seule (demande le mot de passe).
grafana-role:
	bash scripts/bootstrap_grafana.sh $(DB_NAME)

# Copie le provisioning et redémarre Grafana (nécessite sudo).
grafana-install:
	sudo bash scripts/install_grafana_provisioning.sh

# Vérifie que Grafana et la datasource répondent. ADMIN_PASSWORD=... si besoin.
grafana-check:
	curl -fsS http://127.0.0.1:3000/api/health
	@echo
	curl -fsS -u admin:$(ADMIN_PASSWORD) \
		http://127.0.0.1:3000/api/datasources/uid/finance-postgresql/health
	@echo

etl:
	python -m finance_tracker.etl

verify:
	python scripts/verify_freshness.py

test:
	python -m pytest -q tests

dashboard:
	python scripts/build_dashboard.py
