"""btwr entry point — wired by the `btwr` script in pyproject.toml."""

import click


@click.group()
def main() -> None:
    """btwr — bt-web-report CLI."""


@main.command()
def doctor() -> None:
    """Sanity-check the local environment."""
    import bt_web_report_schemas

    click.echo(f"btwr ok — schemas v{bt_web_report_schemas.__version__}")


if __name__ == "__main__":
    main()
