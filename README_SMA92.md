# SMA92 Options Lab

Laboratoire Python pour **modéliser SMA92**, suivre sa chaîne d'options sur **Saxo Bank SIM** et repérer des écarts à investiguer.

> **Hypothèse de travail importante** : aucune action cotée ne porte le code `SMA92` chez Saxo. Le rapprochement le plus plausible est **SMA Solar Technology AG**, dont le code est **S92** sur Xetra (`S92.DE`). Le projet utilise donc `SMA92` comme libellé de travail et `S92` comme instrument Saxo. Si vous visiez un autre actif, changez `--symbol`, `--keywords`, `--uic` et le CSV de prix.

> **Ce n'est pas un conseil en investissement.** Les prix Saxo SIM peuvent être simulés, retardés ou incomplets. Le mode `--dry-run` utilise une fixture **synthétique** et ne décrit pas le marché réel.

## Ce que le dépôt contient

| Fichier | Rôle |
|---|---|
| `monte_carlo_sma92.py` | Point d'entrée de la simulation Monte Carlo du sous-jacent et des options |
| `saxo_options_7d.py` | Point d'entrée Saxo : chaîne d'options courante + historique 7 jours |
| `src/sma92/` | Moteur Black-Scholes, Monte Carlo, client REST, analyse, rapports |
| `data/sma_solar_daily.csv` | 253 clôtures quotidiennes S92.XETR, du 2025-10-20 au 2026-10-19 |
| `data/fixtures/saxo_sim_snapshot.json` | Fixture synthétique pour tester sans token |
| `data/output/` | Rapports et CSV générés (ignorés par Git) |
| `tests/` | 33 tests unitaires et d'intégration |

## Démarrage rapide

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt  # pytest inclus

# 1) Simulation Monte Carlo, avec le CSV historique fourni
python monte_carlo_sma92.py

# 2) Pipeline Saxo sans réseau ni token
python saxo_options_7d.py --dry-run

# 3) Tests
python -m pytest -q
```

Les dépendances runtime sont seulement `numpy` et `requests`. Les rapports sont en Markdown/CSV/JSON, sans dépendance à `pandas` ou `matplotlib`.

## Résultat déjà produit

Avec le snapshot du **19 octobre 2026** :

- spot S92 : **55,70 €** ;
- volatilité réalisée 60 j : **53,4 %** (20 j : 55,8 %, 252 j : 68,3 %) ;
- horizon 30 jours, taux 2 %, sans dividende ;
- 200 000 trajectoires GBM, variables antithétiques.

Résultat de la simulation :

| Indicateur | Valeur |
|---|---:|
| Médiane terminale | 55,14 € |
| Intervalle 5 %–95 % | 42,88 € – 70,91 € |
| Call K=55,50, 30 j | 3,53 € (IC95 3,51–3,56) |
| Put K=55,50, 30 j | 3,25 € (IC95 3,23–3,26) |
| Contrôle Black-Scholes | 3,54 € / 3,25 € |

Le call et le put sont donnés **par action**. Avec une taille de contrat de 100, la prime d'un contrat vaut environ 353 € pour le call et 325 € pour le put, hors frais et hors spread.

Ces chiffres sont un **prix de modèle**, pas une prévision. Le vrai test consiste à comparer ce prix à un mid Saxo et à regarder la volatilité implicite correspondante.

## Simulation Monte Carlo

### Modèles disponibles

| Modèle | Usage | Limite |
|---|---|---|
| `gbm` (défaut) | Référence rapide, prix européen exact en loi terminale | Volatilité constante, queues gaussiennes, pas de sauts |
| `bootstrap` | Rééchantillonne les rendements historiques standardisés | Conserve mieux skew/queues, mais reste historique et non risque-neutre |
| `student-t` | Queues plus épaisses, variance finie si `df > 2` | Symétrique, pas de skew |

Exemples :

```bash
# Changer l'horizon, le taux et plusieurs strikes
python monte_carlo_sma92.py \
  --days 60 --rate 0.025 --strikes 45,50,55,60,65

# Bootstrap historique
python monte_carlo_sma92.py --model bootstrap --paths 300000

