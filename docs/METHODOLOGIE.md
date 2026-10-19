# Méthodologie des analyses

Ce document décrit **comment** les corrélations sont calculées, **pourquoi** ces
choix ont été faits, et **comment les interpréter** — y compris leurs limites.
Il complète les commentaires du code et du schéma SQL.

---

## 1. Données

### 1.1 Source

Les cours sont récupérés sur l'endpoint public Yahoo Finance
`v8/finance/chart`, en fréquence quotidienne. La source est gratuite mais
**non officielle** : elle peut limiter le débit, changer de format ou réviser
un historique. C'est pourquoi :

- les données brutes sont conservées en base (`finance.prices_daily`) ;
- chaque exécution remplace l'historique complet d'un actif (les clôtures
  ajustées changent après un split ou un dividende) ;
- un contrôle de fraîcheur et de couverture fait échouer le build Jenkins en
  cas de problème.

### 1.2 Actifs

Trois univers :

| Univers | Contenu | Rôle |
|---|---|---|
| `core` | 11 ETF sectoriels SPDR + `URTH` + `BTC` + `GLD` + `TLT` | matrice principale, 15 × 15 |
| `stock` | `ASML`, `BESI`, `NOVO`, `SMA92` | actions ciblées, matrice 4 × 15 |
| `tech` | `SMH`, `SOXX`, `VGT` | comparateurs semi-conducteurs / tech |

Correspondances :

| Affichage | Yahoo | Devise |
|---|---|---|
| `ASML` | `ASML.AS` | EUR |
| `BESI` | `BESI.AS` | EUR |
| `NOVO` | `NOVO-B.CO` | DKK |
| `SMA92` | `S92.DE` | EUR |
| `BTC` | `BTC-USD` | USD |

`SMA92` est le libellé métier de **SMA Solar Technology AG** ; le symbole Yahoo
est `S92.DE`. `BESI.B` et `NOVO.B` sont des notations de courtier : les symboles
Yahoo correspondants sont `BESI.AS` et `NOVO-B.CO`.

### 1.3 Prix utilisés

On utilise `adj_close` (clôture ajustée des splits et dividendes) et non
`close`. Pour une analyse de corrélation, il faut en effet comparer des
rendements **totaux** : sinon, un détachement de dividende crée une baisse
artificielle qui n'a rien à voir avec le marché.

Si Yahoo ne fournit pas `adjclose` pour une séance, on retombe sur `close` et
on le signale dans les logs. Une séance sans clôture valide est ignorée : on
n'interpole jamais un prix.

### 1.4 Devises

Les ETF sont en USD, `NOVO` en DKK, les autres actions en EUR. Deux bases sont
calculées :

- **`LOCAL`** : rendement dans la devise de cotation. C'est la vue « action »
  au sens strict.
- **`EUR`** (défaut) : conversion en euros, représentative d'un investisseur
  européen. Le rendement inclut donc l'effet de change.

Conversion :

```
P_eur(t) = P_local(t) × eur_per_unit(t)
```

`eur_per_unit` vaut 1 pour EUR, et `1 / EURUSD` ou `1 / EURDKK` pour USD/DKK.
Le taux est joint « as of » : dernier taux connu à la date de séance. Un jour
férié FX ne crée donc pas de trou. Si aucun taux n'est disponible, le rendement
EUR est `NULL` — on n'invente pas un taux à 1.

Pourquoi ne pas tout calculer en USD ? Parce que la question de l'utilisateur
est celle d'un investisseur européen. La vue `LOCAL` reste disponible pour
isoler l'effet action de l'effet change.

---

## 2. Rendements

On calcule des rendements **logarithmiques** :

```
r_t = ln(P_t / P_{t-1})
```

Propriétés utiles :

- additivité temporelle : `r_{t1→t3} = r_{t1→t2} + r_{t2→t3}` ;
- symétrie : une hausse de +10 % puis une baisse de -10 % ne donne pas
  exactement 0 en arithmétique, mais s'en approche en log ;
- base des rendements cumulés : `exp(Σ r_t) - 1`.

C'est la même convention que le script R (`diff(log(adjclose))`), ce qui permet
de comparer les résultats.

Les rendements sont calculés **dans `finance.v_returns`**, sur l'historique
propre de chaque actif. Le premier point de chaque actif n'a pas de rendement
(`NULL`), et n'est jamais compté dans `n_obs`.

---

## 3. Alignement des calendriers

