import subprocess
from pathlib import Path

import pytest
import yaml

from bt_web_report_cli.new_project import create_project, publish_project
from bt_web_report_cli.runtime import (
    run_pnpm_script,
    validate_project_yaml,
)
from bt_web_report_schemas.project import SCHEMA_VERSION


def test_run_pnpm_script_runs_pnpm_in_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    create_project(
        target,
        slug="project-sample",
        title="Sample",
        repo="bt-proj-sample",
        production_url="https://sample.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
    )
    calls: list[dict[str, object]] = []

    def fake_run(args, *, cwd, text, check):
        calls.append({"args": tuple(args), "cwd": cwd})
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("bt_web_report_cli.runtime.subprocess.run", fake_run)

    run_pnpm_script(target, "build")

    assert calls[0]["cwd"] == target.resolve()
    assert calls[0]["args"] == ("pnpm", "run", "build")


def test_run_pnpm_script_rejects_missing_project_yaml(tmp_path: Path) -> None:
    bare = tmp_path / "not-a-project"
    bare.mkdir()
    with pytest.raises(RuntimeError, match="no project.yaml"):
        run_pnpm_script(bare, "build")


def test_run_pnpm_script_rejects_missing_package_json(tmp_path: Path) -> None:
    pre_vendored = tmp_path / "old-project"
    pre_vendored.mkdir()
    _write_project_payload(pre_vendored)
    (pre_vendored / "project.yaml").write_text(
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
                "source_files": {"data_dir": "data", "assets_dir": "public/assets"},
                "publishing": {
                    "production_url": "https://sample.bldgtyp.com",
                    "cloudflare_pages_project": "bt-proj-sample",
                },
                "narrative": {
                    "climate": {
                        "weather_station_name": "TBD",
                        "state_name": "TBD",
                        "ashrae_location_name": "TBD",
                    }
                },
            },
            sort_keys=False,
        )
    )
    with pytest.raises(RuntimeError, match="re-seed"):
        run_pnpm_script(pre_vendored, "build")


def test_run_pnpm_script_raises_on_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    create_project(
        target,
        slug="project-sample",
        title="Sample",
        repo="bt-proj-sample",
        production_url="https://sample.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
    )

    def fake_run(args, *, cwd, text, check):
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr("bt_web_report_cli.runtime.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="exit 1"):
        run_pnpm_script(target, "build")


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
    (path / "package.json").write_text(
        '{"scripts":{"build":"echo build","dev":"echo dev","dev:editor":"echo editor"},'
        '"dependencies":{"@bldgtyp/web-report-schemas":"^0.3.0"}}'
    )
    (path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (path / "astro.config.mjs").write_text("export default {};\n")
    (path / "tsconfig.json").write_text("{}\n")
    (path / "src").mkdir()
    (path / "src" / "pages").mkdir()
    (path / "src" / "pages" / "index.astro").write_text("---\n---\n")
    (path / "scripts").mkdir()
    (path / "scripts" / "seed-ci.yml").write_text("name: CI\non: [push]\njobs: {}\n")
    (path / "scripts" / "seed-deploy.yml").write_text("name: Deploy\non: [push]\njobs: {}\n")
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
