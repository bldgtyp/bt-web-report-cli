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


def write_report_data(output_dir: Path, manifest: BaseModel, variants_rows: list[dict[str, Any]]) -> None:
    """Write manifest and CSV data through an atomic directory replacement."""

    output_dir = output_dir.expanduser().resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    backup_dir = output_dir.with_name(f".{output_dir.name}.backup")

    try:
        _write_json(temp_dir / "manifest.json", manifest)
        _write_csv(temp_dir / "variants.csv", variants_rows)
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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=VARIANTS_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
