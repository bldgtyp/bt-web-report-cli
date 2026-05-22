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


def test_prepare_runtime_workspace_keeps_node_dependencies_outside_project(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    app_support = tmp_path / "support"

    workspace = prepare_runtime_workspace(
        project,
        kind="build",
        renderer_source=renderer,
        base_dir=app_support,
        install=False,
    )

    assert workspace.renderer_path == app_support / "renderer" / "current"
    assert workspace.workspace_path == app_support / "builds" / "sample"
    assert (workspace.workspace_path / "package.json").is_symlink()
    assert not (workspace.workspace_path / "src").is_symlink()
    assert not (workspace.workspace_path / "tina").is_symlink()
    assert (workspace.workspace_path / "tina" / "__generated__" / "_lookup.json").exists()
    assert (workspace.workspace_path / "src" / "pages" / "index.astro").exists()
    assert (workspace.workspace_path / "content").resolve() == (project / "content").resolve()
    assert not (project / "node_modules").exists()
    assert not (project / "package.json").exists()


def test_prepare_runtime_workspace_installs_app_support_dependencies_instead_of_symlinking_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    app_support = tmp_path / "support"
    installed: list[Path] = []
    monkeypatch.setenv("NODE_AUTH_TOKEN", "test-token")

    def fake_run_pnpm_install(
        target: Path,
        pnpm_executable: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        installed.append(target)
        (target / "node_modules").mkdir()
        return subprocess.CompletedProcess((pnpm_executable, "install"), 0)

    monkeypatch.setattr("bt_web_report_cli.runtime._run_pnpm_install", fake_run_pnpm_install)

    workspace = prepare_runtime_workspace(
        project,
        kind="preview",
        renderer_source=renderer,
        base_dir=app_support,
    )

    renderer_runtime = app_support / "renderer" / "current"
    assert installed == [renderer_runtime]
    assert (renderer_runtime / "node_modules").is_dir()
    assert not (renderer_runtime / "node_modules").is_symlink()
    assert (workspace.workspace_path / "node_modules").resolve() == (renderer_runtime / "node_modules").resolve()


def test_prepare_runtime_workspace_reinstalls_when_renderer_dependency_inputs_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    app_support = tmp_path / "support"
    installed: list[Path] = []
    monkeypatch.setenv("NODE_AUTH_TOKEN", "test-token")

    def fake_run_pnpm_install(
        target: Path,
        pnpm_executable: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        installed.append(target)
        (target / "node_modules").mkdir()
        (target / "node_modules" / "install-count.txt").write_text(str(len(installed)))
        return subprocess.CompletedProcess((pnpm_executable, "install"), 0)

    monkeypatch.setattr("bt_web_report_cli.runtime._run_pnpm_install", fake_run_pnpm_install)

    prepare_runtime_workspace(
        project,
        kind="preview",
        renderer_source=renderer,
        base_dir=app_support,
    )
    prepare_runtime_workspace(
        project,
        kind="preview",
        renderer_source=renderer,
        base_dir=app_support,
    )
    (renderer / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\npackages: {}\n")
    prepare_runtime_workspace(
        project,
        kind="preview",
        renderer_source=renderer,
        base_dir=app_support,
    )

    renderer_runtime = app_support / "renderer" / "current"
    assert installed == [renderer_runtime, renderer_runtime]
    assert (renderer_runtime / "node_modules" / "install-count.txt").read_text() == "2"


def test_prepare_runtime_workspace_replaces_stale_source_node_modules_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    app_support = tmp_path / "support"
    renderer_runtime = app_support / "renderer" / "current"
    renderer_runtime.mkdir(parents=True)
    (renderer_runtime / "node_modules").symlink_to(renderer / "node_modules", target_is_directory=True)
    monkeypatch.setenv("NODE_AUTH_TOKEN", "test-token")

    def fake_run_pnpm_install(
        target: Path,
        pnpm_executable: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        (target / "node_modules").mkdir()
        return subprocess.CompletedProcess((pnpm_executable, "install"), 0)

    monkeypatch.setattr("bt_web_report_cli.runtime._run_pnpm_install", fake_run_pnpm_install)

    prepare_runtime_workspace(
        project,
        kind="preview",
        renderer_source=renderer,
        base_dir=app_support,
    )

    assert (renderer_runtime / "node_modules").is_dir()
    assert not (renderer_runtime / "node_modules").is_symlink()


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
            base_dir=tmp_path / "support",
            install=False,
        )


def test_prepare_runtime_workspace_retries_transient_non_empty_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    app_support = tmp_path / "support"
    workspace = app_support / "previews" / "sample"
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
        base_dir=app_support,
        install=False,
    )

    assert calls == 1
    assert (prepared.workspace_path / "src" / "pages" / "index.astro").exists()
    assert not (prepared.workspace_path / "src" / "stale.txt").exists()


def test_run_renderer_script_points_tina_at_project_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    project = _make_project(tmp_path / "Project" / "04_Web")
    app_support = tmp_path / "support"
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
        base_dir=app_support,
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
    app_support = tmp_path / "support"
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
        base_dir=app_support,
        install=False,
    )

    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env[PROJECT_SCHEMA_JSON_ENV] == str(schema.resolve())


def test_create_project_copies_only_content_payload(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    stale_archive = renderer / "public" / "assets" / "envelope" / "assemblies" / "recommended-assemblies.zip"
    stale_archive.parent.mkdir(parents=True)
    stale_archive.write_text("stale zip")
    target = tmp_path / "Project" / "04_Web"
    phpp = tmp_path / "Project" / "07_PHPP" / "model.xlsx"
    phpp.parent.mkdir(parents=True)
    phpp.write_text("fixture")

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


def test_create_project_pins_renderer_workflows_to_resolved_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    workflow = renderer / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - name: Checkout shared renderer\n"
        "        uses: actions/checkout@v4\n"
        "        with:\n"
        "          repository: bldgtyp/bt-web-report-template\n"
        "          path: renderer\n"
    )
    target = tmp_path / "Project" / "04_Web"
    monkeypatch.setattr("bt_web_report_cli.new_project._resolve_renderer_ref", lambda _source: "abc123")

    create_project(
        target,
        slug="project-2606",
        title="2606 Vandam",
        repo="bt-proj-2606-vandam",
        production_url="https://project-2606.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
    )

    copied_workflow = (target / ".github" / "workflows" / "ci.yml").read_text()
    assert (
        "repository: bldgtyp/bt-web-report-template\n          ref: abc123\n          path: renderer" in copied_workflow
    )


def test_create_project_ignores_ds_store_in_existing_target(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    target.mkdir(parents=True)
    (target / ".DS_Store").write_text("finder")

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


def test_create_project_requires_overwrite_for_real_existing_content(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    target.mkdir(parents=True)
    (target / "old.md").write_text("old")

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
