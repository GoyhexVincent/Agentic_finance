# Finance Tracker

Pipeline de collecte, de stockage et de visualisation de données de marché :
cours quotidiens d'ETF sectoriels et d'actions, calcul de corrélations, suivi de
leur évolution dans le temps et mise à disposition dans Grafana.

Le projet reprend l'architecture de [`DVF_tracker`](../DVF_tracker) et
[`NDBC_tracker`](../NDBC_tracker) : source ouverte → ETL Python → PostgreSQL →
Jenkins → Grafana.

> **Statut : v1 fonctionnelle.** Le schéma, l'ETL, les analyses SQL, le
> dashboard Grafana, le Jenkinsfile et les tests sont en place. La base de
> développement locale contient un backfill 2015 → aujourd'hui.

---

## Objectif

1. Récupérer l'historique quotidien des 15 actifs du script R
   `r_dataviz/finance_simplified/correlations_etf.R` :
   11 ETF sectoriels SPDR, `URTH`, `BTC`, `GLD`, `TLT`.
2. Calculer une matrice de corrélation sur les rendements logarithmiques.
3. Mesurer l'évolution de ces corrélations (fenêtres 63 / 126 / 252 séances,
   snapshots mensuels, corrélations annuelles, ruptures de régime).
4. Ajouter quatre actions (`ASML`, `BESI`, `NOVO`, `SMA Solar`) et mesurer leur
   corrélation, leur bêta et leur R² avec chacun de ces ETF.
5. Ajouter trois ETF tech/semi (`SMH`, `SOXX`, `VGT`) comme comparateurs.
6. Publier le tout dans un dashboard Grafana, alimenté quotidiennement par
   Jenkins.

---

## Architecture

```
┌──────────────────────────┐
│ Yahoo Finance            │
│ clôtures ajustées + FX   │
└────────────┬─────────────┘
             │ HTTPS (quotidien)
             ▼
┌──────────────────────────┐
│ ETL Python               │
│ urllib + psycopg2        │
│ COPY bulk, idempotent    │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ PostgreSQL 17            │
│ base   : marine_database │
│ schéma : finance         │
│ rôle   : finance         │
│ vues + fonctions SQL     │
└────────────┬─────────────┘
             │ lecture seule
             ▼
┌──────────────────────────┐
│ Grafana                  │
│ dashboard JSON versionné │
└──────────────────────────┘
             ▲
             │ cron quotidien 06:00
┌──────────────────────────┐
│ Jenkins                  │
└──────────────────────────┘
```

En développement local, la base peut être `finance` et le rôle `oryx`
(authentification peer). Le schéma reste `finance`.

---

## Fonctionnalités

### Données

- 22 actifs : 15 core, 4 actions, 3 ETF tech/semi.
- Prix OHLCV + clôture ajustée (splits/dividendes).
- Taux de change EUR/USD et EUR/DKK pour les rendements en euros.
- Backfill depuis 2015, mise à jour quotidienne.
- Journal d'exécution (`finance.ingestion_runs`), contrôle de fraîcheur.

### Analyses

- Corrélation de Pearson par paires, avec `n_obs` et IC 95 % (Fisher).
- Rendements en EUR (défaut) ou en devise locale (`LOCAL`).
- Fenêtres glissantes 63 / 126 / 252 séances.
- Snapshots mensuels et corrélations annuelles.
- Bêta, R² et corrélation conditionnelle en stress.
- Clustering hiérarchique (average linkage) pour identifier les groupes et ordonner les listes ; la matrice conserve l'ordre de référence pour que lignes et colonnes coïncident.
- Détection de ruptures (`ruptures` Binseg si installé, sinon segmentation
  binaire interne).
- Événements de marché et ruptures annotés dans Grafana.

### Dashboard

17 panneaux : matrice 15 × 15, classement vs URTH, matrice tech 3 × 3,
bêta/R², matrice 4 × 15 actions × ETF, corrélations glissantes, stress,
statistiques, fraîcheur, méthodologie.

---

## Démarrage rapide (développement local)

