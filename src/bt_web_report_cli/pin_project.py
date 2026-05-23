"""One-shot `btwr pin` — rewrites a per-project repo's ci.yml / deploy.yml
to replace floating ``main`` refs with explicit SHAs.

Background: today, per-project repos call the template's reusable workflow at
``@main`` and pass ``renderer-ref: main`` / ``schemas-ref: main``. That means
every push to the template or schemas repo can cascade into a redeploy of
every live project. Phase 1 of the Option-C migration stops that cascade by
pinning the workflow file, the renderer source, and the schemas source to
explicit commits.

The command is intentionally a string-replacement on the workflow YAML — it
does not parse YAML — so the rewrite is predictable and the diff is small.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

WORKFLOWS = (
    Path(".github") / "workflows" / "ci.yml",
    Path(".github") / "workflows" / "deploy.yml",
)
_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
_TEMPLATE_USES_RE = re.compile(
    r"^(?P<indent>\s*)uses:\s*bldgtyp/bt-web-report-template/"
    r"(?P<path>\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml)@(?P<ref>[A-Za-z0-9_./-]+)\s*$"
)
_REF_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>renderer-ref|schemas-ref):\s*(?P<value>\S+)\s*$")


class PinError(RuntimeError):
    """Raised when a project cannot be pinned."""


@dataclass(frozen=True)
class PinPlan:
    """Resolved targets for a pin operation."""

    project_path: Path
    workflow_ref: str
    renderer_ref: str
    schemas_ref: str


@dataclass(frozen=True)
class PinResult:
    """Outcome of pinning a single project."""

    project_path: Path
    files_changed: tuple[Path, ...]
    files_unchanged: tuple[Path, ...]
    files_missing: tuple[Path, ...]


def pin_project(
    project_path: Path,
    *,
    renderer_ref: str,
    schemas_ref: str,
    workflow_ref: str | None = None,
) -> PinResult:
    """Rewrite ``project_path``'s workflows to pin renderer/schemas/workflow refs.

    Idempotent: re-running with the same refs produces zero diff. If a workflow
    is already pinned to a different SHA, this call overwrites it — callers
    who want to detect already-pinned files should inspect the workflow text
    first via :func:`workflow_pin_status`.

    Raises :class:`PinError` if the project has no workflows directory, or if
    a workflow does not contain any of the expected lines to rewrite.
    """

    project = project_path.expanduser().resolve()
    if not project.exists():
        raise PinError(f"Project path does not exist: {project}")
    workflows_dir = project / ".github" / "workflows"
    if not workflows_dir.is_dir():
        raise PinError(f"No .github/workflows directory in {project}")

    plan = PinPlan(
        project_path=project,
        workflow_ref=workflow_ref or renderer_ref,
        renderer_ref=renderer_ref,
        schemas_ref=schemas_ref,
    )
    _validate_refs(plan)

    changed: list[Path] = []
    unchanged: list[Path] = []
    missing: list[Path] = []
    matched_any = False
    for relative in WORKFLOWS:
        path = project / relative
        if not path.exists():
            missing.append(relative)
            continue
        original = path.read_text()
        rewritten, matched = _rewrite_workflow_text(original, plan)
        if matched:
            matched_any = True
        if rewritten == original:
            unchanged.append(relative)
        else:
            path.write_text(rewritten)
            changed.append(relative)

    if not matched_any:
        raise PinError(
            f"No pinnable lines found in {project / '.github' / 'workflows'}. "
            "Expected at least one `uses: bldgtyp/bt-web-report-template/...@<ref>`, "
            "`renderer-ref:`, or `schemas-ref:` line."
        )

    return PinResult(
        project_path=project,
        files_changed=tuple(changed),
        files_unchanged=tuple(unchanged),
        files_missing=tuple(missing),
    )


def _validate_refs(plan: PinPlan) -> None:
    """Ensure refs look like SHAs or version tags, not floating branches."""

    for label, value in (
        ("workflow_ref", plan.workflow_ref),
        ("renderer_ref", plan.renderer_ref),
        ("schemas_ref", plan.schemas_ref),
    ):
        if not value:
            raise PinError(f"{label} cannot be empty")
        if value == "main" or value == "master":
            raise PinError(
                f"{label}={value!r} is a floating branch — pin requires an explicit "
                "SHA (full or short) or a version tag like v1.2.3"
            )


def _rewrite_workflow_text(text: str, plan: PinPlan) -> tuple[str, bool]:
    """Return (rewritten_text, did_match_any_pinnable_line)."""

    matched = False
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        eol = ""
        body = line
        if body.endswith("\r\n"):
            eol = "\r\n"
            body = body[:-2]
        elif body.endswith("\n"):
            eol = "\n"
            body = body[:-1]

        uses_match = _TEMPLATE_USES_RE.match(body)
        if uses_match:
            matched = True
            new_body = (
                f"{uses_match['indent']}uses: bldgtyp/bt-web-report-template/"
                f"{uses_match['path']}@{plan.workflow_ref}"
            )
            out.append(new_body + eol)
            continue

        ref_match = _REF_KEY_RE.match(body)
        if ref_match:
            matched = True
            key = ref_match["key"]
            new_value = plan.renderer_ref if key == "renderer-ref" else plan.schemas_ref
            out.append(f"{ref_match['indent']}{key}: {new_value}{eol}")
            continue

        out.append(line)
    return "".join(out), matched


def workflow_pin_status(project_path: Path) -> dict[Path, dict[str, str | None]]:
    """Inspect the current pin state of each workflow.

    Returns a mapping of relative workflow path → dict with keys
    ``workflow``, ``renderer``, ``schemas`` whose values are the current refs
    (or ``None`` when the line is absent).
    """

    project = project_path.expanduser().resolve()
    status: dict[Path, dict[str, str | None]] = {}
    for relative in WORKFLOWS:
        path = project / relative
        if not path.exists():
            continue
        info: dict[str, str | None] = {"workflow": None, "renderer": None, "schemas": None}
        for raw in path.read_text().splitlines():
            body = raw.rstrip("\r\n")
            uses_match = _TEMPLATE_USES_RE.match(body)
            if uses_match:
                info["workflow"] = uses_match["ref"]
                continue
            ref_match = _REF_KEY_RE.match(body)
            if ref_match:
                key = ref_match["key"]
                if key == "renderer-ref":
                    info["renderer"] = ref_match["value"]
                elif key == "schemas-ref":
                    info["schemas"] = ref_match["value"]
        status[relative] = info
    return status
