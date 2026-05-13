"""btwr entry point — wired by the `btwr` script in pyproject.toml."""

from pathlib import Path

import click

from bt_web_report_cli.scrape import scrape_project


@click.group()
def main() -> None:
    """btwr — bt-web-report CLI."""


@main.command()
def doctor() -> None:
    """Sanity-check the local environment."""
    import bt_web_report_schemas

    click.echo(f"btwr ok — schemas v{bt_web_report_schemas.__version__}")


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


if __name__ == "__main__":
    main()
