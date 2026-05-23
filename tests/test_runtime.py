import errno
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from bt_web_report_cli.new_project import create_project, publish_project
from bt_web_report_cli.runtime import (
    PROJECT_SCHEMA_JSON_ENV,
    TINA_CONTENT_ROOT_ENV,
    prepare_runtime_workspace,
    run_renderer_script,
    validate_project_yaml,
)
from bt_web_report_schemas.project import SCHEMA_VERSION


def test_prepare_runtime_workspace_symlinks_single_node_modules_from_template(tmp_path: Path) -> None:
    """ONE node_modules: the workspace template's. Project gets a symlink, never a copy."""

    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    builds = tmp_path / "builds"

    workspace = prepare_runtime_workspace(
        project,
        kind="build",
        renderer_source=renderer,
        base_dir=builds,
    )

    # Workspace lands under base_dir/<bucket>/<slug>/ — never inside the project.
    assert workspace.workspace_path == builds / "builds" / "sample"
    assert workspace.renderer_path == renderer

    # node_modules is a symlink pointing AT the workspace template's single install.
    nm = workspace.workspace_path / "node_modules"
    assert nm.is_symlink()
    assert nm.resolve() == (renderer / "node_modules").resolve()

    # The PROJECT must remain content-only: never gets node_modules or package.json.
    assert not (project / "node_modules").exists()
    assert not (project / "package.json").exists()

    # Renderer payload pieces: package.json is a symlink; src/ and tina/ are copies
    # (Astro/Tina expect them as siblings of the project content).
    assert (workspace.workspace_path / "package.json").is_symlink()
    assert not (workspace.workspace_path / "src").is_symlink()
    assert not (workspace.workspace_path / "tina").is_symlink()
    assert (workspace.workspace_path / "tina" / "__generated__" / "_lookup.json").exists()
    assert (workspace.workspace_path / "src" / "pages" / "index.astro").exists()

    # Project content is symlinked in from the project dir.
    assert (workspace.workspace_path / "content").resolve() == (project / "content").resolve()


def test_prepare_runtime_workspace_errors_when_template_node_modules_missing(tmp_path: Path) -> None:
    """If the workspace template has no node_modules, btwr refuses to set up a workspace.

    We never install a second copy — the user must `pnpm install` in the
    workspace template once. This is the absolute one-node_modules rule.
    """

    renderer = _make_renderer(tmp_path / "renderer")
    shutil.rmtree(renderer / "node_modules")
    project = _make_project(tmp_path / "Project" / "04_Web")

    with pytest.raises(RuntimeError, match="Workspace template has no node_modules"):
        prepare_runtime_workspace(
            project,
            kind="build",
            renderer_source=renderer,
            base_dir=tmp_path / "builds",
        )


