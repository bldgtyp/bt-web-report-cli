"""Project asset authoring helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml  # type: ignore[import-untyped]
from PIL import Image, ImageOps

DEFAULT_DISPLAY_MAX_WIDTH = 1200
DEFAULT_ASSETS_DIR = Path("public/assets")


@dataclass(frozen=True)
class ImageSize:
    width: int
    height: int


@dataclass(frozen=True)
class ImagePairResult:
    display_path: Path
    full_path: Path
    display_url: str
    full_url: str
    display_size: ImageSize
    full_size: ImageSize

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["display_path"] = str(self.display_path)
        data["full_path"] = str(self.full_path)
        return data


def create_image_pair(
    project_path: Path,
    source_path: Path,
    asset_path: str | Path,
    *,
    full_asset_path: str | Path | None = None,
    max_width: int = DEFAULT_DISPLAY_MAX_WIDTH,
    max_height: int | None = None,
    overwrite: bool = False,
) -> ImagePairResult:
    """Create display and high-resolution image assets for a report image slot.

    `asset_path` and `full_asset_path` are relative to the project's configured
    assets directory, normally `public/assets`. Returned URLs are project-root
    web URLs such as `/assets/windows/radiation/winter.optimized.png`.
    """

    project = project_path.expanduser().resolve()
    source = source_path.expanduser().resolve()
    if not project.exists():
        raise FileNotFoundError(f"Project path does not exist: {project}")
    if not source.is_file():
        raise FileNotFoundError(f"Source image does not exist: {source}")
    if max_width < 1:
        raise ValueError("max_width must be greater than zero.")
    if max_height is not None and max_height < 1:
        raise ValueError("max_height must be greater than zero when provided.")

    assets_dir = resolve_assets_dir(project)
    assets_dir.mkdir(parents=True, exist_ok=True)
    asset_rel = _clean_asset_path(asset_path)
    full_asset_rel = (
        _clean_asset_path(full_asset_path) if full_asset_path is not None else _default_full_path(asset_rel)
    )
    if full_asset_rel == asset_rel:
        raise ValueError("full_asset_path must be different from asset_path.")
    display_path = assets_dir / Path(*asset_rel.parts)
    full_path = assets_dir / Path(*full_asset_rel.parts)
    _guard_output(display_path, overwrite=overwrite)
    if full_path != display_path:
        _guard_output(full_path, overwrite=overwrite)

    with Image.open(source) as raw_image:
        image = ImageOps.exif_transpose(raw_image)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        _save_image(image, full_path)
        full_size = ImageSize(width=image.width, height=image.height)

        display_image = image.copy()
        display_image.thumbnail(_display_bounds(max_width=max_width, max_height=max_height), Image.Resampling.LANCZOS)
        display_path.parent.mkdir(parents=True, exist_ok=True)
        _save_image(display_image, display_path)
        display_size = ImageSize(width=display_image.width, height=display_image.height)

    try:
        assets_relative = assets_dir.relative_to(project)
    except ValueError as exc:
        raise ValueError(f"Configured assets_dir must be inside the project folder: {assets_dir}") from exc
    return ImagePairResult(
        display_path=display_path,
        full_path=full_path,
        display_url=_asset_url(assets_relative, asset_rel),
        full_url=_asset_url(assets_relative, full_asset_rel),
        display_size=display_size,
        full_size=full_size,
    )


def resolve_assets_dir(project_path: Path) -> Path:
    """Resolve the project's configured assets directory."""

    project = project_path.expanduser().resolve()
    project_yaml = project / "project.yaml"
    if not project_yaml.exists():
        return project / DEFAULT_ASSETS_DIR

    loaded = yaml.safe_load(project_yaml.read_text()) or {}
    data = loaded if isinstance(loaded, dict) else {}
    configured = _find_assets_entry(data) or str(DEFAULT_ASSETS_DIR)
    assets_dir = Path(configured).expanduser()
    if not assets_dir.is_absolute():
        assets_dir = project / assets_dir
    return assets_dir.resolve()


def _find_assets_entry(data: dict[str, Any]) -> str | None:
    for key in ("assets_dir", "assets"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value

    source_files = data.get("source_files")
    if isinstance(source_files, dict):
        value = source_files.get("assets_dir")
        if isinstance(value, str) and value:
            return value

    return None


def _clean_asset_path(value: str | Path) -> PurePosixPath:
    text = str(value).replace("\\", "/").strip()
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Asset path must be a clean relative path: {value!s}")
    if not path.suffix:
        raise ValueError(f"Asset path must include a file extension: {value!s}")
    return path


def _default_full_path(asset_path: PurePosixPath) -> PurePosixPath:
    if asset_path.stem.endswith(".optimized"):
        return asset_path.with_name(f"{asset_path.stem.removesuffix('.optimized')}.full{asset_path.suffix}")
    return asset_path.with_name(f"{asset_path.stem}.full{asset_path.suffix}")


def _guard_output(path: Path, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output image already exists: {path}")


def _display_bounds(*, max_width: int, max_height: int | None) -> tuple[int, int]:
    return (max_width, max_height or 1_000_000)


def _asset_url(assets_relative: Path, asset_path: PurePosixPath) -> str:
    public_relative = assets_relative
    if public_relative.parts and public_relative.parts[0] == "public":
        public_relative = Path(*public_relative.parts[1:])
    return "/" + PurePosixPath(public_relative.as_posix(), asset_path).as_posix()


def _save_image(image: Image.Image, path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        image.save(path, optimize=True, quality=85)
        return
    if suffix == ".webp":
        image.save(path, optimize=True, quality=85)
        return
    if suffix == ".png":
        image.save(path, optimize=True)
        return
    image.save(path)
