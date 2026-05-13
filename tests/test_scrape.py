import csv
import json
from pathlib import Path

from click.testing import CliRunner

from bt_web_report_cli.__main__ import main


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "test-files" / "phpp"
VANDAM_FIXTURE = FIXTURE_DIR / "2606-29-Vandam-St-260506.xlsx"
LINDE_FIXTURE = FIXTURE_DIR / "2524-Linde-Residence-250709.xlsx"


def test_scrape_fixture_writes_manifest_and_variants_csv(tmp_path: Path) -> None:
    output_dir = tmp_path / "data"
    runner = CliRunner()

    result = runner.invoke(main, ["scrape", str(VANDAM_FIXTURE), "--out", str(output_dir)])

    assert result.exit_code == 0, result.output
    assert "scraped PHPP 10.6: 5 variants, recommended=enerphit_by_demand" in result.output

    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert manifest["phpp_version"] == "10.6"
    assert manifest["recommended_variant_id"] == "enerphit_by_demand"
    assert [variant["id"] for variant in manifest["variants"]] == [
        "code_minimum",
        "improved_envelope",
        "improved_hvac",
        "enerphit_by_component",
        "enerphit_by_demand",
    ]
    assert manifest["variants"][-1]["recommended"] is True
    assert manifest["source_workbook"]["size_bytes"] > 1_000_000

    rows = list(csv.DictReader((output_dir / "variants.csv").open()))
    tfa = [row for row in rows if row["field_id"] == "geometry.tfa"]
    assert len(tfa) == 5
    assert tfa[0]["variant_id"] == "code_minimum"
    assert tfa[0]["units"] == "m2"
    assert float(tfa[0]["value"]) == 290.2644471258237


def test_scrape_linde_fixture_uses_dynamic_r_value_labels(tmp_path: Path) -> None:
    output_dir = tmp_path / "data"
    runner = CliRunner()

    result = runner.invoke(main, ["scrape", str(LINDE_FIXTURE), "--out", str(output_dir)])

    assert result.exit_code == 0, result.output
    assert "scraped PHPP 10.6: 5 variants, recommended=as_drawn" in result.output

    rows = list(csv.DictReader((output_dir / "variants.csv").open()))
    r_value_rows = [row for row in rows if row["section"] == "r_values" and row["variant_id"] == "as_drawn"]
    r_value_ids = {row["field_id"] for row in r_value_rows}
    r_value_labels = {row["phpp_label"] for row in r_value_rows}

    assert "r_values.assembly_01" in r_value_ids
    assert "r_values.r_values" not in r_value_ids
    assert "r_values.assembly_02" not in r_value_ids
    assert "r_values.assembly_04" not in r_value_ids
    assert "01ud-c-W-CS - Crawlspace" in r_value_labels
    assert "06ud-g-R-FL - Flat" in r_value_labels
    assert "02ud-" not in r_value_labels


def test_scrape_unknown_version_fails_loudly(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "scrape",
            str(VANDAM_FIXTURE),
            "--out",
            str(tmp_path / "data"),
            "--phpp-version",
            "10.7",
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported PHPP version '10.7'" in result.output