def test_prepare_runtime_workspace_rejects_stale_project_schema(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    raw = yaml.safe_load((project / "project.yaml").read_text())
    raw["schema_version"] = "0.1.0"
    (project / "project.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(RuntimeError, match=r"schema_version.*0\.2\.0"):
        prepare_runtime_workspace(
            project,
            kind="preview",
            renderer_source=renderer,
            base_dir=tmp_path / "builds",
            install=False,
        )


def test_prepare_runtime_workspace_retries_transient_non_empty_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    builds = tmp_path / "builds"
    workspace = builds / "previews" / "sample"
    workspace.mkdir(parents=True)
    (workspace / "src").mkdir()
    (workspace / "src" / "stale.txt").write_text("stale")
    calls = 0
    real_rmtree = shutil.rmtree

    def flaky_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal calls
        if Path(path).name == "sample" and calls == 0:
            calls += 1
            raise OSError(errno.ENOTEMPTY, "Directory not empty", "src")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("bt_web_report_cli.runtime.shutil.rmtree", flaky_rmtree)
    monkeypatch.setattr("bt_web_report_cli.runtime.REMOVE_RETRY_DELAYS", (0,))

    prepared = prepare_runtime_workspace(
        project,
        kind="preview",
        renderer_source=renderer,
        base_dir=builds,
        install=False,
    )

    assert calls == 1
    assert (prepared.workspace_path / "src" / "pages" / "index.astro").exists()
    assert not (prepared.workspace_path / "src" / "stale.txt").exists()


def test_run_renderer_script_points_tina_at_project_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    builds = tmp_path / "builds"
    calls: list[dict[str, object]] = []

    def fake_run(
        args: tuple[str, str],
        *,
        cwd: Path,
        env: dict[str, str],
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "cwd": cwd, "env": env, "text": text, "check": check})
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("bt_web_report_cli.runtime.subprocess.run", fake_run)

    workspace = run_renderer_script(
        project,
        "dev:editor",
        kind="preview",
        renderer_source=renderer,
        base_dir=builds,
        install=False,
    )

    assert calls[0]["cwd"] == workspace.workspace_path
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env[TINA_CONTENT_ROOT_ENV] == os.path.relpath(project.resolve(), workspace.workspace_path / "tina")


def test_run_renderer_script_points_local_renderer_at_sibling_project_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    renderer = _make_renderer(workspace_root / "bt-web-report-template")
    schema = workspace_root / "bt-web-report-schemas" / "schemas" / "project.schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text("{}\n")
    project = _make_project(tmp_path / "Project" / "04_Web")
    builds = tmp_path / "builds"
    calls: list[dict[str, object]] = []

    def fake_run(
        args: tuple[str, str],
        *,
        cwd: Path,
        env: dict[str, str],
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "cwd": cwd, "env": env, "text": text, "check": check})
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("bt_web_report_cli.runtime.subprocess.run", fake_run)

    run_renderer_script(
        project,
        "build",
        kind="build",
        renderer_source=renderer,
        base_dir=builds,
        install=False,
    )

    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env[PROJECT_SCHEMA_JSON_ENV] == str(schema.resolve())


def _patch_resolvers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    renderer_ref: str = "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
    schemas_ref: str = "ssssssssssssssssssssssssssssssssssssssss",
) -> None:
    monkeypatch.setattr("bt_web_report_cli.new_project._resolve_renderer_ref", lambda _source: renderer_ref)
    monkeypatch.setattr("bt_web_report_cli.new_project._resolve_schemas_ref", lambda _source: schemas_ref)


def test_create_project_copies_only_content_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    stale_archive = renderer / "public" / "assets" / "envelope" / "assemblies" / "recommended-assemblies.zip"
    stale_archive.parent.mkdir(parents=True)
    stale_archive.write_text("stale zip")
    target = tmp_path / "Project" / "04_Web"
    phpp = tmp_path / "Project" / "07_PHPP" / "model.xlsx"
    phpp.parent.mkdir(parents=True)
    phpp.write_text("fixture")
    _patch_resolvers(monkeypatch)

    create_project(
        target,
        slug="project-2606",
        title="2606 Vandam",
        repo="bt-proj-2606-vandam",
        production_url="https://project-2606.bldgtyp.com",
        phpp=phpp,
        renderer_source=renderer,
        init_git=False,
    )

    assert (target / "project.yaml").exists()
    assert (target / "content" / "summary.mdx").exists()
    assert (target / "data" / "manifest.json").exists()
    assert not (target / "public" / "assets" / "envelope" / "assemblies" / "recommended-assemblies.zip").exists()
    assert not (target / "package.json").exists()
    assert not (target / "src").exists()
    assert not (target / "node_modules").exists()
    project_yaml = yaml.safe_load((target / "project.yaml").read_text())
    assert project_yaml["schema_version"] == SCHEMA_VERSION
    assert project_yaml["source_files"]["phpp_path"] == "../07_PHPP/model.xlsx"
    assert project_yaml["narrative"]["climate"]["weather_station_name"] == "TBD"
    validate_project_yaml(target)


