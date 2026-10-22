# Runbook — installation, exploitation, dépannage

Ce document décrit les opérations courantes : installation locale, déploiement
sur le Raspberry Pi, exécution manuelle, import Grafana, ajout d'actif et
résolution des erreurs fréquentes.

---

## 1. Architecture des environnements

| Environnement | Base | Rôle | Schéma | Grafana/Jenkins |
|---|---|---|---|---|
| Développement local | `finance_db` | `oryx` (peer) | `finance` | non installés |
| Raspberry Pi / production | `finance_db` | `finance` | `finance` | Jenkins + Grafana |

Le schéma est **toujours** `finance`. Seuls la base, l'utilisateur et le mot de
passe changent. Le fichier `.env` local permet de développer sans toucher à la
production.

---

## 2. Installation locale

```bash
cd Finance_tracker
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Créer la base locale, possédée par le rôle de développement
sudo -u postgres bash scripts/create_database.sh finance_db "$USER"

cp .env.example .env
# Éditer .env : PGDATABASE=finance_db, PGUSER=oryx, PGPASSWORD= (vide)
# et PGHOST=/var/run/postgresql pour l'authentification peer locale.
```

Appliquer le schéma :

```bash
psql -d finance_db -f sql/10_schema.sql
```

Lancer un premier import :

```bash
python -m finance_tracker.etl --log-level INFO
```

Vérifier :

```bash
python scripts/verify_freshness.py
```

---

## 3. Déploiement sur le Raspberry Pi

### 3.1 Créer la base `finance_db`

Commande idempotente fournie :

```bash
sudo -u postgres bash scripts/create_database.sh finance_db
# ou
sudo -u postgres createdb -O postgres finance_db
# ou
sudo -u postgres make db-create
```

### 3.2 Créer le rôle et le schéma

Le plus simple, depuis la racine du dépôt :

```bash
bash scripts/bootstrap_finance_db.sh
```

Le script crée la base si besoin, demande le mot de passe `finance` et exécute
`sql/00_bootstrap.sql`. Il force `-U postgres` pour éviter les conflits avec un
`PGUSER` résiduel.

Équivalent manuel, en une seule ligne `psql` :

```bash
read -rsp 'Mot de passe finance : ' FINANCE_PASSWORD; echo
sudo -u postgres psql -U postgres -d postgres -v finance_password="$FINANCE_PASSWORD" -v dbname=finance_db < sql/00_bootstrap.sql
unset FINANCE_PASSWORD
```

Le script :

- crée le rôle `finance` s'il n'existe pas ;
- met à jour son mot de passe ;
- crée le schéma `finance` et lui en donne la propriété ;
- révoque les droits PUBLIC ;
- accorde la lecture au rôle `grafana` s'il existe, y compris pour les futurs
  objets.

> Le mot de passe doit être stocké dans le credential Jenkins
> `pg-password-finance`, jamais dans le dépôt. Le script ne le contient pas.

### 3.3 Appliquer le schéma

```bash
PGPASSWORD='...' psql -U finance -d finance_db -f sql/10_schema.sql
```

Ou simplement lancer l'ETL : il rejoue le schéma à chaque exécution.

### 3.4 Configurer l'environnement

Créer `.env` (ou utiliser les variables Jenkins) :

```dotenv
PGHOST=localhost
PGPORT=5432
PGDATABASE=finance_db
PGUSER=finance
PGPASSWORD=...
FINANCE_SCHEMA=finance
FINANCE_START_DATE=2015-01-01
FINANCE_MIN_OBS=60
FINANCE_MIN_IMPORT_INTERVAL_HOURS=18
```

### 3.5 Premier backfill

```bash
. .venv/bin/activate
# Optionnel : détection de ruptures via PELT/Binseg (numpy/scipy)
# pip install -r requirements-optional.txt
python -m finance_tracker.etl
```

Compter ~10–20 secondes de téléchargement et quelques secondes d'écriture sur
un Pi. Le backfill peut être relancé avec `--force` pour ignorer le garde-fou.

### 3.6 Job Jenkins

1. Créer un job **Pipeline script from SCM**.
2. SCM : dépôt `Finance_tracker`, branche `main`, script `Jenkinsfile`.
3. Créer le credential **Secret text** `pg-password-finance`.
4. Lancer un premier build manuel : Jenkins n'applique le `triggers` du
   Jenkinsfile qu'à partir du build suivant.