```bash
cd Finance_tracker
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Adapter : PGDATABASE=finance, PGUSER=oryx, PGPASSWORD=vide,
# PGHOST=/var/run/postgresql pour l'authentification peer.

psql -d finance -f sql/10_schema.sql
python -m finance_tracker.etl
python scripts/verify_freshness.py
```

Le dashboard s'importe ensuite dans Grafana :
`grafana/dashboard_finance.json` (voir [`grafana/grafana_setup.md`](grafana/grafana_setup.md)).

---

## Déploiement Raspberry Pi

### 1. Créer le rôle et le schéma

```bash
psql -U postgres -d postgres \
     -v finance_password='UN_MOT_DE_PASSE_SOLIDE' \
     -v dbname='marine_database' \
     -f sql/00_bootstrap.sql
```

Le script crée le rôle `finance`, le schéma `finance`, révoque les droits
PUBLIC et accorde la lecture à `grafana` si le rôle existe.

### 2. Configurer l'environnement

```dotenv
PGHOST=localhost
PGPORT=5432
PGDATABASE=marine_database
PGUSER=finance
PGPASSWORD=...
FINANCE_SCHEMA=finance
FINANCE_START_DATE=2015-01-01
FINANCE_MIN_IMPORT_INTERVAL_HOURS=18
```

### 3. Lancer le backfill

```bash
. .venv/bin/activate
python -m finance_tracker.etl
```

### 4. Jenkins

- Job **Pipeline script from SCM**, branche `main`, script `Jenkinsfile`.
- Credential **Secret text** `pg-password-finance`.
- Cron `H 6 * * *` (après la clôture US), fuseau `Europe/Paris`.
- Le job lance les tests, l'ETL et le contrôle de fraîcheur.

Voir [`docs/RUNBOOK.md`](docs/RUNBOOK.md) pour le détail.

---

## Structure du dépôt

```
Finance_tracker/
├── README.md
├── Jenkinsfile
├── requirements.txt
├── requirements-optional.txt       # ruptures (PELT/Binseg)
├── .env.example
├── sql/
│   ├── 00_bootstrap.sql            # rôle, base, schéma, droits (superuser)
│   └── 10_schema.sql               # tables, vues, fonctions, événements
├── finance_tracker/
│   ├── config.py                   # variables d'environnement
│   ├── assets.py                   # référentiel des 22 actifs
│   ├── yahoo.py                    # téléchargement + parsing
│   ├── db.py                       # COPY, upserts, journal
│   ├── etl.py                      # orchestration
│   └── analytics.py                # clustering, ruptures
├── scripts/
│   ├── verify_freshness.py
│   └── build_dashboard.py
├── tests/
│   ├── fixtures/
│   ├── test_yahoo.py
│   ├── test_analytics.py
│   └── test_schema_contract.py
├── grafana/
│   ├── dashboard_finance.json
│   ├── grafana_setup.md
│   └── ALERTES.md
└── docs/
    ├── PLAN.md
    ├── METHODOLOGIE.md
    ├── RUNBOOK.md
    └── OPTIMISATIONS.md
```

---

## Modèle de données

| Objet | Rôle |
|---|---|
| `finance.assets` | référentiel : ticker, symbole Yahoo, type, univers, devise, ordre |
| `finance.prices_daily` | OHLCV + clôture ajustée, clé `(asset_id, trade_date)` |
| `finance.fx_daily` | taux `eur_per_unit` par devise |
| `finance.ingestion_runs` | journal des imports |
| `finance.events` | annotations macro |
| `finance.correlation_breaks` | ruptures détectées |
| `finance.v_returns` | rendements locaux et EUR |
| `finance.v_asset_stats` | volatilité, rendement cumulé, drawdown |
| `finance.v_freshness` | fraîcheur par actif |

Fonctions principales :

