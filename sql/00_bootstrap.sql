-- ═══════════════════════════════════════════════════════════════════════════
--  Finance Tracker — bootstrap PostgreSQL
--
--  À exécuter UNE FOIS par un superutilisateur (rôle postgres), depuis la
--  base postgres. Le script est idempotent : il peut être rejoué pour changer
--  le mot de passe ou réparer les droits.
--
--  Exemple :
--      psql -U postgres -d postgres \
--           -v finance_password='un_mot_de_passe_solide' \
--           -v dbname='marine_database' \
--           -f sql/00_bootstrap.sql
--
--  Le mot de passe n'est jamais stocké dans le dépôt. Utiliser un gestionnaire
--  de secrets ou une variable d'environnement pour le passer à psql.
--
--  Résultat :
--    * rôle propriétaire "finance" (LOGIN, sans SUPERUSER) ;
--    * base cible existante (ou créée) ;
--    * schéma "finance" appartenant à "finance" ;
--    * droit de lecture pour le rôle "grafana" s'il existe, y compris pour les
--      objets créés plus tard (ALTER DEFAULT PRIVILEGES).
-- ═══════════════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on

-- Valeurs par défaut surchargeables avec -v dbname=... -v finance_password=...
\if :{?dbname}
\else
  \set dbname marine_database
\endif

\if :{?finance_password}
\else
  \echo 'ERREUR : fournir -v finance_password=...'
  \quit 1
\endif

-- ───────────────────────────── Rôle applicatif ─────────────────────────────
SELECT format('CREATE ROLE finance LOGIN PASSWORD %L', :'finance_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finance')
\gexec

-- Si le rôle existait déjà, on met à jour le mot de passe sans toucher aux
-- autres attributs. On s'assure aussi qu'il n'est pas superutilisateur.
ALTER ROLE finance LOGIN PASSWORD :'finance_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- ───────────────────────────── Base cible ─────────────────────────────
-- La base est créée si elle n'existe pas. En production, "marine_database"
-- existe déjà (projet NDBC) : le bloc est alors un no-op.
SELECT format('CREATE DATABASE %I OWNER postgres', :'dbname')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname')
\gexec

\connect :dbname

-- Le rôle doit pouvoir se connecter et créer des objets dans SON schéma.
-- On ne lui donne pas CREATE sur public : le schéma finance est son espace.
GRANT CONNECT ON DATABASE :dbname TO finance;

-- ───────────────────────────── Schéma dédié ─────────────────────────────
CREATE SCHEMA IF NOT EXISTS finance AUTHORIZATION finance;
ALTER SCHEMA finance OWNER TO finance;
REVOKE ALL ON SCHEMA finance FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA finance TO finance;

-- ───────────────────────────── Lecture Grafana ─────────────────────────────
-- Le rôle grafana est probablement déjà créé pour NDBC/DVF. S'il n'existe
-- pas, on l'ignore : le dashboard ne pourra pas se connecter tant qu'il n'est
-- pas créé, mais le reste du pipeline fonctionne.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana') THEN
        RAISE NOTICE 'Rôle "grafana" absent : droits de lecture non posés.';
        RETURN;
    END IF;

    EXECUTE format('GRANT CONNECT ON DATABASE %I TO grafana', current_database());
    EXECUTE 'GRANT USAGE ON SCHEMA finance TO grafana';
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA finance TO grafana';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance '
            'GRANT SELECT ON TABLES TO grafana';
    RAISE NOTICE 'Droits de lecture accordés à "grafana" sur le schéma finance.';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Droits Grafana non posés : %', SQLERRM;
END $$;

\echo 'Bootstrap terminé.'
\echo '  Base    :' :dbname
\echo '  Schéma  : finance (propriétaire : finance)'
\echo '  Lecture : grafana (si le rôle existe)'
