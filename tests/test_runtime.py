from pathlib import Path

import pytest

from bt_web_report_cli.new_project import create_project
from bt_web_report_cli.runtime import prepare_runtime_workspace


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
    assert (workspace.workspace_path / "content").resolve() == (project / "content").resolve()
    assert not (project / "node_modules").exists()
    assert not (project / "package.json").exists()


def test_create_project_copies_only_content_payload(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path / "renderer")
    target = tmp_path / "Project" / "04_Web"
    phpp = tmp_path / "Project" / "07_PHPP" / "model.xlsx"
    phpp.parent.mkdir(parents=True)
    phpp.write_text("fixture")

    create_project(
        target,
        slug="2606-vandam",
        title="2606 Vandam",
        repo="bt-proj-2606-vandam",
        production_url="https://2606-vandam.bldgtyp.com",
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
        repo="bt-proj-project-2606",
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
            repo="bt-proj-project-2606",
            production_url="https://project-2606.bldgtyp.com",
            renderer_source=renderer,
            init_git=False,
        )

    create_project(
        target,
        slug="project-2606",
        title="Project",
        repo="bt-proj-project-2606",
        production_url="https://project-2606.bldgtyp.com",
        renderer_source=renderer,
        init_git=False,
        overwrite=True,
    )

    assert not (target / "old.md").exists()
    assert (target / "project.yaml").exists()


def _make_renderer(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "package.json").write_text('{"scripts":{"build":"echo build","dev":"echo dev","dev:editor":"echo editor"}}')
    (path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (path / "astro.config.mjs").write_text("export default {};\n")
    (path / "tsconfig.json").write_text("{}\n")
    (path / "src").mkdir()
    (path / "scripts").mkdir()
    (path / "tina").mkdir()
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
