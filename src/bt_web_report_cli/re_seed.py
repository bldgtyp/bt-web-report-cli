"""``btwr re-seed`` — update a vendored project's renderer source.

Phase 5 of the Option-C migration. Per the plan's decision #3, re-seeds are
expected to be rare ("once a site is live, we 99.99% never want it to
change"), so this implementation is deliberately simple: it diffs the new
template seed against the project's current vendored files, prints the
unified diff, and on confirm overwrites the changed files in one commit.

Preserved paths (never overwritten) are the authored project content:

    content/, data/, public/, project.yaml, .bldgtyp/* (re-stamped, not diffed),
    .git/

Vendored paths from SEED_PAYLOAD / SEED_WORKFLOW_MAP are eligible for
overwrite. A dirty working tree blocks the run unless the only dirty paths
are in the preserved set.
"""

from __future__ import annotations

import difflib
import filecmp
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from bt_web_report_cli import __version__ as CLI_VERSION
from bt_web_report_cli.new_project import (
    IGNORED_TEMPLATE_CONTENT_NAMES,
    PLATFORM_DIR,
    PLATFORM_YAML_NAME,
    SEED_PAYLOAD,
    SEED_WORKFLOW_MAP,
    _run_command,
)

PRESERVED_RELATIVE_PATHS: tuple[str, ...] = (
    "content",
    "data",
    "public",
    "project.yaml",
    ".git",
)


class ReSeedError(RuntimeError):
    """Raised when re-seed cannot proceed."""


@dataclass(frozen=True)
class ReSeedFileChange:
    """One file-level change planned by a re-seed."""

    relative_path: Path
    action: str  # "add" | "remove" | "modify"
    diff: str  # unified-diff text for modify, empty otherwise


@dataclass(frozen=True)
class ReSeedPlan:
    """The full plan for one re-seed run."""

    project_path: Path
    template_path: Path
    target_ref: str
    previous_ref: str | None
    changes: tuple[ReSeedFileChange, ...]

    @property
    def is_noop(self) -> bool:
        return not self.changes


def plan_re_seed(
    project_path: Path,
    *,
    template_path: Path,
    target_ref: str | None = None,
) -> ReSeedPlan:
    """Compute the re-seed plan without making any writes.

    ``target_ref`` is the SHA the template was checked out to; if ``None``
    the current ``HEAD`` of the template is used. The function does NOT
    fetch or check out — callers must ensure the template tree is already
    at the desired ref. This separation keeps the planner pure and testable.
    """

    project = project_path.expanduser().resolve()
    template = template_path.expanduser().resolve()

    if not (project / "project.yaml").exists():
        raise ReSeedError(f"Not a bt-web-report project (no project.yaml): {project}")
    if not (template / "package.json").exists():
        raise ReSeedError(f"Not a template tree (no package.json): {template}")

    previous = _read_previous_renderer_ref(project)
    resolved_target = target_ref or _resolve_template_head(template)
    if resolved_target == "unknown":
        raise ReSeedError(
            f"Could not resolve template HEAD for {template}. Re-seed needs an "
            "explicit ref via --from <SHA> when the template is not a git checkout."
        )

    changes: list[ReSeedFileChange] = []
    for relative in _iter_seed_relpaths(template):
        changes.extend(_diff_one(project, template, relative))
    for source_rel, target_rel in SEED_WORKFLOW_MAP:
        changes.extend(_diff_workflow(project, template, Path(source_rel), Path(target_rel)))

    return ReSeedPlan(
        project_path=project,
        template_path=template,
        target_ref=resolved_target,
        previous_ref=previous,
        changes=tuple(changes),
    )