def test_create_project_seeds_per_project_workflows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The seed copies scripts/per-project-{ci,deploy}.yml — NOT the template's own workflows.

    The bug this prevents: copying the template's own .github/workflows/ would
    bring `manage-custom-domain: false` (correct for the demo deploy, wrong
    for per-project) into every new project.
    """

    renderer = _make_renderer(tmp_path / "renderer")
    # Plant a wrong workflow at the template's .github/workflows/ to prove
    # the seed never reads from there.
    template_own = renderer / ".github" / "workflows" / "ci.yml"
    template_own.parent.mkdir(parents=True)
    template_own.write_text("name: WRONG\n")
    target = tmp_path / "Project" / "04_Web"
    _patch_resolvers(monkeypatch)

    create_project(
        target,
        slug="project-2606",
        title="2606 Vandam",
        repo="bt-proj-2606-vandam",
        production_url="https://project-2606.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
    )

    ci_text = (target / ".github" / "workflows" / "ci.yml").read_text()
    deploy_text = (target / ".github" / "workflows" / "deploy.yml").read_text()
    # The right workflows came from scripts/per-project-*.yml; not from the
    # template's own ci.yml (which we planted as "name: WRONG" above).
    assert "WRONG" not in ci_text
    assert ci_text.startswith("name: CI\n")
    assert deploy_text.startswith("name: Deploy\n")


def test_create_project_pins_per_project_workflow_to_renderer_and_schemas_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seeded per-project ci.yml has its uses/renderer-ref/schemas-ref all pinned."""

    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    _patch_resolvers(monkeypatch, renderer_ref="rfaceb0a", schemas_ref="s5ec00ba")

    create_project(
        target,
        slug="project-2606",
        title="2606 Vandam",
        repo="bt-proj-2606-vandam",
        production_url="https://project-2606.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
    )

    ci_text = (target / ".github" / "workflows" / "ci.yml").read_text()
    deploy_text = (target / ".github" / "workflows" / "deploy.yml").read_text()
    for text in (ci_text, deploy_text):
        assert (
            "uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@rfaceb0a"
        ) in text
        assert "renderer-ref: rfaceb0a" in text
        assert "schemas-ref: s5ec00ba" in text
        assert "@main" not in text
        assert ": main" not in text


def test_create_project_pins_legacy_local_reusable_workflow_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Back-compat: a hand-edited workflow using the template's `./` local form is still pinned.

    This codepath exists because someone might copy the template's own ci.yml
    into a project repo manually. The seed itself never does this — see
    test_create_project_seeds_per_project_workflows — but the pinning logic
    still has to handle it for safety.
    """

    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    # Pre-create the project workflow in the "wrong" (local `./`) form to
    # exercise the back-compat branch in _pin_renderer_workflows.
    workflows = target / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n"
        "  build:\n"
        "    uses: ./.github/workflows/_renderer-build.yml\n"
        "    with:\n"
        "      renderer-ref: ${{ github.sha }}\n"
        "      schemas-ref: main\n"
    )
    _patch_resolvers(monkeypatch, renderer_ref="rdeadbee", schemas_ref="sbeefcaf")

    # Note: we manually invoke _pin_renderer_workflows here because
    # create_project would otherwise overwrite ci.yml from the per-project
    # template. The test isolates the pin-logic back-compat branch.
    from bt_web_report_cli.new_project import _pin_renderer_workflows

    _pin_renderer_workflows(target, renderer_ref="rdeadbee", schemas_ref="sbeefcaf")

    ci_text = (workflows / "ci.yml").read_text()
    assert (
        "uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@rdeadbee"
    ) in ci_text
    assert "renderer-ref: rdeadbee" in ci_text
    assert "schemas-ref: sbeefcaf" in ci_text


def test_resolve_renderer_ref_refuses_unspecified_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Floating 'main' is no longer a legal default — Phase 1 cascade stop."""

    from bt_web_report_cli.new_project import RENDERER_REF_ENV, _resolve_renderer_ref

    monkeypatch.delenv(RENDERER_REF_ENV, raising=False)
    with pytest.raises(RuntimeError, match=RENDERER_REF_ENV):
        _resolve_renderer_ref(tmp_path)


