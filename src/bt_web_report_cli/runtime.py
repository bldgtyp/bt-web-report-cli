"""Shared renderer runtime for content-only project repositories."""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import tempfile
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
TINA_CONTENT_ROOT_ENV = "BTWR_TINA_CONTENT_ROOT"

APP_SUPPORT_DEFAULT = Path("~/Library/Application Support/bt-web-report-manager").expanduser()
REMOVE_RETRY_DELAYS = (0.1, 0.25, 0.5)
REMOVE_RETRY_ERRNOS = {errno.ENOTEMPTY, errno.EBUSY, errno.EPERM}
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
    """Return the shared manager-owned runtime root."""

    override = os.environ.get(APP_SUPPORT_ENV) or os.environ.get(MANAGER_APP_SUPPORT_ENV)
    if override:
        return Path(override).expanduser()
    return APP_SUPPORT_DEFAULT


def resolve_renderer_source(explicit: Path | None = None) -> Path | None:
    """Find the source renderer used to refresh app-support runtime files."""

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


def renderer_dir(base_dir: Path | None = None) -> Path:
    return (base_dir or app_support_dir()) / "renderer" / "current"


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


def ensure_renderer(
    *,
    renderer_source: Path | None = None,
    base_dir: Path | None = None,
    pnpm_executable: str = "pnpm",
    install: bool = True,
) -> Path:
    """Refresh the shared renderer and install deps once in app support."""

    target = renderer_dir(base_dir)
    source = resolve_renderer_source(renderer_source)
    if source is not None:
        _sync_renderer_source(source, target)
        target_node_modules = target / "node_modules"
        if target_node_modules.is_symlink():
            _remove(target_node_modules)
    elif not (target / "package.json").exists():
        msg = (
            "No renderer runtime is installed. Set BTWR_RENDERER_SOURCE or pass "
            "--renderer-source once so btwr can populate the shared renderer."
        )
        raise RuntimeError(msg)

    if install and not (target / "node_modules").exists():
        _install_renderer_dependencies(target, pnpm_executable)
    return target


def prepare_runtime_workspace(
    project_path: Path,
    *,
    kind: Literal["build", "preview"],
    renderer_source: Path | None = None,
    base_dir: Path | None = None,
    pnpm_executable: str = "pnpm",
    install: bool = True,
) -> RuntimeWorkspace:
    """Create a disposable symlink workspace for a content-only project."""

    resolved_project = project_path.expanduser().resolve()
    validate_project_yaml(resolved_project)

    renderer = ensure_renderer(
        renderer_source=renderer_source,
        base_dir=base_dir,
        pnpm_executable=pnpm_executable,
        install=install,
    )
    bucket = "builds" if kind == "build" else "previews"
    workspace = (base_dir or app_support_dir()) / bucket / project_slug(resolved_project)
    if workspace.exists():
        _remove_tree(workspace)
    workspace.mkdir(parents=True)

    for name in RENDERER_PAYLOAD:
        source = renderer / name
        if source.exists():
            destination = workspace / name
            if name in LOCAL_RENDERER_PAYLOAD and source.is_dir():
                shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*IGNORED_RENDERER_NAMES))
            else:
                _symlink(source, destination)

    node_modules = renderer / "node_modules"
    if node_modules.exists():
        _symlink(node_modules, workspace / "node_modules")

    for name in PROJECT_PAYLOAD:
        source = resolved_project / name
        if source.exists():
            _symlink(source, workspace / name)

    return RuntimeWorkspace(project_path=resolved_project, renderer_path=renderer, workspace_path=workspace)


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
    workspace = prepare_runtime_workspace(
        project_path,
        kind=kind,
        renderer_source=renderer_source,
        base_dir=base_dir,
        pnpm_executable=pnpm_executable,
        install=install,
    )
    env = os.environ.copy()
    env[TINA_CONTENT_ROOT_ENV] = os.path.relpath(workspace.project_path, workspace.workspace_path / "tina")
    result = subprocess.run((pnpm_executable, script), cwd=workspace.workspace_path, env=env, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Renderer script '{script}' failed with exit {result.returncode}.")
    return workspace


def _sync_renderer_source(source: Path, target: Path) -> None:
    if not source.exists():
        raise RuntimeError(f"Renderer source does not exist: {source}")
    target.mkdir(parents=True, exist_ok=True)

    source_names = {item.name for item in source.iterdir() if item.name not in IGNORED_RENDERER_NAMES}
    for existing in target.iterdir():
        if existing.name in IGNORED_RENDERER_NAMES:
            continue
        if existing.name not in source_names:
            _remove(existing)

    for item in source.iterdir():
        if item.name in IGNORED_RENDERER_NAMES:
            continue
        destination = target / item.name
        if destination.exists() or destination.is_symlink():
            _remove(destination)
        if item.is_dir():
            shutil.copytree(item, destination, ignore=shutil.ignore_patterns(*IGNORED_RENDERER_NAMES))
        else:
            shutil.copy2(item, destination)


def _install_renderer_dependencies(target: Path, pnpm_executable: str) -> None:
    env = os.environ.copy()
    token = env.get("NODE_AUTH_TOKEN") or _github_auth_token()
    if token:
        with tempfile.TemporaryDirectory() as temp_dir:
            npmrc = Path(temp_dir) / ".npmrc"
            npmrc.write_text(
                "@bldgtyp:registry=https://npm.pkg.github.com\n" f"//npm.pkg.github.com/:_authToken={token}\n"
            )
            env["NPM_CONFIG_USERCONFIG"] = str(npmrc)
            result = _run_pnpm_install(target, pnpm_executable, env)
    else:
        result = _run_pnpm_install(target, pnpm_executable, env)
    if result.returncode != 0:
        raise RuntimeError(f"Renderer dependency install failed with exit {result.returncode}.")


def _run_pnpm_install(target: Path, pnpm_executable: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (pnpm_executable, "install", "--ignore-scripts", "--no-frozen-lockfile"),
        cwd=target,
        env=env,
        text=True,
        check=False,
    )


def _github_auth_token() -> str | None:
    try:
        result = subprocess.run(("gh", "auth", "token"), text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


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