5. Vérifier que le cron est bien `H 6 * * *` et que Jenkins est en
   `Europe/Paris`.

### 3.7 Grafana

Une fois les données chargées, créer le rôle de lecture puis installer le
provisioning :

```bash
bash scripts/bootstrap_grafana.sh finance_db
sudo install -m 640 -o root -g grafana \
  grafana/finance.env.example /etc/grafana/finance.env
sudo editor /etc/grafana/finance.env
sudo bash scripts/install_grafana_provisioning.sh
```

Voir §5 et [`grafana/grafana_setup.md`](../grafana/grafana_setup.md).

---

## 4. Commandes utiles

```bash
# Import complet
python -m finance_tracker.etl

# Import d'un sous-ensemble, sans écrire en base
python -m finance_tracker.etl --assets URTH,XLK,ASML --dry-run

# Forcer malgré le garde-fou de 18 h
python -m finance_tracker.etl --force

# Recalculer uniquement clustering + ruptures
python -m finance_tracker.analytics

# Contrôle de fraîcheur
python scripts/verify_freshness.py

# Tests hors-ligne
python -m pytest -q tests

# Régénérer le dashboard Grafana
python scripts/build_dashboard.py
```

---

## 5. Déployer Grafana

Le détail est dans [`grafana/grafana_setup.md`](../grafana/grafana_setup.md).
Résumé pour le Pi :

```bash
# 1. Rôle PostgreSQL en lecture seule (demande le mot de passe)
bash scripts/bootstrap_grafana.sh finance_db

# 2. Variables du service Grafana
sudo install -m 640 -o root -g grafana \
  grafana/finance.env.example /etc/grafana/finance.env
sudo editor /etc/grafana/finance.env   # GF_PG_PASSWORD, GF_PG_DATABASE…

# 3. Datasource + dashboard provisionnés
sudo bash scripts/install_grafana_provisioning.sh
```

La datasource `Finance-PostgreSQL` (uid `finance-postgresql`) et le dashboard
sont alors créés automatiquement. Vérifier :

```bash
curl -u admin:... \
  http://127.0.0.1:3000/api/datasources/uid/finance-postgresql/health
# {"message":"Database Connection OK","status":"OK"}
```

Puis ouvrir <http://127.0.0.1:3000/d/finance-correlation>.

Si les variables `stock`/`etf` sont vides, le schéma n'est pas encore chargé ou
le rôle n'a pas les droits :

```sql
GRANT USAGE ON SCHEMA finance TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA finance TO grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
  GRANT SELECT ON TABLES TO grafana;
```

Rejouer `sql/20_grafana_readonly.sql` est plus sûr : il pose aussi
`EXECUTE` sur les fonctions et révoque les droits d'écriture.

> Après un changement de mot de passe, incrémenter `version` dans
> `grafana/provisioning/datasources/finance-postgres.yaml` : Grafana ne
> réapplique un datasource provisionné que si ce numéro augmente.

---

## 6. Alertes

Voir [`grafana/ALERTES.md`](../grafana/ALERTES.md). En résumé :

- **données de marché anciennes** : `URTH` > 5 jours ;
- **pipeline non exécuté** : dernier import réussi > 36 h ;
- **changement de régime** : variation de corrélation glissante > seuil.

Le contrôle de fraîcheur Jenkins couvre déjà les deux premières ; les alertes
Grafana ajoutent la notification et la surveillance du régime.

---

## 7. Ajouter un actif

1. Ajouter une entrée `Asset(...)` dans `finance_tracker/assets.py`.
2. Choisir l'univers :
   - `core` : il apparaîtra dans la matrice 15 × 15 **et** dans les colonnes ;
     il faut alors ajouter la colonne dans `corr_matrix_core` et
     `corr_matrix_stocks` (SQL), puis régénérer le dashboard.
   - `stock` : il apparaîtra dans les lignes de la matrice actions.
   - `tech` : il apparaîtra dans la matrice tech.
3. Relancer `python -m finance_tracker.etl --force`.
4. Vérifier `finance.assets` et `finance.prices_daily`.
5. Si l'actif est `core`, mettre à jour les fonctions SQL larges et
   `scripts/build_dashboard.py`, puis régénérer le JSON.

