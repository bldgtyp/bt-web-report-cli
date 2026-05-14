"""btwr entry point — wired by the `btwr` script in pyproject.toml."""

from pathlib import Path

import click

from bt_web_report_cli.new_project import create_project
from bt_web_report_cli.runtime import app_support_dir, prepare_runtime_workspace, run_renderer_script
from bt_web_report_cli.scrape import scrape_project


@click.group()
def main() -> None:
    """btwr — bt-web-report CLI."""


@main.command()
def doctor() -> None:
    """Sanity-check the local environment."""
    import bt_web_report_schemas

    click.echo(f"btwr ok — schemas v{bt_web_report_schemas.__version__}")
    click.echo(f"runtime root: {app_support_dir()}")


@main.command()
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--phpp",
    "phpp_path",
    type=click.Path(path_type=Path),
    help="Explicit PHPP workbook path.",
)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(path_type=Path),
    help="Generated data output directory.",
)
@click.option("--reader", type=click.Choice(["openpyxl"]), default="openpyxl", show_default=True)
@click.option("--phpp-version", help="Override workbook PHPP version detection.")
@click.option("--debug-raw", is_flag=True, help="Reserved for later raw/debug dumps.")
def scrape(
    project_path: Path,
    phpp_path: Path | None,
    output_dir: Path | None,
    reader: str,
    phpp_version: str | None,
    debug_raw: bool,
) -> None:
    """Scrape a PHPP workbook into deterministic report data."""

    if debug_raw:
        raise click.ClickException("--debug-raw is reserved for a later Phase 1 slice.")
    try:
        manifest = scrape_project(
            project_path,
            phpp_path=phpp_path,
            output_dir=output_dir,
            reader_name=reader,
            phpp_version=phpp_version,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"scraped PHPP {manifest.phpp_version}: "
        f"{len(manifest.variants)} variants, recommended={manifest.recommended_variant_id}"
    )


@main.command()
@click.argument("target_web_path", type=click.Path(path_type=Path))
@click.option("--slug", required=True, help="Project slug, for example 2606-vandam.")
@click.option("--title", required=True, help="Client-visible project title.")
@click.option("--repo", required=True, help="GitHub repo name, normally bt-proj-<slug>.")
@click.option("--production-url", required=True, help="Production URL, for example https://2606-vandam.bldgtyp.com.")
@click.option("--client", help="Client name.")
@click.option("--building", help="Building name.")
@click.option("--phase", help="Project phase.")
@click.option("--phpp", type=click.Path(path_type=Path), help="Optional PHPP workbook path.")
@click.option("--renderer-source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--no-git", is_flag=True, help="Create files without git init.")
def new(
    target_web_path: Path,
    slug: str,
    title: str,
    repo: str,
    production_url: str,
    client: str | None,
    building: str | None,
    phase: str | None,
    phpp: Path | None,
    renderer_source: Path | None,
    no_git: bool,
) -> None:
    """Create a content-only report repo in 04_Web."""

    try:
        target = create_project(
            target_web_path,
            slug=slug,
            title=title,
            repo=repo,
            production_url=production_url,
            client=client,
            building=building,
            phase=phase,
            phpp=phpp,
            renderer_source=renderer_source,
            init_git=not no_git,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created content-only project: {target}")


@main.command()
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
@click.option("--renderer-source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--pnpm", "pnpm_executable", default="pnpm", show_default=True)
@click.option("--skip-install", is_flag=True, help="Do not install renderer dependencies.")
def build(project_path: Path, renderer_source: Path | None, pnpm_executable: str, skip_install: bool) -> None:
    """Build a project through the shared app-support renderer."""

    try:
        workspace = run_renderer_script(
            project_path,
            "build",
            kind="build",
            renderer_source=renderer_source,
            pnpm_executable=pnpm_executable,
            install=not skip_install,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"built {project_path}: {workspace.workspace_path / 'dist'}")


@main.command()
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
@click.option("--renderer-source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--pnpm", "pnpm_executable", default="pnpm", show_default=True)
@click.option("--skip-install", is_flag=True, help="Do not install renderer dependencies.")
def preview(project_path: Path, renderer_source: Path | None, pnpm_executable: str, skip_install: bool) -> None:
    """Run the Astro dev server from a disposable preview workspace."""

    try:
        run_renderer_script(
            project_path,
            "dev",
            kind="preview",
            renderer_source=renderer_source,
            pnpm_executable=pnpm_executable,
            install=not skip_install,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
@click.option("--renderer-source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--pnpm", "pnpm_executable", default="pnpm", show_default=True)
@click.option("--skip-install", is_flag=True, help="Do not install renderer dependencies.")
def editor(project_path: Path, renderer_source: Path | None, pnpm_executable: str, skip_install: bool) -> None:
    """Run the Tina local editor from a disposable preview workspace."""

    try:
        run_renderer_script(
            project_path,
            "dev:editor",
            kind="preview",
            renderer_source=renderer_source,
            pnpm_executable=pnpm_executable,
            install=not skip_install,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("prepare-runtime")
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
@click.option("--renderer-source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--pnpm", "pnpm_executable", default="pnpm", show_default=True)
@click.option("--skip-install", is_flag=True, help="Do not install renderer dependencies.")
def prepare_runtime(
    project_path: Path,
    renderer_source: Path | None,
    pnpm_executable: str,
    skip_install: bool,
) -> None:
    """Prepare and print the disposable runtime workspace path."""

    try:
        workspace = prepare_runtime_workspace(
            project_path,
            kind="build",
            renderer_source=renderer_source,
            pnpm_executable=pnpm_executable,
            install=not skip_install,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(workspace.workspace_path)


if __name__ == "__main__":
    main()
