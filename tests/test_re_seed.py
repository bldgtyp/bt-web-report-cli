"""Tests for ``btwr re-seed`` — Phase 5 of the Option-C migration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from bt_web_report_cli.new_project import (
    PLATFORM_DIR,
    PLATFORM_YAML_NAME,
    create_project,
)
from bt_web_report_cli.re_seed import (
    ReSeedError,
    apply_re_seed,
    plan_re_seed,
)


def _seed_renderer(path: Path) -> Path:
    """Build a minimal but complete fake template tree."""

    path.mkdir(parents=True)
    (path / "package.json").write_text(
        '{"name":"@bldgtyp/web-report-template","version":"0.0.1",'
        '"dependencies":{"@bldgtyp/web-report-schemas":"^0.3.0"}}'
    )
    (path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (path / "astro.config.mjs").write_text("export default {};\n")
    (path / "tsconfig.json").write_text("{}\n")
    (path / "playwright.config.ts").write_text("export default {};\n")
    (path / "vitest.config.ts").write_text("export default {};\n")
    (path / ".gitignore").write_text("node_modules/\n")
    (path / ".dropboxignore").write_text("node_modules\n")
    (path / ".editorconfig").write_text("root = true\n")
    (path / ".npmrc").write_text("# placeholder\n")
    (path / "README.md").write_text("# Renderer\n")
    (path / "src").mkdir()
    (path / "src" / "pages").mkdir()
    (path / "src" / "pages" / "index.astro").write_text("---\n---\nhello\n")
    (path / "tina" / "__generated__").mkdir(parents=True)
    (path / "tina" / "__generated__" / "_lookup.json").write_text("{}\n")
    (path / "scripts").mkdir()
    (path / "scripts" / "build-pdf.mjs").write_text("// v1\n")
    (path / "scripts" / "retry.sh").write_text("#!/bin/sh\nexec \"$@\"\n")
    (path / "scripts" / "seed-ci.yml").write_text("name: CI\non: [push]\njobs: {}\n")
    (path / "scripts" / "seed-deploy.yml").write_text("name: Deploy\non: [push]\njobs: {}\n")
    (path / "playwright").mkdir()
    (path / "playwright" / "smoke.spec.ts").write_text("// smoke\n")
    (path / "content").mkdir()
    (path / "content" / "summary.mdx").write_text("# Summary\n")
    (path / "data").mkdir()
    (path / "data" / "manifest.json").write_text('{"status":"pending"}\n')
    (path / "public" / "assets").mkdir(parents=True)
    return path


def _seed_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Create a vendored project from a template snapshot. Returns (template, project)."""

    template = _seed_renderer(tmp_path / "renderer")
    project = tmp_path / "Project" / "04_Web"
    create_project(
        project,
        slug="project-sample",
        title="Sample",
        repo="bt-proj-sample",
        production_url="https://sample.bldgtyp.com",
        renderer_source=template,
        init_git=False,
    )
    return template, project


def _fake_head_resolver(sha: str) -> object:
    """Return a fake _run_command that resolves `git rev-parse HEAD` to ``sha``."""

    def fake(args, cwd=None, check=True):
        if tuple(args) == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, stdout=f"{sha}\n", stderr="")
        return subprocess.run(tuple(args), cwd=cwd, text=True, capture_output=True)

    return fake


def test_re_seed_noop_when_template_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)

    plan = plan_re_seed(project, template_path=template, target_ref="abcdef0")
    assert plan.is_noop
    assert plan.changes == ()


def test_re_seed_detects_modified_vendored_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    (template / "src" / "pages" / "index.astro").write_text("---\n---\ngoodbye\n")

    plan = plan_re_seed(project, template_path=template, target_ref="abcdef0")
    paths = {change.relative_path for change in plan.changes}
    assert Path("src/pages/index.astro") in paths
    modify = next(c for c in plan.changes if c.relative_path == Path("src/pages/index.astro"))
    assert modify.action == "modify"
    assert "goodbye" in modify.diff
    assert "hello" in modify.diff


def test_re_seed_detects_added_vendored_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    (template / "scripts" / "new-helper.mjs").write_text("// added\n")

    plan = plan_re_seed(project, template_path=template, target_ref="abcdef0")
    paths = {change.relative_path: change.action for change in plan.changes}
    assert paths.get(Path("scripts/new-helper.mjs")) == "add"