def test_resolve_renderer_ref_refuses_floating_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting BTWR_RENDERER_REF=main is also rejected to prevent accidental cascade."""

    from bt_web_report_cli.new_project import RENDERER_REF_ENV, _resolve_renderer_ref

    monkeypatch.setenv(RENDERER_REF_ENV, "main")
    with pytest.raises(RuntimeError, match="floating branch"):
        _resolve_renderer_ref(tmp_path)


def test_resolve_renderer_ref_explicit_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit BTWR_RENDERER_REF wins over the @main default."""

    from bt_web_report_cli.new_project import RENDERER_REF_ENV, _resolve_renderer_ref

    monkeypatch.setenv(RENDERER_REF_ENV, "v1.2.3")
    assert _resolve_renderer_ref(tmp_path) == "v1.2.3"


def test_resolve_renderer_ref_head_resolves_to_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy "pin to HEAD" behaviour stays available via BTWR_RENDERER_REF=HEAD."""

    from bt_web_report_cli.new_project import RENDERER_REF_ENV, _resolve_renderer_ref

    monkeypatch.setenv(RENDERER_REF_ENV, "HEAD")
    fake = {"value": "deadbeef"}

    def fake_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = fake["value"]
            stderr = ""

        return R()

    monkeypatch.setattr("bt_web_report_cli.new_project._run_command", fake_run)
    assert _resolve_renderer_ref(tmp_path) == "deadbeef"


def test_resolve_schemas_ref_explicit_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_web_report_cli.new_project import SCHEMAS_REF_ENV, _resolve_schemas_ref

    monkeypatch.setenv(SCHEMAS_REF_ENV, "v9.9.9")
    assert _resolve_schemas_ref(tmp_path) == "v9.9.9"


def test_resolve_schemas_ref_refuses_floating_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_web_report_cli.new_project import SCHEMAS_REF_ENV, _resolve_schemas_ref

    monkeypatch.setenv(SCHEMAS_REF_ENV, "main")
    with pytest.raises(RuntimeError, match="floating branch"):
        _resolve_schemas_ref(tmp_path)


def test_resolve_schemas_ref_resolves_from_workspace_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env override → look at the workspace-sibling bt-web-report-schemas checkout."""

    from bt_web_report_cli.new_project import SCHEMAS_REF_ENV, _resolve_schemas_ref

    workspace = tmp_path / "workspace"
    renderer = workspace / "bt-web-report-template"
    schemas = workspace / "bt-web-report-schemas"
    renderer.mkdir(parents=True)
    schemas.mkdir(parents=True)
    monkeypatch.delenv(SCHEMAS_REF_ENV, raising=False)

    def fake_run(args, cwd=None, check=True):
        # The resolver runs `git rev-parse HEAD` in the schemas sibling dir.
        if tuple(args) == ("git", "rev-parse", "HEAD") and cwd == schemas:
            return subprocess.CompletedProcess(args, 0, stdout="s1eedbeef\n", stderr="")
        return subprocess.run(tuple(args), cwd=cwd, text=True, capture_output=True)

    monkeypatch.setattr("bt_web_report_cli.new_project._run_command", fake_run)
    assert _resolve_schemas_ref(renderer) == "s1eedbeef"


def test_resolve_schemas_ref_errors_when_sibling_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_web_report_cli.new_project import SCHEMAS_REF_ENV, _resolve_schemas_ref

    monkeypatch.delenv(SCHEMAS_REF_ENV, raising=False)
    workspace = tmp_path / "workspace"
    renderer = workspace / "bt-web-report-template"
    renderer.mkdir(parents=True)
    # No sibling bt-web-report-schemas/ — resolver must raise.

    with pytest.raises(RuntimeError, match="sibling checkout"):
        _resolve_schemas_ref(renderer)


def test_create_project_ignores_ds_store_in_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    target.mkdir(parents=True)
    (target / ".DS_Store").write_text("finder")
    _patch_resolvers(monkeypatch)

    create_project(
        target,
        slug="project-2606",
        title="Project",
        repo="bt-proj-2606-vandam",
        production_url="https://project-2606.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
    )

    assert (target / "project.yaml").exists()
    assert (target / ".DS_Store").exists()


