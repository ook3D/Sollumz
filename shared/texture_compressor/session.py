import re
import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import bpy
import numpy as np
from bpy.types import Image

from ... import logger
from . import DEFAULT_GAME, CompressJob, TextureCompressorSettings, find_preset
from .cache import TextureCache, digest_bytes, digest_file
from .imageutils import (
    image_has_alpha,
    image_source_path,
    is_dds_image,
    is_generated_image,
    is_valid_dds_file,
    texture_extension,
)

# Blender image format -> file extension, used to name the temporary input file written for packed
# images so the compressor recognizes the format.
_FORMAT_EXT = {
    "PNG": ".png",
    "TARGA": ".tga",
    "TARGA_RAW": ".tga",
    "JPEG": ".jpg",
    "JPEG2000": ".jp2",
    "BMP": ".bmp",
    "TIFF": ".tif",
    "OPEN_EXR": ".exr",
    "HDR": ".hdr",
    "CINEON": ".cin",
    "DPX": ".dpx",
    "IRIS": ".rgb",
    "WEBP": ".webp",
}


def _to_rgba(pixels: np.ndarray, channels: int) -> np.ndarray:
    """Expands a flat 1 to 3 channel pixel buffer to RGBA (gray replicated, opaque alpha)."""
    src = pixels.reshape(-1, channels)
    rgba = np.ones((src.shape[0], 4), dtype=np.float32)
    if channels == 1:
        rgba[:, :3] = src
    else:
        rgba[:, :channels] = src[:, :3]
    return rgba.reshape(-1)


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)


def _temp_basename(image: Image) -> str:
    # id(image) guarantees uniqueness, since distinct images can share a sanitized name (e.g.
    # "rock 01" and "rock_01" both sanitize to "rock_01"). The image lives for the whole export.
    return f"{_safe_filename(image.name)}_{id(image):x}"


def _input_ext(image: Image) -> str:
    suffix = texture_extension(image)
    if suffix and suffix.lower() != ".dds":
        return suffix
    # Fall back to the image's Blender format, with ".png" is a fallback for unknown formats.
    return _FORMAT_EXT.get(image.file_format, ".png")


