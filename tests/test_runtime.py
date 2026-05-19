import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bt_web_report_cli.new_project import create_project, publish_project
from bt_web_report_cli.runtime import TINA_CONTENT_ROOT_ENV, prepare_runtime_workspace, run_renderer_script


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
            raise OSError(66, "Directory not empty", "src")
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


def test_create_project_copies_only_content_payload(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
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
    assert not (target / "package.json").exists()
    assert not (target / "src").exists()
    assert not (target / "node_modules").exists()
    assert "phpp_path: ../07_PHPP/model.xlsx" in (target / "project.yaml").read_text()


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
        "slug: sample\n"
        "project_title: Sample\n"
        "source_files:\n"
        "  data_dir: data\n"
        "  assets_dir: public/assets\n"
    )
    return path


def _write_project_payload(path: Path) -> None:
    (path / "content").mkdir(exist_ok=True)
    (path / "content" / "summary.mdx").write_text("# Summary\n")
    (path / "data").mkdir(exist_ok=True)
    (path / "data" / "manifest.json").write_text('{"status":"pending"}\n')
    (path / "public" / "assets").mkdir(parents=True, exist_ok=True)
