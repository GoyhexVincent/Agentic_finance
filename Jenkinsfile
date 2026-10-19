pipeline {
    agent any

    options {
        buildDiscarder(logRotator(numToKeepStr: "30"))
        timeout(time: 20, unit: "MINUTES")
        disableConcurrentBuilds()
    }

    parameters {
        booleanParam(
            name: "FORCE_IMPORT",
            defaultValue: false,
            description: "Force la réimportation même si le garde-fou de fréquence " +
                         "(FINANCE_MIN_IMPORT_INTERVAL_HOURS = 18 h) s'y oppose."
        )
    }

    // Le cron Jenkins utilise le fuseau du serveur. Configurer Jenkins en
    // Europe/Paris (ou ajuster l'heure) pour viser 06:00 après la clôture US.
    triggers {
        cron("H 6 * * *")
    }

    environment {
        PGHOST     = "localhost"
        PGPORT     = "5432"
        PGDATABASE = "marine_database"
        PGUSER     = "finance"
        PGPASSWORD = credentials("pg-password-finance")

        FINANCE_SCHEMA                   = "finance"
        FINANCE_START_DATE               = "2015-01-01"
        FINANCE_MIN_OBS                  = "60"
        FINANCE_MIN_IMPORT_INTERVAL_HOURS = "18"
        FINANCE_CLUSTER_K                = "5"
        FINANCE_BREAK_WINDOW             = "126"

        // Les contrôles de fraîcheur sont volontairement tolérants le lundi
        // et après un jour férié. Le benchmark doit rester plus frais.
        MAX_BENCHMARK_AGE_DAYS = "5"
        MAX_ASSET_AGE_DAYS     = "10"
        MAX_IMPORT_AGE_HOURS   = "36"

        TZ = "Europe/Paris"
    }

    stages {
        stage("Setup Python Environment") {
            steps {
                sh """
                    python3 -m venv .venv
                    . .venv/bin/activate
                    pip install --upgrade pip
                    pip install -r requirements.txt
                """
            }
        }

        stage("Unit Tests") {
            steps {
                sh """
                    . .venv/bin/activate
                    python -m unittest discover -s tests -v
                """
            }
        }

        stage("Run Finance ETL") {
            steps {
                sh """
                    . .venv/bin/activate
                    FINANCE_FORCE_IMPORT=${params.FORCE_IMPORT ? '1' : '0'} \
                        python -m finance_tracker.etl
                """
            }
        }

        stage("Verify Freshness & SQL") {
            steps {
                sh """
                    . .venv/bin/activate
                    python scripts/verify_freshness.py
                """
            }
        }
    }

    post {
        always {
            cleanWs(cleanWhenNotBuilt: false, deleteDirs: true)
        }
        success {
            echo "✅ Données de marché mises à jour, corrélations recalculées."
        }
        unstable {
            echo "⚠️  Import partiel : certains actifs ont échoué, les autres sont à jour."
        }
        failure {
            echo "❌ Échec du pipeline : consulter les logs du stage rouge " +
                 "(réseau Yahoo, credentials PostgreSQL, schéma finance)."
        }
    }
}