def test_create_project_requires_overwrite_for_real_existing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    target.mkdir(parents=True)
    (target / "old.md").write_text("old")
    _patch_resolvers(monkeypatch)

    with pytest.raises(RuntimeError, match="not empty"):
        create_project(
            target,
            slug="project-2606",
            title="Project",
            repo="bt-proj-2606-vandam",
            production_url="https://project-2606.bldgtyp.com",
            renderer_source=renderer,
            init_git=False,
        )

    create_project(
        target,
        slug="project-2606",
        title="Project",
        repo="bt-proj-2606-vandam",
        production_url="https://project-2606.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
        overwrite=True,
    )

    assert not (target / "old.md").exists()
    assert (target / "project.yaml").exists()


def test_publish_project_creates_public_repo_sets_origin_commits_and_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "Project" / "04_Web"
    target.mkdir(parents=True)
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path | None = None,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        calls.append((command, cwd))
        if command[:3] == ("gh-test", "repo", "view"):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if command[:4] == ("git-test", "remote", "get-url", "origin"):
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="no origin")
        if command[:4] == ("git-test", "diff", "--cached", "--quiet"):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("bt_web_report_cli.new_project.subprocess.run", fake_run)

    result = publish_project(
        target,
        repo_owner="bldgtyp-projects",
        repo_name="bt-proj-2606-vandam",
        commit_message="Initial commit project-2606 report",
        git_executable="git-test",
        gh_executable="gh-test",
    )

    commands = [command for command, _cwd in calls]
    assert result.repo_full_name == "bldgtyp-projects/bt-proj-2606-vandam"
    assert result.remote_url == "https://github.com/bldgtyp-projects/bt-proj-2606-vandam.git"
    assert result.committed is True
    assert ("gh-test", "repo", "create", "bldgtyp-projects/bt-proj-2606-vandam", "--public") in commands
    assert (
        "git-test",
        "remote",
        "add",
        "origin",
        "https://github.com/bldgtyp-projects/bt-proj-2606-vandam.git",
    ) in commands
    assert ("git-test", "commit", "-m", "Initial commit project-2606 report") in commands
    assert ("git-test", "push", "-u", "origin", "HEAD:main") in commands


def test_publish_project_reuses_existing_repo_remote_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "Project" / "04_Web"
    target.mkdir(parents=True)
    (target / ".git").mkdir()
    calls: list[tuple[str, ...]] = []
    remote_url = "https://github.com/bldgtyp-projects/bt-proj-2606-vandam.git"

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path | None = None,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        calls.append(command)
        if command[:3] == ("gh-test", "repo", "view"):
            return subprocess.CompletedProcess(command, 0, stdout='{"name":"bt-proj-2606-vandam"}\n', stderr="")
        if command[:4] == ("git-test", "remote", "get-url", "origin"):
            return subprocess.CompletedProcess(command, 0, stdout=f"{remote_url}\n", stderr="")
        if command[:4] == ("git-test", "diff", "--cached", "--quiet"):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("bt_web_report_cli.new_project.subprocess.run", fake_run)

    result = publish_project(
        target,
        repo_owner="bldgtyp-projects",
        repo_name="bt-proj-2606-vandam",
        commit_message="Initial commit project-2606 report",
        git_executable="git-test",
        gh_executable="gh-test",
    )

    assert result.committed is False
    assert ("gh-test", "repo", "create", "bldgtyp-projects/bt-proj-2606-vandam", "--public") not in calls
    assert ("git-test", "commit", "-m", "Initial commit project-2606 report") not in calls
    assert ("git-test", "push", "-u", "origin", "HEAD:main") in calls