Les marchés US, néerlandais, danois et allemand n'ont pas les mêmes jours
fériés ; Bitcoin cote 7j/7. Deux stratégies sont utilisées, volontairement
différentes.

### 3.1 Matrice de corrélation : paires complètes

Pour chaque paire `(A, B)`, on joint les rendements sur les **dates communes**
et on calcule la corrélation sur ces observations. C'est la méthode
« pairwise complete » :

- pas de prix forward-fillé ;
- pas de rendement nul artificiel les jours où un marché est fermé ;
- `n_obs` peut différer d'une paire à l'autre, et il est affiché.

C'est statistiquement plus propre qu'une grille commune remplie de zéros. Le
prix à payer est que les paires ne portent pas exactement sur le même
échantillon ; c'est pourquoi `n_obs` et les dates de début/fin sont exposés.

### 3.2 Corrélations glissantes : dernières N observations communes

Pour une fenêtre de `N` séances, on prend les `N` dernières observations où
**les deux actifs** ont un rendement, puis on calcule la corrélation. On ne
forward-fill pas le prix : on préfère une fenêtre légèrement plus longue en
temps calendaire à une fenêtre polluée par des zéros.

Conséquence : la fenêtre 126 « séances » peut couvrir un peu plus de six mois
calendaires si l'un des actifs a des jours fériés. C'est acceptable et
documenté.

---

## 4. Corrélation

### 4.1 Pearson

Pour deux séries de rendements `x` et `y` de moyenne `x̄`, `ȳ` :

```
ρ = Σ (x_i - x̄)(y_i - ȳ)
    ─────────────────────────────────────
    sqrt( Σ (x_i - x̄)² × Σ (y_i - ȳ)² )
```

PostgreSQL fournit `corr(x, y)`, qui calcule exactement ce coefficient. Les
covariances utilisent `n-1`, mais le facteur s'annule dans le rapport.

Interprétation :

| Valeur | Lecture |
|---|---|
| proche de +1 | les actifs montent/descendent ensemble |
| proche de 0 | pas de relation linéaire nette |
| proche de -1 | les actifs évoluent en sens opposé |

Une corrélation de 0 ne signifie pas « indépendance » : elle signifie seulement
« pas de relation **linéaire** détectée ». Deux actifs peuvent avoir une
relation non linéaire forte.

### 4.2 Intervalle de confiance

Pour `n > 3`, l'intervalle de confiance à 95 % est calculé par la transformation
de Fisher :

```
z = atanh(ρ)
IC = tanh( z ± 1.96 / sqrt(n - 3) )
```

Il est affiché par `corr_pairs` et `corr_benchmark`. Une corrélation de 0,80
calculée sur 30 observations a un intervalle très large ; la même valeur sur
2 000 observations est beaucoup plus précise. Sans `n_obs`, une corrélation
seule est difficile à interpréter.

### 4.3 Pourquoi Pearson et pas Spearman ?

Pearson mesure la co-mouvement linéaire et est la convention du script R. Il
est sensible aux valeurs extrêmes, mais les rendements quotidiens d'ETF sont
rarement extrêmes au point de fausser tout le résultat. Spearman (corrélation
des rangs) serait plus robuste aux queues épaisses ; il pourra être ajouté plus
tard comme seconde lecture, sans remplacer Pearson.

---

## 5. Évolution dans le temps

### 5.1 Fenêtres glissantes

Trois fenêtres sont proposées :

| Fenêtre | Équivalent approximatif | Usage |
|---|---|---|
| 63 séances | 3 mois | réactivité, mais bruit élevé |
| 126 séances | 6 mois | compromis par défaut |
| 252 séances | 12 mois | régime de long terme, plus stable |

La corrélation glissante est calculée avec `corr(...) OVER (ROWS BETWEEN N-1
PRECEDING AND CURRENT ROW)`. Chaque point est donc une corrélation sur la
fenêtre qui se termine ce jour-là.

**Attention** : les points successifs partagent `N-1` observations. Ils sont
fortement autocorrélés. Une « rupture » visuelle n'est pas un test statistique ;
il faut la confirmer par une méthode dédiée et par le contexte de marché.

### 5.2 Snapshots mensuels

`finance.corr_monthly()` retourne la **dernière** corrélation glissante de
chaque mois. Cela permet de construire un tableau mois × actif sans envoyer
tous les jours au dashboard. Le snapshot est daté du dernier jour de bourse du
mois, pas du premier.

### 5.3 Corrélations annuelles