- `corr_pairs(start, end, basis, min_obs)`
- `corr_benchmark(...)`
- `corr_by_year(...)`
- `rolling_corr(asset, benchmark, window, ...)`
- `rolling_corr_all(...)`
- `corr_monthly(...)`
- `beta_vs_benchmark(asset, benchmark, ...)`
- `stress_corr(benchmark, threshold, ...)`
- `corr_matrix_core(...)`, `corr_matrix_stocks(...)`, `corr_matrix_tech(...)`

Les matrices sont des fonctions SQL à colonnes fixes, car Grafana ne sait pas
pivoter nativement une matrice catégorielle. Voir
[`docs/METHODOLOGIE.md`](docs/METHODOLOGIE.md) et
[`grafana/grafana_setup.md`](grafana/grafana_setup.md).

---

## Analyses et méthode

Le détail des formules, des choix d'alignement et des limites est dans
[`docs/METHODOLOGIE.md`](docs/METHODOLOGIE.md). Points clés :

- rendements logarithmiques `ln(P_t / P_{t-1})` sur clôtures ajustées ;
- conversion EUR par taux « as of », ou devise locale ;
- corrélation par paires sur dates communes, sans forward-fill ;
- IC 95 % de Fisher ;
- fenêtres glissantes sur les N dernières observations communes ;
- bêta = pente de l'action sur l'ETF, R² = part de variance expliquée ;
- stress = corrélation les jours où URTH < -1 % ;
- clustering = average linkage sur `1 - corrélation` ;
- ruptures = Binseg/PELT ou segmentation binaire, à interpréter avec prudence.

---

## Performance et choix d'insertion

- Parsing JSON direct, sans pandas/yfinance.
- Déduplication par date, fuseau de la place.
- `DELETE` ciblé + `COPY` pour les prix, dans une transaction.
- `execute_values` pour les petites tables.
- `ANALYZE` après chargement, verrou consultatif anti-concurrence.
- Pas de TimescaleDB : 65 000 lignes, PostgreSQL standard suffit.

Mesure locale : ~1,2 s pour charger 65 654 prix + 6 141 taux FX
(~59 000 lignes/s), hors téléchargement réseau. Détails et pistes d'évolution
dans [`docs/OPTIMISATIONS.md`](docs/OPTIMISATIONS.md).

---

## Tests

```bash
python -m unittest discover -s tests -v
```

Les tests sont hors-ligne : parsing Yahoo sur fixture, clustering, ruptures,
contrat du schéma. Le contrôle de fraîcheur et les requêtes SQL du dashboard
sont testés manuellement / via Jenkins.

---

## Sécurité

- Rôle `finance` propriétaire du schéma, sans SUPERUSER/CREATEDB/CREATEROLE.
- Rôle `grafana` en lecture seule.
- `REVOKE ALL ON SCHEMA finance FROM PUBLIC`.
- `ALTER DEFAULT PRIVILEGES` pour les futures vues.
- Aucun mot de passe dans le dépôt ; `.env` ignoré.
- Credential Jenkins `pg-password-finance`.

---

## Limites

- Yahoo Finance est une source non officielle, révisable et limitée en débit.
- Les ETF SPDR sont américains : une action européenne peut avoir une
  corrélation faible simplement parce qu'elle n'est pas dans l'indice.
- Corrélation ≠ causalité ; les fenêtres glissantes se chevauchent.
- Les ruptures sont descriptives, pas un test statistique.
- Bitcoin cote 7j/7 ; les dates communes excluent une partie de l'information.

Voir [`docs/METHODOLOGIE.md`](docs/METHODOLOGIE.md) §10 pour la liste complète.

---

## Documents

- [`docs/PLAN.md`](docs/PLAN.md) — plan initial et décisions d'architecture.
- [`docs/METHODOLOGIE.md`](docs/METHODOLOGIE.md) — formules et logique des analyses.
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — installation, exploitation, dépannage.
- [`docs/OPTIMISATIONS.md`](docs/OPTIMISATIONS.md) — parsing, COPY, performance.
- [`grafana/grafana_setup.md`](grafana/grafana_setup.md) — datasource et dashboard.
- [`grafana/ALERTES.md`](grafana/ALERTES.md) — règles d'alerte.

## Licence

À définir.
