"""Shared renderer runtime for content-only project repositories.

There is exactly ONE ``node_modules`` directory in the entire system:
the workspace template's ``bt-web-report-template/node_modules/``.
``btwr build / preview / editor`` create disposable runtime workspaces
at ``<workspace>/.builds/{builds,previews}/<slug>/``
and symlink that single ``node_modules`` in. No per-project install,
no app-support copy, no second ``node_modules`` anywhere.

This is the absolute, non-negotiable rule for the bt-web-report platform:
no ``node_modules`` ever exists inside a per-project repo, and only the
workspace template hosts one centrally. See
`context/legacy/design.html` and the project-rebuild constraint thread.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from bt_web_report_schemas.project import Project

APP_SUPPORT_ENV = "BTWR_APP_SUPPORT"
MANAGER_APP_SUPPORT_ENV = "BTWR_MANAGER_APP_SUPPORT"
RENDERER_SOURCE_ENV = "BTWR_RENDERER_SOURCE"
BUILDS_ROOT_ENV = "BTWR_BUILDS_ROOT"
PROJECT_SCHEMA_JSON_ENV = "BTWR_PROJECT_SCHEMA_JSON"
TINA_CONTENT_ROOT_ENV = "BTWR_TINA_CONTENT_ROOT"

APP_SUPPORT_DEFAULT = Path("~/Library/Application Support/bt-web-report-manager").expanduser()
WORKSPACE_ROOT_FALLBACK = Path("~/Dropbox/bldgtyp-00/00_PH_Tools/bldgtyp/bt-web-report").expanduser()

REMOVE_RETRY_DELAYS = (0.1, 0.25, 0.5)
REMOVE_RETRY_ERRNOS = {errno.ENOTEMPTY, errno.EBUSY, errno.EPERM}

# Files / dirs copied or symlinked from the workspace template into the
# disposable runtime workspace. Order: non-LOCAL items become symlinks
# (read-only), LOCAL items get copied (because Astro / Tina need them
# to be siblings of the project content for relative-path resolution).
RENDERER_PAYLOAD = (
    "astro.config.mjs",
    "package.json",
    "pnpm-lock.yaml",
    "playwright",
    "playwright.config.ts",
    "scripts",
    "src",
    "tina",
    "tsconfig.json",
)
LOCAL_RENDERER_PAYLOAD = {"src", "tina"}
PROJECT_PAYLOAD = ("project.yaml", "content", "data", "public")
IGNORED_RENDERER_NAMES = {
    ".astro",
    ".git",
    ".wrangler",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
}


@dataclass(frozen=True)
class RuntimeWorkspace:
    project_path: Path
    renderer_path: Path
    workspace_path: Path


def app_support_dir() -> Path:
    """Return the manager-owned support root (used by `btwr doctor` banner only)."""

    override = os.environ.get(APP_SUPPORT_ENV) or os.environ.get(MANAGER_APP_SUPPORT_ENV)
    if override:
        return Path(override).expanduser()
    return APP_SUPPORT_DEFAULT


def builds_root() -> Path:
    """Return the workspace-level ``.builds/`` root for runtime workspaces.

    Lives under the workspace tree, NEVER inside a per-project Dropbox
    folder. Overridable via ``BTWR_BUILDS_ROOT`` for tests / alternate setups.
    """

    override = os.environ.get(BUILDS_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    workspace_root = _workspace_root_from_source()
    if workspace_root is not None:
        return workspace_root / ".builds"
    return WORKSPACE_ROOT_FALLBACK / ".builds"


def resolve_renderer_source(explicit: Path | None = None) -> Path | None:
    """Find the workspace template — the single canonical renderer source."""

    if explicit is not None:
        return explicit.expanduser().resolve()
    env_value = os.environ.get(RENDERER_SOURCE_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()

    workspace_root = _workspace_root_from_source()
    if workspace_root is not None:
        return workspace_root / "bt-web-report-template"
    return None


def _workspace_root_from_source() -> Path | None:
    """Find the local multi-repo workspace from an editable CLI checkout."""

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "bt-web-report-template"
        if (candidate / "package.json").exists() and (candidate / "src").exists():
            return parent
    if (WORKSPACE_ROOT_FALLBACK / "bt-web-report-template" / "package.json").exists():
        return WORKSPACE_ROOT_FALLBACK
    return None


def project_slug(project_path: Path) -> str:
    project_file = project_path / "project.yaml"
    if project_file.exists():
        raw = yaml.safe_load(project_file.read_text()) or {}
        slug = raw.get("slug")
        if isinstance(slug, str) and slug.strip():
            return slug.strip()
    return project_path.parent.name.lower().replace(" ", "-")


def validate_project_yaml(project_path: Path) -> None:
    """Fail fast when a content repo is not compatible with the renderer schema."""

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


def prepare_runtime_workspace(
    project_path: Path,
    *,
    kind: Literal["build", "preview"],
    renderer_source: Path | None = None,
    base_dir: Path | None = None,
    pnpm_executable: str = "pnpm",  # kept for signature compat; install no longer happens here
    install: bool = True,
) -> RuntimeWorkspace:
    """Build a disposable symlink workspace for a content-only project.

    ``base_dir`` defaults to :func:`builds_root` (the workspace ``.builds/``).
    Layout under ``base_dir`` is ``builds/<slug>/`` (for ``btwr build``) or
    ``previews/<slug>/`` (for ``btwr preview`` / ``btwr editor``).

    ``install`` historically would run ``pnpm install`` into a second
    ``node_modules``; that is no longer permitted. If the workspace template
    has no ``node_modules``, the user must run ``pnpm install`` once in the
    template manually — this method does not duplicate it.
    """

    del pnpm_executable  # signature-compat only

    resolved_project = project_path.expanduser().resolve()
    validate_project_yaml(resolved_project)

    source = resolve_renderer_source(renderer_source)
    if source is None:
        raise RuntimeError(
            "Renderer source could not be found. Set BTWR_RENDERER_SOURCE or pass "
            "--renderer-source so btwr can locate the workspace template."
        )

    node_modules = source / "node_modules"
    if not node_modules.exists():
        raise RuntimeError(
            f"Workspace template has no node_modules: {node_modules}\n"
            "Run `pnpm install` once in the workspace template; that is the single "
            "node_modules the entire system uses. `btwr build/preview/editor` never "
            "installs a per-project copy."
        )
    if install is False:
        # Documented escape hatch — useful in tests / when the caller has
        # already validated the template install is current. No-op here.
        pass

    workspace_root = base_dir or builds_root()
    bucket = "builds" if kind == "build" else "previews"
    workspace = workspace_root / bucket / project_slug(resolved_project)
    if workspace.exists():
        _remove_tree(workspace)
    workspace.mkdir(parents=True)

    for name in RENDERER_PAYLOAD:
        item = source / name
        if not item.exists():
            continue
        destination = workspace / name
        if name in LOCAL_RENDERER_PAYLOAD and item.is_dir():
            shutil.copytree(item, destination, ignore=shutil.ignore_patterns(*IGNORED_RENDERER_NAMES))
        else:
            _symlink(item, destination)

    _symlink(node_modules, workspace / "node_modules")

    for name in PROJECT_PAYLOAD:
        item = resolved_project / name
        if item.exists():
            _symlink(item, workspace / name)

    return RuntimeWorkspace(
        project_path=resolved_project,
        renderer_path=source,
        workspace_path=workspace,
    )


def run_renderer_script(
    project_path: Path,
    script: str,
    *,
    kind: Literal["build", "preview"],
    renderer_source: Path | None = None,
    base_dir: Path | None = None,
    pnpm_executable: str = "pnpm",
    install: bool = True,
) -> RuntimeWorkspace:
    source = resolve_renderer_source(renderer_source)
    workspace = prepare_runtime_workspace(
        project_path,
        kind=kind,
        renderer_source=source,
        base_dir=base_dir,
        install=install,
    )
    env = os.environ.copy()
    env[TINA_CONTENT_ROOT_ENV] = os.path.relpath(workspace.project_path, workspace.workspace_path / "tina")
    project_schema_json = _project_schema_json_for_renderer_source(source)
    if project_schema_json is not None:
        env[PROJECT_SCHEMA_JSON_ENV] = str(project_schema_json)
    result = subprocess.run(
        (pnpm_executable, script),
        cwd=workspace.workspace_path,
        env=env,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Renderer script '{script}' failed with exit {result.returncode}.")
    return workspace


def _project_schema_json_for_renderer_source(source: Path | None) -> Path | None:
    if source is None:
        return None
    candidate = source.parent / "bt-web-report-schemas" / "schemas" / "project.schema.json"
    if candidate.exists():
        return candidate.resolve()
    return None


def _symlink(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        _remove(destination)
    destination.symlink_to(source, target_is_directory=source.is_dir())


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        _remove_tree(path)


def _remove_tree(path: Path) -> None:
    for delay in (*REMOVE_RETRY_DELAYS, None):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno not in REMOVE_RETRY_ERRNOS:
                raise
            if delay is None:
                msg = (
                    f"Could not remove runtime directory {path}: {exc}. "
                    "Stop any running btwr preview/editor for this project and retry."
                )
                raise RuntimeError(msg) from exc
            time.sleep(delay)