`finance.corr_by_year()` regroupe les observations par année civile. C'est utile
pour voir les grands régimes :

- 2018 : fin de cycle, tensions commerciales ;
- 2020 : choc COVID, corrélations qui montent brutalement ;
- 2022 : resserrement monétaire, décorrélation actions/obligations ;
- 2023-2025 : rallye technologie/IA, concentration du marché.

Une année avec moins de 20 observations est ignorée par défaut (`p_min_obs`).
Le nombre d'observations est toujours retourné.

### 5.4 Ruptures de régime

Deux implémentations :

1. **`ruptures`** (optionnel) : `Binseg` avec `model="l2"`, `min_size=252`,
   maximum 3 ruptures. On détecte des changements de moyenne dans la série de
   corrélations glissantes.
2. **Segmentation binaire interne** (si `ruptures` absent) : on cherche
   récursivement la coupure qui réduit le plus la somme des carrés des écarts,
   avec un gain minimum de 5 % et un effet minimum de 0,10 sur la moyenne.

Les ruptures sont stockées dans `finance.correlation_breaks` et affichées en
annotations Grafana. Le score est la différence absolue de moyenne entre les
252 observations avant et après la rupture.

Lecture : une rupture signifie « la corrélation moyenne change durablement »,
pas « la relation causale change ». Avec des fenêtres glissantes, la date
détectée est retardée par la fenêtre elle-même (une rupture réelle en janvier
peut apparaître en juillet avec une fenêtre de 252 jours). Il faut en tenir
compte.

---

## 6. Actions vs ETF

### 6.1 Corrélation

La matrice 4 × 15 donne la corrélation de chaque action avec chaque actif core.
Elle répond à la question « l'action bouge-t-elle avec cet ETF ? », mais pas à
« de combien ? ».

### 6.2 Bêta

La bêta est la pente de la régression de l'action sur l'ETF :

```
r_action = α + β × r_etf + ε
β = Cov(r_action, r_etf) / Var(r_etf)
```

- `β > 1` : l'action amplifie les mouvements de l'ETF ;
- `β = 1` : amplitude comparable ;
- `β < 1` : amplitude plus faible ;
- `β < 0` : relation moyenne inverse.

PostgreSQL calcule `β` avec `regr_slope(r_action, r_etf)`.

### 6.3 R²

`R² = ρ²` dans une régression linéaire simple. Il mesure la part de la variance
de l'action expliquée par l'ETF :

- `R² = 0,64` : 64 % de la variance est associée à l'ETF ;
- `R² = 0,04` : 4 % seulement, même si la corrélation peut sembler « moyenne ».

C'est souvent le chiffre le plus parlant : une corrélation de 0,5 donne un R² de
0,25, donc 75 % de la variance reste inexpliquée.

### 6.4 Corrélation conditionnelle en stress

On calcule la corrélation uniquement les jours où le benchmark baisse de plus
de 1 % :

```
ρ_stress = corr(r_action, r_etf | r_URTH < -1 %)
```

On la compare à la corrélation sur toute la période. Beaucoup d'actifs dits
« diversifiants » voient leur corrélation monter dans les baisses. Cette mesure
est plus actionnable pour le risque qu'une corrélation moyenne.

Limite : le seuil -1 % est arbitraire ; `n_stress` est affiché. Sur certaines
périodes, il n'y a que quelques dizaines de jours de stress, d'où un intervalle
de confiance large.

---

## 7. Clustering

Objectif : identifier les groupes d'actifs qui se ressemblent et ordonner les
listes (légendes, tableaux) pour que les actifs proches se suivent, au lieu d'un
ordre alphabétique qui mélange technologies, obligations et or.

- **Distance** : `d(A,B) = 1 - ρ(A,B)`. Deux actifs corrélés à +1 sont à
  distance 0 ; corrélés à -1, à distance 2.
- **Méthode** : agrégation hiérarchique ascendante, **average linkage**. La
  distance entre deux clusters est la moyenne des distances entre leurs
  membres.
- **Nombre de clusters** : `FINANCE_CLUSTER_K`, 5 par défaut.
- **Ordre** : parcours des feuilles du dendrogramme ; `display_order` est
  recalculé dans `finance.assets`.

La matrice Grafana conserve l'ordre de référence (XLK, XLV, …) pour que les
lignes et les colonnes coïncident : une matrice de corrélation doit être lisible
en diagonale. Le clustering est donc visible dans le panneau « Groupes de
corrélation » et dans l'ordre des listes, mais il ne réordonne pas les colonnes
de la matrice. Réordonner dynamiquement les colonnes demanderait de générer une
vue SQL à colonnes variables après chaque clustering ; ce n'est pas fait dans
la v1, car Grafana ne sait pas pivoter une matrice catégorielle.

