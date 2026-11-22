"""Texture compression on export.

Converts non-DDS textures (e.g. PNGs) to DDS during export using an external compressor tool. The settings each texture
is converted with come from user-customizable, per-game presets. A preset holds the compressor CLI arguments plus rules to
automatically match textures.
"""

# TODO(rdr2): remaining work to use the RDR2 presets on the dev/rdr2 branch (everything resolves to GTA V for now):
#   1. Map `SollumzGame` ("GTA5"/"RDR2") to the preset store game ids ("gta5"/"rdr2"), e.g. `value.lower()`.
#   2. Group the export pre-pass (`_collect_texture_compression_items` in sollumz_operators.py) by game: each
#      object's `sz_game`. Call `session.prepare(items, game)` once per game.
#   3. Pass the asset's game to `session.get` in the YTD export code, which falls back to GTA V. E.g. add a
#      `game` field to `ExportContext`, set where the context is created for each object or texture dictionary.
#   4. `compressor_preset_search` (properties.py) always lists GTA V presets. Use the scene's game instead, the preset
#      override is stored in the image, which isn't tied to a game.
#   5. Preferences (ui.py): only offer games in `enabled_games` in the presets game dropdown.

import fnmatch
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_GAME = "gta5"
"""Preset store game id of GTA V, currently the only game assets are exported for."""


@dataclass(slots=True, frozen=True)
class CompressJob:
    """One texture conversion.
    Compress `input` into the DDS file `output`, using the compressor CLI arguments `args`.
    """

    input: Path
    output: Path
    args: str


@dataclass(slots=True, frozen=True)
class PresetRule:
    name: str
    patterns: tuple[str, ...]
    """Glob patterns matched against the lowercased texture name. If empty, no name matching."""
    parameters: frozenset[str]
    """Lowercased texture parameter names, e.g. "bumpsampler". If empty, no texture parameter matching."""
    args: str
    """Compressor CLI arguments."""
    args_alpha: str = ""
    """Compressor CLI arguments used instead of `args` for textures with transparent pixels. If empty, always use
    `args`.
    """

    def args_for(self, has_alpha: bool) -> str:
        return self.args_alpha if has_alpha and self.args_alpha else self.args


def _split_list(value) -> list[str]:
    return [s for part in str(value or "").split(",") if (s := part.strip().lower())]


def rule_from_preset_dict(preset: dict) -> PresetRule:
    """Builds a `PresetRule` from a preset store dict
    (`{"name": ..., "data": {"pattern": ..., "parameters": ..., "args": ..., "args_alpha": ...}}`).
    `pattern` and `parameters` are comma-separated lists.
    """
    data = preset.get("data")
    if not isinstance(data, dict):
        data = {}
    return PresetRule(
        name=str(preset.get("name", "")).strip(),
        patterns=tuple(_split_list(data.get("pattern"))),
        parameters=frozenset(_split_list(data.get("parameters"))),
        args=str(data.get("args", "")).strip(),
        args_alpha=str(data.get("args_alpha", "")).strip(),
    )


def rules_from_preset_dicts(presets: Iterable[dict]) -> tuple[PresetRule, ...]:
    """Converts preset dicts from the preset store into `PresetRule`s, preserving order."""
    rules = []
    for preset in presets:
        rule = rule_from_preset_dict(preset)
        if not rule.args:
            from ... import logger

            logger.warning(f"Texture compression preset '{rule.name}' has no arguments and will be ignored.")
            continue
        rules.append(rule)
    return tuple(rules)


def _is_catch_all(rule: PresetRule) -> bool:
    return not rule.patterns and not rule.parameters


def _matches_parameter(rule: PresetRule, parameter_name: str | None) -> bool:
    return bool(parameter_name) and parameter_name.lower() in rule.parameters


def _matches_name(rule: PresetRule, texture_name: str) -> bool:
    texture_name = (texture_name or "").lower()
    return any(fnmatch.fnmatchcase(texture_name, pattern) for pattern in rule.patterns)


