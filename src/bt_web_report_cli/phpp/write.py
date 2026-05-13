"""Atomic generated-data writes."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class CsvTableSpec:
    filename: str
    fieldnames: tuple[str, ...]


VARIANTS_FIELDNAMES = (
    "section",
    "field_id",
    "phpp_label",
    "variant_id",
    "variant_name",
    "datatype",
    "units",
    "value",
    "excel_row",
)

CLIMATE_MONTHLY_FIELDNAMES = (
    "month",
    "metric",
    "orientation",
    "units",
    "value",
    "source_label",
    "excel_row",
)

ROOM_AIRFLOWS_FIELDNAMES = (
    "row_type",
    "room_name",
    "amount",
    "allocation_to_vent_unit",
    "room_area_ft2",
    "room_volume_ft3",
    "room_height_ft",
    "v_sup_high_cfm",
    "v_eta_high_cfm",
    "v_sup_med_cfm",
    "v_eta_med_cfm",
    "v_sup_low_cfm",
    "v_eta_low_cfm",
    "excel_row",
)

BUILDING_METRICS_FIELDNAMES = (
    "metric",
    "variant_id",
    "units",
    "value",
    "source_field_id",
    "source_label",
    "excel_row",
)

CERTIFICATION_FIELDNAMES = (
    "metric",
    "role",
    "variant_id",
    "units",
    "value",
    "source_field_id",
    "source_label",
    "excel_row",
)

ENERGY_FIELDNAMES = (
    "metric_group",
    "end_use",
    "variant_id",
    "units",
    "value",
    "source_field_id",
    "source_label",
    "excel_row",
)

DEMAND_DETAIL_FIELDNAMES = (
    "demand_type",
    "contribution_type",
    "item",
    "variant_id",
    "units",
    "value",
    "source_field_id",
    "source_label",
    "excel_row",
)

VARIANTS_TABLE = CsvTableSpec("variants.csv", VARIANTS_FIELDNAMES)
CLIMATE_MONTHLY_TABLE = CsvTableSpec("climate-monthly.csv", CLIMATE_MONTHLY_FIELDNAMES)
ROOM_AIRFLOWS_TABLE = CsvTableSpec("room-airflows.csv", ROOM_AIRFLOWS_FIELDNAMES)
BUILDING_METRICS_TABLE = CsvTableSpec("building-metrics.csv", BUILDING_METRICS_FIELDNAMES)
CERTIFICATION_TABLE = CsvTableSpec("certification.csv", CERTIFICATION_FIELDNAMES)
ENERGY_TABLE = CsvTableSpec("energy.csv", ENERGY_FIELDNAMES)
DEMAND_DETAIL_TABLE = CsvTableSpec("demand-detail.csv", DEMAND_DETAIL_FIELDNAMES)

REPORT_CSV_TABLES = (
    VARIANTS_TABLE,
    CLIMATE_MONTHLY_TABLE,
    ROOM_AIRFLOWS_TABLE,
    BUILDING_METRICS_TABLE,
    CERTIFICATION_TABLE,
    ENERGY_TABLE,
    DEMAND_DETAIL_TABLE,
)

DERIVED_REPORT_CSV_TABLES = (
    ("building_metrics", BUILDING_METRICS_TABLE),
    ("certification", CERTIFICATION_TABLE),
    ("energy", ENERGY_TABLE),
    ("demand_detail", DEMAND_DETAIL_TABLE),
)

FLOAT_SIGNIFICANT_DIGITS = 12


def write_report_data(
    output_dir: Path,
    manifest: BaseModel,
    variants_rows: list[dict[str, Any]],
    climate_monthly_rows: list[dict[str, Any]],
    room_airflow_rows: list[dict[str, Any]],
    building_metric_rows: list[dict[str, Any]],
    certification_rows: list[dict[str, Any]],
    energy_rows: list[dict[str, Any]],
    demand_detail_rows: list[dict[str, Any]],
) -> None:
    """Write manifest and CSV data through an atomic directory replacement."""

    output_dir = output_dir.expanduser().resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    backup_dir = output_dir.with_name(f".{output_dir.name}.backup")

    try:
        _write_json(temp_dir / "manifest.json", manifest)
        for spec, rows in (
            (VARIANTS_TABLE, variants_rows),
            (CLIMATE_MONTHLY_TABLE, climate_monthly_rows),
            (ROOM_AIRFLOWS_TABLE, room_airflow_rows),
            (BUILDING_METRICS_TABLE, building_metric_rows),
            (CERTIFICATION_TABLE, certification_rows),
            (ENERGY_TABLE, energy_rows),
            (DEMAND_DETAIL_TABLE, demand_detail_rows),
        ):
            _write_csv(temp_dir / spec.filename, rows, spec.fieldnames)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if output_dir.exists():
            os.replace(output_dir, backup_dir)
        os.replace(temp_dir, output_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    except Exception:
        if output_dir.exists() is False and backup_dir.exists():
            os.replace(backup_dir, output_dir)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise


def _write_json(path: Path, manifest: BaseModel) -> None:
    path.write_text(manifest.model_dump_json(indent=2) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: tuple[str, ...]) -> None:
    path.write_bytes(csv_bytes(rows, fieldnames))


def csv_bytes(rows: list[dict[str, Any]], fieldnames: tuple[str, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(csv_rows(rows, fieldnames))
    return output.getvalue().encode()


def csv_rows(rows: list[dict[str, Any]], fieldnames: tuple[str, ...]) -> list[dict[str, Any]]:
    return [_csv_row(row, fieldnames) for row in rows]


def _csv_row(row: dict[str, Any], fieldnames: tuple[str, ...]) -> dict[str, Any]:
    return {fieldname: _csv_value(row.get(fieldname)) for fieldname in fieldnames}


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return f"{value:.{FLOAT_SIGNIFICANT_DIGITS}g}"
    return value
