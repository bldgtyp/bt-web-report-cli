"""Content-only project bootstrap for `btwr new`."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import yaml

from bt_web_report_cli.runtime import resolve_renderer_source

CONTENT_PAYLOAD = ("content", "data", "public", ".github", ".gitignore", ".dropboxignore", ".editorconfig", "README.md")
IGNORED_EXISTING_NAMES = {".DS_Store", ".localized", "Icon\r", "desktop.ini", "Thumbs.db"}


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
            shutil.copytree(item, destination, ignore=shutil.ignore_patterns("node_modules", "dist", ".astro"))
        else:
            shutil.copy2(item, destination)

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
        "schema_version": "0.1.0",
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
    }
    (target / "project.yaml").write_text(yaml.safe_dump(value, sort_keys=False))


def _init_git(target: Path) -> None:
    if (target / ".git").exists():
        return
    subprocess.run(("git", "init"), cwd=target, text=True, check=True)
    subprocess.run(("git", "branch", "-M", "main"), cwd=target, text=True, check=True)
