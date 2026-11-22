"""Tests for export-time texture compression."""

import struct
import subprocess
import sys
from pathlib import Path

import bpy
import numpy as np
import pytest

from .. import known_paths
from ..ytd.properties import TextureImageSource, get_texture_name
from ..ytd.ytdexport import create_txd_asset, extract_texture_dds_data_source
from .shared import data_path, log_capture
from .. import sollumz_preferences as prefs_mod
from ..iecontext import ExportContext, ExportSettings, export_context_scope
from ..shared.presets import store
from ..shared.texture_compressor import (
    CompressJob,
    PresetRule,
    TextureCompressorSettings,
    find_preset,
    preset_matches,
    rule_from_preset_dict,
    rules_from_preset_dicts,
)
from ..shared.texture_compressor import cache as cache_mod
from ..shared.texture_compressor import compressonator, nvtt
from ..shared.texture_compressor.session import TextureConversionSession
from ..ydr.ydrexport import get_embedded_textures_from_materials

DDS_HEADER_PLACEHOLDER = b"DDS " + b"\x00" * 124
DDS_PLACEHOLDER = DDS_HEADER_PLACEHOLDER + b"\x00" * 16  # header + some pixel data


def _rule(name="", patterns=(), parameters=(), args="--fake-args", args_alpha=""):
    if isinstance(patterns, str):
        patterns = (patterns,)
    return PresetRule(
        name=name,
        patterns=tuple(p.lower() for p in patterns),
        parameters=frozenset(s.lower() for s in parameters),
        args=args,
        args_alpha=args_alpha,
    )


DIFFUSE_RULE = _rule("Diffuse", patterns="*_d", parameters=("DiffuseSampler",), args="--format bc7 --mips")
NORMAL_RULE = _rule("Normal", patterns="*_n", parameters=("BumpSampler",), args="--format bc5 --mips")
DEFAULT_RULE = _rule("Default", args="--format bc7 --mips")


# --- preset_matches -------------------------------------------------------------------------------

def test_preset_matches_catch_all():
    p = _rule()
    assert preset_matches(p, "anything", None) is True
    assert preset_matches(p, "x", "BumpSampler") is True


def test_preset_matches_by_pattern():
    p = _rule(patterns="*_n")
    assert preset_matches(p, "rock_n", None) is True
    assert preset_matches(p, "rock_d", None) is False
    assert preset_matches(p, "ROCK_N", None) is True  # case-insensitive


def test_preset_matches_any_of_multiple_patterns():
    p = _rule(patterns=("*_n", "*_nrm"))
    assert preset_matches(p, "rock_n", None) is True
    assert preset_matches(p, "rock_nrm", None) is True
    assert preset_matches(p, "rock_d", None) is False


def test_preset_matches_by_parameter():
    p = _rule(parameters=("BumpSampler", "PlateBgBumpSampler"))
    assert preset_matches(p, "x", "BumpSampler") is True
    assert preset_matches(p, "x", "platebgbumpsampler") is True  # case-insensitive
    assert preset_matches(p, "x", "DiffuseSampler") is False
    assert preset_matches(p, "x", None) is False  # YTD textures have no texture parameter


def test_preset_matches_or_semantics():
    p = _rule(patterns="*_n", parameters=("BumpSampler",))
    assert preset_matches(p, "rock_n", "DiffuseSampler") is True  # name hits
    assert preset_matches(p, "rock_d", "BumpSampler") is True  # texture parameter hits
    assert preset_matches(p, "rock_d", "DiffuseSampler") is False  # neither


def test_preset_matches_name_is_fallback_for_known_parameters():
    p = _rule(patterns="*_n", parameters=("BumpSampler",))
    known = frozenset({"bumpsampler", "diffusesampler"})
    assert preset_matches(p, "rock_n", "DiffuseSampler", known) is False  # another preset's texture parameter decides
    assert preset_matches(p, "rock_n", "OtherSampler", known) is True  # unknown texture parameter: name decides
    assert preset_matches(p, "rock_n", None, known) is True

    name_only = _rule(patterns="script_rt_*")
    # Lists no texture parameters, the name decides
    assert preset_matches(name_only, "script_rt_tv", "DiffuseSampler", known) is True


def test_find_preset_first_match_wins():
    presets = (NORMAL_RULE, DEFAULT_RULE)
    assert find_preset(presets, "rock_n", None).name == "Normal"
    assert find_preset(presets, "rock", None).name == "Default"  # catch-all


def test_find_preset_parameter_match_wins_over_earlier_name_match():
    # "rock_n" plugged into a DiffuseSampler: the texture parameter says how the game uses it, so the later Diffuse
    # preset wins over the earlier Normal preset that only matches by name.
    presets = (NORMAL_RULE, DIFFUSE_RULE)
    assert find_preset(presets, "rock_n", "DiffuseSampler").name == "Diffuse"
    assert find_preset(presets, "rock_n", None).name == "Normal"  # no texture parameter (e.g. YTD): name decides
    assert find_preset(presets, "rock_n", "UnknownSampler").name == "Normal"


def test_find_preset_catch_all_only_when_nothing_else_matches():
    presets = (DEFAULT_RULE, NORMAL_RULE)
    assert find_preset(presets, "rock_n", None).name == "Normal"
    assert find_preset(presets, "rock", "BumpSampler").name == "Normal"
    assert find_preset(presets, "rock", None).name == "Default"


def test_find_preset_earlier_name_only_preset_wins_over_parameter_match():
    script_rt = _rule("Script RT", patterns="script_rt_*", args="--format bgra8 --no-mips")
    presets = (script_rt, DIFFUSE_RULE)
    assert find_preset(presets, "script_rt_tvscreen", "DiffuseSampler").name == "Script RT"
    assert find_preset(presets, "rock", "DiffuseSampler").name == "Diffuse"


def test_find_preset_override_wins():
    presets = (NORMAL_RULE, DIFFUSE_RULE)
    assert find_preset(presets, "rock_n", None, "Diffuse").name == "Diffuse"


def test_find_preset_override_missing_falls_back_to_auto():
    presets = (NORMAL_RULE, DEFAULT_RULE)
    assert find_preset(presets, "rock", None, "Nope").name == "Default"


def test_find_preset_no_match_returns_none():
    assert find_preset((NORMAL_RULE,), "rock", None) is None


def test_rule_from_preset_dict_normalizes():
    rule = rule_from_preset_dict({
        "name": " Foo ",
        "data": {
            "pattern": " *_N , *_Nrm,, ",
            "parameters": "BumpSampler, PlateBgBumpSampler, ",
            "args": " --format bc1 ",
            "args_alpha": " --format bc3 ",
        },
    })
    assert rule.name == "Foo"
    assert rule.patterns == ("*_n", "*_nrm")
    assert rule.parameters == frozenset({"bumpsampler", "platebgbumpsampler"})
    assert rule.args == "--format bc1"
    assert rule.args_alpha == "--format bc3"