def test_re_seed_preserves_authored_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    (project / "content" / "summary.mdx").write_text("# Custom summary\n")
    (project / "data" / "custom.csv").write_text("col1,col2\n1,2\n")
    (template / "content" / "summary.mdx").write_text("# Template default summary\n")

    plan = plan_re_seed(project, template_path=template, target_ref="abcdef0")
    paths = {change.relative_path for change in plan.changes}
    assert Path("content/summary.mdx") not in paths
    assert Path("data/custom.csv") not in paths


def test_re_seed_apply_writes_changes_and_stamps_platform_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    (template / "src" / "pages" / "index.astro").write_text("---\n---\nupdated content\n")

    plan = plan_re_seed(project, template_path=template, target_ref="newsha7")
    written = apply_re_seed(plan, commit=False)

    assert written >= 1
    assert (project / "src" / "pages" / "index.astro").read_text() == "---\n---\nupdated content\n"

    platform = yaml.safe_load((project / PLATFORM_DIR / PLATFORM_YAML_NAME).read_text())
    assert platform["renderer_seed_ref"] == "newsha7"


def test_re_seed_apply_idempotent_on_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    (template / "src" / "pages" / "index.astro").write_text("---\n---\nv2\n")

    plan1 = plan_re_seed(project, template_path=template, target_ref="ref-v2")
    apply_re_seed(plan1, commit=False)

    plan2 = plan_re_seed(project, template_path=template, target_ref="ref-v2")
    assert plan2.is_noop


def test_re_seed_refuses_dirty_non_preserved_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    # Initialize git in the project so the dirty-check actually exercises.
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=t@example.com", "commit", "-m", "init"],
        cwd=project,
        capture_output=True,
        check=True,
    )
    # Dirty a vendored file — re-seed must refuse.
    (project / "src" / "pages" / "index.astro").write_text("---\n---\nlocal edit\n")
    (template / "scripts" / "build-pdf.mjs").write_text("// v2\n")

    plan = plan_re_seed(project, template_path=template, target_ref="abcdef0")
    with pytest.raises(ReSeedError, match="dirty"):
        apply_re_seed(plan, commit=False)


def test_re_seed_allows_dirty_authored_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=t@example.com", "commit", "-m", "init"],
        cwd=project,
        capture_output=True,
        check=True,
    )
    # Dirty an authored-content file — re-seed must still allow it.
    (project / "content" / "summary.mdx").write_text("# Dirty authored edit\n")
    (template / "scripts" / "build-pdf.mjs").write_text("// v2\n")

    plan = plan_re_seed(project, template_path=template, target_ref="abcdef0")
    apply_re_seed(plan, commit=False)

    # Author's edit survives.
    assert (project / "content" / "summary.mdx").read_text() == "# Dirty authored edit\n"
    # Template's update was applied.
    assert (project / "scripts" / "build-pdf.mjs").read_text() == "// v2\n"


def test_re_seed_errors_when_project_yaml_missing(tmp_path: Path) -> None:
    template = _seed_renderer(tmp_path / "renderer")
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(ReSeedError, match="not a bt-web-report project|no project.yaml"):
        plan_re_seed(bare, template_path=template, target_ref="abcdef0")


def test_re_seed_resolves_workflow_paths_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    (template / "scripts" / "seed-ci.yml").write_text("name: CI\non: [push]\njobs: {build: {}}\n")

    plan = plan_re_seed(project, template_path=template, target_ref="newsha7")
    paths = {change.relative_path: change for change in plan.changes}
    assert Path(".github/workflows/ci.yml") in paths
    assert paths[Path(".github/workflows/ci.yml")].action == "modify"

    apply_re_seed(plan, commit=False)
    assert "build" in (project / ".github" / "workflows" / "ci.yml").read_text()


def test_re_seed_commit_skipped_when_no_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template, project = _seed_project(tmp_path, monkeypatch)
    (template / "src" / "pages" / "index.astro").write_text("---\n---\nupdated\n")

    plan = plan_re_seed(project, template_path=template, target_ref="newsha7")
    # commit=True but no .git — should not raise.
    apply_re_seed(plan, commit=True)
    assert not (project / ".git").exists()