def apply_re_seed(plan: ReSeedPlan, *, commit: bool = True) -> int:
    """Execute the plan; return the number of files written."""

    project = plan.project_path
    template = plan.template_path

    _refuse_dirty_working_tree(project)

    written = 0
    for change in plan.changes:
        destination = project / change.relative_path
        if change.action == "remove":
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            elif destination.is_dir():
                shutil.rmtree(destination)
            written += 1
            continue

        source = _resolve_source_for(change.relative_path, template)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns(*IGNORED_TEMPLATE_CONTENT_NAMES),
            )
        else:
            shutil.copy2(source, destination)
        written += 1

    _stamp_platform_yaml(project, target_ref=plan.target_ref)

    if commit:
        _commit_re_seed(project, plan.target_ref)
    return written


def render_plan_text(plan: ReSeedPlan, *, max_diff_lines_per_file: int = 200) -> str:
    """Format the plan as text suitable for stdout."""

    if plan.is_noop:
        return (
            f"re-seed: {plan.project_path}\n"
            f"  template: {plan.template_path}\n"
            f"  target ref:   {plan.target_ref}\n"
            f"  previous ref: {plan.previous_ref or '(unknown)'}\n"
            "  no changes — project is already at the target template state.\n"
        )

    lines: list[str] = [
        f"re-seed: {plan.project_path}",
        f"  template:    {plan.template_path}",
        f"  target ref:  {plan.target_ref}",
        f"  previous:    {plan.previous_ref or '(unknown)'}",
        f"  changes:     {len(plan.changes)}",
        "",
    ]
    for change in plan.changes:
        lines.append(f"  [{change.action}] {change.relative_path}")
    lines.append("")

    for change in plan.changes:
        if not change.diff:
            continue
        diff_lines = change.diff.splitlines()
        if len(diff_lines) > max_diff_lines_per_file:
            head = "\n".join(diff_lines[:max_diff_lines_per_file])
            lines.append(head)
            omitted = len(diff_lines) - max_diff_lines_per_file
            lines.append(f"  ... ({omitted} more lines)")
        else:
            lines.append(change.diff)
        lines.append("")
    return "\n".join(lines)


def _iter_seed_relpaths(template: Path) -> Iterable[Path]:
    """Yield every relative path the seed payload would write."""

    for name in SEED_PAYLOAD:
        source = template / name
        if not source.exists():
            continue
        if source.is_dir():
            for child in _walk_files(source, IGNORED_TEMPLATE_CONTENT_NAMES):
                yield Path(name) / child.relative_to(source)
        else:
            yield Path(name)


def _walk_files(root: Path, ignored: set[str]) -> Iterable[Path]:
    for child in root.iterdir():
        if child.name in ignored:
            continue
        if child.is_dir():
            yield from _walk_files(child, ignored)
        else:
            yield child


def _diff_one(project: Path, template: Path, relative: Path) -> Iterable[ReSeedFileChange]:
    if _is_preserved(relative):
        return []
    project_file = project / relative
    template_file = template / relative
    if not project_file.exists():
        return [ReSeedFileChange(relative_path=relative, action="add", diff="")]
    if not template_file.exists():
        # Should not happen — _iter_seed_relpaths only yields existing files.
        return []
    if filecmp.cmp(project_file, template_file, shallow=False):
        return []

    diff_text = _unified_diff(project_file, template_file, str(relative))
    return [ReSeedFileChange(relative_path=relative, action="modify", diff=diff_text)]


def _diff_workflow(
    project: Path, template: Path, source_rel: Path, target_rel: Path
) -> Iterable[ReSeedFileChange]:
    source = template / source_rel
    destination = project / target_rel
    if not source.exists():
        raise ReSeedError(f"Template missing seed workflow: {source}")
    if not destination.exists():
        return [ReSeedFileChange(relative_path=target_rel, action="add", diff="")]
    if filecmp.cmp(source, destination, shallow=False):
        return []
    diff_text = _unified_diff(destination, source, str(target_rel))
    return [ReSeedFileChange(relative_path=target_rel, action="modify", diff=diff_text)]