Le clustering est un **outil de lecture**, pas une classification officielle.
Avec des ETF sectoriels US, un facteur marché très fort peut regrouper 15 actifs
dans un même cluster ; c'est un résultat, pas un bug. Les clusters changent avec
la période, la devise et le nombre `k`.

Pour aller plus loin, on pourrait clusteriser les **résidus** d'une régression
sur URTH (corrélations partielles), ce qui ferait ressortir les proximités
sectorielles au-delà du marché. Ce n'est pas implémenté dans la v1.

---

## 8. Événements et annotations

`finance.events` contient quelques repères macro (COVID, hausses de taux, SVB,
rallye IA, etc.). Ils sont affichés en annotations Grafana pour aider à lire
les régimes. La liste est **indicative et non exhaustive** ; elle ne sert pas de
preuve causale.

Les ruptures de corrélation sont également annotées. Trop d'annotations nuit à
la lecture : si besoin, filtrer par actif ou désactiver l'annotation.

---

## 9. Alertes

Trois alertes sont documentées dans [`grafana/ALERTES.md`](../grafana/ALERTES.md) :

1. **Données de marché trop anciennes** : `URTH` > 5 jours.
2. **Pipeline non exécuté** : dernier import réussi > 36 h.
3. **Changement de régime** : variation de corrélation glissante > seuil.

Les deux premières sont aussi vérifiées par `scripts/verify_freshness.py`, donc
par Jenkins. La troisième est une alerte Grafana, à calibrer selon l'actif.

---

## 10. Limites et pièges d'interprétation

1. **Corrélation ≠ causalité.** Deux actifs peuvent être corrélés parce qu'ils
   partagent un facteur (marché, taux, dollar, pétrole) ou par coïncidence.
2. **Les ETF SPDR sont américains.** `XLV` ne contient pas nécessairement Novo
   Nordisk ; `XLE` ne contient pas SMA Solar. Une corrélation faible avec ces
   ETF peut simplement signifier « pas dans l'indice », pas « pas de lien
   économique ».
3. **Chevauchement des fenêtres.** Les corrélations glissantes ne sont pas des
   observations indépendantes. Les tests de significativité classiques ne
   s'appliquent pas directement.
4. **Tests multiples.** Une matrice 15 × 15 contient 105 paires. À 5 %, environ
   5 paires peuvent sembler significatives par hasard. Ne pas surinterpréter
   une valeur isolée.
5. **Régimes.** Une corrélation pleine période peut masquer deux régimes
   opposés. Toujours regarder les sous-périodes et les fenêtres glissantes.
6. **Devise.** En EUR, la corrélation d'un actif US avec un actif européen
   inclut l'effet EUR/USD. Comparer `EUR` et `LOCAL` permet de le voir.
7. **Bitcoin 24/7.** Les rendements BTC incluent les week-ends ; la matrice
   utilise les dates communes avec les autres actifs, ce qui exclut une partie
   de l'information. Les résultats BTC sont donc à lire avec prudence.
8. **Données révisables.** Yahoo peut corriger un historique. L'ETL remplace
   tout l'historique, mais une analyse exportée peut ne pas être reproductible
   à la virgule près.
9. **Survivorship bias.** On ne suit que des actifs existants aujourd'hui ; les
   ETF/actions disparus ne sont pas représentés.
10. **Ruptures = descriptive.** La détection de ruptures est un algorithme, pas
    un test d'hypothèse. Elle produit des candidats à examiner.

---

## 11. Reproductibilité et comparaison avec R

Pour comparer avec `correlations_etf.R` :

1. charger les mêmes 15 actifs ;
2. se placer sur la même période (2020-01-01 → aujourd'hui) ;
3. utiliser `basis = LOCAL` pour se rapprocher du script R, qui ne convertit
   pas en EUR ;
4. comparer les matrices avec une tolérance de ±0,01.

Les écarts possibles viennent :

- de la source exacte (`yfhist` vs endpoint Yahoo) ;
- des dates de séance et des jours fériés ;
- de la gestion des `NULL` et des dates communes ;
- des révisions de clôtures ajustées.

Un écart plus important doit être investigué (actif manquant, mauvaise devise,
doublon de date).
