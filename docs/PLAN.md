# Finance Tracker — plan d'architecture et de mise en œuvre

> **Statut : plan initial réalisé en v1.**
> Ce document conserve les décisions d'architecture et le raisonnement initial.
> L'implémentation (schéma, ETL, analyses, dashboard, Jenkins, tests) est
> documentée dans le [`README`](../README.md), la
> [`METHODOLOGIE`](METHODOLOGIE.md) et le [`RUNBOOK`](RUNBOOK.md).

---

## 1. Objectif

Construire un tracker de marché quotidien centré sur la **corrélation** :

- récupérer l'historique de 15 actifs (11 ETF sectoriels US, `URTH`, `BTC`,
  `GLD`, `TLT`) ;
- calculer une matrice de corrélation sur les rendements logarithmiques ;
- montrer comment ces corrélations évoluent dans le temps ;
- mesurer la corrélation de quatre actions (`ASML`, `BESI`, `NOVO`,
  `SMA Solar`) avec chacun de ces ETF ;
- automatiser la mise à jour quotidienne et exposer le tout dans Grafana.

Ce périmètre correspond à la migration progressive de
`r_dataviz/finance_simplified/correlations_etf.R` et de
`technologies_sousjacent_correlations.R` vers un pipeline PostgreSQL/Grafana.

---

## 2. Constats sur l'existant

### 2.1 Scripts R

`correlations_etf.R` :

- 15 actifs : `XLK`, `XLV`, `XLF`, `XLY`, `XLC`, `XLI`, `XLP`, `XLE`, `XLU`,
  `XLRE`, `XLB`, `URTH`, `BTC`, `GLD`, `TLT` ;
- téléchargement via `yfhist::get_data()` depuis 2020-01-01 ;
- rendements `diff(log(adjclose))` ;
- corrélation de Pearson sur toute la période ;
- corrélations annuelles avec `URTH` ;
- corrélations glissantes sur 252 séances avec `URTH` ;
- détection de ruptures de régime avec `strucchange`.

`technologies_sousjacent_correlations.R` :

- ajoute `VGT`, `SMH`, `SOXX` et plusieurs actions du secteur hardware
  (`ASML`, `LRCX`, `AMAT`, `KLAC`, `WDC`, `SNDK`) ;
- calcule une matrice de corrélation et des performances cumulées.

Le second script est important : les quatre actions demandées ne sont pas
toutes des actions « sectorielles US ». `ASML` et `BESI` sont des
semi-conducteurs, `NOVO` est une société de santé danoise, `SMA Solar` est un
équipementier solaire allemand. Les comparer uniquement aux 11 ETF SPDR US
donnera des corrélations utiles, mais parfois difficiles à interpréter. D'où la
suggestion d'ajouter `SMH`, `SOXX` et `VGT` dans une matrice complémentaire.

### 2.2 Architecture des projets voisins

`DVF_tracker` et `NDBC_tracker` partagent le même schéma d'architecture :

```
source ouverte → Python/psycopg2 → PostgreSQL (+ TimescaleDB)
                                   → Jenkins (cron)
                                   → Grafana (dashboard JSON)
```

Ils utilisent :

- un schéma PostgreSQL dédié (`dvf`, ou `public` pour NDBC) ;
- un script d'import idempotent qui rejoue le schéma ;
- un contrôle de fraîcheur qui fait échouer le build Jenkins si les données
  sont trop anciennes ;
- un dashboard Grafana versionné au format JSON ;
- des identifiants Jenkins stockés dans un credential `Secret text`.

Nous reprenons ces principes, avec deux différences assumées :

1. **Pas de TimescaleDB nécessaire** : 19 actifs × ~2 500 séances ≈ 50 000
   lignes. Une table PostgreSQL classique avec un index `(asset_id, trade_date)`
   suffit largement. TimescaleDB reste possible plus tard, sans changer le
   modèle logique.
2. **Un rôle propriétaire distinct** : `finance` possède le schéma et les
   objets ; `grafana` ne reçoit que le droit de lecture.

### 2.3 État de la machine de travail

Vérifications effectuées sur la machine où se trouve ce dépôt :