def test_publish_project_makes_existing_private_repo_public(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "Project" / "04_Web"
    target.mkdir(parents=True)
    calls: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path | None = None,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        calls.append(command)
        if command[:3] == ("gh-test", "repo", "view"):
            return subprocess.CompletedProcess(command, 0, stdout='{"isPrivate":true}\n', stderr="")
        if command[:4] == ("git-test", "remote", "get-url", "origin"):
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="no origin")
        if command[:4] == ("git-test", "diff", "--cached", "--quiet"):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("bt_web_report_cli.new_project.subprocess.run", fake_run)

    result = publish_project(
        target,
        repo_owner="bldgtyp-projects",
        repo_name="bt-proj-2606-vandam",
        commit_message="Initial commit project-2606 report",
        git_executable="git-test",
        gh_executable="gh-test",
    )

    assert result.committed is False
    assert (
        "gh-test",
        "repo",
        "edit",
        "bldgtyp-projects/bt-proj-2606-vandam",
        "--visibility",
        "public",
        "--accept-visibility-change-consequences",
    ) in calls


def _make_renderer(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "package.json").write_text('{"scripts":{"build":"echo build","dev":"echo dev","dev:editor":"echo editor"}}')
    (path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (path / "astro.config.mjs").write_text("export default {};\n")
    (path / "tsconfig.json").write_text("{}\n")
    (path / "src").mkdir()
    (path / "src" / "pages").mkdir()
    (path / "src" / "pages" / "index.astro").write_text("---\n---\n")
    (path / "scripts").mkdir()
    # Per-project workflow templates — the seed source for every new project's
    # .github/workflows/{ci,deploy}.yml. The shape mirrors the real files
    # in bt-web-report-template/scripts/.
    (path / "scripts" / "per-project-ci.yml").write_text(
        "name: CI\n"
        "on:\n  push:\n    branches: [main]\n"
        "jobs:\n"
        "  build:\n"
        "    uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@main\n"
        "    with:\n"
        "      project-repo: ${{ github.repository }}\n"
        "      project-ref: ${{ github.ref }}\n"
        "      renderer-ref: main\n"
        "      schemas-ref: main\n"
        "      run-deploy: false\n"
    )
    (path / "scripts" / "per-project-deploy.yml").write_text(
        "name: Deploy\n"
        "on:\n  push:\n    branches: [main]\n"
        "jobs:\n"
        "  deploy:\n"
        "    uses: bldgtyp/bt-web-report-template/.github/workflows/_renderer-build.yml@main\n"
        "    with:\n"
        "      project-repo: ${{ github.repository }}\n"
        "      project-ref: ${{ github.ref }}\n"
        "      renderer-ref: main\n"
        "      schemas-ref: main\n"
        "      run-deploy: true\n"
        "    secrets:\n"
        "      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}\n"
        "      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}\n"
    )
    (path / "tina" / "__generated__").mkdir(parents=True)
    (path / "tina" / "__generated__" / "_lookup.json").write_text("{}\n")
    (path / "node_modules").mkdir()
    _write_project_payload(path)
    return path


def _make_project(path: Path) -> Path:
    path.mkdir(parents=True)
    _write_project_payload(path)
    (path / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": SCHEMA_VERSION,
                "slug": "sample",
                "project_title": "Sample",
                "client_name": "Client",
                "building_name": "Building",
                "phase": "Design",
                "report_date": "2026-05-21",
                "prepared_by": "BLDGTYP",
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
                    "data_dir": "data",
                    "assets_dir": "public/assets",
                },
                "publishing": {
                    "production_url": "https://sample.bldgtyp.com",
                    "cloudflare_pages_project": "bt-proj-sample",
                },
                "narrative": {
                    "climate": {
                        "weather_station_name": "TBD",
                        "state_name": "TBD",
                        "ashrae_location_name": "TBD",
                    },
                },
            },
            sort_keys=False,
        )
    )
    return path


def _write_project_payload(path: Path) -> None:
    (path / "content").mkdir(exist_ok=True)
    (path / "content" / "summary.mdx").write_text("# Summary\n")
    (path / "data").mkdir(exist_ok=True)
    (path / "data" / "manifest.json").write_text('{"status":"pending"}\n')
    (path / "public" / "assets").mkdir(parents=True, exist_ok=True)
