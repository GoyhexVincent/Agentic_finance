# Optimisations de parsing et d'insertion

Ce document explique les choix de performance du pipeline et ce qui pourrait
être fait si le volume augmentait. Les mesures ont été faites sur la machine de
développement (PostgreSQL 17 local), pas sur le Raspberry Pi.

---

## 1. Parsing Yahoo

### 1.1 Pas de pandas / yfinance

L'endpoint `v8/finance/chart` renvoie déjà des tableaux JSON alignés
(`timestamp`, `open`, `high`, `low`, `close`, `volume`, `adjclose`). On les
parcourt avec `zip` et on construit un dictionnaire indexé par date.

Avantages :

- pas de dépendance pandas/numpy (installation plus légère sur Raspberry Pi) ;
- pas de conversion DataFrame → lignes → SQL ;
- le contrôle des types et des valeurs manquantes est explicite.

Coût : il faut gérer soi-même les retries, les fuseaux et les valeurs `null`.
C'est isolé dans `finance_tracker/yahoo.py`.

### 1.2 Déduplication par date

Yahoo peut renvoyer deux points pour une même date (rare, mais possible lors
d'une correction). On stocke dans un `dict[date, row]` : la dernière valeur
écrase la précédente, puis on trie les dates. C'est O(n) et cela évite une
violation de clé primaire au moment du COPY.

### 1.3 Fuseau de la place

Les timestamps sont convertis avec `exchangeTimezoneName` (ex.
`America/New_York`, `Europe/Amsterdam`) et non en UTC. Sans cela, une séance US
ouvrant à 09:30 EST serait datée de la veille sur un serveur européen.

### 1.4 Téléchargement séquentiel

Les 22 actifs + 2 paires FX sont téléchargés séquentiellement, avec un délai de
0,4 s. C'est volontaire :

- Yahoo limite le débit et bloque les rafales ;
- 24 requêtes prennent ~10 s, ce qui est négligeable pour un job quotidien ;
- un `ThreadPoolExecutor` serait plus rapide mais augmenterait le risque de
  `429` et rendrait les logs moins lisibles.

Si le nombre d'actifs dépasse ~100, on pourra paralléliser par petits lots
(4–6 threads) avec un backoff global.

---

## 2. Insertion PostgreSQL

### 2.1 `COPY` plutôt que des INSERT ligne à ligne

Les prix sont chargés avec `COPY ... FROM STDIN WITH (FORMAT CSV, NULL '')`.
C'est la voie la plus rapide pour un gros volume :

- une seule commande réseau ;
- pas de parsing de 65 000 `INSERT` ;
- pas de surcoût `ON CONFLICT` par ligne.

Le CSV est construit en mémoire (`io.StringIO` + `csv.writer`), sans fichier
temporaire. Les valeurs `NULL` sont des champs vides, les dates sont ISO, les
nombres sont formatés avec 6 décimales (10 pour les taux FX).

### 2.2 Remplacement complet par actif

Pour chaque actif :

```sql
DELETE FROM finance.prices_daily WHERE asset_id = %s;
COPY finance.prices_daily (...) FROM STDIN ...;
```

Pourquoi ne pas faire un `UPSERT` ?

- les clôtures ajustées changent après split/dividende : un `DO NOTHING`
  laisserait des prix faux ;
- un `DO UPDATE` coûte plus cher qu'un `DELETE` ciblé + `COPY` ;
- le volume est faible (~3 000 lignes par actif), le `DELETE` est instantané
  grâce à l'index `(asset_id, trade_date)`.

Le `DELETE` et le `COPY` sont dans la **même transaction** : en cas d'échec, on
revient à l'état précédent, jamais à un historique partiel.

### 2.3 Une transaction par run, pas par actif

`_load()` ouvre une transaction, prend un verrou consultatif
(`pg_advisory_xact_lock`), charge tous les actifs et les FX, puis commit.
Avantages :

- atomicité : un run interrompu ne laisse pas la base à moitié mise à jour ;
- un seul `COMMIT` ;
- le verrou empêche deux builds Jenkins concurrents d'écrire en même temps.

Les téléchargements sont faits **avant** la transaction : une panne réseau ne
bloque pas la base et ne laisse pas de transaction ouverte.

### 2.4 `execute_values` pour les petites tables

Le référentiel (`assets`), les ruptures et les événements utilisent
`execute_values` avec un `page_size` raisonnable. Pour 22 lignes, `COPY` serait
sur-dimensionné. `execute_values` regroupe les inserts en un seul aller-retour.

### 2.5 `ANALYZE` après chargement

Après un remplacement massif, les statistiques du planificateur sont périmées.
`ANALYZE finance.prices_daily` et `finance.fx_daily` sont exécutés dans la
transaction. Sans cela, les jointures de `v_returns` peuvent choisir un plan
sous-optimal.

### 2.6 Index

- `prices_daily (asset_id, trade_date)` : clé primaire, sert au `DELETE`, au
  `LAG` et aux jointures.
- `prices_daily (trade_date DESC)` : utile pour les filtres temporels Grafana.
- `fx_daily (currency, trade_date)` : clé primaire, sert au `LATERAL` « as of ».
- `correlation_breaks (break_date DESC)` : annotations.

Pas d'index sur `close`/`adj_close` : inutile pour les requêtes actuelles.

### 2.7 Mesure locale

Sur la machine de développement, le chargement complet (22 actifs, 65 654 lignes
de prix + 6 141 taux FX, `DELETE` + `COPY` + `ANALYZE`) prend environ
**1,2 seconde**, soit ~59 000 lignes/s. Le téléchargement réseau (~24 requêtes)
prend ~10 secondes. Le coût dominant est donc le réseau, pas la base.

Sur Raspberry Pi, attendre quelques secondes pour le chargement ; le volume
reste très inférieur aux capacités de PostgreSQL.

---

## 3. Pourquoi pas TimescaleDB ?

`NDBC_tracker` utilise TimescaleDB pour des mesures à haute fréquence. Ici :

- 22 actifs × ~3 000 séances ≈ 65 000 lignes ;
- une requête de corrélation scanne quelques milliers de lignes ;
- PostgreSQL standard répond en quelques dizaines de millisecondes.

TimescaleDB n'apporterait rien de mesurable et ajouterait une dépendance. Le
schéma reste compatible si le volume augmente : on pourra convertir
`prices_daily` en hypertable sur `trade_date` sans changer les vues.

---

## 4. Si le volume augmente

| Situation | Optimisation |
|---|---|
| 100+ actifs, téléchargement long | lots parallèles de 4–6 requêtes, backoff global |
| 10M+ lignes de prix | hypertable TimescaleDB, compression des vieux chunks |
| mises à jour incrémentales | staging `UNLOGGED` + `MERGE`/`ON CONFLICT` sur les N derniers jours |
| beaucoup de révisions | conserver un `ingested_at` par version et une table d'historique |
| dashboard lent | vues matérialisées `corr_monthly`/`corr_matrix`, rafraîchies après l'ETL |
| cache réseau | stocker les réponses JSON brutes dans un objet/S3, pas en base |

Aujourd'hui, la simplicité (remplacement complet, pas de cache, pas de
matérialisation) est le bon compromis : le pipeline tient en quelques centaines
de lignes, est facile à auditer et se rejoue sans état.

---

## 5. Ce qui n'est pas optimisé (volontairement)

- **Pas de cache HTTP** : un run quotidien doit voir les corrections de Yahoo.
- **Pas de parallélisme** : le gain serait de quelques secondes, au prix d'un
  risque de rate limit.
- **Pas de table `staging` permanente** : le `COPY` direct après `DELETE` suffit
  pour 65 000 lignes.
- **Pas de compression** : le volume est trop faible.
- **Pas de partitionnement** : idem.

Ces choix pourront être revus si le nombre d'actifs ou la fréquence augmente.
