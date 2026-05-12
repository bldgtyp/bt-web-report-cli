# bt-web-report-cli

The `btwr` CLI. Wraps PHPP scraping, project bootstrap, build, deploy.

## Subcommands (planned)

| Command | Purpose |
|---|---|
| `btwr scrape <project_path>` | Read PHPP → write `data/*.csv` + `manifest.json` |
| `btwr new` | Bootstrap a new `bt-proj-<slug>` project |
| `btwr build` | Local Astro build |
| `btwr deploy` | Trigger Cloudflare Pages deploy |
| `btwr doctor` | Sanity-check local env (schemas import, tokens present) |

## Dev quickstart

Run from the workspace root (`bt-web-report/`):

```bash
uv sync
uv run btwr doctor
```

See [`../context/data_pipeline.md`](../context/data_pipeline.md) for
the PHPP → CSV → manifest pipeline this CLI implements.
