"""Project runtime helpers.

Phase 4 of the Option-C migration: the project directory IS the runtime.
``btwr build / preview / editor`` shell out to ``pnpm`` directly with the
project as the working directory; there is no separate disposable
workspace under ``~/Library/Application Support/bt-web-report-manager/``
anymore. The runtime payload, dependency install, schema sibling-checkout
helper, and symlink workspace are all gone.

What stays:
    - ``resolve_renderer_source`` — used by ``btwr new`` and ``btwr re-seed``
      to locate the template source from which to seed a project.
    - ``validate_project_yaml`` — used by ``btwr new`` and `btwr scrape`
      to catch malformed project.yaml early.
    - ``app_support_dir`` — still referenced by ``btwr doctor`` for the
      banner. The directory itself is no longer used at runtime.
    - ``run_pnpm_script`` — the project-as-cwd replacement for the old
      ``run_renderer_script``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml
from pydantic import ValidationError

from bt_web_report_schemas.project import Project

APP_SUPPORT_ENV = "BTWR_APP_SUPPORT"
MANAGER_APP_SUPPORT_ENV = "BTWR_MANAGER_APP_SUPPORT"
RENDERER_SOURCE_ENV = "BTWR_RENDERER_SOURCE"

APP_SUPPORT_DEFAULT = Path("~/Library/Application Support/bt-web-report-manager").expanduser()


def app_support_dir() -> Path:
    """Return the manager-owned support root (used by `btwr doctor` only)."""

    override = os.environ.get(APP_SUPPORT_ENV) or os.environ.get(MANAGER_APP_SUPPORT_ENV)
    if override:
        return Path(override).expanduser()
    return APP_SUPPORT_DEFAULT


def resolve_renderer_source(explicit: Path | None = None) -> Path | None:
    """Locate the template source used as the seed for new/re-seeded projects."""

    if explicit is not None:
        return explicit.expanduser().resolve()
    env_value = os.environ.get(RENDERER_SOURCE_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "bt-web-report-template"
        if (candidate / "package.json").exists() and (candidate / "src").exists():
            return candidate
    return None


def validate_project_yaml(project_path: Path) -> None:
    """Fail fast when a project's ``project.yaml`` does not match the schema."""

    project_file = project_path / "project.yaml"
    if not project_file.exists():
        raise RuntimeError(f"project.yaml does not exist: {project_path}")
    raw = yaml.safe_load(project_file.read_text()) or {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"project.yaml must contain a mapping: {project_file}")
    try:
        Project.model_validate(raw)
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first_error.get("loc", ())) or "project.yaml"
        message = first_error.get("msg", "invalid project.yaml")
        raise RuntimeError(f"{project_file}: {location} {message}") from exc


def run_pnpm_script(
    project_path: Path,
    script: str,
    *,
    pnpm_executable: str = "pnpm",
    extra_args: tuple[str, ...] = (),
) -> None:
    """Run ``pnpm run <script>`` in the project directory.

    The project directory IS the build runtime — it owns package.json,
    pnpm-lock.yaml, src/, tina/, scripts/, etc. ``btwr build/preview/editor``
    simply cd into it and exec pnpm; no symlink workspace, no app-support
    cache, no renderer-source resolution at run time.

    Raises ``RuntimeError`` if the project does not look like a vendored
    bt-web-report repo (missing ``project.yaml`` or ``package.json``), or
    if pnpm exits non-zero.
    """

    resolved = project_path.expanduser().resolve()
    if not (resolved / "project.yaml").exists():
        raise RuntimeError(f"Not a bt-web-report project (no project.yaml): {resolved}")
    if not (resolved / "package.json").exists():
        raise RuntimeError(
            f"Project has no package.json — it may be a pre-vendored repo. "
            f"Run `btwr re-seed {resolved}` to update it to the vendored layout."
        )
    validate_project_yaml(resolved)

    args = (pnpm_executable, "run", script, *extra_args)
    result = subprocess.run(args, cwd=resolved, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pnpm run {script} failed with exit {result.returncode}.")


def project_slug(project_path: Path) -> str:
    """Return the project's slug from ``project.yaml`` (fallback: parent dir name)."""

    project_file = project_path / "project.yaml"
    if project_file.exists():
        raw = yaml.safe_load(project_file.read_text()) or {}
        slug = raw.get("slug")
        if isinstance(slug, str) and slug.strip():
            return slug.strip()
    return project_path.parent.name.lower().replace(" ", "-")
