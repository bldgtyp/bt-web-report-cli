"""`btwr scrape` orchestration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from bt_web_report_schemas.manifest import Manifest, SourceWorkbook, VariantMeta
from bt_web_report_schemas.phpp import get_schema

from bt_web_report_cli import __version__
from bt_web_report_cli.io.project import default_output_dir, resolve_workbook_path
from bt_web_report_cli.io.workbook_openpyxl import OpenpyxlWorkbookReader
from bt_web_report_cli.phpp.transform import build_derived_tables
from bt_web_report_cli.phpp.write import VARIANTS_FIELDNAMES, csv_rows, write_report_data


def scrape_project(
    project_path: Path,
    *,
    phpp_path: Path | None = None,
    output_dir: Path | None = None,
    reader_name: str = "openpyxl",
    phpp_version: str | None = None,
) -> Manifest:
    """Scrape a PHPP workbook and write deterministic report data."""

    if reader_name != "openpyxl":
        msg = f"Unsupported reader '{reader_name}'. Phase 1 currently supports: openpyxl."
        raise ValueError(msg)

    workbook_path = resolve_workbook_path(project_path, phpp_path)
    try:
        reader = OpenpyxlWorkbookReader(workbook_path)
    except FileNotFoundError as exc:
        msg = f"PHPP workbook does not exist: {workbook_path}"
        raise FileNotFoundError(msg) from exc

    detected_version = phpp_version or reader.detect_phpp_version()
    schema = get_schema(detected_version)
    variant_columns = reader.read_variant_columns(schema)
    variants = tuple(
        VariantMeta(
            id=column.id,
            name=column.name,
            order=column.order,
            recommended=column == variant_columns[-1],
            source_column=column.source_column,
        )
        for column in variant_columns
    )
    rows = reader.read_variants(schema, variant_columns)
    if not rows:
        msg = f"No variant data found in workbook: {workbook_path}"
        raise ValueError(msg)
    climate_rows = reader.read_climate_monthly(schema)
    room_airflow_rows = reader.read_room_airflows(schema)
    if not climate_rows:
        msg = f"No monthly climate data found in workbook: {workbook_path}"
        raise ValueError(msg)
    report_rows = csv_rows(rows, VARIANTS_FIELDNAMES)
    derived_tables = build_derived_tables(report_rows, (variant.id for variant in variants))

    recommended_variant = variants[-1]
    manifest = Manifest(
        phpp_version=schema.version,
        generated_at=datetime.now(UTC),
        generator=f"btwr@{__version__}",
        variants=variants,
        recommended_variant_id=recommended_variant.id,
        source_workbook=_fingerprint(workbook_path),
    )

    write_report_data(
        output_dir or default_output_dir(project_path),
        manifest,
        report_rows,
        climate_rows,
        room_airflow_rows,
        derived_tables.building_metrics,
        derived_tables.certification,
        derived_tables.energy,
        derived_tables.demand_detail,
    )
    return manifest


def _fingerprint(path: Path) -> SourceWorkbook:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return SourceWorkbook(path=str(path), sha256=digest.hexdigest(), size_bytes=stat.st_size)
