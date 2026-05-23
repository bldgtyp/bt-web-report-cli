"""Tests for ``btwr pin`` — Phase 1 cascade stop."""

from __future__ import annotations

from pathlib import Path

import pytest

from bt_web_report_cli.pin_project import PinError, pin_project, workflow_pin_status

_CI_FLOATING = """name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  build:
    uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@main
    with:
      project-repo: ${{ github.repository }}
      project-ref: ${{ github.ref }}
      renderer-ref: main
      schemas-ref: main
      run-deploy: false
    secrets:
      BLDGTYP_PACKAGES_TOKEN: ${{ secrets.BLDGTYP_PACKAGES_TOKEN }}
"""

_DEPLOY_FLOATING = """name: Deploy to Cloudflare Pages

on:
  workflow_dispatch:
  push:
    branches: [main]

jobs:
  deploy:
    uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@main
    with:
      project-repo: ${{ github.repository }}
      project-ref: ${{ github.ref }}
      renderer-ref: main
      schemas-ref: main
      run-deploy: true
      cloudflare-pages-project: ${{ vars.CLOUDFLARE_PAGES_PROJECT }}
    secrets:
      BLDGTYP_PACKAGES_TOKEN: ${{ secrets.BLDGTYP_PACKAGES_TOKEN }}
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
"""


def _make_project(root: Path, *, ci: str = _CI_FLOATING, deploy: str = _DEPLOY_FLOATING) -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(ci)
    (workflows / "deploy.yml").write_text(deploy)
    return root


def test_pin_rewrites_uses_and_ref_inputs(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "vandam")

    result = pin_project(
        project,
        renderer_ref="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        schemas_ref="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        workflow_ref="cccccccccccccccccccccccccccccccccccccccc",
    )

    ci_text = (project / ".github" / "workflows" / "ci.yml").read_text()
    deploy_text = (project / ".github" / "workflows" / "deploy.yml").read_text()
    for text in (ci_text, deploy_text):
        assert "@main" not in text
        assert ": main" not in text
        assert (
            "uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@"
            "cccccccccccccccccccccccccccccccccccccccc"
        ) in text
        assert "renderer-ref: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in text
        assert "schemas-ref: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in text
    assert set(result.files_changed) == {
        Path(".github/workflows/ci.yml"),
        Path(".github/workflows/deploy.yml"),
    }


def test_pin_defaults_workflow_ref_to_renderer_ref(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "vandam")

    pin_project(
        project,
        renderer_ref="0123456789abcdef0123456789abcdef01234567",
        schemas_ref="fedcba9876543210fedcba9876543210fedcba98",
    )

    ci_text = (project / ".github" / "workflows" / "ci.yml").read_text()
    assert (
        "uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@"
        "0123456789abcdef0123456789abcdef01234567"
    ) in ci_text


def test_pin_is_idempotent(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "vandam")
    args = dict(
        renderer_ref="0123456789abcdef0123456789abcdef01234567",
        schemas_ref="fedcba9876543210fedcba9876543210fedcba98",
    )

    pin_project(project, **args)
    before = (project / ".github" / "workflows" / "ci.yml").read_text()
    result = pin_project(project, **args)
    after = (project / ".github" / "workflows" / "ci.yml").read_text()

    assert before == after
    assert result.files_changed == ()
    assert set(result.files_unchanged) == {
        Path(".github/workflows/ci.yml"),
        Path(".github/workflows/deploy.yml"),
    }


def test_pin_rejects_floating_branch_refs(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "vandam")

    with pytest.raises(PinError, match="floating branch"):
        pin_project(project, renderer_ref="main", schemas_ref="abc1234")
    with pytest.raises(PinError, match="floating branch"):
        pin_project(project, renderer_ref="abc1234", schemas_ref="master")
    with pytest.raises(PinError, match="floating branch"):
        pin_project(
            project,
            renderer_ref="abc1234",
            schemas_ref="abc1234",
            workflow_ref="main",
        )


def test_pin_errors_on_empty_workflows_dir(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    (project / ".github" / "workflows").mkdir(parents=True)

    with pytest.raises(PinError, match="No pinnable lines"):
        pin_project(project, renderer_ref="abc1234", schemas_ref="def5678")


def test_pin_errors_when_no_workflows_dir(tmp_path: Path) -> None:
    project = tmp_path / "bare"
    project.mkdir()

    with pytest.raises(PinError, match="No .github/workflows"):
        pin_project(project, renderer_ref="abc1234", schemas_ref="def5678")


def test_pin_preserves_unrelated_lines(tmp_path: Path) -> None:
    """The Cloudflare Pages project var and secrets blocks are untouched."""

    project = _make_project(tmp_path / "vandam")

    pin_project(
        project,
        renderer_ref="0123456789abcdef0123456789abcdef01234567",
        schemas_ref="fedcba9876543210fedcba9876543210fedcba98",
    )

    deploy_text = (project / ".github" / "workflows" / "deploy.yml").read_text()
    assert "cloudflare-pages-project: ${{ vars.CLOUDFLARE_PAGES_PROJECT }}" in deploy_text
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in deploy_text
    assert "BLDGTYP_PACKAGES_TOKEN: ${{ secrets.BLDGTYP_PACKAGES_TOKEN }}" in deploy_text


def test_pin_handles_already_pinned_files(tmp_path: Path) -> None:
    """Re-pinning an already-pinned project rewrites to the new ref."""

    ci = _CI_FLOATING.replace(
        "@main",
        "@1111111111111111111111111111111111111111",
    ).replace(
        "renderer-ref: main",
        "renderer-ref: 1111111111111111111111111111111111111111",
    ).replace(
        "schemas-ref: main",
        "schemas-ref: 2222222222222222222222222222222222222222",
    )
    project = tmp_path / "previously-pinned"
    (project / ".github" / "workflows").mkdir(parents=True)
    (project / ".github" / "workflows" / "ci.yml").write_text(ci)

    pin_project(
        project,
        renderer_ref="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        schemas_ref="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    ci_text = (project / ".github" / "workflows" / "ci.yml").read_text()
    assert "renderer-ref: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in ci_text
    assert "schemas-ref: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in ci_text
    assert "1111111111111111111111111111111111111111" not in ci_text
    assert "2222222222222222222222222222222222222222" not in ci_text


def test_workflow_pin_status_reports_current_refs(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "vandam")

    pin_project(
        project,
        renderer_ref="0123456789abcdef0123456789abcdef01234567",
        schemas_ref="fedcba9876543210fedcba9876543210fedcba98",
    )

    status = workflow_pin_status(project)
    assert status[Path(".github/workflows/ci.yml")] == {
        "workflow": "0123456789abcdef0123456789abcdef01234567",
        "renderer": "0123456789abcdef0123456789abcdef01234567",
        "schemas": "fedcba9876543210fedcba9876543210fedcba98",
    }


def test_pin_accepts_version_tags(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "vandam")

    pin_project(project, renderer_ref="v1.2.3", schemas_ref="v0.4.0")

    ci_text = (project / ".github" / "workflows" / "ci.yml").read_text()
    assert (
        "uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@v1.2.3"
    ) in ci_text
    assert "renderer-ref: v1.2.3" in ci_text
    assert "schemas-ref: v0.4.0" in ci_text
