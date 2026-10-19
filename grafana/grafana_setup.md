# Guide Grafana — Finance Tracker

> Dashboard : `grafana/dashboard_finance.json`
> Datasource : PostgreSQL, base `marine_database`, schéma `finance`, rôle
> `grafana` en lecture seule.

---

## 1. Prérequis

- Grafana installé (testé avec les versions 10/11 ; le JSON utilise le
  `schemaVersion` 30, compatible avec les versions récentes).
- Base `marine_database` accessible depuis Grafana.
- Schéma `finance` créé par `sql/00_bootstrap.sql` + `sql/10_schema.sql`.
- Rôle `grafana` existant, ou un rôle de lecture dédié.

Si Grafana n'est pas encore installé, suivre la procédure de
[`../../NDBC_tracker/grafana_setup.md`](../../NDBC_tracker/grafana_setup.md) §1.

---

## 2. Créer / vérifier le rôle de lecture

En superutilisateur `postgres` :

```sql
CREATE USER grafana WITH PASSWORD '...';
GRANT CONNECT ON DATABASE marine_database TO grafana;
GRANT USAGE ON SCHEMA finance TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA finance TO grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
  GRANT SELECT ON TABLES TO grafana;
```

Le rôle `grafana` est probablement déjà créé pour NDBC/DVF. Dans ce cas, il
suffit de lui donner les droits sur le schéma `finance`.

> **Important** : `GRANT SELECT ON ALL TABLES` n'est pas rétroactif pour les
> objets créés plus tard. `ALTER DEFAULT PRIVILEGES` est indispensable si de
> nouvelles vues/fonctions sont ajoutées.

Vérification :

```bash
psql -U finance -d marine_database -c "\dp finance.*"
```

---

## 3. Ajouter la datasource

Dans Grafana : **Administration → Data sources → Add new data source →
PostgreSQL**.

