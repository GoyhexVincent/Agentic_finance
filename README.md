# Finance Tracker

Dépôt de collecte, de stockage et de visualisation de données de marché :
cours quotidiens d'ETF sectoriels et d'actions, calcul de corrélations,
suivi de leur évolution dans le temps et mise à disposition dans Grafana.

Le projet reprend l'architecture des dépôts [`DVF_tracker`](../DVF_tracker) et
[`NDBC_tracker`](../NDBC_tracker) : source ouverte → ETL Python → PostgreSQL →
Jenkins → Grafana.

> **Statut : initialisation.** Le dépôt est créé, l'architecture cible et les
> choix techniques sont documentés dans [`docs/PLAN.md`](docs/PLAN.md). Le code
> métier sera ajouté après validation des quelques décisions listées dans ce
> plan.

---

## Objectif à court terme

1. Récupérer l'historique quotidien des 15 actifs du script R
   `r_dataviz/finance_simplified/correlations_etf.R` :
   11 ETF sectoriels SPDR, `URTH`, `BTC`, `GLD`, `TLT`.
2. Calculer une matrice de corrélation sur les rendements logarithmiques.
3. Mesurer l'évolution de ces corrélations (fenêtres glissantes de 63, 126 et
   252 séances).
4. Ajouter quatre actions (`ASML`, `BESI`, `NOVO`, `SMA Solar`) et mesurer
   leur corrélation avec chacun de ces ETF.
5. Publier le tout dans un dashboard Grafana, alimenté quotidiennement par
   Jenkins.

## Principes

- **Une base, un schéma, un rôle dédié** : base `finance`, schéma `finance`,
  rôle propriétaire `finance`, rôle de lecture `grafana`.
- **ETL idempotent** : chaque exécution peut être rejouée sans doublon.
- **Traçabilité** : chaque import est journalisé ; les données brutes restent
  en base pour permettre de recalculer les analyses sans retélécharger.
- **Séparation des responsabilités** : le rôle d'écriture n'est pas le rôle de
  lecture Grafana ; aucun mot de passe dans le dépôt.
- **Reproductibilité** : tests hors-ligne, contrôle de fraîcheur, documentation
  de la méthodologie.

## Documentation

- [`docs/PLAN.md`](docs/PLAN.md) — architecture, modèle de données, pipeline,
  panneaux Grafana, planning et points à valider.

## Prérequis (cible)

- Python 3.11+
- PostgreSQL 14+ (PostgreSQL 17 convient ; TimescaleDB non requis)
- Jenkins (planification quotidienne)
- Grafana (dashboard)

## Licence

À définir.
