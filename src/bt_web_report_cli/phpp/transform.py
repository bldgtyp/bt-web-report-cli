"""Report-facing transformations from normalized PHPP rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

M2_TO_FT2 = 10.76391042
M3_TO_FT3 = 35.31466672


@dataclass(frozen=True)
class DerivedTables:
    """Deterministic CSV row groups derived from Variants worksheet values."""

    building_metrics: list[dict[str, Any]]
    certification: list[dict[str, Any]]
    energy: list[dict[str, Any]]
    demand_detail: list[dict[str, Any]]


def build_report_input_rows(variant_rows: list[dict[str, Any]], variant_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Build legacy report-card calculated rows for variants.csv."""

    index = _RowIndex(variant_rows)
    rows: list[dict[str, Any]] = []
    for variant_id in tuple(variant_ids):
        tfa_m2 = _number_from_row(index.get("geometry.tfa", variant_id))
        rows.extend(
            _converted_variant_rows(
                index,
                variant_id,
                (
                    _ReportInputConversion(
                        source_field_id="envelope.envelope_air_leakage_rate_q50",
                        field_id="report_inputs.envelope_air_leakage_rate_q50",
                        datatype="Envelope Air Leakage Rate (q50)",
                        units="cfm/ft2",
                        factor=0.054680665,
                    ),
                    _ReportInputConversion(
                        source_field_id="systems.cold_air_duct_length_ea",
                        field_id="report_inputs.cold_air_duct_length_ea",
                        datatype="Cold Air Duct Length (ea)",
                        units="ft",
                        factor=3.280839895,
                    ),
                    _ReportInputConversion(
                        source_field_id="systems.cold_air_duct_insulation_thickness",
                        field_id="report_inputs.cold_air_duct_insulation_thickness",
                        datatype="Cold Air Duct Insulation Thickness",
                        units="inches",
                        factor=0.039370079,
                    ),
                ),
            )
        )
        rows.extend(
            _annual_demand_report_input(
                index,
                variant_id,
                "certification_results.heat_demand",
                "report_inputs.heat_demand_annual",
                "Heat Demand",
                tfa_m2,
            )
        )
        rows.extend(
            _annual_demand_report_input(
                index,
                variant_id,
                "certification_results.total_cooling_demand",
                "report_inputs.cooling_demand_annual",
                "Cooling Demand",
                tfa_m2,
            )
        )
        rows.extend(_energy_total_report_input(index, variant_id, "primary_energy", "total_primary_energy"))
        rows.extend(
            _energy_total_report_input(index, variant_id, "site_energy", "total_site_energy", exclude=("solar_pv",))
        )
    return rows


def build_derived_tables(variant_rows: list[dict[str, Any]], variant_ids: Iterable[str]) -> DerivedTables:
    """Build the Phase 1 report tables from normalized Variants rows."""

    index = _RowIndex(variant_rows)
    ordered_variant_ids = tuple(variant_ids)
    return DerivedTables(
        building_metrics=_building_metrics(index, ordered_variant_ids),
        certification=_certification(index, ordered_variant_ids),
        energy=_energy(index, ordered_variant_ids),
        demand_detail=_demand_detail(index, ordered_variant_ids),
    )


class _RowIndex:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._by_field_variant = {(row["field_id"], row["variant_id"]): row for row in rows}
        self._by_section = _group_by_section(rows)

    def get(self, field_id: str, variant_id: str) -> dict[str, Any] | None:
        return self._by_field_variant.get((field_id, variant_id))

    def section(self, section_id: str) -> tuple[dict[str, Any], ...]:
        return self._by_section.get(section_id, ())


@dataclass(frozen=True)
class _ReportInputConversion:
    source_field_id: str
    field_id: str
    datatype: str
    units: str
    factor: float


