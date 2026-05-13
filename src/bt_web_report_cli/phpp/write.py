"""Atomic generated-data writes."""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel


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
        _write_csv(temp_dir / "variants.csv", variants_rows, VARIANTS_FIELDNAMES)
        _write_csv(temp_dir / "climate-monthly.csv", climate_monthly_rows, CLIMATE_MONTHLY_FIELDNAMES)
        _write_csv(temp_dir / "room-airflows.csv", room_airflow_rows, ROOM_AIRFLOWS_FIELDNAMES)
        _write_csv(temp_dir / "building-metrics.csv", building_metric_rows, BUILDING_METRICS_FIELDNAMES)
        _write_csv(temp_dir / "certification.csv", certification_rows, CERTIFICATION_FIELDNAMES)
        _write_csv(temp_dir / "energy.csv", energy_rows, ENERGY_FIELDNAMES)
        _write_csv(temp_dir / "demand-detail.csv", demand_detail_rows, DEMAND_DETAIL_FIELDNAMES)
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
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