def _unified_diff(current: Path, target: Path, label: str) -> str:
    try:
        current_lines = current.read_text().splitlines(keepends=True)
        target_lines = target.read_text().splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"  (binary file differs: {label})\n"
    diff = difflib.unified_diff(
        current_lines,
        target_lines,
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
        n=3,
    )
    return "".join(diff)


def _resolve_source_for(relative: Path, template: Path) -> Path:
    """Map a project-relative path back to its source in the template tree."""

    for source_rel, target_rel in SEED_WORKFLOW_MAP:
        if Path(target_rel) == relative:
            return template / source_rel
    return template / relative


def _is_preserved(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    top = parts[0]
    if top in PRESERVED_RELATIVE_PATHS:
        return True
    return False


def _read_previous_renderer_ref(project: Path) -> str | None:
    platform_file = project / PLATFORM_DIR / PLATFORM_YAML_NAME
    if not platform_file.exists():
        return None
    try:
        raw = yaml.safe_load(platform_file.read_text())
    except yaml.YAMLError:
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("renderer_seed_ref")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_template_head(template: Path) -> str:
    result = _run_command(("git", "rev-parse", "HEAD"), cwd=template, check=False)
    if result.returncode == 0:
        sha = result.stdout.strip()
        if sha:
            return sha
    return "unknown"


def _refuse_dirty_working_tree(project: Path) -> None:
    """Refuse to run if the project has dirty non-preserved files."""

    result = _run_command(("git", "status", "--porcelain"), cwd=project, check=False)
    if result.returncode != 0:
        # Not a git repo — let re-seed proceed (the user can manually inspect).
        return
    dirty_lines = [line for line in result.stdout.splitlines() if line.strip()]
    blocking: list[str] = []
    for line in dirty_lines:
        # Format: 'XY <path>' where XY is the status code (2 chars + space).
        relative = line[3:].strip()
        if relative.startswith("\"") and relative.endswith("\""):
            relative = relative[1:-1]
        # `git status` may show "old -> new" for renames; we only need the new path.
        if " -> " in relative:
            relative = relative.split(" -> ", 1)[1]
        if not _is_preserved(Path(relative)):
            blocking.append(relative)
    if blocking:
        bullets = "\n".join(f"  {p}" for p in blocking[:20])
        more = f"\n  ... and {len(blocking) - 20} more" if len(blocking) > 20 else ""
        raise ReSeedError(
            "Refusing to re-seed: working tree has dirty non-preserved paths.\n"
            f"{bullets}{more}\n"
            "Commit or stash these changes first."
        )


def _stamp_platform_yaml(project: Path, *, target_ref: str) -> None:
    """Write the new .bldgtyp/platform.yaml with the resolved ref + timestamp."""

    platform_dir = project / PLATFORM_DIR
    platform_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "renderer_seed_ref": target_ref,
        "schemas_pin": _read_schemas_pin(project),
        "cli_version": CLI_VERSION,
        "seeded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (platform_dir / PLATFORM_YAML_NAME).write_text(yaml.safe_dump(payload, sort_keys=False))


def _read_schemas_pin(project: Path) -> str:
    package = project / "package.json"
    if not package.exists():
        return "unknown"
    import json

    try:
        data = json.loads(package.read_text())
    except json.JSONDecodeError:
        return "unknown"
    deps = data.get("dependencies") or {}
    spec = deps.get("@bldgtyp/web-report-schemas")
    return spec or "unknown"


def _commit_re_seed(project: Path, target_ref: str) -> None:
    """Stage the result and commit on the current branch (no push)."""

    if not (project / ".git").exists():
        return
    _run_command(("git", "add", "-A"), cwd=project)
    staged = _run_command(("git", "diff", "--cached", "--quiet"), cwd=project, check=False)
    if staged.returncode == 0:
        return
    short = target_ref[:7] if len(target_ref) >= 7 else target_ref
    message = f"chore(renderer): re-seed to template@{short}"
    _run_command(("git", "commit", "-m", message), cwd=project)