- PostgreSQL 17 est actif ;
- l'ancienne base de test locale `finance` existe déjà, mais elle est vide (schéma `public`
  uniquement) et appartient à `postgres` ; elle a servi de base de test locale,
  tandis que la cible du projet est la base dédiée `finance_db` ;
- le rôle `finance` n'existe pas ;
- le rôle `grafana` n'existe pas ;
- Jenkins et Grafana ne sont pas installés/actifs localement ;
- l'API Yahoo Finance est accessible depuis cette machine ;
- les projets DVF/NDBC pointent vers `marine_database`, absente de cette
  machine ; le nouveau projet utilisera au contraire la base dédiée
  `finance_db`, à créer sur le Pi.

Conséquence : les scripts seront **entièrement paramétrables par variables
d'environnement**. La création du rôle `finance` nécessitera un accès
superutilisateur (`postgres`) ; un script `sql/00_bootstrap.sql` sera fourni et
devra être lancé une fois par l'administrateur de la base.

### 2.4 Résolution des symboles

| Affichage demandé | Symbole Yahoo retenu | Place | Devise | Remarque |
|---|---|---|---|---|
| `ASML` | `ASML.AS` | Euronext Amsterdam | EUR | ligne européenne, plutôt que l'ADR `ASML` |
| `BESI.B` | `BESI.AS` | Euronext Amsterdam | EUR | BE Semiconductor Industries |
| `NOVO.B` | `NOVO-B.CO` | Copenhague | DKK | action de catégorie B |
| `SMA92` | `S92.DE` | Xetra | EUR | SMA Solar Technology AG |

