# bt-web-report-cli

The `btwr` CLI. Wraps PHPP scraping, project bootstrap, shared-runtime build,
and local preview/editor commands.

## Subcommands

| Command | Purpose |
|---|---|
| `btwr scrape <project_path>` | Read PHPP → write `data/*.csv` + `manifest.json` |
| `btwr new` | Bootstrap and publish a new `bt-proj-<number>-<name>` project |
| `btwr build` | Local Astro build |
| `btwr preview` | Local Astro preview through app support |
| `btwr editor` | Local Tina editor plus Astro preview through app support |
| `btwr doctor` | Sanity-check local env (schemas import, tokens present) |

`btwr new` writes a content-only `04_Web/`, initializes git, creates or verifies
the private GitHub repo under `bldgtyp-projects`, sets `origin`, creates the
initial commit, and pushes `main`. The copied GitHub Actions workflow handles
Cloudflare Pages project/domain setup and deploy after the first push.

## Dev quickstart

Run from the workspace root (`bt-web-report/`):

```bash
uv sync
uv run btwr doctor
```

See [`../context/data-pipeline.html`](../context/data-pipeline.html) for
the PHPP → CSV → manifest pipeline this CLI implements.
