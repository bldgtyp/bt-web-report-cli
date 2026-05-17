import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pytest
from click.testing import CliRunner

from bt_web_report_cli.__main__ import main

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(os.environ.get("BTWR_TEST_PHPP_DIR", WORKSPACE_ROOT / "test-files" / "phpp"))
VANDAM_COMPAT_DIR = FIXTURE_DIR / "2606-Vandam-St"
VANDAM_WORKBOOK = VANDAM_COMPAT_DIR / "2606-29-Vandam-St.xlsx"
VANDAM_LEGACY_DATA = VANDAM_COMPAT_DIR / "phpp-data"

RAW_LEGACY_PREFIX = "phpp_data_"

LEGACY_OUTPUT_MATRIX = {
    "Phius_net_source_energy.csv": "energy.csv",
    "bldg_data.csv": "building-metrics.csv",
    "climate_radiation.csv": "climate-monthly.csv",
    "climate_temps.csv": "climate-monthly.csv",
    "demand_HeatAndCool.csv": "certification.csv",
    "demand_Phius_cooling.csv": "certification.csv",
    "demand_Phius_heating.csv": "certification.csv",
    "energy_PER.csv": "energy.csv",
    "energy_Site.csv": "energy.csv",
    "energy_TonsCO2.csv": "energy.csv",
    "envelope_airflow.csv": "variants.csv",
    "cooling_demand_Code Minimum.csv": "demand-detail.csv",
    "cooling_demand_EnerPHit by Component.csv": "demand-detail.csv",
    "cooling_demand_EnerPHit by Demand.csv": "demand-detail.csv",
    "cooling_demand_Improved Envelope.csv": "demand-detail.csv",
    "cooling_demand_Improved HVAC.csv": "demand-detail.csv",
    "heating_demand_Code Minimum.csv": "demand-detail.csv",
    "heating_demand_EnerPHit by Component.csv": "demand-detail.csv",
    "heating_demand_EnerPHit by Demand.csv": "demand-detail.csv",
    "heating_demand_Improved Envelope.csv": "demand-detail.csv",
    "heating_demand_Improved HVAC.csv": "demand-detail.csv",
    "load_Phius_cooling.csv": "certification.csv",
    "load_Phius_heating.csv": "certification.csv",
    "room_airflows.csv": "room-airflows.csv",
    "variant_inputs.csv": "variants.csv",
    "variant_inputs_ENVELOPE.csv": "variants.csv",
    "variant_inputs_RESULTS.csv": "variants.csv",
    "variant_inputs_SYSTEMS.csv": "variants.csv",
}

LEGACY_VARIANT_NAMES = (
    "Code Minimum",
    "Improved Envelope",
    "Improved HVAC",
    "EnerPHit by Component",
    "EnerPHit by Demand",
)

DERIVED_VARIANT_INPUT_FIELDS = {
    "Envelope Air Leakage Rate (q50)": "report_inputs.envelope_air_leakage_rate_q50",
    "Cold Air Duct Length (ea)": "report_inputs.cold_air_duct_length_ea",
    "Cold Air Duct Insulation Thickness": "report_inputs.cold_air_duct_insulation_thickness",
    "Total Primary Energy": "report_inputs.total_primary_energy",
    "Total Site Energy": "report_inputs.total_site_energy",
    "Heat Demand": "report_inputs.heat_demand_annual",
    "Cooling Demand": "report_inputs.cooling_demand_annual",
}


@dataclass(frozen=True)
class LegacyVariantInputRecord:
    field_id: str
    datatype: str
    units: str
    variant_name: str
    value: str


