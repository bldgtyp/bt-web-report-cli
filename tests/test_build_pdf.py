"""Tests for the `btwr build-pdf` command."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from bt_web_report_cli.__main__ import main
from bt_web_report_cli.runtime import RuntimeWorkspace


def _fake_workspace(project_path: Path, workspace_path: Path) -> RuntimeWorkspace:
    return RuntimeWorkspace(
        project_path=project_path,
        renderer_path=workspace_path,
        workspace_path=workspace_path,
    )


def test_build_pdf_runs_renderer_build_pdf_script_and_prints_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "04_Web"
    project.mkdir()
    workspace = tmp_path / "workspace"
    pdf_path = workspace / "dist" / "report.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")

    calls: dict[str, object] = {}

    def fake_run_renderer_script(project_path: Path, script: str, **kwargs: object) -> RuntimeWorkspace:
        calls["project_path"] = project_path
        calls["script"] = script
        calls["kind"] = kwargs.get("kind")
        return _fake_workspace(project_path, workspace)

    monkeypatch.setattr("bt_web_report_cli.__main__.run_renderer_script", fake_run_renderer_script)

    result = CliRunner().invoke(main, ["build-pdf", str(project), "--pnpm", "pnpm-dev"])

    assert result.exit_code == 0, result.output
    assert calls["script"] == "build:pdf"
    assert calls["kind"] == "build"
    assert f"PDF ready: {pdf_path}" in result.output


def test_build_pdf_errors_when_artifact_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "04_Web"
    project.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()  # no dist/report.pdf produced

    monkeypatch.setattr(
        "bt_web_report_cli.__main__.run_renderer_script",
        lambda project_path, script, **kwargs: _fake_workspace(project_path, workspace),
    )

    result = CliRunner().invoke(main, ["build-pdf", str(project)])

    assert result.exit_code != 0
    assert "was not produced" in result.output
