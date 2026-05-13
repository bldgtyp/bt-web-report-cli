from pathlib import Path

from bt_web_report_cli.io.project import resolve_workbook_path


def test_resolve_workbook_path_reads_template_source_files_phpp_path(tmp_path: Path) -> None:
    project = tmp_path / "04_Web"
    project.mkdir()
    workbook = tmp_path / "07_PHPP" / "model.xlsx"
    workbook.parent.mkdir()
    workbook.write_text("placeholder")
    (project / "project.yaml").write_text(
        'source_files:\n'
        '  phpp_path: "../07_PHPP/model.xlsx"\n'
        '  data_dir: "data"\n'
        '  assets_dir: "public/assets"\n'
    )

    assert resolve_workbook_path(project) == workbook.resolve()
