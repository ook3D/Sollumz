from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..presets import store
from . import DEFAULT_GAME


def _active_backend(context):
    from ...sollumz_preferences import get_addon_preferences

    return get_addon_preferences(context).texture_compressor.active_backend


def _preset_category(context, game: str):
    """The active compressor's preset category for `game`, or `None`."""
    backend = _active_backend(context)
    return backend.preset_categories.get(game) if backend is not None else None


class TextureCompressorPresetOperatorBase:
    """Mixin for operators editing the active compressor's presets of one game."""

    game: StringProperty(default=DEFAULT_GAME, options={"HIDDEN", "SKIP_SAVE"})
    """Game id of the presets to edit."""

    @classmethod
    def poll(cls, context):
        return _active_backend(context) is not None

    def category(self, context):
        """The preset category being edited. Reports an error and returns `None` if the active compressor has no
        presets for `game`."""
        category = _preset_category(context, self.game)
        if category is None:
            self.report({"ERROR"}, f"The texture compressor has no presets for game '{self.game}'.")
        return category


class SOLLUMZ_OT_texture_compressor_preset_edit(TextureCompressorPresetOperatorBase, Operator):
    bl_idname = "sollumz.texture_compressor_preset_edit"
    bl_label = "Compression Preset"
    bl_options = {"REGISTER", "INTERNAL"}
    bl_description = "Add or edit a texture compression preset"

    original_name: StringProperty(
        name="Original Name",
        description="Name of the preset being edited. Empty when adding a new preset",
        options={"HIDDEN", "SKIP_SAVE"},
    )

    name: StringProperty(
        name="Name",
        description="Preset name",
        options={"SKIP_SAVE"},
    )
    pattern: StringProperty(
        name="Name Pattern",
        description=(
            "Comma-separated glob patterns matched against the texture name, e.g. '*_n, *_nrm'. Leave empty to not "
            "match by name. If the preset also has texture parameters, the name is only used for textures whose "
            "texture parameter is not listed in any preset (e.g. textures in a texture dictionary)"
        ),
        options={"SKIP_SAVE"},
    )
    parameters: StringProperty(
        name="Texture Parameters",
        description="Comma-separated shader texture parameter names, e.g. 'BumpSampler'. Leave empty to not match by "
        "texture parameter",
        options={"SKIP_SAVE"},
    )
    args: StringProperty(
        name="Arguments",
        description="Command-line arguments for the compressor. The input and output files are added automatically",
        options={"SKIP_SAVE"},
    )
    args_alpha: StringProperty(
        name="Arguments (Alpha)",
        description=(
            "Command-line arguments used instead for textures with transparent pixels, e.g. to pick a format with an "
            "alpha channel. Leave empty to always use the arguments above"
        ),
        options={"SKIP_SAVE"},
    )

    @classmethod
    def description(cls, context, properties):
        category = _preset_category(context, properties.game)
        preset = None
        if category is not None and properties.original_name:
            preset = store.find_preset(category, properties.original_name)
        if preset is None:
            return cls.bl_description

        data = preset.get("data") or {}
        pattern = str(data.get("pattern", "")).strip()
        parameters = str(data.get("parameters", "")).strip()
        matches = (
            " or ".join(
                s
                for s in (
                    f"name '{pattern}'" if pattern else "",
                    f"texture parameters {parameters}" if parameters else "",
                )
                if s
            )
            or "any texture"
        )
        args = str(data.get("args", "")).strip() or "(none)"
        description = f"Edit this preset.\nMatches: {matches}\nArguments: {args}"
        if args_alpha := str(data.get("args_alpha", "")).strip():
            description += f"\nArguments (Alpha): {args_alpha}"
        return description

    def invoke(self, context, event):
        category = self.category(context)
        if category is None:
            return {"CANCELLED"}
        if self.original_name:
            preset = store.find_preset(category, self.original_name)
            if preset is not None:
                data = preset.get("data") or {}
                self.name = preset.get("name", "")
                self.pattern = data.get("pattern", "")
                self.parameters = data.get("parameters", "")
                self.args = data.get("args", "")
                self.args_alpha = data.get("args_alpha", "")
        return context.window_manager.invoke_props_dialog(self, width=800)

    def execute(self, context):
        category = self.category(context)
        if category is None:
            return {"CANCELLED"}
        name = self.name.strip()
        if not name:
            self.report({"ERROR"}, "Preset name cannot be empty.")
            return {"CANCELLED"}

        presets = list(store.load_presets(category))
        if any(p.get("name") == name for p in presets if p.get("name") != self.original_name):
            self.report({"ERROR"}, f"A preset named '{name}' already exists.")
            return {"CANCELLED"}

        entry = {
            "name": name,
            "data": {
                "pattern": self.pattern,
                "parameters": self.parameters,
                "args": self.args,
                "args_alpha": self.args_alpha,
            },
        }
        index = None
        if self.original_name:
            index = next((i for i, p in enumerate(presets) if p.get("name") == self.original_name), None)
        if index is not None:
            presets[index] = entry  # edited in place: the list order is the matching priority
        else:
            presets.append(entry)
        store.save_presets(category, presets)
        return {"FINISHED"}


class SOLLUMZ_OT_texture_compressor_preset_remove(TextureCompressorPresetOperatorBase, Operator):
    """Remove this texture compression preset"""

    bl_idname = "sollumz.texture_compressor_preset_remove"
    bl_label = "Remove Preset"
    bl_options = {"INTERNAL"}

    preset_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        category = self.category(context)
        if category is None or not store.delete_preset(category, self.preset_name):
            return {"CANCELLED"}
        return {"FINISHED"}


class SOLLUMZ_OT_texture_compressor_preset_move(TextureCompressorPresetOperatorBase, Operator):
    """Move this preset up or down. Earlier presets are matched first"""

    bl_idname = "sollumz.texture_compressor_preset_move"
    bl_label = "Move Preset"
    bl_options = {"INTERNAL"}

    preset_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    direction: EnumProperty(
        items=(
            ("UP", "Up", "Move the preset up", 0),
            ("DOWN", "Down", "Move the preset down", 1),
        ),
        options={"HIDDEN", "SKIP_SAVE"},
    )

    def execute(self, context):
        category = self.category(context)
        if category is None:
            return {"CANCELLED"}
        presets = list(store.load_presets(category))
        src = next((i for i, p in enumerate(presets) if p.get("name") == self.preset_name), None)
        if src is None:
            return {"CANCELLED"}
        dst = src - 1 if self.direction == "UP" else src + 1
        if dst < 0 or dst >= len(presets):
            return {"CANCELLED"}
        presets[src], presets[dst] = presets[dst], presets[src]
        store.save_presets(category, presets)
        return {"FINISHED"}


class SOLLUMZ_OT_texture_compressor_preset_restore_defaults(TextureCompressorPresetOperatorBase, Operator):
    """Replace all presets with the bundled defaults. This cannot be undone"""

    bl_idname = "sollumz.texture_compressor_preset_restore_defaults"
    bl_label = "Restore Default Presets"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        category = self.category(context)
        if category is None:
            return {"CANCELLED"}
        # Without a user presets file, the store falls back to the bundled defaults.
        store.user_preset_path(category).unlink(missing_ok=True)
        store.invalidate(category)
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)