def _converted_variant_rows(
    index: _RowIndex,
    variant_id: str,
    conversions: tuple[_ReportInputConversion, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for conversion in conversions:
        source = index.get(conversion.source_field_id, variant_id)
        if source is None:
            continue
        value = _number(source["value"])
        if value is None:
            continue
        rows.append(
            _report_input_row(
                source,
                field_id=conversion.field_id,
                datatype=conversion.datatype,
                units=conversion.units,
                value=value * conversion.factor,
            )
        )
    return rows


def _annual_demand_report_input(
    index: _RowIndex,
    variant_id: str,
    source_field_id: str,
    field_id: str,
    datatype: str,
    tfa_m2: float | None,
) -> list[dict[str, Any]]:
    source = index.get(source_field_id, variant_id)
    value = _number_from_row(source)
    if source is None or value is None or tfa_m2 is None:
        return []
    return [
        _report_input_row(
            source,
            field_id=field_id,
            datatype=datatype,
            units="kWh/yr",
            value=value * tfa_m2,
        )
    ]


def _energy_total_report_input(
    index: _RowIndex,
    variant_id: str,
    section_id: str,
    metric: str,
    *,
    exclude: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    values: list[float] = []
    source_rows: list[dict[str, Any]] = []
    for row in index.section(section_id):
        if row["variant_id"] != variant_id:
            continue
        item = row["field_id"].split(".", 1)[1]
        if item in exclude:
            continue
        value = _number(row["value"])
        if value is None:
            continue
        values.append(value)
        source_rows.append(row)
    if not values or not source_rows:
        return []
    return [
        {
            "section": "report_inputs",
            "field_id": f"report_inputs.{metric}",
            "phpp_label": metric.replace("_", " ").title(),
            "variant_id": variant_id,
            "variant_name": source_rows[0]["variant_name"],
            "datatype": "Total Primary Energy" if metric == "total_primary_energy" else "Total Site Energy",
            "units": "kWh/yr",
            "value": sum(values),
            "excel_row": "",
        }
    ]


def _report_input_row(
    source: dict[str, Any],
    *,
    field_id: str,
    datatype: str,
    units: str,
    value: float,
) -> dict[str, Any]:
    return {
        "section": "report_inputs",
        "field_id": field_id,
        "phpp_label": source["phpp_label"],
        "variant_id": source["variant_id"],
        "variant_name": source["variant_name"],
        "datatype": datatype,
        "units": units,
        "value": value,
        "excel_row": source["excel_row"],
    }


def _building_metrics(index: _RowIndex, variant_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant_id in variant_ids:
        tfa = _number_from_row(index.get("geometry.tfa", variant_id))
        envelope_area = _number_from_row(index.get("geometry.building_envelope_area", variant_id))
        gross_volume = _number_from_row(index.get("geometry.gross_volume", variant_id))
        tfa_ft2 = tfa * M2_TO_FT2 if tfa is not None else None
        envelope_area_ft2 = envelope_area * M2_TO_FT2 if envelope_area is not None else None
        gross_volume_ft3 = gross_volume * M3_TO_FT3 if gross_volume is not None else None

        rows.extend(
            [
                *_converted_metric(index, "geometry.tfa", variant_id, "treated_floor_area", "ft2", M2_TO_FT2),
                *_converted_metric(
                    index,
                    "geometry.building_envelope_area",
                    variant_id,
                    "building_envelope_area",
                    "ft2",
                    M2_TO_FT2,
                ),
                *_converted_metric(index, "geometry.vn50", variant_id, "interior_net_volume", "ft3", M3_TO_FT3),
                *_converted_metric(index, "geometry.gross_volume", variant_id, "gross_volume", "ft3", M3_TO_FT3),
            ]
        )

        if tfa_ft2 is not None and envelope_area_ft2 is not None and tfa_ft2 != 0:
            rows.append(_calculated_metric("envelope_area_to_tfa", variant_id, "ft2/ft2", envelope_area_ft2 / tfa_ft2))
        if gross_volume_ft3 is not None and envelope_area_ft2 is not None and gross_volume_ft3 != 0:
            rows.append(
                _calculated_metric(
                    "envelope_area_to_gross_volume",
                    variant_id,
                    "ft2/ft3",
                    envelope_area_ft2 / gross_volume_ft3,
                )
            )
        if gross_volume_ft3 is not None and tfa_ft2 is not None and gross_volume_ft3 != 0:
            rows.append(_calculated_metric("tfa_to_gross_volume", variant_id, "ft2/ft3", tfa_ft2 / gross_volume_ft3))

        for field_id, metric in _WINDOW_AREA_FIELDS:
            rows.extend(_converted_metric(index, field_id, variant_id, metric, "ft2", M2_TO_FT2))
    return rows


def _certification(index: _RowIndex, variant_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant_id in variant_ids:
        tfa_m2 = _number_from_row(index.get("geometry.tfa", variant_id))
        for metric, result_field_id, limit_field_id in _CERTIFICATION_METRICS:
            result = index.get(result_field_id, variant_id) if result_field_id else None
            if result is not None:
                rows.append(_source_row(metric, "result", variant_id, result, tfa_m2=tfa_m2))

            limit = index.get(limit_field_id, variant_id)
            if limit is not None:
                rows.append(_source_row(metric, "limit", variant_id, limit, tfa_m2=tfa_m2))
    return rows


def _energy(index: _RowIndex, variant_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant_id in variant_ids:
        primary_energy_values: list[float] = []
        for section_id, metric_group in _ENERGY_SECTIONS.items():
            for row in index.section(section_id):
                if row["variant_id"] != variant_id:
                    continue
                value = _number(row["value"])
                if value is None:
                    continue
                rows.append(
                    {
                        "metric_group": metric_group,
                        "end_use": row["field_id"].split(".", 1)[1],
                        "variant_id": variant_id,
                        "units": row["units"],
                        "value": value,
                        "source_field_id": row["field_id"],
                        "source_label": row["phpp_label"],
                        "excel_row": row["excel_row"],
                    }
                )
                if section_id == "primary_energy":
                    rows.append(
                        {
                            "metric_group": "phius_net_source_energy",
                            "end_use": row["field_id"].split(".", 1)[1],
                            "variant_id": variant_id,
                            "units": row["units"],
                            "value": value,
                            "source_field_id": row["field_id"],
                            "source_label": row["phpp_label"],
                            "excel_row": row["excel_row"],
                        }
                    )
                if section_id == "primary_energy":
                    primary_energy_values.append(value)

        if primary_energy_values:
            rows.append(
                {
                    "metric_group": "phius_net_source_energy",
                    "end_use": "total",
                    "variant_id": variant_id,
                    "units": "kWh",
                    "value": sum(primary_energy_values),
                    "source_field_id": "primary_energy.*",
                    "source_label": "Primary Energy total",
                    "excel_row": "",
                }
            )

        tfa_m2 = _number_from_row(index.get("geometry.tfa", variant_id))
        limit = index.get("certification_limits.phius_net_source_energy_limit", variant_id)
        if limit is not None:
            value, units = _absolute_value_and_units(limit["value"], limit["units"], tfa_m2)
            rows.append(
                {
                    "metric_group": "phius_net_source_energy",
                    "end_use": "limit",
                    "variant_id": variant_id,
                    "units": units,
                    "value": value,
                    "source_field_id": limit["field_id"],
                    "source_label": limit["phpp_label"],
                    "excel_row": limit["excel_row"],
                }
            )
    return rows


def _demand_detail(index: _RowIndex, variant_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant_id in variant_ids:
        tfa_m2 = _number_from_row(index.get("geometry.tfa", variant_id))
        rows.extend(_demand_rows(index, variant_id, "heating", "loss", _HEATING_LOSS_FIELDS))
        rows.extend(_demand_rows(index, variant_id, "heating", "gain", _HEATING_GAIN_FIELDS))
        rows.extend(
            _limit_demand_row(
                index,
                variant_id,
                "heating",
                "certification_limits.heat_demand_limit",
                tfa_m2,
            )
        )
        rows.extend(_demand_rows(index, variant_id, "cooling", "loss", _COOLING_LOSS_FIELDS))
        rows.extend(_demand_rows(index, variant_id, "cooling", "gain", _COOLING_GAIN_FIELDS))
        rows.extend(
            _limit_demand_row(
                index,
                variant_id,
                "cooling",
                "certification_limits.total_cooling_demand_limit",
                tfa_m2,
            )
        )
    return rows


def _converted_metric(
    index: _RowIndex,
    field_id: str,
    variant_id: str,
    metric: str,
    units: str,
    factor: float,
) -> list[dict[str, Any]]:
    row = index.get(field_id, variant_id)
    if row is None:
        return []
    value = _number(row["value"])
    if value is None:
        return []
    return [
        {
            "metric": metric,
            "variant_id": variant_id,
            "units": units,
            "value": value * factor,
            "source_field_id": row["field_id"],
            "source_label": row["phpp_label"],
            "excel_row": row["excel_row"],
        }
    ]


def _calculated_metric(metric: str, variant_id: str, units: str, value: float) -> dict[str, Any]:
    return {
        "metric": metric,
        "variant_id": variant_id,
        "units": units,
        "value": value,
        "source_field_id": "",
        "source_label": "",
        "excel_row": "",
    }


def _source_row(
    metric: str,
    role: str,
    variant_id: str,
    row: dict[str, Any],
    *,
    tfa_m2: float | None,
) -> dict[str, Any]:
    value, units = _absolute_value_and_units(row["value"], row["units"], tfa_m2)
    return {
        "metric": metric,
        "role": role,
        "variant_id": variant_id,
        "units": units,
        "value": value,
        "source_field_id": row["field_id"],
        "source_label": row["phpp_label"],
        "excel_row": row["excel_row"],
    }


def _demand_rows(
    index: _RowIndex,
    variant_id: str,
    demand_type: str,
    contribution_type: str,
    field_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field_id in field_ids:
        row = index.get(field_id, variant_id)
        if row is None:
            continue
        value = _number(row["value"])
        if value is None:
            continue
        rows.append(
            {
                "demand_type": demand_type,
                "contribution_type": contribution_type,
                "item": field_id.split(".", 1)[1],
                "variant_id": variant_id,
                "units": row["units"],
                "value": value,
                "source_field_id": row["field_id"],
                "source_label": row["phpp_label"],
                "excel_row": row["excel_row"],
            }
        )
    return rows


def _limit_demand_row(
    index: _RowIndex,
    variant_id: str,
    demand_type: str,
    field_id: str,
    tfa_m2: float | None,
) -> list[dict[str, Any]]:
    row = index.get(field_id, variant_id)
    if row is None:
        return []
    value, units = _absolute_value_and_units(row["value"], row["units"], tfa_m2)
    return [
        {
            "demand_type": demand_type,
            "contribution_type": "limit",
            "item": "demand_limit",
            "variant_id": variant_id,
            "units": units,
            "value": value,
            "source_field_id": row["field_id"],
            "source_label": row["phpp_label"],
            "excel_row": row["excel_row"],
        }
    ]


def _absolute_value_and_units(value: Any, units: str, tfa_m2: float | None) -> tuple[Any, str]:
    numeric_value = _number(value)
    if "/m2" not in units:
        return numeric_value if numeric_value is not None else value, units
    absolute_units = units.replace("/m2", "")
    if numeric_value is None or tfa_m2 is None:
        return value, absolute_units
    return numeric_value * tfa_m2, absolute_units


def _number_from_row(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    return _number(row["value"])


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _group_by_section(rows: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], ...]]:
    section_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        section_rows.setdefault(row["section"], []).append(row)
    return {section_id: tuple(grouped_rows) for section_id, grouped_rows in section_rows.items()}


_WINDOW_AREA_FIELDS = (
    ("geometry.window_area_north", "window_area_north"),
    ("geometry.window_area_east", "window_area_east"),
    ("geometry.window_area_south", "window_area_south"),
    ("geometry.window_area_west", "window_area_west"),
    ("geometry.window_area_horiz", "window_area_horizontal"),
)

_CERTIFICATION_METRICS = (
    ("heat_demand", "certification_results.heat_demand", "certification_limits.heat_demand_limit"),
    (
        "sensible_cooling_demand",
        "certification_results.sensible_cooling_demand",
        "certification_limits.sensible_cooling_demand_limit",
    ),
    (
        "latent_cooling_demand",
        "certification_results.latent_cooling_demand",
        "certification_limits.latent_cooling_demand_limit",
    ),
    (
        "total_cooling_demand",
        "certification_results.total_cooling_demand",
        "certification_limits.total_cooling_demand_limit",
    ),
    ("peak_heat_load", "certification_results.peak_heat_load", "certification_limits.peak_heat_load_limit"),
    ("peak_cooling_load", "certification_results.peak_cooling_load", "certification_limits.peak_cooling_load_limit"),
    ("pe_demand", "certification_results.pe_demand", "certification_limits.pe_limit"),
    ("per_demand", "certification_results.per_demand", "certification_limits.per_limit"),
    ("phius_net_source_energy", None, "certification_limits.phius_net_source_energy_limit"),
)

_ENERGY_SECTIONS = {
    "site_energy": "site_energy",
    "primary_energy": "primary_energy",
    "primary_energy_renewable": "per",
    "co2e": "co2e",
}

_HEATING_LOSS_FIELDS = (
    "heating_demand.walls_ag",
    "heating_demand.walls_bg",
    "heating_demand.roofs",
    "heating_demand.floor_slabs",
    "heating_demand.windows",
    "heating_demand.exterior_door",
    "heating_demand.thermal_bridges",
    "heating_demand.tb_perimeter",
    "heating_demand.tb_bg",
    "heating_demand.ventilation",
)

_HEATING_GAIN_FIELDS = (
    "heating_demand.heating_demand",
    "heating_demand.north",
    "heating_demand.east",
    "heating_demand.south",
    "heating_demand.west",
    "heating_demand.horizontal",
    "heating_demand.sum_opaque_areas",
    "heating_demand.internal_gains",
)

_COOLING_LOSS_FIELDS = (
    "cooling_demand.cooling_demand_2",
    "cooling_demand.walls_ag",
    "cooling_demand.walls_bg",
    "cooling_demand.roofs",
    "cooling_demand.floor_slabs",
    "cooling_demand.windows",
    "cooling_demand.exterior_door",
    "cooling_demand.thermal_bridges",
    "cooling_demand.tb_perimeter",
    "cooling_demand.tb_bg",
    "cooling_demand.ventilation_basic",
    "cooling_demand.ventilation_addn_l",
)

_COOLING_GAIN_FIELDS = (
    "cooling_demand.north",
    "cooling_demand.east",
    "cooling_demand.south",
    "cooling_demand.west",
    "cooling_demand.horizontal",
    "cooling_demand.sum_opaque_areas",
    "cooling_demand.internal_gains",
)
