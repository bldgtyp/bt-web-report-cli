import csv
import os
from pathlib import Path

import pytest

from bt_web_report_cli.phpp.transform import build_derived_tables
from bt_web_report_cli.phpp.write import DERIVED_REPORT_CSV_TABLES, csv_bytes

FIXTURE_DIR = Path(os.environ.get("BTWR_TEST_PHPP_DIR", Path(__file__).resolve().parents[2] / "test-files" / "phpp"))
FIXTURE_PROJECTS = {
    "vandam": "2606-Vandam-St",
    "linde": "2524-Linde-Residence",
}


@pytest.mark.parametrize("fixture_name", ["vandam", "linde"])
def test_golden_variants_transform_to_derived_csvs(fixture_name: str) -> None:
    fixture_dir = FIXTURE_DIR / FIXTURE_PROJECTS[fixture_name] / "scrape-output"
    variants_path = fixture_dir / "variants.csv"
    if not variants_path.exists():
        pytest.skip(f"Scrape-output fixture is not available: {variants_path}")
    variants_rows = list(csv.DictReader(variants_path.open()))
    variant_ids = _variant_ids(variants_rows)

    derived = build_derived_tables(variants_rows, variant_ids)

    for attribute, spec in DERIVED_REPORT_CSV_TABLES:
        actual = csv_bytes(getattr(derived, attribute), spec.fieldnames).decode().splitlines()
        expected = (fixture_dir / spec.filename).read_text().splitlines()
        assert actual == expected


def _variant_ids(rows: list[dict[str, str]]) -> tuple[str, ...]:
    variant_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        variant_id = row["variant_id"]
        if variant_id in seen:
            continue
        seen.add(variant_id)
        variant_ids.append(variant_id)
    return tuple(variant_ids)
