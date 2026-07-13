from bpy.types import UILayout

from ..presets import store
from .operators import (
    SOLLUMZ_OT_texture_compressor_preset_edit,
    SOLLUMZ_OT_texture_compressor_preset_move,
    SOLLUMZ_OT_texture_compressor_preset_remove,
    SOLLUMZ_OT_texture_compressor_preset_restore_defaults,
)


def _match_summary(pattern: str, parameters: str) -> str:
    """Compact one-line summary of a preset's matching rules. Long texture parameter lists are collapsed to a
    count, the full list is available in the edit button tooltip and dialog."""
    parameter_names = [s for s in (part.strip() for part in parameters.split(",")) if s]
    parts = []
    if pattern:
        parts.append(pattern)
    if len(parameter_names) == 1:
        parts.append(parameter_names[0])
    elif parameter_names:
        parts.append(f"{len(parameter_names)} parameters")
    return " · ".join(parts) if parts else "(any texture)"


def draw_texture_compressor_prefs(layout: UILayout, tc_prefs):
    """Draws the texture compression preferences: compressor selection and its presets editor."""
    layout.prop(tc_prefs, "compressor")

    backend = tc_prefs.active_backend
    if backend is None:
        return

    layout.prop(tc_prefs, backend.executable_prop)
    if not tc_prefs.executable_path.is_file():
        row = layout.row()
        row.alert = True
        row.label(text="Executable not found. Non-DDS textures will be skipped on export.", icon="ERROR")
        if backend.install_hint or backend.download_url:
            col = layout.column(align=True)
            for line in backend.install_hint:
                col.label(text=line, icon="BLANK1")
            if backend.download_url:
                row = layout.row()
                row.operator("wm.url_open", text=f"Download {backend.label}", icon="URL").url = backend.download_url

    categories = backend.preset_categories
    if not categories:
        return
    # game = tc_prefs.presets_game if tc_prefs.presets_game in categories else next(iter(categories))
    game = "gta5"
    _draw_presets_box(layout, tc_prefs, game, categories[game], show_game_selector=len(categories) > 1)


def _draw_presets_box(layout: UILayout, tc_prefs, game: str, category, show_game_selector: bool):
    box = layout.box()
    header = box.row()
    title = header.row(align=True)
    title.alignment = "LEFT"
    title.label(text="Presets", icon="PRESET")
    if False and show_game_selector:
        game_selector = title.row()
        game_selector.use_property_split = False
        game_selector.ui_units_x = 6
        game_selector.prop(tc_prefs, "presets_game", expand=True)
    hint = header.row()
    hint.alignment = "RIGHT"
    hint.active = False
    hint.label(text="Matched top to bottom.")

    presets = store.load_presets(category)
    if presets:
        col = box.column(align=True)
        for i, preset in enumerate(presets):
            name = preset.get("name", "")
            data = preset.get("data") or {}
            pattern = str(data.get("pattern", "")).strip()
            parameters = str(data.get("parameters", "")).strip()
            args = str(data.get("args", "")).strip()
            has_args_alpha = bool(str(data.get("args_alpha", "")).strip())

            row = col.row()
            split = row.split(factor=0.25)
            split.label(text=name, icon="TEXTURE")
            split = split.split(factor=0.4)
            match = split.row()
            match.active = False
            match.label(text=_match_summary(pattern, parameters))
            rest = split.row()
            args_row = rest.row()
            args_row.active = bool(args)
            args_row.label(text=args or "(no arguments)", icon="IMAGE_RGB_ALPHA" if has_args_alpha else "NONE")

            controls = rest.row(align=True)
            op = controls.operator(SOLLUMZ_OT_texture_compressor_preset_edit.bl_idname, text="", icon="GREASEPENCIL")
            op.game = game
            op.original_name = name
            up = controls.row(align=True)
            up.enabled = i > 0
            op = up.operator(SOLLUMZ_OT_texture_compressor_preset_move.bl_idname, text="", icon="TRIA_UP")
            op.game = game
            op.preset_name = name
            op.direction = "UP"
            down = controls.row(align=True)
            down.enabled = i < len(presets) - 1
            op = down.operator(SOLLUMZ_OT_texture_compressor_preset_move.bl_idname, text="", icon="TRIA_DOWN")
            op.game = game
            op.preset_name = name
            op.direction = "DOWN"
            op = controls.operator(SOLLUMZ_OT_texture_compressor_preset_remove.bl_idname, text="", icon="X")
            op.game = game
            op.preset_name = name
    else:
        row = box.row()
        row.active = False
        row.label(text="No presets. Non-DDS textures will be skipped with a warning.")

    footer = box.row()
    op = footer.operator(SOLLUMZ_OT_texture_compressor_preset_edit.bl_idname, text="Add Preset", icon="ADD")
    op.game = game
    op = footer.operator(
        SOLLUMZ_OT_texture_compressor_preset_restore_defaults.bl_idname, text="Restore Defaults", icon="LOOP_BACK"
    )
    op.game = game
