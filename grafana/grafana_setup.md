# Guide Grafana — Finance Tracker

> Dashboard : `grafana/dashboard_finance.json`
> Datasource provisionnée : PostgreSQL, base `finance_db`, schéma `finance`,
> rôle `grafana` en lecture seule.

Le déploiement est **provisionné** : la datasource et le dashboard sont des
fichiers versionnés, copiés sur la machine Grafana. Il n'y a rien à saisir
dans l'interface, donc aucun écart possible entre la machine de développement
et le Raspberry Pi.

| Fichier | Rôle |
|---|---|
| `grafana/provisioning/datasources/finance-postgres.yaml` | datasource `Finance-PostgreSQL` (uid `finance-postgresql`) |
| `grafana/provisioning/dashboards/finance.yaml` | provider qui charge le JSON toutes les 30 s |
| `grafana/dashboard_finance.json` | dashboard (généré par `scripts/build_dashboard.py`) |
| `grafana/finance.env.example` | variables d'environnement du service Grafana |
| `sql/20_grafana_readonly.sql` | rôle PostgreSQL `grafana` + droits de lecture |
| `scripts/bootstrap_grafana.sh` | applique le SQL ci-dessus |
| `scripts/install_grafana_provisioning.sh` | copie les fichiers et configure systemd |

---

## 1. Prérequis

- Grafana installé (validé avec Grafana 12.2 ; compatible 10/11) ;
- PostgreSQL accessible depuis Grafana, base `finance_db` créée et schéma
  `finance` chargé (étape ETL) ;
- un accès `sudo -u postgres` pour créer le rôle de lecture.

Si Grafana n'est pas installé sur Debian / Raspberry Pi OS :

```bash
sudo apt install -y grafana
sudo systemctl enable --now grafana-server
```

---

## 2. Créer le rôle de lecture

Depuis la racine du dépôt :

```bash
bash scripts/bootstrap_grafana.sh finance_db
```

Le script demande deux fois le mot de passe, puis exécute
`sql/20_grafana_readonly.sql`. Il est idempotent : le relancer met à jour le
mot de passe et répare les droits.

Équivalent manuel :

```bash
read -rsp 'Mot de passe grafana : ' GRAFANA_PASSWORD; echo
sudo -u postgres psql -U postgres -d postgres \
  -v grafana_password="$GRAFANA_PASSWORD" \
  -v dbname=finance_db \
  -f sql/20_grafana_readonly.sql
unset GRAFANA_PASSWORD
```

Le script accorde :

```sql
GRANT CONNECT ON DATABASE finance_db TO grafana;
GRANT USAGE ON SCHEMA finance TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA finance TO grafana;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA finance TO grafana;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA finance TO grafana;

ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
  GRANT SELECT ON TABLES TO grafana;
```

et révoque explicitement `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` ainsi que
`CREATE` sur le schéma. `default_transaction_read_only = on` est également
positionné sur le rôle : même un `GRANT` d'écriture ajouté par erreur ne
permettrait pas de modifier les données.

> `GRANT SELECT ON ALL TABLES` n'est pas rétroactif pour les objets créés plus
> tard. C'est `ALTER DEFAULT PRIVILEGES` qui couvre les nouvelles vues et
> fonctions créées par le rôle `finance`.

Vérification :

```bash
sudo -u postgres psql -U postgres -d finance_db -c '\dp finance.*'
```

---

## 3. Configurer l'environnement Grafana

Copier l'exemple :

```bash
sudo install -m 640 -o root -g grafana \
  grafana/finance.env.example /etc/grafana/finance.env
sudo editor /etc/grafana/finance.env
```

Renseigner au minimum :

```dotenv
GF_PG_HOST=localhost
GF_PG_PORT=5432
GF_PG_DATABASE=finance_db
GF_PG_USER=grafana
GF_PG_PASSWORD='le_mot_de_passe_du_rôle_grafana'
GF_FINANCE_DASHBOARDS_PATH=/var/lib/grafana/dashboards
```

Ces variables sont injectées dans le YAML de provisioning au démarrage de
Grafana. Le fichier contient un secret : il doit rester en `640 root:grafana`
et ne jamais être versionné.

Si PostgreSQL écoute sur une autre machine, adapter `GF_PG_HOST` et vérifier
`listen_addresses` / `pg_hba.conf` côté serveur.

---

## 4. Installer le provisioning

```bash
sudo bash scripts/install_grafana_provisioning.sh
```

Le script :

1. copie les deux YAML dans `/etc/grafana/provisioning/` ;
2. copie `dashboard_finance.json` dans `/var/lib/grafana/dashboards/` ;
3. crée `/etc/grafana/finance.env` s'il manque ;
4. ajoute le drop-in systemd
   `/etc/systemd/system/grafana-server.service.d/finance-env.conf` qui charge
   ce fichier ;
5. redémarre Grafana et attend `/api/health`.

Il est idempotent : à relancer après chaque `make dashboard` ou modification
du YAML.

---

## 5. Vérifier

### 5.1 Datasource

```bash
curl -u admin:VOTRE_MOT_DE_PASSE \
  http://127.0.0.1:3000/api/datasources/uid/finance-postgresql/health
```

Attendu :

```json
{"message":"Database Connection OK","status":"OK"}
```

Dans Grafana : **Connections → Data sources → Finance-PostgreSQL**. La
datasource est en lecture seule (`editable: false`) ; le bouton
**Save & test** fonctionne quand même.