@dataclass(slots=True)
class TextureConversionSession:
    """Texture conversion state for one export operation.

    The export operator creates one session from the settings snapshot, calls `prepare` up front with every texture
    being exported, matching each non-DDS texture to a compression preset and converting them all in a single batched
    compressor invocation, and calls `cleanup` after all bundles have been saved. Results are cached as temp
    DDS paths, keyed per image. When export needs a texture, `get` returns its result.
    A texture that matches no preset (or fails to convert) is skipped and export warns about the texture.
    """

    settings: TextureCompressorSettings | None = None
    # (id(image), texture_name, game) -> converted DDS path, or None once handled but not converted
    # (skipped / failed). See `_temp_basename` for why id(image) is a valid key.
    _results: dict = field(default_factory=dict)
    # (id(image), texture_name, game) -> matched preset name (or None if no preset matched), to warn on
    # conflicting settings. Only set for textures that went through preset matching, so DDS images
    # and textures without a readable input never produce conflict warnings.
    _matched: dict = field(default_factory=dict)
    _temp_dir: Path | None = None
    _unavailable: bool = False  # compressor executable missing, warned once, disabled this export.
    _cache: TextureCache | None = None
    _cache_opened: bool = False  # opened on first use (or failed to, in which case `_cache` stays None)
    _reused: int = 0  # textures taken from the cache, this export
    _converted: int = 0  # textures converted by the compressor, this export

    def _get_cache(self) -> TextureCache | None:
        if not self._cache_opened:
            self._cache_opened = True
            if self.settings.cache_dir is not None:
                self._cache = TextureCache.open(self.settings.cache_dir, self.settings.cache_salt)
        return self._cache

    @staticmethod
    def _has_input(image: Image) -> bool:
        packed = image.packed_file
        return bool(packed and packed.data) or is_generated_image(image) or image_source_path(image).is_file()

    @staticmethod
    def _input_digest(image: Image) -> str | None:
        """Hash of the pixels the compressor would read for `image`, or `None` if there is no input."""
        packed = image.packed_file
        data = packed.data if packed else None
        if data:
            return digest_bytes(bytes(data))

        if is_generated_image(image):
            width, height = image.size
            pixels = np.empty(width * height * image.channels, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            # Everything `_write_generated` uses
            meta = (
                f"{width}x{height}x{image.channels}",
                str(image.is_float),
                image.colorspace_settings.name,
                image.alpha_mode,
            )
            return digest_bytes(pixels.tobytes(), "generated", *meta)

        path = image_source_path(image)
        return digest_file(path) if path.is_file() else None

    def _temp_path(self) -> Path:
        if self._temp_dir is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="sollumz_texconv_"))
        return self._temp_dir

    def _write_generated(self, image: Image) -> Path | None:
        """Writes the pixels of a generated image to a temp PNG, leaving `image` untouched."""
        output_path = self._temp_path() / (_temp_basename(image) + "_src.png")
        width, height = image.size
        tmp = None
        try:
            pixels = np.empty(width * height * image.channels, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            if image.channels != 4:
                pixels = _to_rgba(pixels, image.channels)

            tmp = bpy.data.images.new("__sollumz_texconv_tmp", width, height, alpha=True, float_buffer=image.is_float)
            tmp.colorspace_settings.name = image.colorspace_settings.name
            tmp.alpha_mode = image.alpha_mode
            tmp.pixels.foreach_set(pixels)
            tmp.file_format = "PNG"
            tmp.save(filepath=str(output_path))
        except (RuntimeError, ValueError, TypeError) as e:
            logger.warning(f"Generated image '{image.name}' could not be written for conversion: {e}")
            return None
        finally:
            if tmp is not None:
                bpy.data.images.remove(tmp)
        return output_path

    def _resolve_input(self, image: Image) -> Path | None:
        packed = image.packed_file
        data = packed.data if packed else None
        if data:
            input_path = self._temp_path() / (_temp_basename(image) + "_src" + _input_ext(image))
            try:
                input_path.write_bytes(bytes(data))
            except OSError as e:
                logger.warning(f"Texture '{image.name}' could not be written for conversion: {e}")
                return None
            return input_path

        if is_generated_image(image):
            return self._write_generated(image)

        path = image_source_path(image)
        if not path.is_file():
            # Export emits the "file does not exist" warning
            return None

        return path

    def prepare(self, items, game: str = DEFAULT_GAME):
        """Converts the non-DDS textures in `items` to DDS in one batched compressor invocation.

        `items` is an iterable of `(image, texture_name, parameter_name, override_preset_name)`.
        `parameter_name` is the shader texture parameter, `None` for textures without one (e.g. a standalone YTD).
        Does nothing when no compressor is configured or its executable is unavailable. Already prepared textures are
        not converted again.
        """
        settings = self.settings
        if settings is None or self._unavailable:
            return

        if not settings.executable.is_file():
            logger.warning(
                f"Texture compression skipped: compressor executable not found at '{settings.executable}'. "
                f"Set the path in Sollumz preferences > Import / Export > Texture Compression."
            )
            self._unavailable = True
            return

        queued: list[tuple[str, tuple, CompressJob]] = []
        for image, texture_name, parameter_name, override_name in items:
            if image is None or not texture_name:
                continue

            key = (id(image), texture_name, game)
            seen = key in self._results

            # DDS textures are embedded as-is
            if is_dds_image(image):
                if not seen and (override_name or "").strip():
                    logger.warning(
                        f"Texture '{texture_name}' is already a DDS texture. Its compression preset "
                        f"override is ignored."
                    )
                self._results[key] = None
                continue

            if seen:
                # Same texture seen again (duplicate slot / multiple nodes sharing the image). Only
                # the first conversion is used, so warn if this occurrence would have matched a
                # different preset.
                if key in self._matched:
                    previous = self._matched[key]
                    preset = find_preset(settings.presets_for(game), texture_name, parameter_name, override_name or "")
                    current = preset.name if preset is not None else None
                    if current != previous:
                        logger.warning(
                            f"Texture '{texture_name}' is used with different compression settings "
                            f"('{previous}' vs '{current}'). Using '{previous}'."
                        )
                continue

            preset = find_preset(settings.presets_for(game), texture_name, parameter_name, override_name or "")
            if preset is None:
                self._results[key] = None
                self._matched[key] = None
                continue

            if not self._has_input(image):
                # Export emits the "file does not exist" warning
                self._results[key] = None
                continue

            # Only scan pixels when the preset has different arguments for transparent textures.
            use_args_alpha = bool(preset.args_alpha) and image_has_alpha(image)
            args = preset.args_for(use_args_alpha)

            cache_key = None
            if (cache := self._get_cache()) is not None and (digest := self._input_digest(image)) is not None:
                cache_key = cache.key(args, digest)
                cached = cache.get(cache_key)
                if cached is not None and is_valid_dds_file(cached):
                    self._results[key] = cached
                    self._matched[key] = preset.name
                    self._reused += 1
                    continue

            input_path = self._resolve_input(image)
            if input_path is None:
                self._results[key] = None
                continue

            output_dir = self._temp_path() / game
            output_dir.mkdir(exist_ok=True)
            output_path = output_dir / f"{_safe_filename(texture_name)}_{id(image):x}.dds"
            self._results[key] = None  # pending
            self._matched[key] = preset.name
            queued.append((texture_name, key, cache_key, CompressJob(input_path, output_path, args)))

        if not queued:
            return

        try:
            settings.run(settings.executable, [job for *_, job in queued])
        except Exception:
            logger.warning(f"Texture compression failed:\n{traceback.format_exc()}")
            return

        for texture_name, key, cache_key, job in queued:
            if not is_valid_dds_file(job.output):
                logger.warning(f"Texture '{texture_name}' compression produced no valid DDS output.")
                continue

            self._results[key] = job.output
            self._converted += 1
            if cache_key is not None and self._cache is not None:
                try:
                    self._results[key] = self._cache.put(cache_key, job.output, move=True)
                except OSError as e:
                    logger.warning(f"Texture cache disabled: cannot write to '{self._cache.directory}': {e}")
                    self._cache = None

    def get(self, image: Image, texture_name: str, game: str = DEFAULT_GAME) -> Path | None:
        """The DDS converted by `prepare` for `texture_name` exported for `game`, or `None` when the texture was not
        converted or `prepare` never saw it.
        """
        return self._results.get((id(image), texture_name, game))

    def cleanup(self):
        if self._reused:
            logger.info(f"Texture compression: {self._reused} reused from cache, {self._converted} converted.")
        if self._cache is not None:
            self._cache.prune(self.settings.cache_size_limit)
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        self._results.clear()
        self._matched.clear()
        self._unavailable = False
        self._cache = None
        self._cache_opened = False
        self._reused = 0
        self._converted = 0
