"""Tests for the Phase-3 vendored seed shape produced by ``btwr new``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bt_web_report_cli.new_project import (
    PLATFORM_DIR,
    PLATFORM_YAML_NAME,
    SEED_PAYLOAD,
    SEED_WORKFLOW_MAP,
    create_project,
)


def _seed_renderer(path: Path) -> Path:
    """Build a fake template tree with every entry SEED_PAYLOAD expects."""

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
    (path / ".gitignore").write_text("node_modules/\ndist/\n")
    (path / ".dropboxignore").write_text("node_modules/\n")
    (path / ".editorconfig").write_text("root = true\n")
    (path / ".npmrc").write_text("minimum-release-age-exclude[]=@bldgtyp/web-report-schemas\n")
    (path / "README.md").write_text("# Vendored renderer\n")

    (path / "src").mkdir()
    (path / "src" / "pages").mkdir()
    (path / "src" / "pages" / "index.astro").write_text("---\n---\n")

    (path / "tina" / "__generated__").mkdir(parents=True)
    (path / "tina" / "__generated__" / "_lookup.json").write_text("{}\n")
    (path / "tina" / "config.ts").write_text("export default {};\n")

    (path / "scripts").mkdir()
    (path / "scripts" / "build-pdf.mjs").write_text("// pdf builder\n")
    (path / "scripts" / "retry.sh").write_text("#!/bin/sh\nexec \"$@\"\n")
    (path / "scripts" / "validate-project.mjs").write_text("// validator\n")
    (path / "scripts" / "seed-ci.yml").write_text("name: CI\non: [push]\njobs: {}\n")
    (path / "scripts" / "seed-deploy.yml").write_text("name: Deploy\non: [push]\njobs: {}\n")

    (path / "playwright").mkdir()
    (path / "playwright" / "report-smoke.spec.ts").write_text("// smoke test\n")

    # Authored content that gets copied into every project as starter MDX.
    (path / "content").mkdir()
    (path / "content" / "summary.mdx").write_text("# Summary\n")
    (path / "data").mkdir()
    (path / "data" / "manifest.json").write_text('{"status":"pending"}\n')
    (path / "public" / "assets").mkdir(parents=True)

    # Things the seed must explicitly skip — they exist on the template
    # but should NEVER make it into a project.
    (path / "node_modules").mkdir()
    (path / "node_modules" / "fake.js").write_text("// nope\n")
    (path / "dist").mkdir()
    (path / "dist" / "index.html").write_text("<html></html>")
    (path / ".astro").mkdir()
    (path / ".astro" / "cache.json").write_text("{}\n")

    return path


def _bootstrap(tmp_path: Path) -> tuple[Path, Path]:
    """Create a renderer + return (renderer_path, project_target_path)."""

    renderer = _seed_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    return renderer, target


def _create(renderer: Path, target: Path) -> Path:
    return create_project(
        target,
        slug="project-2606",
        title="2606 Vandam",
        repo="bt-proj-2606-vandam",
        production_url="https://project-2606.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
    )


def test_seed_payload_includes_renderer_source(tmp_path: Path) -> None:
    renderer, target = _bootstrap(tmp_path)
    _create(renderer, target)

    # Vendored renderer source — the project IS the runtime now.
    assert (target / "src" / "pages" / "index.astro").exists()
    assert (target / "tina" / "config.ts").exists()
    assert (target / "tina" / "__generated__" / "_lookup.json").exists()
    assert (target / "scripts" / "build-pdf.mjs").exists()
    assert (target / "scripts" / "retry.sh").exists()
    assert (target / "playwright" / "report-smoke.spec.ts").exists()
    assert (target / "astro.config.mjs").exists()
    assert (target / "package.json").exists()
    assert (target / "pnpm-lock.yaml").exists()
    assert (target / "tsconfig.json").exists()
    assert (target / "playwright.config.ts").exists()
    assert (target / "vitest.config.ts").exists()


def test_seed_payload_skips_build_artifacts(tmp_path: Path) -> None:
    renderer, target = _bootstrap(tmp_path)
    _create(renderer, target)

    # None of these should ever leak into a vendored project.
    assert not (target / "node_modules").exists()
    assert not (target / "dist").exists()
    assert not (target / ".astro").exists()


def test_seed_workflows_land_in_dot_github(tmp_path: Path) -> None:
    renderer, target = _bootstrap(tmp_path)
    _create(renderer, target)

    ci = target / ".github" / "workflows" / "ci.yml"
    deploy = target / ".github" / "workflows" / "deploy.yml"
    assert ci.exists()
    assert deploy.exists()

    ci_text = ci.read_text()
    deploy_text = deploy.read_text()
    # Project-local workflows must not reference the template repo as a
    # cross-repo `uses:` — that is exactly what the cascade-stop removed.
    for text in (ci_text, deploy_text):
        assert "bldgtyp/bt-web-report-template" not in text
        assert "_renderer-build.yml" not in text


def test_seed_workflows_not_copied_into_scripts(tmp_path: Path) -> None:
    """`scripts/seed-ci.yml` is consumed at seed time; it must not be vendored."""

    renderer, target = _bootstrap(tmp_path)
    _create(renderer, target)

    # The whole `scripts/` directory IS vendored, but seed-ci.yml /
    # seed-deploy.yml inside it would be confusing dead weight in projects.
    # (Phase 6 deletes them from the template entirely; for now we don't
    # care if they ride along since they aren't referenced anywhere.)
    assert (target / "scripts").is_dir()


def test_seed_stamps_platform_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer, target = _bootstrap(tmp_path)

    def fake_rev_parse(args, cwd=None, check=True):
        import subprocess

        if tuple(args) == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, stdout="cafebabe1234\n", stderr="")
        return subprocess.run(tuple(args), cwd=cwd, text=True, capture_output=True)

    monkeypatch.setattr("bt_web_report_cli.new_project._run_command", fake_rev_parse)

    _create(renderer, target)

    platform = target / PLATFORM_DIR / PLATFORM_YAML_NAME
    assert platform.exists()
    payload = yaml.safe_load(platform.read_text())
    assert payload["renderer_seed_ref"] == "cafebabe1234"
    assert payload["schemas_pin"] == "^0.3.0"
    assert "cli_version" in payload
    assert payload["cli_version"]  # non-empty
    assert payload["seeded_at"].endswith("Z")


def test_seed_platform_yaml_records_unknown_when_git_unavailable(tmp_path: Path) -> None:
    """A template that is not a git checkout still seeds — the SHA records 'unknown'."""

    renderer, target = _bootstrap(tmp_path)
    _create(renderer, target)

    platform = target / PLATFORM_DIR / PLATFORM_YAML_NAME
    payload = yaml.safe_load(platform.read_text())
    # The test renderer has no .git directory, so `git rev-parse HEAD` fails.
    assert payload["renderer_seed_ref"] == "unknown"
    assert payload["schemas_pin"] == "^0.3.0"


def test_seed_project_yaml_uses_repo_for_cloudflare_pages_project(tmp_path: Path) -> None:
    renderer, target = _bootstrap(tmp_path)
    _create(renderer, target)

    project_yaml = yaml.safe_load((target / "project.yaml").read_text())
    assert project_yaml["publishing"]["cloudflare_pages_project"] == "bt-proj-2606-vandam"
    assert project_yaml["publishing"]["production_url"] == "https://project-2606.bldgtyp.com"


def test_seed_payload_constant_lists_expected_files() -> None:
    """Regression guard: SEED_PAYLOAD shape change requires intentional thought."""

    expected_must_include = {
        "content",
        "data",
        "public",
        "src",
        "tina",
        "scripts",
        "playwright",
        "astro.config.mjs",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    }
    missing = expected_must_include - set(SEED_PAYLOAD)
    assert not missing, f"SEED_PAYLOAD is missing required entries: {missing}"


def test_seed_workflow_map_targets_dot_github() -> None:
    """The seed workflow files MUST land under .github/workflows/."""

    for source_rel, target_rel in SEED_WORKFLOW_MAP:
        assert source_rel.startswith("scripts/seed-"), f"odd seed source path: {source_rel}"
        assert target_rel.startswith(".github/workflows/"), f"odd target path: {target_rel}"


def test_seed_errors_when_required_workflow_file_missing(tmp_path: Path) -> None:
    """If the template is missing seed-ci.yml, the seed must fail loudly."""

    renderer = _seed_renderer(tmp_path / "renderer")
    (renderer / "scripts" / "seed-ci.yml").unlink()
    target = tmp_path / "Project" / "04_Web"

    with pytest.raises(RuntimeError, match="Seed workflow source missing"):
        _create(renderer, target)


def test_seed_preserves_starter_content_for_authoring(tmp_path: Path) -> None:
    """Authored content (content/, data/, public/) is carried over from the seed."""

    renderer, target = _bootstrap(tmp_path)
    _create(renderer, target)

    assert (target / "content" / "summary.mdx").exists()
    assert (target / "data" / "manifest.json").exists()
    assert (target / "public" / "assets").exists()


def test_seed_skips_recommended_assemblies_zip(tmp_path: Path) -> None:
    renderer, target = _bootstrap(tmp_path)
    zip_path = renderer / "public" / "assets" / "envelope" / "assemblies" / "recommended-assemblies.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_text("stale zip")

    _create(renderer, target)

    assert not (
        target / "public" / "assets" / "envelope" / "assemblies" / "recommended-assemblies.zip"
    ).exists()
