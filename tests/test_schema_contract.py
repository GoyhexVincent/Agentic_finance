from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "sql" / "10_schema.sql").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "sql" / "00_bootstrap.sql").read_text(encoding="utf-8")


class TestSchemaContract(unittest.TestCase):
    def test_tables_exist(self):
        for table in (
            "assets",
            "prices_daily",
            "fx_daily",
            "ingestion_runs",
            "events",
            "correlation_breaks",
        ):
            self.assertRegex(
                SCHEMA,
                rf"CREATE TABLE IF NOT EXISTS finance\.{table}\b",
                f"table manquante : {table}",
            )

    def test_functions_exist(self):
        for function in (
            "corr_pairs",
            "corr_benchmark",
            "corr_by_year",
            "rolling_corr",
            "rolling_corr_all",
            "corr_monthly",
            "beta_vs_benchmark",
            "stress_corr",
            "corr_matrix_core",
            "corr_matrix_stocks",
            "corr_matrix_tech",
        ):
            self.assertRegex(
                SCHEMA,
                rf"CREATE OR REPLACE FUNCTION finance\.{function}\b",
                f"fonction manquante : {function}",
            )

    def test_bootstrap_is_idempotent_and_protected(self):
        self.assertIn("WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finance')", BOOTSTRAP)
        self.assertIn("REVOKE ALL ON SCHEMA finance FROM PUBLIC", BOOTSTRAP)
        self.assertIn("ALTER DEFAULT PRIVILEGES FOR ROLE finance", BOOTSTRAP)
        self.assertNotIn("PASSWORD '", BOOTSTRAP, "aucun mot de passe en clair attendu")

    def test_schema_has_no_hardcoded_password(self):
        self.assertNotIn("PGPASSWORD", SCHEMA)


if __name__ == "__main__":
    unittest.main()