@pytest.fixture(scope="session")
def scraped_legacy_vandam(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not VANDAM_WORKBOOK.exists():
        pytest.skip(f"PHPP workbook fixture is not available: {VANDAM_WORKBOOK}")
    output_dir = tmp_path_factory.mktemp("vandam-legacy-equivalence") / "data"
    result = CliRunner().invoke(main, ["scrape", str(VANDAM_WORKBOOK), "--out", str(output_dir)])
    assert result.exit_code == 0, result.output
    return output_dir


def test_legacy_output_matrix_covers_all_vandam_target_csvs() -> None:
    if not VANDAM_LEGACY_DATA.exists():
        pytest.skip(f"Legacy target CSV fixture is not available: {VANDAM_LEGACY_DATA}")

    csv_names = sorted(path.name for path in VANDAM_LEGACY_DATA.glob("*.csv"))
    derived_names = [name for name in csv_names if not name.startswith(RAW_LEGACY_PREFIX)]
    raw_names = [name for name in csv_names if name.startswith(RAW_LEGACY_PREFIX)]

    assert raw_names == [
        "phpp_data_climate_2026-05-06_14-20-20.csv",
        "phpp_data_room_ventilation_2026-05-06_14-20-20.csv",
        "phpp_data_variants_2026-05-06_14-20-20.csv",
    ]
    assert set(derived_names) == set(LEGACY_OUTPUT_MATRIX)


def test_legacy_variant_input_calculated_rows_are_in_new_variants_csv(scraped_legacy_vandam: Path) -> None:
    if not VANDAM_LEGACY_DATA.exists():
        pytest.skip(f"Legacy target CSV fixture is not available: {VANDAM_LEGACY_DATA}")

    actual = _new_variant_records(scraped_legacy_vandam / "variants.csv")
    for expected in _legacy_derived_variant_input_records():
        key = (expected.field_id, expected.variant_name)
        assert key in actual
        row = actual[key]
        assert row["datatype"] == expected.datatype
        assert row["units"] == expected.units
        assert _number(row["value"]) == pytest.approx(_number(expected.value))


def test_legacy_building_metric_values_are_in_new_building_metrics_csv(scraped_legacy_vandam: Path) -> None:
    actual = _records_by(("metric", "variant_id"), scraped_legacy_vandam / "building-metrics.csv")
    for old_metric, new_metric in BUILDING_METRIC_MAP.items():
        for row in _read_legacy_rows("bldg_data.csv", old_metric):
            for variant_name in LEGACY_VARIANT_NAMES:
                key = (new_metric, _slugify_variant(variant_name))
                assert key in actual
                assert _number(actual[key]["value"]) == pytest.approx(_number(row[variant_name]))


def test_legacy_climate_values_are_in_new_climate_monthly_csv(scraped_legacy_vandam: Path) -> None:
    actual = _records_by(("month", "metric", "orientation"), scraped_legacy_vandam / "climate-monthly.csv")

    for old_metric, new_metric in CLIMATE_TEMP_MAP.items():
        rows = _read_legacy_csv("climate_temps.csv")
        for old_month, month in MONTH_MAP.items():
            key = (month, new_metric, "")
            assert key in actual
            row = next(row for row in rows if row["Month"] == old_month)
            assert _number(actual[key]["value"]) == pytest.approx(_number(row[old_metric]))

    for orientation in ("North", "East", "South", "West", "Horizontal"):
        for row in _read_legacy_csv("climate_radiation.csv"):
            key = (MONTH_MAP[row["Month"]], "solar_radiation", orientation.lower())
            assert key in actual
            assert _number(actual[key]["value"]) == pytest.approx(_number(row[orientation]))


def test_legacy_room_airflow_values_are_in_new_room_airflows_csv(scraped_legacy_vandam: Path) -> None:
    actual = _records_by(("room_name",), scraped_legacy_vandam / "room-airflows.csv")
    for row in _read_legacy_csv("room_airflows.csv"):
        key = (row["Room Name"],)
        assert key in actual
        for old_field, new_field in ROOM_AIRFLOW_FIELD_MAP.items():
            assert _number(actual[key][new_field]) == pytest.approx(_number(row[old_field]))


def test_legacy_certification_values_are_in_new_certification_csv(scraped_legacy_vandam: Path) -> None:
    actual = _records_by(("metric", "role", "variant_id"), scraped_legacy_vandam / "certification.csv")
    for filename, mappings in CERTIFICATION_FILE_MAP.items():
        for row in _read_legacy_csv(filename):
            metric, role = mappings[row["Datatype"]]
            for variant_name in LEGACY_VARIANT_NAMES:
                key = (metric, role, _slugify_variant(variant_name))
                assert key in actual
                assert _number(actual[key]["value"]) == pytest.approx(_number(row[variant_name]))


def test_legacy_energy_values_are_in_new_energy_csv(scraped_legacy_vandam: Path) -> None:
    actual = _records_by(("metric_group", "end_use", "variant_id"), scraped_legacy_vandam / "energy.csv")
    for filename, metric_group in ENERGY_FILE_MAP.items():
        for row in _read_legacy_csv(filename):
            end_use = ENERGY_END_USE_MAP.get(row["Datatype"], _slugify(row["Datatype"]))
            if row["Datatype"] == "PHIUS Net Source Energy Limit":
                end_use = "limit"
            for variant_name in LEGACY_VARIANT_NAMES:
                key = (metric_group, end_use, _slugify_variant(variant_name))
                assert key in actual
                assert _number(actual[key]["value"]) == pytest.approx(_number(row[variant_name]))


def test_legacy_demand_detail_values_are_in_new_demand_detail_csv(scraped_legacy_vandam: Path) -> None:
    actual = _records_by(
        ("demand_type", "contribution_type", "source_label", "variant_id"),
        scraped_legacy_vandam / "demand-detail.csv",
    )
    for demand_type in ("heating", "cooling"):
        for variant_name in LEGACY_VARIANT_NAMES:
            filename = f"{demand_type}_demand_{variant_name}.csv"
            for row in _read_legacy_csv(filename):
                if row["Datatype"].endswith("Demand Limit"):
                    source_label = DEMAND_LIMIT_SOURCE_LABELS[row["Datatype"]]
                    key = (demand_type, "limit", source_label, _slugify_variant(variant_name))
                    assert key in actual
                    assert _number(actual[key]["value"]) == pytest.approx(_number(row["Losses"]))
                    continue
                for old_column, contribution_type in (("Losses", "loss"), ("Gains", "gain")):
                    if _number(row[old_column]) == 0:
                        continue
                    key = (demand_type, contribution_type, row["Datatype"], _slugify_variant(variant_name))
                    assert key in actual
                    assert _number(actual[key]["value"]) == pytest.approx(_number(row[old_column]))


def _legacy_derived_variant_input_records() -> Iterable[LegacyVariantInputRecord]:
    for filename in ("variant_inputs_ENVELOPE.csv", "variant_inputs_SYSTEMS.csv", "variant_inputs_RESULTS.csv"):
        with (VANDAM_LEGACY_DATA / filename).open(newline="") as file:
            for row in csv.DictReader(file):
                datatype = row["Datatype"]
                field_id = DERIVED_VARIANT_INPUT_FIELDS.get(datatype)
                if field_id is None:
                    continue
                if field_id in {"report_inputs.heat_demand_annual", "report_inputs.cooling_demand_annual"}:
                    if row["Units"] != "kWh/yr":
                        continue
                for variant_name in LEGACY_VARIANT_NAMES:
                    yield LegacyVariantInputRecord(
                        field_id=field_id,
                        datatype=datatype,
                        units=row["Units"],
                        variant_name=variant_name,
                        value=row[variant_name],
                    )


def _new_variant_records(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    return {(row["field_id"], row["variant_name"]): row for row in rows}


def _read_legacy_csv(filename: str) -> list[dict[str, str]]:
    with (VANDAM_LEGACY_DATA / filename).open(newline="") as file:
        return list(csv.DictReader(file))


def _read_legacy_rows(filename: str, datatype: str) -> list[dict[str, str]]:
    return [row for row in _read_legacy_csv(filename) if row["Datatype"] == datatype]


def _records_by(keys: tuple[str, ...], path: Path) -> dict[tuple[str, ...], dict[str, str]]:
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    return {tuple(row[key] for key in keys): row for row in rows}


def _slugify_variant(value: str) -> str:
    if value == "Code Minimum":
        return "code_minimum"
    return _slugify(value)


def _slugify(value: str) -> str:
    return (
        "".join(character if character.isalnum() else "_" for character in value.lower()).strip("_").replace("__", "_")
    )


def _number(value: str) -> float:
    if value in {"", "-", " "}:
        return 0.0
    return float(value)


BUILDING_METRIC_MAP = {
    "Floor Area*": "treated_floor_area",
    "Building Envelope Area": "building_envelope_area",
    "Interior Net Volume": "interior_net_volume",
    "Gross Volume": "gross_volume",
    "Ext. Surface Area / Floor Area": "envelope_area_to_tfa",
    "Ext. Surface Area / Gross Volume": "envelope_area_to_gross_volume",
    "Floor Area / Gross Volume": "tfa_to_gross_volume",
    "Window Area (North)": "window_area_north",
    "Window Area (East)": "window_area_east",
    "Window Area (South)": "window_area_south",
    "Window Area (West)": "window_area_west",
    "Window Area (Horiz)": "window_area_horizontal",
}

MONTH_MAP = {
    "Jan": "jan",
    "Feb": "feb",
    "Mar": "mar",
    "Apr": "apr",
    "May": "may",
    "June": "jun",
    "July": "jul",
    "Aug": "aug",
    "Sept": "sep",
    "Oct": "oct",
    "Nov": "nov",
    "Dec": "dec",
}

CLIMATE_TEMP_MAP = {
    "Exterior temperature": "exterior_temperature",
    "Dew point temperature": "dew_point_temperature",
    "Sky temperature": "sky_temperature",
}

ROOM_AIRFLOW_FIELD_MAP = {
    "Room Vol. (ft3)": "room_volume_ft3",
    "Room Area (ft2)": "room_area_ft2",
    "Room Height (ft)": "room_height_ft",
    "V_Sup_High": "v_sup_high_cfm",
    "V_Eta_High": "v_eta_high_cfm",
    "V_Sup_Med": "v_sup_med_cfm",
    "V_Eta_Med": "v_eta_med_cfm",
    "V_Sup_Low": "v_sup_low_cfm",
    "V_Eta_Low": "v_eta_low_cfm",
}

CERTIFICATION_FILE_MAP = {
    "demand_Phius_heating.csv": {
        "Heat Demand": ("heat_demand", "result"),
        "Heat Demand Limit": ("heat_demand", "limit"),
    },
    "demand_Phius_cooling.csv": {
        "Total Cooling Demand": ("total_cooling_demand", "result"),
        "Total Cooling Demand Limit": ("total_cooling_demand", "limit"),
    },
    "load_Phius_heating.csv": {
        "Peak Heat Load": ("peak_heat_load", "result"),
        "Peak Heat Load Limit": ("peak_heat_load", "limit"),
    },
    "load_Phius_cooling.csv": {
        "Peak Cooling Load": ("peak_cooling_load", "result"),
        "Peak Cooling Load Limit": ("peak_cooling_load", "limit"),
    },
}

ENERGY_FILE_MAP = {
    "energy_Site.csv": "site_energy",
    "energy_PER.csv": "per",
    "energy_TonsCO2.csv": "co2e",
    "Phius_net_source_energy.csv": "phius_net_source_energy",
}

ENERGY_END_USE_MAP = {
    "PHI Lighting": "phi_lighting",
    "PHI Consumer Elec.": "phi_consumer_elec",
    "PHI Small Appliances": "phi_small_appliances",
    "Phius Int. Lighting": "phius_int_lighting",
    "Phius Ext. Lighting": "phius_ext_lighting",
    "Phius MEL": "phius_mel",
    "Aux Elec": "aux_elec",
    "Solar PV": "solar_pv",
    "IPCC Limit": "ipcc_limit",
}

DEMAND_LIMIT_SOURCE_LABELS = {
    "Heating Demand Limit": "Heat Demand Limit",
    "Cooling Demand Limit": "Total Cooling Demand Limit",
}
