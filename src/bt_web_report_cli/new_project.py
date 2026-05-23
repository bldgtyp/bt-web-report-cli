"""Content-only project bootstrap for `btwr new`."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

from bt_web_report_cli.runtime import resolve_renderer_source
from bt_web_report_schemas.project import SCHEMA_VERSION

CONTENT_PAYLOAD = ("content", "data", "public", ".gitignore", ".dropboxignore", ".editorconfig", "README.md")
IGNORED_TEMPLATE_CONTENT_NAMES = {"node_modules", "dist", ".astro", "recommended-assemblies.zip"}
IGNORED_EXISTING_NAMES = {".DS_Store", ".localized", "Icon\r", "desktop.ini", "Thumbs.db"}
RENDERER_REF_ENV = "BTWR_RENDERER_REF"
SCHEMAS_REF_ENV = "BTWR_SCHEMAS_REF"
RENDERER_WORKFLOWS = (Path(".github/workflows/ci.yml"), Path(".github/workflows/deploy.yml"))

# Per-project workflow templates that ship inside the template repo under
# `scripts/`. These are the canonical sources for what `btwr new` writes
# into each project's `.github/workflows/`. We never copy the template's
# own `.github/workflows/{ci,deploy}.yml` — those describe the template's
# own builds and contain settings (e.g. `manage-custom-domain: false`)
# that are wrong for per-project deploys.
PER_PROJECT_WORKFLOW_SOURCES = (
    (Path("scripts/per-project-ci.yml"), Path(".github/workflows/ci.yml")),
    (Path("scripts/per-project-deploy.yml"), Path(".github/workflows/deploy.yml")),
)


@dataclass(frozen=True)
class PublishProjectResult:
    repo_full_name: str
    remote_url: str
    committed: bool
    pushed: bool


def create_project(
    target_web_path: Path,
    *,
    slug: str,
    title: str,
    repo: str,
    production_url: str,
    client: str | None = None,
    building: str | None = None,
    phase: str | None = None,
    phpp: Path | None = None,
    renderer_source: Path | None = None,
    init_git: bool = True,
    overwrite: bool = False,
) -> Path:
    """Create a content-only `04_Web` folder from the shared template payload."""

    target = target_web_path.expanduser().resolve()
    existing_items = meaningful_existing_items(target)
    if existing_items and not overwrite:
        item_list = ", ".join(item.name for item in existing_items[:5])
        suffix = "" if len(existing_items) <= 5 else f", and {len(existing_items) - 5} more"
        raise RuntimeError(f"Target folder already exists and is not empty: {target} ({item_list}{suffix})")
    if existing_items and overwrite:
        _clear_target(target)
    target.mkdir(parents=True, exist_ok=True)

    source = resolve_renderer_source(renderer_source)
    if source is None:
        raise RuntimeError("Renderer source could not be found. Set BTWR_RENDERER_SOURCE or pass --renderer-source.")

    for name in CONTENT_PAYLOAD:
        item = source / name
        if not item.exists():
            continue
        destination = target / name
        if item.is_dir():
            shutil.copytree(item, destination, ignore=shutil.ignore_patterns(*IGNORED_TEMPLATE_CONTENT_NAMES))
        else:
            shutil.copy2(item, destination)

    _seed_per_project_workflows(source, target)
    _pin_renderer_workflows(
        target,
        renderer_ref=_resolve_renderer_ref(source),
        schemas_ref=_resolve_schemas_ref(source),
    )

    _write_project_yaml(
        target,
        slug=slug,
        title=title,
        client=client,
        building=building,
        phase=phase,
        phpp=phpp,
        production_url=production_url,
        cloudflare_pages_project=repo,
    )

    if init_git:
        _init_git(target)
    return target


def publish_project(
    target_web_path: Path,
    *,
    repo_owner: str,
    repo_name: str,
    commit_message: str,
    git_executable: str = "git",
    gh_executable: str = "gh",
) -> PublishProjectResult:
    """Create/verify the GitHub repo, wire origin, commit the payload, and push main."""

    target = target_web_path.expanduser().resolve()
    if not target.exists():
        raise RuntimeError(f"Target folder does not exist: {target}")

    _init_git(target, git_executable=git_executable)
    repo_full_name = f"{repo_owner}/{repo_name}"
    remote_url = f"https://github.com/{repo_full_name}.git"

    _ensure_github_repo(repo_full_name, gh_executable=gh_executable)
    _ensure_origin(target, remote_url, git_executable=git_executable)
    committed = _commit_project(target, commit_message=commit_message, git_executable=git_executable)
    _run_command((git_executable, "push", "-u", "origin", "HEAD:main"), cwd=target)

    return PublishProjectResult(
        repo_full_name=repo_full_name,
        remote_url=remote_url,
        committed=committed,
        pushed=True,
    )


def meaningful_existing_items(target: Path) -> list[Path]:
    if not target.exists():
        return []
    return [item for item in target.iterdir() if item.name not in IGNORED_EXISTING_NAMES]


def _clear_target(target: Path) -> None:
    for item in target.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def _write_project_yaml(
    target: Path,
    *,
    slug: str,
    title: str,
    client: str | None,
    building: str | None,
    phase: str | None,
    phpp: Path | None,
    production_url: str,
    cloudflare_pages_project: str,
) -> None:
    phpp_path = ""
    if phpp is not None:
        phpp_path = os.path.relpath(phpp.expanduser().resolve(), target)
    value = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "project_title": title,
        "client_name": client or "TBD",
        "building_name": building or "TBD",
        "phase": phase or "TBD",
        "report_date": date.today().isoformat(),
        "prepared_by": "bldgtyp, llc",
        "contact_email": "ed@bldgtyp.com",
        "target_standard": "TBD",
        "certification_program": "TBD",
        "certification_path": "TBD",
        "building": {
            "address": "TBD",
            "city": "TBD",
            "state": "TBD",
            "climate_zone": "TBD",
            "building_type": "TBD",
        },
        "source_files": {
            "phpp_path": phpp_path,
            "data_dir": "data",
            "assets_dir": "public/assets",
        },
        "publishing": {
            "production_url": production_url,
            "cloudflare_pages_project": cloudflare_pages_project,
        },
        "narrative": {
            "climate": {
                "weather_station_name": "TBD",
                "state_name": "TBD",
                "ashrae_location_name": "TBD",
            },
        },
    }
    (target / "project.yaml").write_text(yaml.safe_dump(value, sort_keys=False))


def _resolve_renderer_ref(source: Path) -> str:
    """Resolve the explicit ref to pin per-project workflows to.

    Resolution order:
      1. ``BTWR_RENDERER_REF`` env explicit SHA/tag → use as-is.
      2. ``BTWR_RENDERER_REF=HEAD`` or unset → resolve via ``git rev-parse HEAD``
         in the workspace template checkout. This gives a deterministic
         per-project pin without requiring the operator to look up the SHA.
      3. ``BTWR_RENDERER_REF=main`` / ``master`` → refused. Pinning to a
         floating branch is exactly what the cascade-stop forbids.

    Auto-resolving to HEAD when no env is set lets Manager-launched
    ``btwr new`` work out of the box, while still refusing the
    foot-gun cases.
    """

    override = os.environ.get(RENDERER_REF_ENV)
    if override and override in {"main", "master"}:
        raise RuntimeError(
            f"{RENDERER_REF_ENV}={override!r} is a floating branch — pin to an explicit "
            "SHA or tag instead, or unset the env var to auto-resolve to the template's "
            "current HEAD SHA."
        )
    if override and override.upper() != "HEAD":
        return override

    # No override or HEAD → resolve via the workspace template's HEAD SHA.
    result = _run_command(("git", "rev-parse", "HEAD"), cwd=source, check=False)
    if result.returncode == 0:
        ref = result.stdout.strip()
        if ref:
            return ref
    raise RuntimeError(
        f"Cannot resolve renderer ref: `git rev-parse HEAD` failed in {source}. "
        f"Set {RENDERER_REF_ENV} to an explicit SHA, or ensure the workspace "
        "template is a git checkout."
    )


def _resolve_schemas_ref(source: Path) -> str:
    """Resolve the explicit schemas ref to pin per-project workflow inputs to.

    Resolution order:
      1. ``BTWR_SCHEMAS_REF`` env var (HEAD → resolve via the sibling checkout).
      2. ``<source>/../bt-web-report-schemas`` HEAD (the workspace sibling).
      3. RuntimeError — schemas pinning is required for deterministic builds.

    Like :func:`_resolve_renderer_ref`, refuses floating ``main`` / ``master``.
    """

    override = os.environ.get(SCHEMAS_REF_ENV)
    sibling = source.parent / "bt-web-report-schemas"

    if override and override.upper() != "HEAD":
        if override in {"main", "master"}:
            raise RuntimeError(
                f"{SCHEMAS_REF_ENV}={override!r} is a floating branch — pin to an explicit "
                "SHA or tag instead."
            )
        return override

    # Either no override or override == HEAD; both resolve via the sibling checkout.
    if not sibling.exists():
        raise RuntimeError(
            f"Cannot resolve schemas ref: sibling checkout {sibling} does not exist. "
            f"Either set {SCHEMAS_REF_ENV} to an explicit SHA, or clone "
            "bt-web-report-schemas alongside the template."
        )
    result = _run_command(("git", "rev-parse", "HEAD"), cwd=sibling, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"`git rev-parse HEAD` failed in {sibling}. Set {SCHEMAS_REF_ENV} to an "
            "explicit SHA to bypass auto-resolution."
        )
    return result.stdout.strip()


def _seed_per_project_workflows(source: Path, target: Path) -> None:
    """Copy ``scripts/per-project-{ci,deploy}.yml`` → project's ``.github/workflows/``.

    These templates are the canonical per-project workflow shape (cross-repo
    `uses:`, correct `manage-custom-domain` default, no `BLDGTYP_PACKAGES_TOKEN`
    secret). The seed never copies the template repo's own
    ``.github/workflows/{ci,deploy}.yml`` files — those describe the template's
    own CI and contain settings (e.g. ``manage-custom-domain: false``) that
    are wrong for per-project deploys.
    """

    for source_rel, target_rel in PER_PROJECT_WORKFLOW_SOURCES:
        src = source / source_rel
        if not src.exists():
            raise RuntimeError(
                f"Template is missing required seed workflow: {src}. "
                "The template's `scripts/per-project-*.yml` files are the source "
                "of truth for per-project workflows."
            )
        dst = target / target_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


_LOCAL_REUSABLE_PREFIX = "uses: ./.github/workflows/"
_CROSS_REPO_REUSABLE_PREFIX = "uses: bldgtyp/bt-web-report-template/.github/workflows/"
_CROSS_REPO_USES_TEMPLATE = "uses: bldgtyp/bt-web-report-template/.github/workflows/{name}@{ref}"


def _pin_renderer_workflows(target: Path, *, renderer_ref: str, schemas_ref: str) -> None:
    """Rewrite per-project workflow files to pin all template refs.

    Three forms are handled:
      1. ``uses: ./.github/workflows/<name>`` (template's own local form) →
         rewritten to cross-repo with the renderer SHA.
      2. ``uses: bldgtyp/bt-web-report-template/.github/workflows/<name>@<ref>``
         (per-project-*.yml form, where ``<ref>`` is typically ``main``) →
         the ``@<ref>`` suffix is replaced with the renderer SHA.
      3. ``renderer-ref:`` / ``schemas-ref:`` workflow inputs → rewritten to
         the resolved renderer / schemas SHA respectively.

    Legacy ``repository: bldgtyp/bt-web-report-template`` blocks are also
    pinned for back-compat with any older workflow shape that might still
    show up in a hand-edited project repo.
    """

    for relative_path in RENDERER_WORKFLOWS:
        workflow = target / relative_path
        if not workflow.exists():
            continue
        lines = workflow.read_text().splitlines()
        pinned: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]

            if stripped.startswith(_LOCAL_REUSABLE_PREFIX):
                workflow_name = stripped[len(_LOCAL_REUSABLE_PREFIX) :]
                pinned.append(indent + _CROSS_REPO_USES_TEMPLATE.format(name=workflow_name, ref=renderer_ref))
                index += 1
                continue

            if stripped.startswith(_CROSS_REPO_REUSABLE_PREFIX):
                # `uses: bldgtyp/bt-web-report-template/.github/workflows/<name>@<ref>`
                tail = stripped[len(_CROSS_REPO_REUSABLE_PREFIX) :]
                if "@" in tail:
                    workflow_name = tail.split("@", 1)[0]
                else:
                    workflow_name = tail
                pinned.append(indent + _CROSS_REPO_USES_TEMPLATE.format(name=workflow_name, ref=renderer_ref))
                index += 1
                continue

            if stripped.startswith("renderer-ref:"):
                pinned.append(f"{indent}renderer-ref: {renderer_ref}")
                index += 1
                continue

            if stripped.startswith("schemas-ref:"):
                pinned.append(f"{indent}schemas-ref: {schemas_ref}")
                index += 1
                continue

            pinned.append(line)

            if stripped == "repository: bldgtyp/bt-web-report-template":
                next_index = index + 1
                if next_index < len(lines) and lines[next_index].lstrip().startswith("ref:"):
                    pinned.append(f"{indent}ref: {renderer_ref}")
                    index += 2
                    continue
                pinned.append(f"{indent}ref: {renderer_ref}")
            index += 1
        workflow.write_text("\n".join(pinned) + "\n")


def _init_git(target: Path, *, git_executable: str = "git") -> None:
    if (target / ".git").exists():
        return
    _run_command((git_executable, "init"), cwd=target)
    _run_command((git_executable, "branch", "-M", "main"), cwd=target)


def _ensure_github_repo(repo_full_name: str, *, gh_executable: str) -> None:
    view = _run_command(
        (gh_executable, "repo", "view", repo_full_name, "--json", "isPrivate"),
        check=False,
    )
    if view.returncode == 0:
        if json.loads(view.stdout).get("isPrivate"):
            _run_command(
                (
                    gh_executable,
                    "repo",
                    "edit",
                    repo_full_name,
                    "--visibility",
                    "public",
                    "--accept-visibility-change-consequences",
                )
            )
        return
    _run_command((gh_executable, "repo", "create", repo_full_name, "--public"))


def _ensure_origin(target: Path, remote_url: str, *, git_executable: str) -> None:
    origin = _run_command((git_executable, "remote", "get-url", "origin"), cwd=target, check=False)
    if origin.returncode == 0:
        if origin.stdout.strip() != remote_url:
            _run_command((git_executable, "remote", "set-url", "origin", remote_url), cwd=target)
        return
    _run_command((git_executable, "remote", "add", "origin", remote_url), cwd=target)


def _commit_project(target: Path, *, commit_message: str, git_executable: str) -> bool:
    _run_command((git_executable, "add", "-A", "--", ".", ":!.bldgtyp/lock.yaml"), cwd=target)
    staged = _run_command((git_executable, "diff", "--cached", "--quiet"), cwd=target, check=False)
    if staged.returncode == 0:
        return False
    _run_command((git_executable, "commit", "-m", commit_message), cwd=target)
    return True


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(tuple(args), cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(_format_command_error(args, result))
    return result


def _format_command_error(args: Sequence[str], result: subprocess.CompletedProcess[str]) -> str:
    command = " ".join(shlex.quote(part) for part in args)
    pieces = [f"Command failed ({result.returncode}): {command}"]
    if result.stdout.strip():
        pieces.append(result.stdout.strip())
    if result.stderr.strip():
        pieces.append(result.stderr.strip())
    return "\n".join(pieces)