# Student-t à 5 degrés de liberté
python monte_carlo_sma92.py --model student-t --student-t-df 5
```

La volatilité par défaut est la **vol réalisée 60 jours** du CSV. On peut forcer `--vol 0.55` ou utiliser `--spot`. Les sorties vont dans `data/output/monte_carlo/` :

- `mc_report.md` : rapport lisible ;
- `mc_report.json` : paramètres, distribution, prix, grecques ;
- `mc_prices.csv` : prix par strike et par type ;
- `mc_terminal_quantiles.csv` : quantiles de la distribution terminale.

### Précision

L'intervalle de confiance est calculé par **moyennes de blocs**, avec une correction conservatrice, plutôt que par le simple `std/sqrt(n)`. Avec des variables antithétiques, les tirages ne sont pas indépendants ; cette approche évite de surestimer la précision.

Le nombre de pas par défaut est de `252 × jours / 365`. Pour une option européenne, le résultat terminal ne dépend pas du découpage en pas ; les pas servent surtout si l'on ajoute ensuite des barrières, des options américaines ou un contrôle de risque en cours de vie.

## Connexion Saxo SIM

### 1. Obtenir un jeton

1. Créer une application **SIM** sur le portail développeur Saxo.
2. Se connecter au [portail token 24 h](https://www.developer.saxo/openapi/token).
3. Copier le jeton dans un fichier local :

```bash
cp .env.example .env
# éditer .env et renseigner SAXO_ACCESS_TOKEN
```

Le jeton 24 h du portail est **uniquement valable en SIM**. Pour le live, il faut un flux OAuth avec `AppKey`/`AppSecret`, hors périmètre de cette première version.

### 2. Lancer le radar

```bash
python saxo_options_7d.py --symbol SMA92 --keywords "SMA Solar" --days 7
```

Le script :

1. cherche l'action et vérifie l'UIC ;
2. lit les détails de l'instrument et les `RelatedOptionRootsEnhanced` ;
3. récupère l'espace d'options `contractoptionspaces/{root}` ;
4. garde les échéances et strikes pertinents ;
5. récupère les quotes, grecques et open interest via `infoprices` ;
6. récupère un OHLC quotidien par contrat via `chart` pour les 7 derniers jours ;
7. calcule les IV, le VRP, la structure par terme, le skew et des écrans ;
8. écrit les CSV, un `summary.json` et un `report.md`.

Si la résolution est ambiguë, forcer l'instrument :

```bash
python saxo_options_7d.py --uic 1234567 --option-root-id 98765
```

Les sorties vont dans `data/output/saxo_options/` :

| Fichier | Contenu |
|---|---|
| `report.md` | Synthèse lisible |
| `summary.json` | Données structurées, qualité, signaux |
| `options_chain_7d.csv` | Chaîne courante, IV, spread, grecques, flags |
| `expiry_summary.csv` | IV ATM, VRP, structure par terme |
| `signals.csv` | Écrans heuristiques et avertissements |
| `underlying_7d.csv` | OHLC et rendements du sous-jacent |
| `option_history_7d.csv` | OHLC par contrat et IV recalculée |

### 3. Pourquoi ne pas utiliser directement `/trade/v1/optionschain` ?

L'endpoint `optionschain` est une **souscription** temps réel avec un contexte de streaming et une fenêtre de strikes. Pour un premier radar quotidien, le couple suivant est plus simple et suffisant :

- `GET /ref/v1/instruments/contractoptionspaces/{root}` pour la structure et les UIC ;
- `GET /trade/v1/infoprices` pour le snapshot de prix ;
- `GET /chart/v3/charts` pour l'historique par contrat.

Cette approche évite de maintenir un WebSocket et un `ContextId`. En revanche, elle ne remplace pas un vrai carnet temps réel pour l'exécution.

## Limites de l'API Saxo à connaître

1. **Pas d'endpoint “chaîne d'options historique” en une requête.** On reconstruit la chaîne *actuelle*, puis on interroge chaque contrat pour les 7 derniers jours. Les contrats déjà expirés ne sont plus dans l'espace courant : ils manqueront.
2. **Les données SIM ne sont pas une source de backtest de production.** La documentation Saxo indique que certaines fonctionnalités, dont le market data, ne sont pas disponibles en SIM. Les prix peuvent être simulés, retardés ou absents.
3. **Un OHLC n'est pas un carnet.** Un close sans volume peut être un report de prix. Les IV calculées sur des mids peu liquides sont fragiles.
4. **Le 24 h token est SIM only.** Le live exige OAuth, des entitlements de market data et une gestion de refresh token.
5. **Les grecques peuvent manquer** selon l'échange et le contrat. Le skew 25 delta est alors indisponible.
6. **Le contrat peut avoir une taille et un facteur de prix différents.** Le script utilise `ContractSize` de l'espace d'options ; vérifier `PriceToContractFactor` chez Saxo avant de convertir en valeur de contrat.

## Comment suivre le marché des options et chercher des opportunités

### Les cinq indicateurs à suivre en priorité

1. **IV ATM vs vol réalisée 20 j (VRP)**
   Si l'IV est durablement au-dessus de la RV, le marché paie une prime de variance. C'est un candidat pour des stratégies à risque défini (credit spreads, iron condors), pas pour vendre des options nues.
2. **Structure par terme**
   Un front plus cher que le back signale souvent un événement daté (résultats, décision tarifaire, guidance). Une courbe en contango est plus normale.
3. **Skew 25 delta**
   Un skew baissier très riche renchérit la protection ; un skew faible peut rendre les puts relativement bon marché. Les deux extrêmes sont à confronter au calendrier et à la liquidité.
4. **Liquidité**
   Spread bid/ask, open interest, volume, taille disponible. Une IV “intéressante” sur un contrat à 20 % de spread n'est pas exploitable.
5. **Calendrier d'événements**
   Résultats, guidance, décisions de taux, annonces sectorielles. Une IV élevée avant un événement n'est pas forcément une anomalie.

### Écrans concrets à ajouter

- **IV rank / percentile** sur 6 à 12 mois, par échéance et par delta, plutôt qu'une simple comparaison à la RV.
- **Straddle break-even vs mouvement historique** : comparer le prix du straddle ATM au mouvement absolu médian sur des horizons comparables.
- **Skew put/call 25 delta** et son percentile historique.
- **Term structure slope** front/back, avec détection d'inversion.
- **Open interest et volume relatifs** : OI en hausse + volume anormal + spot proche d'un strike = zone à surveiller.
- **Put/call ratio** par échéance, en niveau et en variation.
- **Écart entre IV et realized vol future** (nécessite d'attendre l'échéance : c'est le seul vrai test de calibration).
- **Coût de portage** : taux, dividende, borrow, fiscalité, frais de courtage.
- **Risque de pin / assignment** à l'approche de l'échéance, surtout sur les options américaines.

### Ce qu'il faut stocker dès maintenant

Pour rendre ces écrans utiles, il faut une base historique **quotidienne** :

```text
date, underlying_uic, option_uic, expiry, strike, kind,
bid, ask, mid, last, volume, open_interest,
delta, gamma, vega, theta, iv, underlying_spot, source
```

Un simple stockage append-only en CSV/Parquet ou DuckDB suffit au début. Sans historique, on ne peut pas calculer de percentile ni de z-score ; on ne peut que comparer le jour courant à la vol réalisée.

### Pistes de modélisation

- Remplacer GBM par **Heston** ou un modèle à sauts (Merton, Bates) pour mieux capter le smile.
- Calibrer une **surface SVI** par échéance, puis repricer le smile et détecter les points aberrants.
- Intégrer une **volatilité locale** ou des sauts pour les événements.
- Estimer le drift avec prudence : pour le pricing, rester en mesure risque-neutre ; pour la prévision, distinguer clairement monde réel et monde risque-neutre.
- Ajouter les **options américaines** (exercice anticipé) et les dividendes discrets.
- Backtester avec **bid/ask**, slippage et frais, pas avec les mids.

### Pistes d'ingénierie

- OAuth Authorization Code + PKCE, stockage chiffré du refresh token, rotation.
- Cache local des instruments et des `OptionRootId`.
- Rate limiting et reprise sur 429/5xx (déjà présent), journalisation des appels.
- Validation de schéma des réponses Saxo et alertes si un champ disparaît.
- Orchestration quotidienne (cron) + snapshot append-only.
- Dashboard (Streamlit/Metabase) : spot, IV ATM, VRP, skew, term slope, OI, volume, événements.
- Paper trading : journal des ordres, prix d'exécution simulés, PnL, Greeks agrégés, limites de risque.

## Structure du code

```text
monte_carlo_sma92.py          # CLI simulation
saxo_options_7d.py            # CLI Saxo
src/sma92/
  black_scholes.py            # pricing, IV, grecques, volatilité
  simulation.py               # Monte Carlo GBM/bootstrap/Student-t
  market_data.py              # CSV, dates, volatilités
  saxo.py                     # client REST, résolution, snapshot
  analysis.py                 # IV, VRP, skew, signaux
  fixture.py                  # mode dry-run
  cli_mc.py                   # rapport Monte Carlo
  cli_saxo.py                 # rapport options