`SMA92` n'est pas un symbole Yahoo Finance. La correspondance la plus plausible
est SMA Solar Technology AG, cotée sous `S92.DE` (Xetra) ; elle doit être
confirmée. Voir par exemple la fiche
[ SMA Solar Technology AG (S92) ](https://www.boursedirect.fr/en/market/xetra/sma-solar-technology-ag-DE000A0DJ6J9-S92-EUR-XETR/seance).

Les 15 actifs du script R :

| Ticker | Symbole Yahoo | Type | Catégorie |
|---|---|---|---|
| `XLK` | `XLK` | ETF | Technologie US |
| `XLV` | `XLV` | ETF | Santé US |
| `XLF` | `XLF` | ETF | Finance US |
| `XLY` | `XLY` | ETF | Consommation discrétionnaire US |
| `XLC` | `XLC` | ETF | Communication US |
| `XLI` | `XLI` | ETF | Industrie US |
| `XLP` | `XLP` | ETF | Consommation de base US |
| `XLE` | `XLE` | ETF | Énergie US |
| `XLU` | `XLU` | ETF | Services publics US |
| `XLRE` | `XLRE` | ETF | Immobilier US |
| `XLB` | `XLB` | ETF | Matériaux US |
| `URTH` | `URTH` | ETF | MSCI World (benchmark) |
| `BTC` | `BTC-USD` | Crypto | Bitcoin |
| `GLD` | `GLD` | ETF | Or |
| `TLT` | `TLT` | ETF | Obligations US 20 ans+ |

`BTC`, `GLD` et `TLT` ne sont pas des ETF sectoriels : ils sont conservés dans
la matrice comme actifs de diversification / refuge, avec une catégorie
distincte.

---

## 3. Architecture cible

```
┌──────────────────────────┐
│ Yahoo Finance            │
│ cours ajustés + FX       │
└────────────┬─────────────┘
             │ HTTPS (quotidien)
             ▼
┌──────────────────────────┐
│ ETL Python               │
│ - téléchargement         │
│ - validation             │
│ - upsert idempotent      │
│ - journal d'exécution    │
└────────────┬─────────────┘
             │ psycopg2
             ▼
┌──────────────────────────┐
│ PostgreSQL 17            │
│ base   : finance         │
│ schéma : finance         │
│ rôle   : finance         │
│ - prix / FX / actifs     │
│ - vues de rendements     │
│ - fonctions de corrélation│
└────────────┬─────────────┘
             │ lecture seule
             ▼
┌──────────────────────────┐
│ Grafana                  │
│ dashboard JSON versionné │
└──────────────────────────┘
             ▲
             │ cron quotidien
┌──────────────────────────┐
│ Jenkins                  │
│ 06:00 Europe/Paris       │
└──────────────────────────┘
```

Le job Jenkins tourne après la clôture américaine (22:00 heure de Paris en
hiver, 23:00 en été). Un passage à 06:00 le lendemain garantit que les cours de
clôture de la veille sont disponibles et consolidés.

---

## 4. Périmètre de données

### 4.1 Fréquence et profondeur

- **Fréquence** : quotidienne, clôture ajustée.
- **Historique** : téléchargement à partir de `2015-01-01` par défaut.
- **Fenêtre par défaut du dashboard** : 5 ans, réglable par le sélecteur de
  temps Grafana.
- **Matrice principale** : à partir de `2018-07-01`, date à laquelle `XLC` et
  `XLRE` disposent d'un historique complet. La fonction de corrélation accepte
  néanmoins n'importe quelle fenêtre ; les paires sans assez d'observations
  sont marquées `n_obs` insuffisant.

Pourquoi 2015 et pas 2020 ? Le script R commence en 2020, ce qui inclut le choc
COVID et la reprise, mais masque la période 2015-2019. Pour analyser des
régimes de corrélation, il est préférable de disposer d'un historique plus
long. Le choix reste configurable.

### 4.2 Devise de calcul

Point important : les ETF sont cotés en USD, `ASML`/`BESI`/`SMA` en EUR,
`NOVO` en DKK. Corréler des rendements en devises locales mélange l'effet
action et l'effet de change.

Deux bases de calcul seront stockées :

- `ret_local` : rendement dans la devise de cotation ;
- `ret_eur` : rendement converti en EUR, donc représentatif d'un investisseur
  européen.

Le dashboard aura une variable `basis` (`EUR` par défaut, `LOCAL` en option).
Le calcul EUR utilise les taux de change quotidien `EURUSD=X` et `EURDKK=X`,
avec report du dernier taux connu les jours fériés.

### 4.3 Alignement des calendriers

Les marchés US, néerlandais, danois et allemand n'ont pas les mêmes jours
fériés ; Bitcoin cote 7j/7.

Deux stratégies sont utilisées, volontairement :

- **Matrice de corrélation** : jointure interne sur les dates communes aux deux
  actifs, puis `corr()` sur les observations appariées. C'est la méthode
  statistiquement la plus propre : pas de rendement nul artificiel.
- **Corrélations glissantes** : pour chaque actif, on prend les dernières
  `N` observations **communes** avec le benchmark (ou avec l'ETF choisi), puis
  on calcule la corrélation sur cette fenêtre. On évite ainsi de forward-filler
  des prix et de créer des zéros artificiels.

Cette nuance sera documentée dans `docs/METHODOLOGIE.md`.

---

## 5. Modèle PostgreSQL

Base : `finance` (existante).
Schéma : `finance`.
Propriétaire : rôle `finance`.
Lecture : rôle `grafana`.

### 5.1 Tables

#### `finance.assets`

Référentiel des actifs suivis.

| Colonne | Type | Rôle |
|---|---|---|
| `asset_id` | `smallint identity` | clé technique stable |
| `ticker` | `text unique` | libellé court (`XLK`, `ASML`, `SMA92`…) |
| `yahoo_symbol` | `text unique` | symbole source (`ASML.AS`, `S92.DE`…) |
| `name` | `text` | nom lisible |
| `asset_type` | `text` | `etf`, `equity`, `crypto` |
| `category` | `text` | `sector`, `broad_market`, `commodity`, `bonds`, `crypto`, `semiconductor`, `health`, `solar`… |
| `currency` | `char(3)` | devise de cotation |
| `exchange` | `text` | place de marché |
| `is_benchmark` | `boolean` | `true` pour `URTH` |
| `inception_date` | `date` | première date disponible |
| `metadata` | `jsonb` | secteur, région, émetteur, notes |

#### `finance.prices_daily`

| Colonne | Type | Rôle |
|---|---|---|
| `asset_id` | `smallint` | FK vers `assets` |
| `trade_date` | `date` | date de séance |
| `open`, `high`, `low`, `close` | `numeric` | prix bruts |
| `adj_close` | `numeric` | clôture ajustée splits/dividendes |
| `volume` | `bigint` | volume |
| `currency` | `char(3)` | devise du prix |
| `source` | `text` | `yahoo` |
| `ingested_at` | `timestamptz` | horodatage |

Clé primaire : `(asset_id, trade_date)`.
Index : `(trade_date)`, `(asset_id, trade_date DESC)`.
Contraintes : `adj_close > 0`, `close > 0`.

#### `finance.fx_daily`

| Colonne | Type | Rôle |
|---|---|---|
| `currency` | `char(3)` | devise source |
| `trade_date` | `date` | date |
| `eur_per_unit` | `numeric` | 1 unité de devise = X EUR |
| `source` | `text` | `yahoo` |
| `ingested_at` | `timestamptz` | horodatage |

Clé primaire : `(currency, trade_date)`.
La ligne EUR n'est pas stockée : `eur_per_unit = 1` est implicite.

#### `finance.ingestion_runs`

| Colonne | Type | Rôle |
|---|---|---|
| `run_id` | `bigserial` | identifiant |
| `started_at`, `finished_at` | `timestamptz` | bornes |
| `status` | `text` | `success`, `partial`, `failed` |
| `assets_requested`, `assets_ok` | `int` | couverture |
| `rows_upserted` | `int` | volume |
| `message` | `text` | erreur éventuelle |

Cette table permet au contrôle de fraîcheur et au dashboard de vérifier que le
pipeline a bien tourné, indépendamment de la date des cours.

### 5.2 Vues et fonctions

- `finance.v_returns` : rendements `ret_local` et `ret_eur` par actif et date,
  calculés avec `lag()` sur l'historique propre de chaque actif.
- `finance.v_asset_stats` : première/dernière date, nombre d'observations,
  volatilité annualisée, rendement cumulé, drawdown maximal.
- `finance.corr_pairs(start_date, end_date, basis, min_obs)` : toutes les paires
  d'actifs, avec `corr`, `n_obs`, intervalle de confiance de Fisher à 95 %.
- `finance.corr_benchmark(start_date, end_date, basis, benchmark)` : corrélation
  de chaque actif avec le benchmark.
- `finance.rolling_corr(asset, benchmark, window, start_date, end_date, basis)` :
  corrélation glissante sur les `N` dernières observations communes.
- `finance.rolling_corr_all(benchmark, window, start_date, end_date, basis)` :
  même calcul pour tous les actifs, en une requête (panel Grafana).
- `finance.corr_by_year(start_date, end_date, basis)` : corrélations annuelles
  avec le benchmark.
- `finance.corr_monthly_snapshot(...)` : dernière corrélation glissante de
  chaque mois, pour le tableau d'évolution.

Les fonctions valident `basis` (`EUR`/`LOCAL`) et `window` (entier positif) pour
éviter toute injection SQL. Les fenêtres standard sont 63, 126 et 252 séances.

### 5.3 Droits

- `REVOKE ALL ON SCHEMA finance FROM PUBLIC;`
- `GRANT USAGE, CREATE ON SCHEMA finance TO finance;`
- `GRANT USAGE ON SCHEMA finance TO grafana;`
- `GRANT SELECT ON ALL TABLES IN SCHEMA finance TO grafana;`
- `ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
   GRANT SELECT ON TABLES TO grafana;`

Le second point est indispensable : sans privilèges par défaut, toute nouvelle
vue créée après le `GRANT` initial sera inaccessible à Grafana.

Le script `sql/00_bootstrap.sql` sera idempotent et devra être lancé en
superutilisateur :

```sql
-- Exécuté par postgres, une seule fois (ou après changement de mot de passe)
CREATE ROLE finance LOGIN PASSWORD :'finance_password';
\c finance_db
CREATE SCHEMA IF NOT EXISTS finance AUTHORIZATION finance;
ALTER SCHEMA finance OWNER TO finance;
-- puis les GRANT ci-dessus
```

Le mot de passe ne sera jamais commité : il sera passé via `psql -v` ou un
gestionnaire de secrets.

---

## 6. Pipeline ETL

### 6.1 Principe

Contrairement à DVF (instantanés annuels), les séries Yahoo sont **révisables** :
les clôtures ajustées changent après un split ou un dividende. Un simple
`ON CONFLICT DO NOTHING` figerait des prix faux.

Stratégie retenue : **rafraîchissement complet quotidien**, dans une
transaction par actif.

1. Télécharger l'historique complet de chaque actif depuis `FINANCE_START_DATE`.
2. Valider la réponse : devise, place, type d'instrument, nombres de lignes,
   valeurs positives, dates uniques.
3. Si l'actif est valide : `DELETE` de son historique dans `prices_daily`, puis
   insertion de la nouvelle série. Sinon, conserver l'ancienne série et marquer
   l'actif en échec.
4. Faire de même pour les paires FX.
5. Journaliser le run dans `ingestion_runs`.
6. Rafraîchir les éventuelles vues matérialisées.

Volume attendu : ~50 000 lignes, soit moins d'une seconde d'écriture. Le coût
réseau est de ~20 requêtes HTTP par jour.

### 6.2 Source HTTP

Utilisation de l'endpoint public Yahoo Finance :

```
https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
    ?period1=...&period2=...&interval=1d
    &events=div%2Csplits&includeAdjustedClose=true
```

Pas de clé API. Un adaptateur `yahoo.py` isole le parsing et les retries :

- `User-Agent` explicite ;
- timeout et 3 tentatives avec backoff exponentiel ;
- gestion des `429` et `5xx` ;
- conversion des timestamps dans le fuseau de la place (`exchangeTimezoneName`) ;
- lecture de `indicators.adjclose.adjclose[0]` avec repli sur `close` si absent ;
- déduplication par date.

L'adaptateur est remplaçable par `yfinance` ou Stooq si Yahoo change. Le reste
du pipeline ne dépend que d'un dict `{date, open, high, low, close, adj_close,
volume}`.

### 6.3 Configuration

Variables d'environnement prévues :

| Variable | Défaut | Rôle |
|---|---|---|
| `PGHOST` | `localhost` | hôte PostgreSQL |
| `PGPORT` | `5432` | port |
| `PGDATABASE` | `finance` | base |
| `PGUSER` | `finance` | rôle d'écriture |
| `PGPASSWORD` | — | obligatoire, jamais commité |
| `FINANCE_SCHEMA` | `finance` | schéma |
| `FINANCE_START_DATE` | `2015-01-01` | début du backfill |
| `FINANCE_BASE_CURRENCY` | `EUR` | base des rendements |
| `FINANCE_MIN_OBS` | `60` | observations minimales pour une corrélation |
| `FINANCE_MIN_IMPORT_INTERVAL_HOURS` | `18` | garde-fou anti-double exécution |
| `FINANCE_FORCE_IMPORT` | `0` | ignore le garde-fou |

### 6.4 Contrôles de qualité

- au moins 90 % des séances du benchmark sur la période ;
- dernier cours du benchmark à moins de 4 jours ouvrés ;
- aucun prix négatif ou nul ;
- variations journalières aberrantes signalées (seuil par type d'actif) ;
- devise et place conformes au référentiel ;
- `n_obs` suffisant pour chaque paire ;
- run loggé avec statut `success` / `partial` / `failed`.

En cas d'échec d'un actif secondaire, le run peut être `partial` et Jenkins
passe en `UNSTABLE`. En cas d'échec de `URTH` ou de plus de deux actifs, le
build échoue.

---

## 7. Analyses

### 7.1 Corrélation

Rendements logarithmiques :

```
r_t = ln(P_t / P_{t-1})
```

Corrélation de Pearson :

```
ρ(A,B) = corr(r_A, r_B)
```

Pour chaque paire :

- `n_obs` ;
- intervalle de confiance à 95 % par transformation de Fisher :
  `tanh(atanh(ρ) ± 1.96 / sqrt(n_obs - 3))` ;
- période effective (première et dernière date communes).

L'intervalle de confiance est important : une corrélation de 0,8 calculée sur
30 observations n'a pas la même valeur qu'une corrélation de 0,8 calculée sur
1 500 observations.

### 7.2 Évolution dans le temps

Trois échelles :

1. **Fenêtre glissante** de 63, 126 ou 252 séances (3, 6 ou 12 mois).
2. **Snapshot mensuel** : dernière corrélation glissante du mois, pour un
   tableau de chaleur mois × actif.
3. **Corrélation annuelle** : `corr` par année civile, pour voir les grands
   régimes (2018, COVID 2020, taux 2022, rallye IA 2023-2025).

La fenêtre 126 séances est le défaut : assez longue pour être stable, assez
courte pour voir un changement de régime. Les fenêtres 63 et 252 restent
sélectionnables.

### 7.3 Actions vs ETF

Pour chaque action (`ASML`, `BESI`, `NOVO`, `SMA92`) et chaque ETF :

- corrélation sur la période sélectionnée ;
- corrélation glissante ;
- `n_obs` et intervalle de confiance ;
- éventuellement bêta et R² (voir suggestions).

Point d'interprétation : une corrélation élevée peut simplement refléter le
fait que l'action appartient à l'ETF ou à son secteur. `ASML` et `BESI` sont
des semi-conducteurs ; `NOVO` est une valeur de santé ; `SMA Solar` est un
équipementier solaire. Les ETF SPDR sont **US** : ils ne contiennent pas
forcément ces valeurs européennes. C'est précisément ce que l'analyse doit
mettre en évidence, et pourquoi `SMH`/`SOXX`/`VGT` sont des comparateurs
pertinents.

### 7.4 Ruptures de régime

Le script R utilise `strucchange` pour détecter des changements durables.
Deux options :

- **v1** : ne pas prétendre faire de la détection statistique ; afficher la
  corrélation glissante et sa variation (Δ sur 1 mois / 3 mois). C'est
  transparent et sans dépendance supplémentaire.
- **v2** : ajouter un job Python `ruptures` (PELT) sur les séries de
  corrélation glissante, stocker les dates de rupture dans
  `finance.correlation_breaks`, et les annoter dans Grafana. Validation croisée
  avec `strucchange` sur les mêmes données.

Je recommande de commencer par la v1, puis d'ajouter `ruptures` si les
premiers graphiques montrent des régimes nets.

---

## 8. Dashboard Grafana

### 8.1 Contrainte technique

Le panel Grafana **Heatmap** natif est conçu pour des séries temporelles, pas
pour une matrice catégorielle (`asset_a` × `asset_b`). Voir la
[documentation Grafana](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/heatmap/).

Pour la matrice, deux solutions :

1. **Table panel + coloration des cellules** : requête en format long
   (`asset_a`, `asset_b`, `corr`), puis transformation Grafana
   `Rows to fields` pour pivoter `asset_b` en colonnes. Coloration continue
   divergente (rouge → blanc → bleu) entre -1 et +1.
2. **`tablefunc.crosstab`** si l'extension est disponible, avec une requête
   SQL large. Plus rigide à l'ajout d'actifs.

Je privilégie la table pivotée : pas d'extension, lisible, et suffisante pour
19 actifs. Si le rendu ou la transformation pose problème, on basculera sur
`crosstab`.

Pour l'évolution temporelle, même principe : tableau `mois` × `actif`, coloré
par la corrélation glissante. Un graphique en lignes reste disponible pour
comparer 2-3 actifs sélectionnés.

### 8.2 Panneaux prévus

| # | Panneau | Type | Contenu |
|---|---|---|---|
| 1 | Fraîcheur des données | Stat | dernier cours, dernier import, statut |
| 2 | Couverture | Stat | nombre d'actifs, nombre d'observations |
| 3 | Matrice ETF | Table colorée | 15 × 15, corrélation sur la période |
| 4 | Corrélations avec URTH | Bar gauge | classement des actifs |
| 5 | Évolution mensuelle vs URTH | Table colorée | mois × actif, corrélation 126 j |
| 6 | Corrélations actions × ETF | Table colorée | 4 × 15 |
| 7 | Action vs ETF dans le temps | Time series | `$stock` vs `$etf`, fenêtre `$window` |
| 8 | Corrélation vs URTH dans le temps | Time series | actifs sélectionnés |
| 9 | Statistiques par actif | Table | vol., rendement cumulé, drawdown |
| 10 | Méthodologie | Texte | formules, limites, sources |

Variables Grafana :

- `DS_POSTGRESQL` : datasource ;
- `basis` : `EUR` / `LOCAL` ;
- `window` : `63`, `126`, `252` ;
- `stock` : `ASML`, `BESI`, `NOVO`, `SMA92` ;
- `etf` : liste des 15 ETF ;
- `min_obs` : seuil de fiabilité.

Les requêtes utilisent `$__timeFrom()` et `$__timeTo()` castés en `date`, pour
que le sélecteur de temps Grafana filtre toutes les corrélations.

### 8.3 Lecture attendue

Le dashboard doit répondre en quelques secondes à :

- « Quelles sont les paires les plus corrélées sur les 5 dernières années ? »
- « La corrélation technologie / marché a-t-elle augmenté depuis 2022 ? »
- « ASML est-elle plus corrélée à XLK, SMH ou URTH ? »
- « La corrélation de Novo avec XLV est-elle stable ? »
- « SMA Solar se comporte-t-elle comme un actif énergie, technologie ou
  solaire ? »

---

## 9. Jenkins

### 9.1 Job

- Type : Pipeline script from SCM.
- SCM : dépôt Git `Finance_tracker`, branche `main`.
- Script : `Jenkinsfile`.
- Déclencheur : `cron("H 6 * * *")`, tous les jours à 06:00 environ.
- Fuseau : `Europe/Paris` (à configurer côté Jenkins ; le cron Jenkins utilise
  le fuseau du serveur).
- `disableConcurrentBuilds()`, `timeout(20 minutes)`, conservation de 30 builds.

### 9.2 Credentials

- `pg-password-finance` (Secret text) : mot de passe du rôle `finance`.
- Aucun mot de passe dans le dépôt, aucun mot de passe dans le Jenkinsfile.

### 9.3 Stages

1. **Setup Python Environment** : `python3 -m venv .venv`, `pip install -r
   requirements.txt`.
2. **Unit Tests** : tests hors-ligne (parsing, calculs, idempotence).
3. **Run ETL** : `python -m finance_tracker.etl`.
4. **Verify Freshness** : dernier cours, dernier import, couverture.
5. **SQL Smoke Test** (optionnel) : la matrice et les fonctions répondent.

En cas d'échec d'un actif secondaire : `UNSTABLE` mais données partielles
conservées. En cas d'échec de `URTH` ou de la base : `FAILURE`.

### 9.4 Garde-fou

`FINANCE_MIN_IMPORT_INTERVAL_HOURS=18` : si le job est déclenché plusieurs fois
dans la journée, l'ETL ne réimporte pas. `FINANCE_FORCE_IMPORT=1` pour
outrepasser.

---

## 10. Tests et critères d'acceptation

### 10.1 Tests

- **Parsing Yahoo** : fixture JSON, fuseau horaire, `adj_close`, déduplication.
- **Calculs** : rendements log, corrélation de séries identiques (=1),
  opposées (=-1), bruit blanc (≈0), gestion des `NULL`.
- **SQL** : création du schéma dans un schéma de test, insertion de prix
  synthétiques, vérification de `corr_pairs` et `rolling_corr`.
- **Idempotence** : deux exécutions consécutives donnent le même nombre de
  lignes.
- **Fraîcheur** : un jeu de données ancien fait échouer le contrôle.
- **Non-régression R** : comparer la matrice 2020-2026 des 15 actifs avec le
  résultat du script R (ou avec un calcul Python indépendant) à ±0,01 près.

### 10.2 Critères d'acceptation

- [ ] Le rôle `finance` possède le schéma `finance` ; `grafana` lit sans écrire.
- [ ] Les 19 actifs sont présents, avec au moins 1 500 observations chacun.
- [ ] La matrice 15 × 15 et la matrice 4 × 15 s'affichent dans Grafana.
- [ ] Les corrélations glissantes 63/126/252 sont disponibles.
- [ ] Le dashboard se filtre par période, devise et fenêtre.
- [ ] Jenkins tourne quotidiennement et le contrôle de fraîcheur échoue si les
      données sont trop anciennes.
- [ ] Aucun secret dans le dépôt.
- [ ] `README.md` permet à un nouveau développeur de reproduire l'installation.

---

## 11. Suggestions complémentaires

1. **Ajouter `SMH`, `SOXX`, `VGT`** dans une matrice « tech / semi » séparée.
   C'est le meilleur moyen de répondre à la question « ASML/BESI sont-elles
   corrélées aux ETF ? » avec des comparateurs pertinents.
2. **Ajouter `SPY`, `QQQ`, `VGK`, `VWO`** plus tard pour situer les secteurs US
   par rapport aux marchés large, croissance, Europe et émergents.
3. **Bêta et R²** en plus de la corrélation : la corrélation mesure la
   co-mouvement, la bêta mesure l'amplitude. Pour un suivi de risque, les deux
   sont utiles.
4. **Corrélation conditionnelle en stress** : corrélation calculée uniquement
   les jours où `URTH < -1 %`. Les corrélations augmentent souvent dans les
   baisses ; c'est une information plus actionnable que la corrélation moyenne.
5. **Clustering hiérarchique** : identifier les groupes (tech, défensif,
   taux, or, crypto) plutôt que par ordre alphabétique. Calcul Python + colonne
   `cluster_id`, ou simple ordre manuel dans `assets`.
6. **Détection de ruptures** : `ruptures` (PELT) sur les corrélations glissantes,
   avec annotations Grafana. À faire après la v1, pour ne pas surinterpréter.
7. **Alertes Grafana** : données non rafraîchies depuis 3 jours ; corrélation
   ASML/URTH qui franchit un seuil ; chute de corrélation d'un secteur.
8. **Annotations d'événements** : COVID (mars 2020), début du cycle de hausse
   des taux (2022), rallye IA (2023), crises sectorielles. Grafana annotations
   ou table `finance.events`.
9. **Export Parquet/CSV** : pour continuer à travailler dans R/Python sans
   requêter la base. Un simple `COPY` ou un script d'export suffit.
10. **Documentation de la méthodologie** : les corrélations sont des mesures
    descriptives, pas des relations causales. Les fenêtres glissantes se
    chevauchent, les tests multiples sont nombreux, et les ETF sectoriels US ne
    représentent pas l'économie mondiale.

---

## 12. Décisions à valider

1. **`SMA92` = SMA Solar Technology AG (`S92.DE`)** ?
2. **Devise de référence** : EUR par défaut (recommandé pour un investisseur
   européen), avec bascule `LOCAL` dans Grafana ?
3. **Profondeur historique** : backfill depuis 2015-01-01 (recommandé) ou
   depuis 2020-01-01 pour coller au script R ?
4. **Ajout de `SMH`, `SOXX`, `VGT`** dans une matrice complémentaire
   (recommandé) ou strictement les 15 actifs du script R ?
5. **Dépôt distant** : je reste en local (`git init` déjà fait) ou tu veux que
   je prépare le remote `GoyhexVincent/Finance_tracker` ? Je ne pousserai rien
   sans confirmation.

---

## 13. Ordre d'exécution proposé

| Étape | Contenu | Résultat |
|---|---|---|
| 0 | Validation des 5 décisions ci-dessus | périmètre figé |
| 1 | `sql/00_bootstrap.sql` + `sql/10_schema.sql` | base et schéma prêts |
| 2 | Package Python `finance_tracker/` + adaptateur Yahoo | ETL testé hors-ligne |
| 3 | Backfill 2015 → aujourd'hui + tests d'idempotence | ~50 000 lignes en base |
| 4 | Fonctions SQL de corrélation + validation vs R | matrices et séries fiables |
| 5 | `grafana/dashboard_finance.json` + guide d'import | dashboard opérationnel |
| 6 | `Jenkinsfile` + credentials + premier build | mise à jour quotidienne |
| 7 | `README.md`, `docs/METHODOLOGIE.md`, `docs/RUNBOOK.md` | exploitation autonome |

Une fois les décisions validées, je peux enchaîner les étapes 1 à 7. La première
livraison peut être un v1 fonctionnel (matrice + évolution + actions vs ETF),
puis on itère sur les panneaux et les analyses avancées.