def test_rule_from_preset_dict_tolerates_missing_data():
    rule = rule_from_preset_dict({"name": "Bare"})
    assert rule.name == "Bare"
    assert rule.patterns == ()
    assert rule.parameters == frozenset()
    assert rule.args == ""
    assert rule.args_alpha == ""


def test_rule_args_for():
    rule = _rule(args="--format bc1", args_alpha="--format bc3")
    assert rule.args_for(False) == "--format bc1"
    assert rule.args_for(True) == "--format bc3"
    assert _rule(args="--format bc5").args_for(True) == "--format bc5"  # no alpha args: always `args`


def test_rules_from_preset_dicts_drops_empty_args():
    rules = rules_from_preset_dicts([
        {"name": "Good", "data": {"pattern": "*_d", "parameters": "", "args": "--format bc7"}},
        {"name": "Empty", "data": {"pattern": "*_n", "parameters": "", "args": "   "}},
    ])
    assert [r.name for r in rules] == ["Good"]


@pytest.fixture()
def preset_config_dir(tmp_path, monkeypatch):
    """Redirect all preset storage into an isolated temp dir and clear the module-global store
    caches around the test.
    """
    store.clear_all_caches()
    monkeypatch.setattr(known_paths, "config_directory_path", lambda: str(tmp_path))
    yield tmp_path
    store.clear_all_caches()


@pytest.fixture()
def isolated_prefs(tmp_path, monkeypatch):
    """Redirect the preferences .ini into a temp dir and restore the real preferences afterwards."""
    monkeypatch.setattr(prefs_mod, "prefs_file_path", lambda: str(tmp_path / "sollumz_prefs.ini"))
    prefs = prefs_mod.get_addon_preferences()
    snapshot = prefs_mod._get_bpy_struct_as_dict(prefs)
    try:
        yield prefs
    finally:
        prefs_mod._update_bpy_struct_from_dict(prefs, snapshot, eval_strings=True)


BUNDLED_PRESET_NAMES = [
    "Script Render Target", "Normal", "Specular", "Detail", "Tint Palette", "Distance Map", "Cable", "Diffuse",
    "Default",
]


@pytest.mark.parametrize("game", ("gta5", "rdr2"))
@pytest.mark.parametrize("backend", ("nvtt", "compressonator"))
def test_bundled_default_presets(preset_config_dir, backend, game):
    categories = {
        "nvtt": nvtt.NVTT_PRESET_CATEGORIES,
        "compressonator": compressonator.COMPRESSONATOR_PRESET_CATEGORIES,
    }
    presets = store.load_presets(categories[backend][game])
    rules = rules_from_preset_dicts(presets)
    assert rules and len(rules) == len(presets)  # every preset loads, none dropped for empty args

    # The only catch-all is the automatic fallback, last so every other preset is tried first.
    catch_alls = [r.name for r in rules if not r.patterns and not r.parameters]
    assert catch_alls == ["Default"]


def test_store_edit_roundtrip_preserves_order(preset_config_dir):
    presets = list(store.load_presets(nvtt.NVTT_PRESET_CATEGORIES["gta5"]))
    presets[0], presets[1] = presets[1], presets[0]
    store.save_presets(nvtt.NVTT_PRESET_CATEGORIES["gta5"], presets)

    store.invalidate(nvtt.NVTT_PRESET_CATEGORIES["gta5"])  # force re-read from disk
    reloaded = store.load_presets(nvtt.NVTT_PRESET_CATEGORIES["gta5"])
    assert [p["name"] for p in reloaded] == [p["name"] for p in presets]


def test_to_settings_none_when_disabled(isolated_prefs):
    tc = isolated_prefs.texture_compressor
    tc.compressor = "NONE"
    assert tc.to_settings() is None


def test_to_settings_nvtt_snapshot(preset_config_dir, isolated_prefs):
    tc = isolated_prefs.texture_compressor
    tc.compressor = "NVTT"
    tc.cache_size_limit_mb = 2048
    settings = tc.to_settings()
    assert settings is not None
    assert settings.run is nvtt.run_batch
    assert settings.executable.name == "nvtt_export.exe"
    assert [r.name for r in settings.presets_for("gta5")] == BUNDLED_PRESET_NAMES
    assert settings.cache_size_limit == 2 * 1024**3


def _fake_run(captured, payload=DDS_PLACEHOLDER):
    """A `TextureCompressorSettings.run` stand-in that records each batch and writes `payload`
    to every job output."""
    def run(executable, jobs):
        captured.append(list(jobs))
        for job in jobs:
            job.output.write_bytes(payload)

    return run


def _settings(presets, run):
    # Any existing file passes the session's executable check, the fake run never launches it.
    return TextureCompressorSettings(executable=Path(sys.executable), presets_by_game={"gta5": tuple(presets)}, run=run)


def _make_embedded_png(tmp_path, name, parameter_name):
    img = bpy.data.images.new(name, 8, 8, alpha=True)
    img.pixels = [0.5, 0.3, 0.8, 1.0] * (8 * 8)
    png_path = tmp_path / f"{name}.png"
    img.filepath = str(png_path)
    img.filepath_raw = str(png_path)
    img.file_format = "PNG"
    img.save()

    mat = bpy.data.materials.new(f"{name}_mat")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = parameter_name
    node.image = img
    node.texture_properties.embedded = True
    return img, mat, node


def _prepare_item(node):
    # Same tuple the export operator's pre-pass builds per embedded texture node
    return (node.image, node.sollumz_texture_name, node.name, node.image.sz_compressor_preset)


def _cleanup(*mats_and_imgs):
    for x in mats_and_imgs:
        if isinstance(x, bpy.types.Material):
            bpy.data.materials.remove(x)
        else:
            bpy.data.images.remove(x)


def test_prepare_converts_all_textures_in_one_batch(context, tmp_path):
    img1, mat1, node1 = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    img2, mat2, node2 = _make_embedded_png(tmp_path, "rock_n", "BumpSampler")
    captured = []
    session = TextureConversionSession(_settings((DIFFUSE_RULE, NORMAL_RULE, DEFAULT_RULE), _fake_run(captured)))
    try:
        session.prepare([_prepare_item(node1), _prepare_item(node2)])

        assert len(captured) == 1  # a single batched compressor invocation
        args_by_texture = {job.output.name.rsplit("_", 1)[0]: job.args for job in captured[0]}
        assert args_by_texture == {"rock_d": DIFFUSE_RULE.args, "rock_n": NORMAL_RULE.args}

        with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
            textures = get_embedded_textures_from_materials([mat1, mat2])

        assert len(captured) == 1  # everything came from the prepared cache, no extra invocation
        for name in ("rock_d", "rock_n"):
            with textures[name].data.open() as f:
                assert f.read(4) == b"DDS "
    finally:
        session.cleanup()
        _cleanup(mat1, img1, mat2, img2)


