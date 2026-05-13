"""Project and workbook path resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_workbook_path(project_path: Path, explicit_phpp_path: Path | None = None) -> Path:
    """Resolve the PHPP workbook from a project path or explicit override."""

    if explicit_phpp_path is not None:
        return explicit_phpp_path.expanduser().resolve()

    path = project_path.expanduser().resolve()
    if path.is_file():
        return path

    project_yaml = path / "project.yaml"
    if not project_yaml.exists():
        msg = f"No PHPP workbook provided and no project.yaml found at {project_yaml}."
        raise FileNotFoundError(msg)

    import yaml

    data = yaml.safe_load(project_yaml.read_text()) or {}
    workbook = _find_phpp_entry(data)
    if workbook is None:
        msg = f"project.yaml does not define a PHPP workbook path: {project_yaml}."
        raise ValueError(msg)

    workbook_path = Path(workbook).expanduser()
    if not workbook_path.is_absolute():
        workbook_path = path / workbook_path
    return workbook_path.resolve()


def default_output_dir(project_path: Path) -> Path:
    """Return the default generated data directory for a project or fixture path."""

    path = project_path.expanduser().resolve()
    base = path.parent if path.is_file() else path
    return base / "data"


def _find_phpp_entry(data: dict[str, Any]) -> str | None:
    for key in ("phpp_path", "phpp", "workbook"):
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = value.get("path")
            if isinstance(nested, str):
                return nested
    return None
