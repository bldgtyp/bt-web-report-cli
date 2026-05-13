"""openpyxl-backed PHPP workbook reader."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from bt_web_report_schemas.phpp.models import WorkbookSchema


@dataclass(frozen=True)
class VariantColumn:
    """One active variant column in the PHPP Variants worksheet."""

    id: str
    name: str
    order: int
    column_index: int
    source_column: str


class OpenpyxlWorkbookReader:
    """Read saved PHPP workbook values without launching Excel."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="wmf image format is not supported.*")
            warnings.filterwarnings("ignore", message="Data Validation extension is not supported.*")
            warnings.filterwarnings("ignore", message="Cannot parse header or footer.*")
            self._workbook = load_workbook(path, data_only=True, read_only=False)

    def detect_phpp_version(self) -> str:
        """Read PHPP version from named range, falling back to Data!B5."""

        value = self._read_named_range("PHPP_Version")
        if value in (None, ""):
            value = self._workbook["Data"]["B5"].value
        if value in (None, ""):
            msg = "Could not detect PHPP version from named range PHPP_Version or Data!B5."
            raise ValueError(msg)
        return str(value).strip()

    def read_variant_columns(self, schema: WorkbookSchema) -> tuple[VariantColumn, ...]:
        """Read active variant labels from the Variants header row."""

        sheet = self._workbook[schema.variant_sheet]
        columns: list[VariantColumn] = []
        for cell in sheet[schema.variant_header_row]:
            parsed = _parse_variant_header(cell.value)
            if parsed is None:
                continue
            order, name = parsed
            if order == 0:
                continue
            columns.append(
                VariantColumn(
                    id=_slugify(name),
                    name=name,
                    order=order,
                    column_index=cell.column,
                    source_column=get_column_letter(cell.column),
                )
            )

        if not columns:
            msg = f"No active variants found on {schema.variant_sheet} row {schema.variant_header_row}."
            raise ValueError(msg)
        return tuple(columns)

    def read_variants(self, schema: WorkbookSchema, variant_columns: tuple[VariantColumn, ...]) -> list[dict[str, Any]]:
        """Read Variants worksheet rows into the first long-format report table."""

        sheet = self._workbook[schema.variant_sheet]
        rows: list[dict[str, Any]] = []
        for field in schema.variant_fields():
            if schema.section(field.section_id).start_row == field.row:
                continue
            if _clean_text(field.phpp_label) in {"", "-"}:
                continue
            datatype = sheet.cell(field.row, 3).value
            units = sheet.cell(field.row, 4).value
            phpp_label = _clean_text(datatype) if field.label_from_workbook else field.phpp_label.strip()
            if field.label_from_workbook and _is_placeholder_r_value(phpp_label):
                continue
            for variant in variant_columns:
                value = sheet.cell(field.row, variant.column_index).value
                if _is_blank_row(datatype, units, value):
                    continue
                rows.append(
                    {
                        "section": field.section_id,
                        "field_id": f"{field.section_id}.{field.id}",
                        "phpp_label": phpp_label,
                        "variant_id": variant.id,
                        "variant_name": variant.name,
                        "datatype": _clean_text(datatype) or field.phpp_label.strip(),
                        "units": _clean_text(units),
                        "value": value,
                        "excel_row": field.row,
                    }
                )
        return rows

    def _read_named_range(self, name: str) -> Any:
        defined_name = self._workbook.defined_names.get(name)
        if defined_name is None:
            return None
        for sheet_name, coord in defined_name.destinations:
            return self._workbook[sheet_name][coord].value
        return None


def _parse_variant_header(value: object) -> tuple[int, str] | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"\s*(\d+)\s*-\s*(.+?)\s*$", value)
    if match is None:
        return None
    name = match.group(2).strip()
    if not name:
        return None
    return int(match.group(1)), name


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "variant"


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().replace(",", " ")


def _is_blank_row(datatype: object, units: object, value: object) -> bool:
    return _clean_text(datatype) in {"", "-"} and _clean_text(units) in {"", "-"} and value in (None, "", "-")


def _is_placeholder_r_value(label: str) -> bool:
    return re.fullmatch(r"\d{2}ud-", label) is not None
