import csv
from pathlib import Path

import pytest

from bt_web_report_cli.phpp.transform import build_derived_tables
from bt_web_report_cli.phpp.write import DERIVED_REPORT_CSV_TABLES, csv_bytes


GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"


@pytest.mark.parametrize("fixture_name", ["vandam", "linde"])
def test_golden_variants_transform_to_derived_csvs(fixture_name: str) -> None:
    fixture_dir = GOLDEN_DIR / fixture_name
    variants_rows = list(csv.DictReader((fixture_dir / "variants.csv").open()))
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
