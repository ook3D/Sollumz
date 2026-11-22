from pathlib import Path

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

from ..presets import store
from . import BACKENDS, CompressorBackend, TextureCompressorSettings, rules_from_preset_dicts, find_preset, DEFAULT_GAME
from .cache import executable_fingerprint
from .nvtt import DEFAULT_EXECUTABLE_PATH as NVTT_DEFAULT_EXECUTABLE_PATH

# Imported after nvtt: the backends register on import, in the order they are listed in the preferences.
from .compressonator import DEFAULT_EXECUTABLE_PATH as COMPRESSONATOR_DEFAULT_EXECUTABLE_PATH


_BYTES_PER_MB = 1024**2


def _save_prefs_on_update(_self, _context):
    from ...sollumz_preferences import _save_preferences

    _save_preferences()


def _compressor_enum_items():
    items = [("NONE", "None", "Do not convert textures. Non-DDS embedded textures are skipped with a warning", 0)]
    items.extend(
        (backend.id, backend.label, backend.description, i) for i, backend in enumerate(BACKENDS.values(), start=1)
    )
    return tuple(items)


def _presets_game_enum_items():
    games = sorted({game for backend in BACKENDS.values() for game in backend.preset_categories})
    return tuple((game, game.upper(), f"Show the {game.upper()} presets", i) for i, game in enumerate(games))


class SollumzTextureCompressorPrefs(PropertyGroup):
    compressor: EnumProperty(
        name="Compressor",
        description="Compressor used to convert non-DDS embedded textures to DDS on export",
        items=_compressor_enum_items(),
        default="NONE",
        update=_save_prefs_on_update,
    )
    nvtt_executable_path: StringProperty(
        name="Executable",
        description="Path to nvtt_export.exe, included with the NVIDIA Texture Tools Exporter",
        subtype="FILE_PATH",
        default=NVTT_DEFAULT_EXECUTABLE_PATH,
        update=_save_prefs_on_update,
    )
    compressonator_executable_path: StringProperty(
        name="Executable",
        description="Path to compressonatorcli, included with AMD Compressonator",
        subtype="FILE_PATH",
        default=COMPRESSONATOR_DEFAULT_EXECUTABLE_PATH,
        update=_save_prefs_on_update,
    )
    cache_directory: StringProperty(
        name="Cache Directory",
        description=(
            "Directory where converted textures are kept, so later exports reuse them instead of converting the "
            "same textures again. '//' is relative to the .blend file. Leave empty to disable the cache"
        ),
        subtype="DIR_PATH",
        default="//.sollumz_texture_cache/",
        options={"PATH_SUPPORTS_BLEND_RELATIVE"} if bpy.app.version >= (4, 5, 0) else set(),
        update=_save_prefs_on_update,
    )
    cache_size_limit_mb: IntProperty(
        name="Cache Size Limit (MB)",
        description=(
            "Size of the texture cache, in megabytes, past which the least recently used textures are deleted after "
            "an export"
        ),
        default=4 * 1024,
        min=1,
        soft_max=32 * 1024,
        update=_save_prefs_on_update,
    )
    presets_game: EnumProperty(
        name="Game",
        description="Game whose texture compression presets are shown",
        items=_presets_game_enum_items(),
        default=DEFAULT_GAME,
        update=_save_prefs_on_update,
    )

    @property
    def active_backend(self) -> CompressorBackend | None:
        return BACKENDS.get(self.compressor)

    @property
    def executable_path(self) -> Path | None:
        """Absolute path to the active compressor executable, or `None` when no compressor is selected."""
        backend = self.active_backend
        if backend is None:
            return None
        # bpy.path.abspath: the FILE_PATH file browser can store a Blender "//"-relative path.
        return Path(bpy.path.abspath(getattr(self, backend.executable_prop)))

    def to_settings(self) -> TextureCompressorSettings | None:
        """Snapshot of the active compressor configuration for one export operation, or `None`
        when no compressor is selected."""
        backend = self.active_backend
        if backend is None:
            return None
        presets_by_game = {
            game: rules_from_preset_dicts(store.load_presets(category))
            for game, category in backend.preset_categories.items()
        }
        executable = self.executable_path
        return TextureCompressorSettings(
            executable=executable,
            presets_by_game=presets_by_game,
            run=backend.run,
            cache_dir=self.cache_directory_path,
            cache_salt=executable_fingerprint(backend.id, executable),
            cache_size_limit=self.cache_size_limit_mb * _BYTES_PER_MB,
        )

    @property
    def cache_directory_path(self) -> Path | None:
        """Absolute path of the texture cache directory, or `None` when the cache is disabled."""
        path = self.cache_directory.strip()
        if not path or (
            path.startswith("//") and not bpy.data.filepath  # .blend relative but unsaved disables the cache
        ):
            return None
        return Path(bpy.path.abspath(path))


def auto_compressor_preset_text(context, image, texture_name: str, parameter_name: str | None) -> str:
    """Describes the compression preset picked automatically for a texture, shown in its empty override field."""
    from ...sollumz_preferences import get_addon_preferences
    from .imageutils import is_dds_image

    backend = get_addon_preferences(context).texture_compressor.active_backend
    if backend is None:
        return "* Texture compression disabled"
    if is_dds_image(image):
        return "* Already DDS"
    category = backend.preset_categories.get(DEFAULT_GAME)
    presets = rules_from_preset_dicts(store.load_presets(category)) if category is not None else ()
    preset = find_preset(presets, texture_name, parameter_name)
    return preset.name if preset is not None else "* No matching preset"


def compressor_preset_search(self, context, edit_text):
    """`search=` callback for the per-texture compression preset override properties."""
    from ...sollumz_preferences import get_addon_preferences

    backend = get_addon_preferences(context).texture_compressor.active_backend
    if backend is None:
        return
    category = backend.preset_categories.get(DEFAULT_GAME)
    if category is None:
        return
    for preset in store.load_presets(category):
        if name := preset.get("name"):
            yield name