### 5.2 Dashboard

Ouvrir <http://127.0.0.1:3000/d/finance-correlation>.

Le dashboard est dans le dossier **Finance**. La variable `DS_POSTGRESQL` est
pré-réglée sur `finance-postgresql` : les 17 panneaux doivent se charger sans
sélection manuelle.

Contrôles rapides :

```sql
-- doit renvoyer 22 lignes
SELECT count(*) FROM finance.assets;

-- doit renvoyer la dernière séance importée
SELECT max(last_trade_date) FROM finance.v_freshness;

-- doit renvoyer 15 lignes
SELECT count(*) FROM finance.corr_matrix_core(
  '2018-07-01', current_date, 'EUR', 60);
```

Si les variables `stock`, `etf` ou `assets` sont vides, la datasource n'a pas
`USAGE` + `SELECT` : rejouer `sql/20_grafana_readonly.sql`.

---

## 6. Tester en local sans root

Pour valider le provisioning avant de toucher au Pi :

```bash
mkdir -p .cache && cd .cache
curl -L -o grafana.tar.gz \
  https://dl.grafana.com/oss/release/grafana-12.2.0.linux-amd64.tar.gz
tar xzf grafana.tar.gz
cd ..

GF_PG_PASSWORD='...' bash scripts/run_grafana_local.sh
```

Puis <http://127.0.0.1:3000> (admin / admin). Le script active la connexion
anonyme en lecture seule, uniquement pour ce mode de test.

> Grafana ne sait pas construire une URL `host:port` à partir d'un chemin de
> socket Unix : `GF_PG_HOST=/var/run/postgresql` échoue avec
> `dial unix .../.s.PGSQL.5432`. Utiliser TCP (`localhost:5432`) et un rôle
> avec mot de passe, comme en production.

---

## 7. Importer le dashboard à la main (secours)

Si le provisioning n'est pas utilisé :

1. **Connections → Data sources → PostgreSQL** ;
2. Host `localhost:5432`, base `finance_db`, utilisateur `grafana`,
   SSL `disable` ;
3. **Save & test** ;
4. **+ → Import → Upload JSON** : `grafana/dashboard_finance.json` ;
5. sélectionner la datasource dans la variable **Datasource** en haut du
   dashboard.

Le JSON reste la source de vérité ; le provisioning ne fait que l'importer
automatiquement.

---

## 8. Variables

| Variable | Type | Rôle |
|---|---|---|
| `DS_POSTGRESQL` | datasource | connexion PostgreSQL (défaut : `finance-postgresql`) |
| `basis` | custom | `EUR` (défaut) ou `LOCAL` |
| `window` | custom | fenêtre glissante 63 / 126 / 252 séances |
| `min_obs` | custom | observations minimales pour une corrélation |
| `stock` | query | ASML, BESI, NOVO, SMA92 |
| `etf` | query | tous les ETF du référentiel |
| `assets` | multi | actifs affichés dans les courbes vs URTH |

Les variables `stock`, `etf` et `assets` sont des requêtes SQL sur
`finance.assets`. Un actif ajouté dans le bon univers apparaît automatiquement.

---

## 9. Panneaux

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
(`color-background`). Les seuils sont :

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

## 10. Annotations

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

## 11. Rafraîchissement

`refresh: 1h` : les données ne changent qu'une fois par jour. Une page ouverte
se met à jour après le passage Jenkins, sans rechargement manuel.

---

## 12. Alertes

Voir [`ALERTES.md`](ALERTES.md). Le dashboard affiche déjà les seuils de
fraîcheur ; les règles Grafana ajoutent la notification.

---

## 13. Dépannage

| Problème | Solution |
|---|---|
| `permission denied for schema finance` | rejouer `sql/20_grafana_readonly.sql` |
| `permission denied for table ...` | le script 20 pose `ALTER DEFAULT PRIVILEGES` ; le relancer |
| `password authentication failed` | vérifier `GF_PG_PASSWORD` dans `/etc/grafana/finance.env`, puis redémarrer |
| La datasource garde l'ancien hôte/mot de passe | incrémenter `version` dans `finance-postgres.yaml`, réinstaller, redémarrer |
| `dial unix .../.s.PGSQL.5432` | Grafana attend `hôte:port` TCP, pas un chemin de socket |
| Variables `stock`/`etf` vides | schéma non chargé, ou datasource sans `SELECT` |
| Dashboard absent | `GF_FINANCE_DASHBOARDS_PATH` doit pointer vers le dossier du JSON ; redémarrer |
| Matrice vide | période trop courte / `min_obs` trop élevé / pas de données |
| Panneau « Bêta » vide | l'actif ou l'ETF sélectionné n'a pas assez d'observations |
| Annotations absentes | datasource non sélectionnée dans l'onglet Annotations |
| Dashboard lent | réduire la période, ou matérialiser les snapshots mensuels |

Journaux utiles :

```bash
journalctl -u grafana-server -n 100 --no-pager
curl -s http://127.0.0.1:3000/api/health
```

---

## 14. Mise à jour du dashboard

Le JSON est généré par :

```bash
python scripts/build_dashboard.py
```

Modifier le script plutôt que le JSON à la main, puis :

```bash
sudo bash scripts/install_grafana_provisioning.sh
```

Grafana relit le dossier toutes les 30 s ; un redémarrage n'est nécessaire que
pour la datasource ou le provider.
