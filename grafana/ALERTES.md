# Alertes Grafana — Finance Tracker

Trois alertes suffisent pour la v1 :

1. **données de marché trop anciennes** (benchmark `URTH`) ;
2. **pipeline ETL non exécuté** (dernier import réussi) ;
3. **changement de régime de corrélation** (variation sur 21 séances).

Le contrôle Jenkins (`scripts/verify_freshness.py`) couvre déjà les deux
premières. Les règles Grafana ajoutent la notification et la surveillance du
régime.

---

## 1. Données de marché trop anciennes

**Requête** (datasource PostgreSQL, format table) :

```sql
SELECT max(last_trade_date)::timestamptz AS time,
       (CURRENT_DATE - max(last_trade_date)) AS value
FROM finance.v_freshness
WHERE ticker = 'URTH';
```

**Condition** : `value IS ABOVE 5`.
**Évaluation** : toutes les 6 h, pendant 1 h.
**No data** : `Alerting` (une base vide doit alerter).
**Message** : « URTH n'a pas de cours depuis plus de 5 jours : vérifier Yahoo,
Jenkins ou la connexion PostgreSQL. »

Pourquoi 5 jours ? Un week-end + un jour férié peuvent donner 3–4 jours sans
nouveau cours. 5 jours laisse une marge avant de suspecter une panne.

---

## 2. Pipeline ETL non exécuté

**Requête** :

```sql
SELECT max(finished_at) AS time,
       EXTRACT(EPOCH FROM (NOW() - max(finished_at))) / 3600.0 AS value
FROM finance.ingestion_runs
WHERE status IN ('success', 'partial');
```

**Condition** : `value IS ABOVE 36`.
**Évaluation** : toutes les 6 h, pendant 1 h.
**No data** : `Alerting`.
**Message** : « Aucun import réussi depuis plus de 36 h : le job Jenkins n'a pas
tourné ou a échoué. »

36 h couvre un décalage d'un jour + une exécution manuelle. Le job est quotidien
à 06:00 ; une alerte à 36 h laisse une journée de marge.

---

## 3. Changement de régime de corrélation

Exemple pour ASML contre URTH, fenêtre 126 séances. Remplacer `ASML` par
l'actif suivi.

```sql
WITH r AS (
    SELECT trade_date, correlation
    FROM finance.rolling_corr(
        'ASML', 'URTH', 126,
        CURRENT_DATE - 500, CURRENT_DATE, 'EUR'
    )
), latest AS (
    SELECT correlation FROM r ORDER BY trade_date DESC LIMIT 1
), old AS (
    SELECT correlation FROM r ORDER BY trade_date DESC OFFSET 21 LIMIT 1
)
SELECT max(trade_date)::timestamptz AS time,
       (SELECT correlation FROM latest) - (SELECT correlation FROM old) AS value
FROM r;
```

**Condition** : `value IS ABOVE 0.20` (ou `IS BELOW -0.20` dans une seconde
règle).
**Évaluation** : une fois par jour, pendant 1 jour.
**No data** : `OK` (pas assez d'historique au démarrage).
**Message** : « La corrélation ASML/URTH a varié de plus de 0,20 en 21 séances :
vérifier s'il s'agit d'un changement de régime ou d'un événement ponctuel. »

Pourquoi 21 séances ? C'est environ un mois de bourse. Un seuil de 0,20 est
suffisamment large pour éviter le bruit quotidien, mais assez sensible pour
détecter un vrai retournement. À ajuster selon l'actif.

> Ne pas créer une règle par actif × ETF : commencer par 2–3 actifs suivis.
> Les alertes de corrélation sont fragiles ; elles doivent être rares et
> examinées manuellement.

---

## 4. Configurer la notification

1. **Alerting → Contact points** : créer un contact (email, Slack, Telegram…).
2. **Alerting → Notification policies** : router les alertes du dossier
   `Finance` vers ce contact.
3. **Alerting → Alert rules → New alert rule** :
   - choisir la datasource PostgreSQL ;
   - coller la requête ;
   - `Reduce` : `Last` ;
   - `Threshold` : la condition ci-dessus ;
   - ajouter une annotation `summary` et `description`.
4. Tester avec **Preview** / **Test rule**.

Le dashboard n'embarque pas les règles d'alerte : elles sont intentionnellement
documentées ici plutôt que dans le JSON. Cela évite de coupler le dashboard à
un UID de datasource et à un contact point qui changent d'une installation à
l'autre.

---

## 5. Ce qu'il ne faut pas alerter

- Une corrélation qui passe de 0,79 à 0,81 : bruit.
- Une corrélation négative isolée sur une fenêtre de 63 jours : souvent un
  artefact d'échantillon.
- Un actif qui n'a pas de cours un jour férié : normal.
- Une variation de corrélation le jour d'un split : vérifier les prix ajustés
  avant de conclure.

Les alertes doivent déclencher une **vérification**, pas une décision
automatique.