@pytest.mark.parametrize("alpha, expected", ((1.0, "--opaque"), (0.5, "--alpha")))
def test_prepare_uses_alpha_args_for_transparent_textures(context, tmp_path, alpha, expected):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    img.pixels = [0.5, 0.3, 0.8, 1.0] * (8 * 8 - 1) + [0.5, 0.3, 0.8, alpha]  # only the last pixel varies
    captured = []
    rule = _rule("Diffuse", parameters=("DiffuseSampler",), args="--opaque", args_alpha="--alpha")
    session = TextureConversionSession(_settings((rule,), _fake_run(captured)))
    try:
        session.prepare([_prepare_item(node)])
        assert [job.args for job in captured[0]] == [expected]
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_image_has_alpha(context):
    from ..shared.texture_compressor.imageutils import image_has_alpha

    img = bpy.data.images.new("alpha_test", 4, 4, alpha=True)
    try:
        img.pixels = [1.0] * (4 * 4 * 4)
        assert not image_has_alpha(img)
        img.pixels = [1.0] * (4 * 4 * 4 - 1) + [0.99]  # 252/255, below the opaque threshold
        assert image_has_alpha(img)
        img.alpha_mode = "NONE"  # alpha ignored: treated as opaque
        assert not image_has_alpha(img)
    finally:
        bpy.data.images.remove(img)


