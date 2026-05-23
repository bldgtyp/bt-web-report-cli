"""btwr entry point — wired by the `btwr` script in pyproject.toml."""

import json
from pathlib import Path

import click

from bt_web_report_cli.assets import create_image_pair
from bt_web_report_cli.new_project import create_project, publish_project
from bt_web_report_cli.pin_project import PinError, pin_project, workflow_pin_status
from bt_web_report_cli.runtime import app_support_dir, prepare_runtime_workspace, run_renderer_script
from bt_web_report_cli.scrape import scrape_project


@click.group()
def main() -> None:
    """btwr — bt-web-report CLI."""


@main.group()
def assets() -> None:
    """Author client-visible report assets."""


@assets.command("image-pair")
@click.argument("project_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("source_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--asset-path",
    required=True,
    help="Display image path relative to the project's assets dir, e.g. windows/radiation/winter.png.",
)
@click.option(
    "--full-asset-path",
    help="High-resolution image path relative to the project's assets dir. Defaults to '<stem>-full<ext>'.",
)
@click.option("--max-width", default=1200, show_default=True, help="Maximum display-image width in pixels.")
@click.option("--max-height", type=int, help="Optional maximum display-image height in pixels.")
@click.option("--overwrite", is_flag=True, help="Replace existing output images.")
def image_pair(
    project_path: Path,
    source_path: Path,
    asset_path: str,
    full_asset_path: str | None,
    max_width: int,
    max_height: int | None,
    overwrite: bool,
) -> None:
    """Create a display/full-resolution image pair and print project URLs as JSON."""

    try:
        result = create_image_pair(
            project_path,
            source_path,
            asset_path,
            full_asset_path=full_asset_path,
            max_width=max_width,
            max_height=max_height,
            overwrite=overwrite,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))


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
@click.option("--slug", required=True, help="Project slug, for example project-2606.")
@click.option("--title", required=True, help="Client-visible project title.")
@click.option("--repo", required=True, help="GitHub repo / Pages project name, normally bt-proj-<number>-<name>.")
@click.option("--repo-owner", default="bldgtyp-projects", show_default=True, help="GitHub owner for project repos.")
@click.option(
    "--production-url",
    required=True,
    help="Production URL, normally https://project-<number>.bldgtyp.com.",
)
@click.option("--client", help="Client name.")
@click.option("--building", help="Building name.")
@click.option("--phase", help="Project phase.")
@click.option("--phpp", type=click.Path(path_type=Path), help="Optional PHPP workbook path.")
@click.option("--renderer-source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--no-git", is_flag=True, help="Create files without git init.")
@click.option("--no-github", is_flag=True, help="Skip GitHub repo creation, initial commit, and push.")
@click.option("--gh", "gh_executable", default="gh", show_default=True, help="GitHub CLI executable.")
@click.option("--overwrite", is_flag=True, help="Replace existing 04_Web contents after explicit confirmation.")
def new(
    target_web_path: Path,
    slug: str,
    title: str,
    repo: str,
    repo_owner: str,
    production_url: str,
    client: str | None,
    building: str | None,
    phase: str | None,
    phpp: Path | None,
    renderer_source: Path | None,
    no_git: bool,
    no_github: bool,
    gh_executable: str,
    overwrite: bool,
) -> None:
    """Create and publish a content-only report repo in 04_Web."""

    result = None
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
            overwrite=overwrite,
        )
        if not no_git and not no_github:
            result = publish_project(
                target,
                repo_owner=repo_owner,
                repo_name=repo,
                commit_message=f"Initial commit {slug} report",
                gh_executable=gh_executable,
            )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created content-only project: {target}")
    if no_git:
        click.echo("skipped git initialization")
    elif no_github:
        click.echo("skipped GitHub repo creation and push")
    elif result is not None:
        action = "committed and pushed" if result.committed else "pushed existing commit"
        click.echo(f"{action}: {result.repo_full_name} ({result.remote_url})")


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


@main.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--renderer", "renderer_ref", help="Renderer (template) SHA or tag to pin.")
@click.option("--schemas", "schemas_ref", help="Schemas SHA or tag to pin.")
@click.option(
    "--workflow",
    "workflow_ref",
    help="Reusable-workflow SHA or tag. Defaults to --renderer.",
)
@click.option("--show", is_flag=True, help="Print the current pin state and exit without writing.")
def pin(
    project_path: Path,
    renderer_ref: str | None,
    schemas_ref: str | None,
    workflow_ref: str | None,
    show: bool,
) -> None:
    """Pin a per-project repo's workflows to explicit SHAs.

    Rewrites ``.github/workflows/{ci,deploy}.yml`` to replace floating ``main``
    refs with the provided SHAs. Idempotent: re-running with the same refs
    produces zero diff. Project values like ``cloudflare-pages-project`` are
    not touched.
    """

    if show:
        status = workflow_pin_status(project_path)
        if not status:
            raise click.ClickException(f"No workflows found in {project_path}/.github/workflows/")
        for relative, info in status.items():
            click.echo(f"{relative}:")
            for label, value in info.items():
                click.echo(f"  {label}: {value if value is not None else '(missing)'}")
        return

    if not renderer_ref or not schemas_ref:
        raise click.UsageError("--renderer and --schemas are required (or pass --show to inspect).")

    try:
        result = pin_project(
            project_path,
            renderer_ref=renderer_ref,
            schemas_ref=schemas_ref,
            workflow_ref=workflow_ref,
        )
    except PinError as exc:
        raise click.ClickException(str(exc)) from exc

    for relative in result.files_changed:
        click.echo(f"pinned {relative}")
    for relative in result.files_unchanged:
        click.echo(f"unchanged {relative} (already at target refs)")
    for relative in result.files_missing:
        click.echo(f"missing {relative}")
    if not result.files_changed:
        click.echo("no changes written")


if __name__ == "__main__":
    main()
