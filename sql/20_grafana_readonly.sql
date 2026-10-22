-- ═══════════════════════════════════════════════════════════════════════════
--  Finance Tracker — rôle Grafana en lecture seule
--
--  À exécuter par un superutilisateur (postgres), après sql/10_schema.sql.
--  Le script est idempotent : il crée le rôle s'il manque, met à jour son
--  mot de passe et répare les droits.
--
--  Exemple :
--      psql -U postgres -d postgres \
--           -v grafana_password='...' \
--           -v dbname='finance_db' \
--           -f sql/20_grafana_readonly.sql
--
--  Le rôle ne peut que lire : pas de CREATE sur le schéma, pas d'écriture
--  sur les tables, et default_transaction_read_only verrouille toute
--  transaction en écriture même si un GRANT est ajouté par erreur plus tard.
-- ═══════════════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on

\if :{?dbname}
\else
  \set dbname finance_db
\endif

\if :{?grafana_password}
\else
  \echo 'ERREUR : fournir -v grafana_password=...'
  \quit 1
\endif

-- ───────────────────────────── Rôle de lecture ─────────────────────────────
SELECT format('CREATE ROLE grafana LOGIN PASSWORD %L', :'grafana_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana')
\gexec

ALTER ROLE grafana LOGIN PASSWORD :'grafana_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- Ceinture et bretelles : même un GRANT d'écriture accidentel échouera.
ALTER ROLE grafana SET default_transaction_read_only = on;

-- ───────────────────────────── Droits sur la base ──────────────────────────
\connect :dbname

GRANT CONNECT ON DATABASE :dbname TO grafana;

GRANT USAGE ON SCHEMA finance TO grafana;
REVOKE CREATE ON SCHEMA finance FROM grafana;

GRANT SELECT ON ALL TABLES IN SCHEMA finance TO grafana;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA finance TO grafana;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA finance TO grafana;

-- Retire explicitement les droits d'écriture, y compris sur les objets
-- existants si le rôle a été utilisé comme rôle applicatif par le passé.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA finance FROM grafana;

-- Objets créés plus tard par le rôle propriétaire `finance` (vues, fonctions,
-- tables de résultats) : Grafana doit les lire sans intervention manuelle.
ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
    GRANT SELECT ON TABLES TO grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
    GRANT SELECT ON SEQUENCES TO grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE finance IN SCHEMA finance
    GRANT EXECUTE ON FUNCTIONS TO grafana;

-- ───────────────────────────── Vérification ────────────────────────────────
\echo 'Droits effectifs du rôle grafana :'

SELECT
    has_database_privilege('grafana', current_database(), 'CONNECT') AS can_connect,
    has_schema_privilege('grafana', 'finance', 'USAGE')             AS can_use_schema,
    has_schema_privilege('grafana', 'finance', 'CREATE')            AS can_create_in_schema,
    has_table_privilege('grafana', 'finance.prices_daily', 'SELECT') AS can_read_prices,
    has_table_privilege('grafana', 'finance.prices_daily', 'INSERT') AS can_write_prices;

\echo 'Attendu : can_connect=t, can_use_schema=t, can_create_in_schema=f,'
\echo '          can_read_prices=t, can_write_prices=f'
