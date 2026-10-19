# Données

## `sma_solar_daily.csv`

Clôtures quotidiennes de **SMA Solar Technology AG** (`S92.DE`, Xetra), arrêtées au **19 octobre 2026**.

- 253 observations, du 20 octobre 2025 au 19 octobre 2026.
- Source : API publique Yahoo Finance, `S92.DE`, intervalle `1d`.
- Les prix sont des clôtures ajustées telles que retournées par la source ; les splits/dividendes doivent être vérifiés avant un usage de production.
- Ce fichier sert à calibrer la volatilité historique et à rendre les exemples reproductibles. Il ne remplace pas un flux market data Saxo.

Pour rafraîchir le fichier, remplacer les lignes en conservant l'en-tête `date,close`. Le chargeur ignore les lignes commençant par `#`.

## `fixtures/saxo_sim_snapshot.json`

Snapshot **entièrement synthétique** destiné au mode `--dry-run` et aux tests.

- 20 contrats, 2 échéances, 5 strikes, calls et puts ;
- prix calculés avec un sourire de volatilité simple puis élargis d'un spread ;
- 10 historiques de contrats sur 5 séances ;
- volumes, open interest et grecques fabriqués.

Les prix, les signaux et les variations d'IV de ce fichier ne décrivent **pas** le marché. Ils servent uniquement à vérifier que la chaîne de traitement fonctionne sans jeton Saxo.