def test_per_texture_override_selects_preset(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    node.image.sz_compressor_preset = "Normal"  # override the auto-matched "Diffuse"
    captured = []
    session = TextureConversionSession(_settings((DIFFUSE_RULE, NORMAL_RULE), _fake_run(captured)))
    try:
        session.prepare([_prepare_item(node)])
        assert [job.args for job in captured[0]] == [NORMAL_RULE.args]
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_duplicate_texture_name_converted_once_first_preset_wins(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    session = TextureConversionSession(_settings((DIFFUSE_RULE, NORMAL_RULE), _fake_run(captured)))
    try:
        item = _prepare_item(node)
        conflicting_item = (node.image, node.sollumz_texture_name, "BumpSampler", "")
        session.prepare([item, conflicting_item])

        assert len(captured) == 1
        assert [job.args for job in captured[0]] == [DIFFUSE_RULE.args]  # first occurrence wins
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_same_texture_name_in_different_images_converts_each(context, tmp_path):
    # Two DIFFERENT images sharing a texture name (e.g. two exported assets each with their own
    # "metal_d.png" in different folders) must each be converted from their own pixels — the session
    # spans the whole export operation, so a name alone must never return another image's DDS.
    dir1 = tmp_path / "asset1"
    dir1.mkdir()
    dir2 = tmp_path / "asset2"
    dir2.mkdir()
    img1, mat1, node1 = _make_embedded_png(dir1, "metal_d", "DiffuseSampler")
    img2, mat2, node2 = _make_embedded_png(dir2, "metal_d", "DiffuseSampler")

    captured = []

    def _run(executable, jobs):  # payload identifies which input produced each output
        captured.append(list(jobs))
        for job in jobs:
            job.output.write_bytes(DDS_HEADER_PLACEHOLDER + job.input.as_posix().encode("utf-8"))

    session = TextureConversionSession(_settings((DIFFUSE_RULE,), _run))
    try:
        session.prepare([_prepare_item(node1), _prepare_item(node2)])
        assert len(captured) == 1 and len(captured[0]) == 2  # both images converted, one batch

        with export_context_scope(ExportContext("asset1", ExportSettings(targets=()), textures=session)):
            textures1 = get_embedded_textures_from_materials([mat1])
        with export_context_scope(ExportContext("asset2", ExportSettings(targets=()), textures=session)):
            textures2 = get_embedded_textures_from_materials([mat2])

        with textures1["metal_d"].data.open() as f:
            assert f.read().endswith(b"asset1/metal_d.png")
        with textures2["metal_d"].data.open() as f:
            assert f.read().endswith(b"asset2/metal_d.png")
    finally:
        session.cleanup()
        _cleanup(mat1, img1, mat2, img2)


def test_no_matching_preset_skips_texture(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    session = TextureConversionSession(_settings((NORMAL_RULE,), _fake_run(captured)))  # nothing matches *_d
    try:
        session.prepare([_prepare_item(node)])
        assert captured == []

        with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
            textures = get_embedded_textures_from_materials([mat])
        assert textures["rock_d"].data is None  # non-DDS texture dropped with a warning
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_invalid_compressor_output_rejected(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    session = TextureConversionSession(_settings((DEFAULT_RULE,), _fake_run(captured, payload=b"JUNK")))
    try:
        session.prepare([_prepare_item(node)])
        assert len(captured) == 1
        assert session.get(img, "rock_d") is None  # output failed the DDS magic check
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_missing_executable_disables_conversion(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    settings = TextureCompressorSettings(
        executable=tmp_path / "does_not_exist.exe", presets_by_game={"gta5": (DEFAULT_RULE,)}, run=_fake_run(captured)
    )
    session = TextureConversionSession(settings)
    try:
        session.prepare([_prepare_item(node)])
        assert captured == []  # compressor never launched
        assert session.get(img, "rock_d") is None
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_no_settings_session_is_noop(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    session = TextureConversionSession(None)
    try:
        session.prepare([_prepare_item(node)])
        assert session.get(img, "rock_d") is None
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_uppercase_dds_file_passes_through_unconverted(context, tmp_path):
    dds_path = tmp_path / "rock.DDS"
    dds_path.write_bytes(DDS_PLACEHOLDER)
    img = bpy.data.images.new("rock", 8, 8)
    img.source = "FILE"
    img.filepath = str(dds_path)

    mat = bpy.data.materials.new("rock_mat")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = "DiffuseSampler"
    node.image = img
    node.texture_properties.embedded = True

    captured = []
    session = TextureConversionSession(_settings((DEFAULT_RULE,), _fake_run(captured)))
    try:
        with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
            textures = get_embedded_textures_from_materials([mat])

        assert captured == []  # already DDS, session never invoked
        with textures["rock"].data.open() as f:
            assert f.read(4) == b"DDS "
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_packed_dds_passes_through_unconverted(context, tmp_path):
    # Same image setup as PACK-mode imports
    img = bpy.data.images.new("rock.dds", 1, 1)
    img.source = "FILE"
    img.filepath = "//rock.dds"
    img.pack(data=DDS_PLACEHOLDER, data_len=len(DDS_PLACEHOLDER))

    mat = bpy.data.materials.new("rock_mat")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = "DiffuseSampler"
    node.image = img
    node.texture_properties.embedded = True

    captured = []
    session = TextureConversionSession(_settings((DEFAULT_RULE,), _fake_run(captured)))
    try:
        session.prepare([_prepare_item(node)])
        assert captured == []
        with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
            textures = get_embedded_textures_from_materials([mat])
        with textures[node.sollumz_texture_name].data.open() as f:
            assert f.read(4) == b"DDS "
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_no_session_keeps_old_skip_behavior(context, tmp_path):
    img, mat, _ = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    try:
        with export_context_scope(ExportContext("test", ExportSettings(targets=()))):
            textures = get_embedded_textures_from_materials([mat])
        assert textures["rock_d"].data is None  # png left as-is
    finally:
        _cleanup(mat, img)


def test_cleanup_removes_temp_dir(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    img.pack()  # force the session to write the packed image to its temp dir
    captured = []
    session = TextureConversionSession(_settings((DEFAULT_RULE,), _fake_run(captured)))
    try:
        session.prepare([_prepare_item(node)])
        temp_dir = session._temp_dir
        assert temp_dir is not None and temp_dir.is_dir()
        session.cleanup()
        assert not temp_dir.exists()
        assert session._temp_dir is None
    finally:
        session.cleanup()
        _cleanup(mat, img)


def _make_dds_node(tmp_path, name, parameter_name):
    dds_path = tmp_path / f"{name}.dds"
    dds_path.write_bytes(DDS_PLACEHOLDER)
    img = bpy.data.images.new(name, 8, 8)
    img.source = "FILE"
    img.filepath = str(dds_path)
    mat = bpy.data.materials.new(f"{name}_mat")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = parameter_name
    node.image = img
    node.texture_properties.embedded = True
    return img, mat, node


def test_shared_dds_texture_does_not_warn_conflicting_settings(context, tmp_path):
    # The same DDS image used by two texture parameters that would auto-match different presets
    img, mat, node = _make_dds_node(tmp_path, "metal", "DiffuseSampler")
    captured = []
    session = TextureConversionSession(_settings((DIFFUSE_RULE, NORMAL_RULE), _fake_run(captured)))
    try:
        with log_capture() as logs:
            session.prepare([_prepare_item(node), (node.image, node.sollumz_texture_name, "BumpSampler", "")])
        assert captured == []
        logs.assert_no_warnings_or_errors()
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_dds_texture_with_stale_override_warns_once(context, tmp_path):
    img, mat, node = _make_dds_node(tmp_path, "metal", "DiffuseSampler")
    node.image.sz_compressor_preset = "Renamed Preset"
    session = TextureConversionSession(_settings((DIFFUSE_RULE,), _fake_run([])))
    try:
        with log_capture() as logs:
            session.prepare([_prepare_item(node), _prepare_item(node)])
        logs.assert_warning(match="already a DDS texture", num=1)
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_missing_input_duplicate_does_not_warn_conflicting_settings(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    Path(bpy.path.abspath(img.filepath)).unlink()
    captured = []
    session = TextureConversionSession(_settings((DIFFUSE_RULE, NORMAL_RULE), _fake_run(captured)))
    try:
        with log_capture() as logs:
            session.prepare([_prepare_item(node), (node.image, node.sollumz_texture_name, "BumpSampler", "")])
        assert captured == []
        logs.assert_no_warnings_or_errors()  # the export chokepoint reports the missing file instead
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_duplicate_with_different_preset_warns_conflict(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    session = TextureConversionSession(_settings((DIFFUSE_RULE, NORMAL_RULE), _fake_run([])))
    try:
        with log_capture() as logs:
            session.prepare([_prepare_item(node), (node.image, node.sollumz_texture_name, "BumpSampler", "Normal")])
        logs.assert_warning(match="different compression settings", num=1)
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_truncated_dds_header_rejected(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    session = TextureConversionSession(_settings((DEFAULT_RULE,), _fake_run([], payload=b"DDS " + b"\x00" * 20)))
    try:
        session.prepare([_prepare_item(node)])
        assert session.get(img, "rock_d") is None
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_chokepoint_outside_export_context_warns_instead_of_raising(context, tmp_path):
    img, mat, _ = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    try:
        with log_capture() as logs:
            assert extract_texture_dds_data_source(img, "rock_d") is None
        logs.assert_warning(match="not in DDS format", num=1)
    finally:
        _cleanup(mat, img)


def test_txd_source_refresh_keeps_compressor_preset(context, tmp_path, monkeypatch):
    img = bpy.data.images.new("rock_d", 8, 8)
    monkeypatch.setattr(TextureImageSource, "find_images", lambda self, ctx: [(img, True)])
    txd = context.scene.sz_txds.new_texture_dictionary(name="test_txd_refresh")
    txd_name = txd.name
    try:
        txd.new_source()
        txd.refresh_from_sources(context)
        assert len(txd.textures) == 1 and txd.textures[0].managed_by_source
        img.sz_compressor_preset = "Normal"

        txd.refresh_from_sources(context)

        assert len(txd.textures) == 1
        assert txd.textures[0].image == img
        assert txd.textures[0].image.sz_compressor_preset == "Normal"
    finally:
        txds = context.scene.sz_txds.texture_dictionaries
        idx = next(i for i, t in enumerate(txds) if t.name == txd_name)
        txds.remove(idx)
        bpy.data.images.remove(img)


def _nvtt_jobs(tmp_path, n=2):
    return [CompressJob(tmp_path / f"in{i}.png", tmp_path / f"out{i}.dds", "--format bc7") for i in range(n)]


def test_nvtt_runs_batch_with_relative_name_from_its_directory(tmp_path, monkeypatch):
    calls = []

    def _fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nvtt.subprocess, "run", _fake_run)
    nvtt.run_batch(Path("nvtt_export.exe"), _nvtt_jobs(tmp_path))

    (args, kwargs), = calls
    assert args[1:] == ["--batch", nvtt.BATCH_FILE_NAME]  # ASCII name, the temp dir may be non-ASCII
    assert Path(kwargs["cwd"]) == tmp_path
    assert (tmp_path / nvtt.BATCH_FILE_NAME).is_file()


@pytest.mark.parametrize("failure", ("timeout", "exit_code"))
def test_nvtt_failed_run_deletes_partial_outputs(tmp_path, monkeypatch, failure):
    jobs = _nvtt_jobs(tmp_path)

    def _fake_run(args, **kwargs):
        for job in jobs:  # partially written outputs with a valid-looking header
            job.output.write_bytes(DDS_PLACEHOLDER)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        return subprocess.CompletedProcess(args, 1, "", "crashed")

    monkeypatch.setattr(nvtt.subprocess, "run", _fake_run)
    with log_capture() as logs:
        nvtt.run_batch(Path("nvtt_export.exe"), jobs)

    assert logs.warnings
    assert not any(job.output.exists() for job in jobs)


# The stub replaces the real nvtt_export.exe (not available on CI): it parses the batch file
# like the real one and copies tests/data/textures/test_image.dds to every output.
NVTT_STUB = data_path("nvtt_export_stub.bat")
NVTT_STUB_OUTPUT = data_path("textures", "test_image.dds")

requires_nvtt_stub = pytest.mark.skipif(sys.platform != "win32", reason="nvtt_export_stub.bat requires cmd.exe")


@pytest.fixture()
def nvtt_stub_log(tmp_path, monkeypatch):
    """Batch lines received by the stub, one per converted texture."""
    log = tmp_path / "nvtt_stub_log.txt"
    monkeypatch.setenv("NVTT_STUB_LOG", str(log))
    monkeypatch.delenv("NVTT_STUB_EXIT_CODE", raising=False)
    return log


def _stub_settings(presets):
    return TextureCompressorSettings(executable=NVTT_STUB, presets_by_game={"gta5": tuple(presets)}, run=nvtt.run_batch)


@requires_nvtt_stub
def test_nvtt_stub_converts_all_textures_in_one_batch(context, tmp_path, nvtt_stub_log):
    textures_dir = tmp_path / "my textures"  # the batch line quotes paths with spaces
    textures_dir.mkdir()
    img1, mat1, node1 = _make_embedded_png(textures_dir, "rock_d", "DiffuseSampler")
    img2, mat2, node2 = _make_embedded_png(textures_dir, "rock_n", "BumpSampler")
    session = TextureConversionSession(_stub_settings((DIFFUSE_RULE, NORMAL_RULE, DEFAULT_RULE)))
    try:
        with log_capture() as logs:
            session.prepare([_prepare_item(node1), _prepare_item(node2)])
            with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
                textures = get_embedded_textures_from_materials([mat1, mat2])
        logs.assert_no_warnings_or_errors()

        lines = nvtt_stub_log.read_text().splitlines()
        assert len(lines) == 2  # a single batch with both textures
        for line, name, rule in zip(lines, ("rock_d", "rock_n"), (DIFFUSE_RULE, NORMAL_RULE)):
            assert line.startswith(f'"{(textures_dir / f"{name}.png").as_posix()}" {rule.args} --output "')
            assert line.endswith('.dds"')
        expected = NVTT_STUB_OUTPUT.read_bytes()
        for name in ("rock_d", "rock_n"):
            with textures[name].data.open() as f:
                assert f.read() == expected
    finally:
        session.cleanup()
        _cleanup(mat1, img1, mat2, img2)


@requires_nvtt_stub
def test_nvtt_stub_crash_discards_outputs(context, tmp_path, nvtt_stub_log, monkeypatch):
    monkeypatch.setenv("NVTT_STUB_EXIT_CODE", "5")
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    session = TextureConversionSession(_stub_settings((DEFAULT_RULE,)))
    try:
        with log_capture() as logs:
            session.prepare([_prepare_item(node)])
        assert any("exit code 5" in w for w in logs.warnings)
        assert len(nvtt_stub_log.read_text().splitlines()) == 1  # the stub did run and write the output
        assert session.get(img, "rock_d") is None
    finally:
        session.cleanup()
        _cleanup(mat, img)


@requires_nvtt_stub
def test_nvtt_stub_ytd_export_operator(context, tmp_path, nvtt_stub_log, preset_config_dir, isolated_prefs):
    from .test_import_export import DEFAULT_EXPORT_SETTINGS

    tc = isolated_prefs.texture_compressor
    tc.compressor = "NVTT"
    tc.nvtt_executable_path = str(NVTT_STUB)

    img, mat, _ = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    txd = context.scene.sz_txds.new_texture_dictionary(name="test_txd_nvtt_stub")
    txd_name = txd.name
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    try:
        txd.new_texture(img)
        with log_capture() as logs:
            res = bpy.ops.sollumz.export_ytd(
                directory=str(out_dir),
                direct_export=True,
                use_custom_settings=True,
                **DEFAULT_EXPORT_SETTINGS | {"target_formats": {"CWXML"}, "target_versions": {"GEN8"}},
            )
        assert res == {"FINISHED"}
        logs.assert_no_warnings_or_errors()

        # Converted with the bundled preset the texture matches, through the real nvtt.run_batch
        expected_args = find_preset(tc.to_settings().presets_for("gta5"), "rock_d", None).args_for(False)
        line, = nvtt_stub_log.read_text().splitlines()
        assert line.startswith(f'"{(tmp_path / "rock_d.png").as_posix()}" {expected_args} --output ')

        assert (out_dir / "test_txd_nvtt_stub.ytd.xml").is_file()
        assert (out_dir / "test_txd_nvtt_stub" / "rock_d.dds").read_bytes() == NVTT_STUB_OUTPUT.read_bytes()
    finally:
        txds = context.scene.sz_txds.texture_dictionaries
        txds.remove(next(i for i, t in enumerate(txds) if t.name == txd_name))
        _cleanup(mat, img)


COMPRESSONATOR_EXE = Path(compressonator.DEFAULT_EXECUTABLE_PATH)
requires_compressonator = pytest.mark.skipif(
    not COMPRESSONATOR_EXE.is_file(), reason="compressonatorcli is not installed at the default path"
)


@pytest.mark.parametrize("game", ("gta5", "rdr2"))
def test_compressonator_bundled_presets_mirror_nvtt(preset_config_dir, game):
    cmp_presets = store.load_presets(compressonator.COMPRESSONATOR_PRESET_CATEGORIES[game])
    nvtt_presets = store.load_presets(nvtt.NVTT_PRESET_CATEGORIES[game])

    def _matching(presets):
        return [
            (p["name"], p["data"]["pattern"], p["data"]["parameters"], bool(p["data"]["args_alpha"])) for p in presets
        ]

    assert _matching(cmp_presets) == _matching(nvtt_presets)


def test_compressonator_invalid_args_fail_job(tmp_path):
    job = CompressJob(tmp_path / "in.png", tmp_path / "out.dds", '-fd "BC1')
    with log_capture() as logs:
        compressonator.run_jobs(tmp_path / "compressonatorcli.exe", [job])
    logs.assert_warning(match="invalid arguments")
    assert not job.output.exists()


def _write_test_png(path, w=64, h=32):
    img = bpy.data.images.new("compressonator_src", w, h, alpha=True)
    try:
        px = np.zeros((h, w, 4), np.float32)
        px[..., 0] = np.linspace(0, 1, w)[None, :]
        px[..., 1] = np.linspace(0, 1, h)[:, None]
        px[..., 2] = 0.25
        px[..., 3] = 1.0
        img.pixels.foreach_set(px.ravel())
        img.filepath_raw = str(path)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)


@requires_compressonator
@pytest.mark.parametrize("args, fourcc, mips", (
    ("-fd BC1 -mipsize 4", b"DXT1", 5),  # 64x32 .. 4x2: compressonatorcli stops on the larger side
    ("-fd BC3 -nomipmap", b"DXT5", 1),
    ("-fd BC5 -mipsize 4", b"ATI2", 5),
    ("-fd BC7 -mipsize 8", b"DX10", 4),
    ("-fd ARGB_8888 -nomipmap", b"\0\0\0\0", 1),
))
def test_compressonator_converts(tmp_path, args, fourcc, mips):
    from szio.dds import DdsFile

    src_dir = tmp_path / "téxtures ä"  # compressonatorcli cannot open non-ASCII paths itself
    src_dir.mkdir()
    out_dir = tmp_path / "out ö"
    out_dir.mkdir()
    _write_test_png(src_dir / "rock ñ.png")
    job = CompressJob(src_dir / "rock ñ.png", out_dir / "rock ñ.dds", args)

    with log_capture() as logs:
        compressonator.run_jobs(COMPRESSONATOR_EXE, [job])
    logs.assert_no_warnings_or_errors()

    raw = job.output.read_bytes()
    assert DdsFile.from_buffer(bytearray(raw)).resolution == (64, 32)
    assert raw[84:88] == fourcc
    assert max(1, struct.unpack_from("<I", raw, 28)[0]) == mips
    assert sorted(p.name for p in out_dir.iterdir()) == ["rock ñ.dds"]  # temporary files cleaned up


@pytest.fixture()
def compressonator_runs(monkeypatch):
    """Command lines of the compressonatorcli runs."""
    runs = []
    real_run = compressonator.subprocess.run

    def counting_run(args, **kwargs):
        runs.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(compressonator.subprocess, "run", counting_run)
    return runs


@requires_compressonator
def test_compressonator_batches_jobs_by_arguments(tmp_path, compressonator_runs):
    runs = compressonator_runs
    jobs = []
    for i, args in enumerate(("-fd BC1 -mipsize 4", "-fd BC3 -nomipmap", "-fd BC1 -mipsize 4", "-fd BC3 -nomipmap")):
        _write_test_png(tmp_path / f"tex {i}.png")
        jobs.append(CompressJob(tmp_path / f"tex {i}.png", tmp_path / f"tex {i}.dds", args))

    with log_capture() as logs:
        compressonator.run_jobs(COMPRESSONATOR_EXE, jobs)
    logs.assert_no_warnings_or_errors()

    assert len(runs) == 2  # one folder mode run per distinct arguments
    assert [job.output.read_bytes()[84:88] for job in jobs] == [b"DXT1", b"DXT5", b"DXT1", b"DXT5"]
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("_cmp_")) == []  # cleaned up


@requires_compressonator
def test_compressonator_without_ascii_dir_converts_one_by_one(tmp_path, compressonator_runs, monkeypatch):
    monkeypatch.setattr(compressonator, "_ascii_dir", lambda path: None)  # e.g. 8.3 names disabled
    out_dir = tmp_path / "out ö"
    out_dir.mkdir()
    jobs = []
    for i in range(3):
        _write_test_png(tmp_path / f"tex ñ {i}.png")
        jobs.append(CompressJob(tmp_path / f"tex ñ {i}.png", out_dir / f"tex ñ {i}.dds", "-fd BC1"))

    with log_capture() as logs:
        compressonator.run_jobs(COMPRESSONATOR_EXE, jobs)
    logs.assert_no_warnings_or_errors()
    assert len(compressonator_runs) == 3
    assert sorted(p.name for p in out_dir.iterdir()) == [f"tex ñ {i}.dds" for i in range(3)]


@requires_compressonator
@pytest.mark.parametrize("bad", (0, 2, 4))
def test_compressonator_unreadable_input_does_not_fail_batch(tmp_path, compressonator_runs, bad):
    # A folder mode run stops at the first file it cannot read, it is rerun without that file.
    jobs = []
    for i in range(5):
        if i == bad:
            (tmp_path / f"{i}.png").write_bytes(b"not a png")
        else:
            _write_test_png(tmp_path / f"{i}.png")
        jobs.append(CompressJob(tmp_path / f"{i}.png", tmp_path / f"{i}.dds", "-fd BC1"))

    with log_capture() as logs:
        compressonator.run_jobs(COMPRESSONATOR_EXE, jobs)
    logs.assert_warning(match=f"'{bad}.png'")
    assert not jobs[bad].output.exists()
    assert all(job.output.is_file() for i, job in enumerate(jobs) if i != bad)
    assert len(compressonator_runs) <= 2


@requires_compressonator
def test_compressonator_missing_input_fails_job(tmp_path):
    jobs = [
        CompressJob(tmp_path / "missing.png", tmp_path / "missing.dds", "-fd BC1"),
        CompressJob(tmp_path / "ok.png", tmp_path / "ok.dds", "-fd BC1"),
    ]
    _write_test_png(tmp_path / "ok.png")
    with log_capture() as logs:
        compressonator.run_jobs(COMPRESSONATOR_EXE, jobs)
    logs.assert_warning(match="missing.png")
    assert not jobs[0].output.exists()
    assert jobs[1].output.is_file()  # one failed texture does not affect the others


def test_to_settings_compressonator_snapshot(preset_config_dir, isolated_prefs):
    tc = isolated_prefs.texture_compressor
    tc.compressor = "COMPRESSONATOR"
    settings = tc.to_settings()
    assert settings.run is compressonator.run_jobs
    assert settings.executable == COMPRESSONATOR_EXE
    assert [r.name for r in settings.presets_for("gta5")] == BUNDLED_PRESET_NAMES


def _payload_run(captured):
    """Fake compressor run whose outputs identify the input and arguments they were converted from."""
    def run(executable, jobs):
        captured.append(list(jobs))
        for job in jobs:
            job.output.write_bytes(DDS_HEADER_PLACEHOLDER + f"{job.input.name}|{job.args}".encode())

    return run


def _cache_settings(presets, run, cache_dir, salt="compressor-v1", size_limit=4 * 1024**3):
    return TextureCompressorSettings(
        executable=Path(sys.executable), presets_by_game={"gta5": tuple(presets)}, run=run,
        cache_dir=cache_dir, cache_salt=salt, cache_size_limit=size_limit,
    )


def _export_once(settings, mats, items):
    """One export: prepare + read the textures + cleanup. Returns the DDS bytes by texture name."""
    session = TextureConversionSession(settings)
    try:
        session.prepare(items)
        with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
            textures = get_embedded_textures_from_materials(mats)
        out = {}
        for name, tex in textures.items():
            if tex.data is not None:
                with tex.data.open() as f:
                    out[name] = f.read()
        return out
    finally:
        session.cleanup()


def test_cache_second_export_reuses_conversions(context, tmp_path):
    img1, mat1, node1 = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    img2, mat2, node2 = _make_embedded_png(tmp_path, "rock_n", "BumpSampler")
    captured = []
    settings = _cache_settings((DIFFUSE_RULE, NORMAL_RULE), _payload_run(captured), tmp_path / "cache")
    items = [_prepare_item(node1), _prepare_item(node2)]
    try:
        first = _export_once(settings, [mat1, mat2], items)
        assert len(captured) == 1 and len(captured[0]) == 2

        with log_capture() as logs:
            second = _export_once(settings, [mat1, mat2], items)
        assert len(captured) == 1  # nothing converted
        assert second == first
        assert any("2 reused from cache, 0 converted" in m for m in logs.infos)
        assert (tmp_path / "cache" / cache_mod.MARKER_NAME).is_file()
    finally:
        _cleanup(mat1, img1, mat2, img2)


@pytest.mark.parametrize("change", ("pixels", "args", "compressor"))
def test_cache_misses_when_output_would_change(context, tmp_path, change):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    rule = DIFFUSE_RULE
    settings = _cache_settings((rule,), _payload_run(captured), tmp_path / "cache")
    try:
        _export_once(settings, [mat], [_prepare_item(node)])

        if change == "pixels":
            img.pixels = [0.1, 0.9, 0.2, 1.0] * (8 * 8)
            img.save()
        elif change == "args":
            rule = _rule("Diffuse", parameters=("DiffuseSampler",), args=DIFFUSE_RULE.args + " --min-mip-size 4")
            settings = _cache_settings((rule,), _payload_run(captured), tmp_path / "cache")
        else:
            settings = _cache_settings((rule,), _payload_run(captured), tmp_path / "cache", salt="compressor-v2")
        _export_once(settings, [mat], [_prepare_item(node)])
        assert len(captured) == 2

        _export_once(settings, [mat], [_prepare_item(node)])
        assert len(captured) == 2  # the new conversion is cached too
    finally:
        _cleanup(mat, img)


def test_cache_shared_between_identical_images(context, tmp_path):
    # The key is the content, not the path: a copy of the same file elsewhere reuses the conversion.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    img1, mat1, node1 = _make_embedded_png(tmp_path / "a", "rock_d", "DiffuseSampler")
    img2, mat2, node2 = _make_embedded_png(tmp_path / "b", "rock_d", "DiffuseSampler")
    captured = []
    settings = _cache_settings((DIFFUSE_RULE,), _payload_run(captured), tmp_path / "cache")
    try:
        _export_once(settings, [mat1], [_prepare_item(node1)])
        _export_once(settings, [mat2], [_prepare_item(node2)])
        assert len(captured) == 1
    finally:
        _cleanup(mat1, img1, mat2, img2)


@pytest.mark.parametrize("kind", ("packed", "generated"))
def test_cache_hit_writes_no_temp_files(context, tmp_path, kind):
    if kind == "packed":
        img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
        img.pack()
    else:
        img, mat, node = _make_generated_node("rock_d", "DiffuseSampler")
    captured = []
    settings = _cache_settings((DEFAULT_RULE,), _payload_run(captured), tmp_path / "cache")
    try:
        _export_once(settings, [mat], [_prepare_item(node)])
        session = TextureConversionSession(settings)
        try:
            session.prepare([_prepare_item(node)])
            assert session._temp_dir is None  # neither the packed data nor the generated pixels were written
            assert session.get(img, "rock_d").is_relative_to(tmp_path / "cache")
        finally:
            session.cleanup()
        assert len(captured) == 1
    finally:
        _cleanup(mat, img)


def test_cache_refuses_non_empty_unmarked_directory(context, tmp_path):
    cache_dir = tmp_path / "project"
    cache_dir.mkdir()
    (cache_dir / "notes.txt").write_text("not a cache")
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    settings = _cache_settings((DEFAULT_RULE,), _payload_run(captured), cache_dir)
    try:
        with log_capture() as logs:
            result = _export_once(settings, [mat], [_prepare_item(node)])
        logs.assert_warning(match="not a texture cache")
        assert result["rock_d"].startswith(b"DDS ")  # still converted
        assert sorted(p.name for p in cache_dir.iterdir()) == ["notes.txt"]
    finally:
        _cleanup(mat, img)


def test_cache_write_failure_still_exports(context, tmp_path, monkeypatch):
    def failing_put(self, key, source, move=False):
        raise OSError("disk full")

    monkeypatch.setattr(cache_mod.TextureCache, "put", failing_put)
    img1, mat1, node1 = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    img2, mat2, node2 = _make_embedded_png(tmp_path, "rock_s", "SpecSampler")
    captured = []
    settings = _cache_settings((DEFAULT_RULE,), _payload_run(captured), tmp_path / "cache")
    try:
        with log_capture() as logs:
            result = _export_once(settings, [mat1, mat2], [_prepare_item(node1), _prepare_item(node2)])
        logs.assert_warning(match="disk full", num=1)  # once, then the cache is off for this export
        assert set(result) == {"rock_d", "rock_s"}
    finally:
        _cleanup(mat1, img1, mat2, img2)


def test_cache_corrupt_entry_is_reconverted(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    settings = _cache_settings((DEFAULT_RULE,), _payload_run(captured), tmp_path / "cache")
    try:
        first = _export_once(settings, [mat], [_prepare_item(node)])
        entry, = (tmp_path / "cache").glob("??/*.dds")
        entry.write_bytes(b"DDS ")  # truncated

        assert _export_once(settings, [mat], [_prepare_item(node)]) == first
        assert len(captured) == 2
        assert entry.read_bytes() == first["rock_d"]
    finally:
        _cleanup(mat, img)


def test_cache_prune(tmp_path):
    import os
    import time

    cache = cache_mod.TextureCache.open(tmp_path / "cache", "salt")
    src = tmp_path / "src.dds"
    src.write_bytes(DDS_HEADER_PLACEHOLDER + b"\0" * 872)  # 1000 bytes
    now = time.time()
    keys = [cache.key(f"--args {i}", "digest") for i in range(5)]
    for age, key in zip((50, 40, 30, 20, 10), keys):  # keys[0] least recently used
        os.utime(cache.put(key, src), (now - age, now - age))
    stale_tmp = cache.entry_path(keys[0]).with_name(f"{keys[0]}.dds.abc123.tmp")
    stale_tmp.write_bytes(b"partial")
    os.utime(stale_tmp, (now - 2 * cache_mod._TEMP_FILE_MAX_AGE_SECONDS,) * 2)
    foreign = tmp_path / "cache" / "user_file.dds"
    foreign.write_bytes(b"keep")

    cache.prune(size_limit=4000)  # 5000 bytes cached: down to 80% (3200), oldest first

    assert [cache.get(k) is not None for k in keys] == [False, False, True, True, True]
    assert not stale_tmp.exists()
    assert foreign.read_bytes() == b"keep"

    (tmp_path / "cache" / cache_mod.MARKER_NAME).unlink()
    cache.prune(size_limit=0)  # unmarked: untouched
    assert all(cache.get(k) is not None for k in keys[2:])


def test_cache_pruned_to_settings_size_limit(context, tmp_path):
    img, mat, node = _make_embedded_png(tmp_path, "rock_d", "DiffuseSampler")
    captured = []
    settings = _cache_settings((DEFAULT_RULE,), _payload_run(captured), tmp_path / "cache", size_limit=1)
    try:
        _export_once(settings, [mat], [_prepare_item(node)])
        assert not list((tmp_path / "cache").glob("??/*.dds"))  # the entry is over the 1 byte limit

        settings = _cache_settings((DEFAULT_RULE,), _payload_run(captured), tmp_path / "cache")
        _export_once(settings, [mat], [_prepare_item(node)])
        assert len(list((tmp_path / "cache").glob("??/*.dds"))) == 1
        assert len(captured) == 2
    finally:
        _cleanup(mat, img)


def test_cache_directory_path(isolated_prefs, tmp_path):
    tc = isolated_prefs.texture_compressor
    assert tc.cache_directory == "//.sollumz_texture_cache/"
    assert bpy.data.filepath == ""
    assert tc.cache_directory_path is None  # '//' while unsaved
    tc.cache_directory = ""
    assert tc.cache_directory_path is None  # disabled
    tc.cache_directory = str(tmp_path / "cache")
    assert tc.cache_directory_path == tmp_path / "cache"


def _make_generated_node(name, parameter_name, painted=(1.0, 0.0, 0.0, 1.0)):
    img = bpy.data.images.new(name, 4, 4, alpha=True)
    pixels = list(img.pixels)
    pixels[0:4] = painted  # painted on after generation, so it is not part of the generated pattern
    img.pixels = pixels

    mat = bpy.data.materials.new(f"{name}_mat")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = parameter_name
    node.image = img
    node.texture_properties.embedded = True
    return img, mat, node


def _recording_run(inputs):
    """Fake compressor run that records a copy of each input file's pixels before the session's
    temp dir is cleaned up."""
    def run(executable, jobs):
        for job in jobs:
            chk = bpy.data.images.load(str(job.input))
            inputs.append((job.input.suffix, tuple(chk.size), [round(v, 3) for v in chk.pixels[0:4]]))
            bpy.data.images.remove(chk)
            job.output.write_bytes(DDS_PLACEHOLDER)

    return run


@pytest.mark.parametrize("image_name, expected", (
    ("Rock_D", "rock_d"),
    ("rock_d.png", "rock_d"),
    ("rock_d.DDS", "rock_d"),
    ("metal.001", "metal.001"),  # not an image extension, so it is part of the name
))
def test_texture_name_of_image_without_filepath_uses_image_name(image_name, expected):
    img = bpy.data.images.new(image_name, 4, 4)
    try:
        assert img.filepath == ""
        assert get_texture_name(img) == expected
    finally:
        bpy.data.images.remove(img)


def test_texture_name_prefers_filepath():
    img = bpy.data.images.new("Some Name", 4, 4)
    try:
        img.filepath = "//textures/Rock_N.png"
        assert get_texture_name(img) == "rock_n"
    finally:
        bpy.data.images.remove(img)


def test_generated_image_converted_on_export(context, tmp_path):
    img, mat, node = _make_generated_node("rock_d", "DiffuseSampler")
    inputs = []
    session = TextureConversionSession(_settings((DIFFUSE_RULE, DEFAULT_RULE), _recording_run(inputs)))
    try:
        with log_capture() as logs:
            session.prepare([_prepare_item(node)])
            with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
                textures = get_embedded_textures_from_materials([mat])
        logs.assert_no_warnings_or_errors()

        assert inputs == [(".png", (4, 4), [1.0, 0.0, 0.0, 1.0])]  # painted pixels written out
        texture = textures["rock_d"]
        assert (texture.width, texture.height) == (4, 4)
        with texture.data.open() as f:
            assert f.read(4) == b"DDS "

        # The original image is left untouched and no temporary image is left behind
        assert img.source == "GENERATED"
        assert not img.packed_file
        assert img.filepath == ""
        assert list(img.pixels[0:4]) == [1.0, 0.0, 0.0, 1.0]
        assert not any(i.name.startswith("__sollumz_texconv_tmp") for i in bpy.data.images)
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_generated_image_uses_matching_preset(context, tmp_path):
    img, mat, node = _make_generated_node("rock_n", "BumpSampler")
    captured = []
    session = TextureConversionSession(_settings((DIFFUSE_RULE, NORMAL_RULE), _fake_run(captured)))
    try:
        session.prepare([_prepare_item(node)])
        assert [job.args for job in captured[0]] == [NORMAL_RULE.args]
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_packed_generated_image_converted(context, tmp_path):
    img, mat, node = _make_generated_node("rock_d", "DiffuseSampler")
    img.pack()  # packed without ever being saved: still no file path
    inputs = []
    session = TextureConversionSession(_settings((DEFAULT_RULE,), _recording_run(inputs)))
    try:
        assert img.filepath == ""
        session.prepare([_prepare_item(node)])
        with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
            textures = get_embedded_textures_from_materials([mat])

        assert inputs == [(".png", (4, 4), [1.0, 0.0, 0.0, 1.0])]
        with textures["rock_d"].data.open() as f:
            assert f.read(4) == b"DDS "
    finally:
        session.cleanup()
        _cleanup(mat, img)


def test_generated_image_without_compressor_warns_not_dds(context, tmp_path):
    img, mat, _ = _make_generated_node("rock_d", "DiffuseSampler")
    try:
        with log_capture() as logs:
            with export_context_scope(ExportContext("test", ExportSettings(targets=()))):
                textures = get_embedded_textures_from_materials([mat])
        assert textures["rock_d"].data is None
        logs.assert_warning(match="not in DDS format", num=1)  # not "file does not exist"
    finally:
        _cleanup(mat, img)


def test_generated_image_in_txd_converted(context, tmp_path):
    img, mat, _ = _make_generated_node("rock_d", "DiffuseSampler")
    txd = context.scene.sz_txds.new_texture_dictionary(name="test_txd_generated")
    txd_name = txd.name
    inputs = []
    session = TextureConversionSession(_settings((DEFAULT_RULE,), _recording_run(inputs)))
    try:
        txd.new_texture(img)
        assert txd.textures[0].name == "rock_d"

        session.prepare([(img, "rock_d", None, img.sz_compressor_preset)])
        with export_context_scope(ExportContext("test", ExportSettings(targets=()), textures=session)):
            asset, hi_asset = create_txd_asset(txd)

        assert hi_asset is None
        assert inputs == [(".png", (4, 4), [1.0, 0.0, 0.0, 1.0])]
        with asset.textures["rock_d"].data.open() as f:
            assert f.read(4) == b"DDS "
    finally:
        session.cleanup()
        txds = context.scene.sz_txds.texture_dictionaries
        txds.remove(next(i for i, t in enumerate(txds) if t.name == txd_name))
        _cleanup(mat, img)
