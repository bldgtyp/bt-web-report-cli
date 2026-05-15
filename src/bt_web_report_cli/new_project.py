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

CONTENT_PAYLOAD = ("content", "data", "public", ".github", ".gitignore", ".dropboxignore", ".editorconfig", "README.md")
IGNORED_EXISTING_NAMES = {".DS_Store", ".localized", "Icon\r", "desktop.ini", "Thumbs.db"}


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
