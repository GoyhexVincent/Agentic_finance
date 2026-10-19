from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_monte_carlo_entrypoint(tmp_path) -> None:
    output = tmp_path / "mc"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "monte_carlo_sma92.py"),
            "--paths",
            "20000",
            "--days",
            "30",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "Simulation Monte Carlo" in result.stdout
    assert (output / "mc_report.md").exists()
    assert (output / "mc_report.json").exists()
    payload = json.loads((output / "mc_report.json").read_text(encoding="utf-8"))
    assert payload["config"]["n_paths"] == 20000
    assert len(payload["option_prices"]) == 2


def test_saxo_dry_run_entrypoint(tmp_path) -> None:
    output = tmp_path / "saxo"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "saxo_options_7d.py"),
            "--dry-run",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "Options SMA92" in result.stdout
    assert (output / "report.md").exists()
    assert (output / "summary.json").exists()

    with (output / "options_chain_7d.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 20
    assert all(float(row["iv"]) > 0 for row in rows)

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["synthetic"] is True
    assert summary["data_quality"]["contracts"] == 20
    assert summary["expiries"][0]["atm_iv"] is not None


def test_saxo_dry_run_can_skip_history(tmp_path) -> None:
    output = tmp_path / "saxo_no_history"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "saxo_options_7d.py"),
            "--dry-run",
            "--history-scope",
            "none",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    # La fixture contient déjà des historiques ; le mode dry-run ne les supprime pas.
    assert summary["data_quality"]["with_history"] == 10
