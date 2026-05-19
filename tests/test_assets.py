import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from bt_web_report_cli.__main__ import main
from bt_web_report_cli.assets import create_image_pair, resolve_assets_dir


def test_create_image_pair_writes_display_and_full_images_returns_project_urls(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "04_Web")
    source = _make_source_image(tmp_path / "source.png", size=(2200, 1700))

    result = create_image_pair(project, source, "windows/radiation/winter.png")

    assert result.display_url == "/assets/windows/radiation/winter.png"
    assert result.full_url == "/assets/windows/radiation/winter-full.png"
    assert result.display_size.width == 1200
    assert result.display_size.height == 927
    assert result.full_size.width == 2200
    assert result.full_size.height == 1700
    assert result.display_path == project / "public" / "assets" / "windows" / "radiation" / "winter.png"
    assert result.full_path == project / "public" / "assets" / "windows" / "radiation" / "winter-full.png"
    assert _image_size(result.display_path) == (1200, 927)
    assert _image_size(result.full_path) == (2200, 1700)


def test_create_image_pair_uses_project_assets_dir(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "04_Web", assets_dir="public/custom-assets")
    source = _make_source_image(tmp_path / "source.png", size=(600, 300))

    result = create_image_pair(project, source, "cover/hero.jpg")

    assert resolve_assets_dir(project) == project / "public" / "custom-assets"
    assert result.display_url == "/custom-assets/cover/hero.jpg"
    assert result.full_url == "/custom-assets/cover/hero-full.jpg"
    assert _image_size(result.display_path) == (600, 300)
    assert _image_size(result.full_path) == (600, 300)


def test_create_image_pair_refuses_unsafe_asset_paths(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "04_Web")
    source = _make_source_image(tmp_path / "source.png")

    with pytest.raises(ValueError, match="clean relative path"):
        create_image_pair(project, source, "../escape.png")

    with pytest.raises(ValueError, match="clean relative path"):
        create_image_pair(project, source, "/absolute.png")

    with pytest.raises(ValueError, match="file extension"):
        create_image_pair(project, source, "cover/hero")

    with pytest.raises(ValueError, match="must be different"):
        create_image_pair(project, source, "cover/hero.png", full_asset_path="cover/hero.png")


def test_create_image_pair_refuses_overwrite_without_flag(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "04_Web")
    source = _make_source_image(tmp_path / "source.png")

    create_image_pair(project, source, "cover/hero.png")

    with pytest.raises(FileExistsError, match="already exists"):
        create_image_pair(project, source, "cover/hero.png")

    result = create_image_pair(project, source, "cover/hero.png", overwrite=True)

    assert result.display_url == "/assets/cover/hero.png"


def test_image_pair_cli_prints_json_for_manager(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "04_Web")
    source = _make_source_image(tmp_path / "source.png", size=(1600, 900))

    result = CliRunner().invoke(
        main,
        [
            "assets",
            "image-pair",
            str(project),
            str(source),
            "--asset-path",
            "cover/hero.png",
            "--max-width",
            "800",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["display_url"] == "/assets/cover/hero.png"
    assert payload["full_url"] == "/assets/cover/hero-full.png"
    assert payload["display_size"] == {"width": 800, "height": 450}
    assert payload["full_size"] == {"width": 1600, "height": 900}


def _make_project(path: Path, *, assets_dir: str = "public/assets") -> Path:
    path.mkdir(parents=True)
    (path / "project.yaml").write_text("slug: sample\n" "source_files:\n" f"  assets_dir: {assets_dir}\n")
    return path


def _make_source_image(path: Path, *, size: tuple[int, int] = (1200, 800)) -> Path:
    Image.new("RGBA", size, (255, 255, 255, 255)).save(path)
    return path


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size