tests/                        # 33 tests
data/
  sma_solar_daily.csv
  fixtures/saxo_sim_snapshot.json
```

## Commandes utiles

```bash
# Vérifier le rendu sans token
python saxo_options_7d.py --dry-run --quiet

# Forcer un univers plus large
python saxo_options_7d.py --max-expiries 6 --max-days 180 --max-contracts 150

# Ne pas récupérer l'historique par contrat (plus rapide)
python saxo_options_7d.py --history-scope none

# Simulation avec un autre spot / une autre vol
python monte_carlo_sma92.py --spot 60 --vol 0.50 --days 45

# Tests
python -m pytest -q
```

## Sources et documentation

- [Saxo — environnements SIM/LIVE](https://www.developer.saxo/openapi/learn/environments)
- [Saxo — options chain](https://developer.saxobank.com/openapi/learn/options-chain)
- [Saxo — contract options spaces](https://www.developer.saxo/openapi/referencedocs/ref/v1/instruments/get__ref__contractoptionspaces_optionrootid)
- [Saxo — chart data](https://www.developer.saxo/openapi/referencedocs/chart/v3/charts/get__chart)
- [Saxo — token 24 h](https://www.developer.saxo/openapi/token)
- [Saxo — fiche S92](https://www.home.saxo/fr-fr/markets/stocks/s92-xetr)
- [Yahoo Finance — S92.DE](https://finance.yahoo.com/quote/S92.DE) (source du CSV historique)

## Avertissement

Ce dépôt est un outil de recherche et de paper trading. Il ne constitue ni une recommandation d'investissement, ni une garantie de liquidité, ni un système de trading prêt à l'emploi. Les options peuvent entraîner une perte totale de la prime, et les stratégies à découvert un risque bien supérieur. Vérifier chaque instrument, chaque coût et chaque taille de contrat avant tout ordre.