def preset_matches(
    rule: PresetRule,
    texture_name: str,
    parameter_name: str | None,
    known_parameters: frozenset[str] = frozenset(),
) -> bool:
    """Whether `rule` should be applied to a texture with the given name, used in the given texture parameter of a
    shader (e.g. "DiffuseSampler").

    The texture parameter says how the game uses the texture, so it is more reliable than the name. In a preset that
    lists texture parameters, the name patterns are only a fallback for textures whose texture parameter is not in
    `known_parameters` (the texture parameters listed by any preset), e.g. standalone YTD textures. E.g. a 'rock_n'
    texture in a DiffuseSampler is not treated as a normal map. A preset that lists no texture parameters always
    matches by name.
    """
    if _is_catch_all(rule) or _matches_parameter(rule, parameter_name):
        return True

    if not _matches_name(rule, texture_name):
        return False

    parameter_known = bool(parameter_name) and parameter_name.lower() in known_parameters
    return not rule.parameters or not parameter_known


def find_preset(
    presets: Sequence[PresetRule],
    texture_name: str,
    parameter_name: str | None,
    override_name: str = "",
) -> PresetRule | None:
    """Resolves the preset to convert a texture with, or `None` if nothing matches.

    An explicit override wins. Otherwise the first preset that matches (see `preset_matches`) wins, top to bottom.
    Catch-all presets (no matching rules) are only used when no other preset matches.
    """
    override_name = (override_name or "").strip()
    if override_name:
        for preset in presets:
            if preset.name == override_name:
                return preset
        from ... import logger

        logger.warning(
            f"Texture '{texture_name}' is set to use compression preset '{override_name}', but no "
            f"such preset exists. Falling back to automatic matching."
        )

    known_parameters = frozenset().union(*(preset.parameters for preset in presets))
    for preset in presets:
        if not _is_catch_all(preset) and preset_matches(preset, texture_name, parameter_name, known_parameters):
            return preset

    return next((preset for preset in presets if _is_catch_all(preset)), None)


@dataclass(slots=True, frozen=True)
class TextureCompressorSettings:
    """Immutable texture compression configuration for one export operation, snapshotted from the
    addon preferences and the preset store at operator time."""

    executable: Path
    presets_by_game: Mapping[str, tuple[PresetRule, ...]]
    """Game id -> that game's presets, in matching order."""
    run: Callable[[Path, Sequence[CompressJob]], None]
    """Runs the compressor: `run(executable, jobs)`. Must not raise on conversion problems."""
    cache_dir: Path | None = None
    """Directory of the persistent texture cache. If `None`, the cache is disabled."""
    cache_salt: str = ""
    """Identifies the compressor build, part of every cache key."""
    cache_size_limit: int = 4 * 1024**3
    """Size in bytes past which the least recently used cache entries are pruned after an export."""

    def presets_for(self, game: str) -> tuple[PresetRule, ...]:
        """The presets of `game`."""
        return self.presets_by_game.get(game, ())


@dataclass(slots=True, frozen=True)
class CompressorBackend:
    """A compressor integration. Backends self-register at module import time via `register_backend`."""

    id: str
    label: str
    description: str
    preset_categories: Mapping[str, Any]
    """Game id -> the `shared.presets.PresetCategory` holding this backend's presets for that game."""
    executable_prop: str
    """Name of this backend's executable-path property on `SollumzTextureCompressorPrefs`."""
    run: Callable[[Path, Sequence[CompressJob]], None]
    """Runs the compressor: `run(executable, jobs)`. Must not raise on conversion problems."""
    install_hint: tuple[str, ...] = ()
    """Short instructions shown in the preferences when the executable is not found, one line per item."""
    download_url: str = ""
    """Web page to download the compressor from, opened from the preferences when the executable is not found."""


BACKENDS: dict[str, CompressorBackend] = {}


def register_backend(backend: CompressorBackend):
    existing = BACKENDS.get(backend.id)
    if existing is not None:
        if existing is backend:
            return
        raise ValueError(f"texture compressor backend '{backend.id}' is already registered")
    BACKENDS[backend.id] = backend