Les variables Grafana `stock` et `etf` sont des requêtes : un nouvel actif est
automatiquement proposé, sans modifier le JSON, s'il est dans le bon univers.

---

## 8. Dépannage

### `permission denied for schema finance`

Le rôle `finance` n'est pas propriétaire, ou `grafana` n'a pas `USAGE`.
Rejouer `sql/00_bootstrap.sql` en `postgres`, puis :

```sql
GRANT USAGE ON SCHEMA finance TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA finance TO grafana;
```

### `permission denied for table prices_daily`

Les privilèges par défaut n'ont pas été posés pour les objets créés après le
`GRANT`. Rejouer le bloc Grafana de `sql/10_schema.sql` ou :

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
  GRANT SELECT ON TABLES TO grafana;
```

### `connection ... no password supplied`

En local, utiliser le socket (`PGHOST=/var/run/postgresql`) ou un `.pgpass`.
En production, vérifier `PGPASSWORD` / le credential Jenkins.

### Grafana garde l'ancien hôte ou l'ancien mot de passe

Un datasource provisionné n'est réappliqué que si son champ `version` augmente.
Incrémenter `version` dans
`grafana/provisioning/datasources/finance-postgres.yaml`, relancer
`scripts/install_grafana_provisioning.sh`, puis redémarrer Grafana.

### `dial unix .../.s.PGSQL.5432: no such file or directory`

Grafana construit une URL `hôte:port` : un chemin de socket Unix
(`/var/run/postgresql`) est pris pour un hôte littéral. Utiliser TCP
(`GF_PG_HOST=localhost`) et un rôle avec mot de passe.

### Le dashboard n'apparaît pas dans Grafana

Vérifier que `GF_FINANCE_DASHBOARDS_PATH` pointe vers le dossier contenant
`dashboard_finance.json`, puis :

```bash
journalctl -u grafana-server -n 50 --no-pager | grep -i provision
```

### `Symbole introuvable sur Yahoo (404)`

Le symbole a changé ou n'existe plus. Vérifier dans `assets.py`, tester :

```bash
curl -s "https://query1.finance.yahoo.com/v8/finance/chart/ASML.AS?range=5d&interval=1d"
```

### `HTTP 429`

Yahoo limite le débit. Augmenter `FINANCE_HTTP_DELAY` (0.8–1.5 s), relancer plus
tard. Ne pas paralléliser.

### `Aucune paire de corrélation`

Vérifier que `finance.v_returns` contient des lignes et que `FX` est présent :

```sql
SELECT count(*) FROM finance.v_returns;
SELECT * FROM finance.v_freshness;
SELECT * FROM finance.fx_daily ORDER BY trade_date DESC LIMIT 5;
```

### Le dashboard est vide

- La datasource pointe-t-elle vers la bonne base ?
- Le rôle `grafana` a-t-il `USAGE` + `SELECT` ?
- La période Grafana couvre-t-elle les données ?
- Les variables `stock`/`etf` sont-elles sélectionnées ?

### Les corrélations semblent fausses

- Vérifier `basis` (`EUR` vs `LOCAL`).
- Vérifier que le backfill couvre la période.
- Comparer avec le script R sur la même période et en `LOCAL`.
- Vérifier les trous de données par actif (`v_freshness`).

### Le job Jenkins est `UNSTABLE`

Un actif secondaire a échoué : les autres sont à jour. Consulter
`finance.ingestion_runs.message` et les logs. Relancer avec `FORCE_IMPORT`.

---

## 9. Sauvegarde et restauration

Les données sont petites. Une sauvegarde logique suffit :

```bash
pg_dump -U finance -d finance_db -n finance -Fc \
  -f finance_$(date +%F).dump
```

Restauration :

```bash
pg_restore -U finance -d finance_db -n finance finance_2026-10-19.dump
```

Les données pouvant être retéléchargées, la sauvegarde est surtout utile pour
conserver l'historique exact et les annotations.

---

## 10. Sécurité

- Aucun mot de passe dans le dépôt ; `.env` est ignoré.
- Le rôle `finance` n'est ni superutilisateur, ni `CREATEDB`, ni `CREATEROLE`.
- Le rôle `grafana` est en lecture seule.
- Le schéma `finance` est révoqué pour `PUBLIC`.
- Jenkins stocke le mot de passe dans un credential `Secret text`.
- Les logs ne doivent pas contenir `PGPASSWORD`.