| Paramètre | Valeur |
|---|---|
| Name | `Finance-PostgreSQL` |
| Host | `localhost:5432` (ou l'IP du Pi) |
| Database | `marine_database` |
| User | `grafana` |
| Password | mot de passe du rôle |
| SSL Mode | `disable` en local |
| TimescaleDB | **non** |
| Max open connections | 5–10 |
| Max idle connections | 2 |

Cliquer **Save & test** : « Database Connection OK ».

Le dashboard déclare sa datasource via la variable `DS_POSTGRESQL` ; il
utilisera celle que tu sélectionnes à l'import.

---

## 4. Importer le dashboard

1. **+ (Create) → Import**.
2. **Upload dashboard JSON file** → `grafana/dashboard_finance.json`.
3. Laisser l'UID `finance-correlation`.
4. Sélectionner la datasource `Finance-PostgreSQL`.
5. **Import**.

Le dashboard s'ouvre sur `2018-07-01 → now`.

---

## 5. Variables

| Variable | Type | Rôle |
|---|---|---|
| `DS_POSTGRESQL` | datasource | connexion PostgreSQL |
| `basis` | custom | `EUR` (défaut) ou `LOCAL` |
| `window` | custom | fenêtre glissante 63 / 126 / 252 séances |
| `min_obs` | custom | observations minimales pour une corrélation |
| `stock` | query | ASML, BESI, NOVO, SMA92 |
| `etf` | query | tous les ETF du référentiel |
| `assets` | multi | actifs affichés dans les courbes vs URTH |

Les variables `stock`, `etf` et `assets` sont des requêtes SQL sur
`finance.assets`. Un actif ajouté dans le bon univers apparaît automatiquement.

---

## 6. Panneaux

| # | Panneau | Ce qu'il montre |
|---|---|---|
| 1 | Dernière séance | dernier cours disponible |
| 2 | Actifs suivis | 22 actifs (15 core + 4 actions + 3 tech) |
| 3 | Dernier import | dernier run ETL réussi |
| 4 | Paires valides | nombre de paires avec assez d'observations |
| 5 | Matrice core | 15 × 15, corrélations Pearson |
| 6 | Corrélations avec URTH | classement des actifs |
| 7 | Matrice tech | SMH / SOXX / VGT |
| 8 | Bêta et R² | `$stock` vs `$etf` |
| 9 | Matrice actions | 4 × 15 |
| 10 | Corrélations glissantes | évolution vs URTH |
| 11 | Corrélation `$stock` vs `$etf` | suivi temporel |
| 12 | Stress | corrélation les jours de baisse |
| 13 | Statistiques | vol., rendement cumulé, drawdown |
| 14 | Fraîcheur | âge des données par actif |
| 15 | Méthodologie | formules et limites |
| 16 | Groupes de corrélation | clusters calculés par average linkage |
| 17 | Corrélations annuelles | corrélations par année civile vs URTH |

### Pourquoi une table et pas un heatmap pour la matrice ?

Le panel **Heatmap** natif de Grafana est conçu pour des séries temporelles
(voir la [documentation Grafana](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/heatmap/)).
Il ne sait pas afficher une matrice catégorielle `asset_a × asset_b`.

La matrice est donc une **table** avec coloration des cellules par seuils
(`color-background`). C'est moins « joli » qu'un heatmap, mais lisible, sans
plugin et sans extension PostgreSQL. Les seuils sont :

| Corrélation | Couleur |
|---|---|
| < -0,6 | rouge foncé |
| -0,6 → -0,3 | rouge |
| -0,3 → -0,1 | orange |
| -0,1 → 0,1 | gris |
| 0,1 → 0,3 | bleu clair |
| 0,3 → 0,6 | bleu |
| 0,6 → 0,8 | bleu foncé |
| > 0,8 | violet |

---

## 7. Annotations

Deux annotations sont préconfigurées :

- **Événements de marché** : table `finance.events` (COVID, taux, SVB, etc.) ;
- **Ruptures de corrélation** : table `finance.correlation_breaks`.

Requêtes utilisées :

```sql
SELECT event_date::timestamptz AS time,
       title AS text,
       category AS tags
FROM finance.events
WHERE $__timeFilter(event_date)
ORDER BY event_date;
```

```sql
SELECT b.break_date::timestamptz AS time,
       a.ticker || ' — rupture ' || b.method AS text,
       'break' AS tags
FROM finance.correlation_breaks b
JOIN finance.assets a USING (asset_id)
WHERE $__timeFilter(b.break_date)
ORDER BY b.break_date;
```

Si les annotations ne s'affichent pas, vérifier que la datasource est bien
sélectionnée dans l'onglet **Annotations** du dashboard.

---

## 8. Rafraîchissement

`refresh: 1h` : les données ne changent qu'une fois par jour. Une page ouverte
se met à jour après le passage Jenkins, sans rechargement manuel.

---

## 9. Alertes

Voir [`ALERTES.md`](ALERTES.md). Le dashboard affiche déjà les seuils de
fraîcheur ; les règles Grafana ajoutent la notification.

---

## 10. Dépannage

| Problème | Solution |
|---|---|
| `permission denied for schema finance` | `GRANT USAGE ON SCHEMA finance TO grafana;` |
| `permission denied for table ...` | `GRANT SELECT ON ALL TABLES...` + `ALTER DEFAULT PRIVILEGES...` |
| Variables `stock`/`etf` vides | le schéma n'est pas chargé, ou la datasource n'a pas `SELECT` |
| Matrice vide | période trop courte / `min_obs` trop élevé / pas de données |
| Panneau « Bêta » vide | l'actif ou l'ETF sélectionné n'a pas assez d'observations |
| Annotations absentes | datasource non sélectionnée dans l'onglet Annotations |
| Dashboard lent | réduire la période, ou matérialiser les snapshots mensuels |

---

## 11. Mise à jour du dashboard

Le JSON est généré par :

```bash
python scripts/build_dashboard.py
```

Modifier le script plutôt que le JSON à la main, puis réimporter le fichier
dans Grafana (ou utiliser le provisioning si tu en mets un en place).
