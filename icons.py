import bpy
import bpy.utils.previews
from pathlib import Path
from .sollumz_preferences import get_addon_preferences


ICON_DIR = Path(__file__).parent / "icons"
ICON_EXT = ".png"


class IconManager:
    def __init__(self, icon_dir: Path):
        self._icon_dir = icon_dir
        self._icons = self._get_custom_icons(icon_dir)
        self._icons_preview_collection = None

    def _get_custom_icons(self, path: Path) -> set[str]:
        """Retrieve all .png files from a directory."""
        return {icon.stem for icon in path.glob(f"*{ICON_EXT}")}

    @property
    def is_registered(self) -> bool:
        return self._icons_preview_collection is not None

    def get_icon(self, name: str) -> int:
        """Gets a specific icon ID by name. Returns 0 (no icon) if not found or the icons are not registered."""
        if name not in self._icons or not self.is_registered:
            return 0

        return self._icons_preview_collection[name].icon_id

    def icon_label(self, name: str, panel: bpy.types.Panel):
        """Render an icon label as text layout."""
        if name not in self._icons:
            return

        if name != "sollumz_icon":
            panel.layout.label(text="", icon_value=self.get_icon(name))
        else:
            preferences = get_addon_preferences(bpy.context)
            if preferences.sollumz_icon_header:
                panel.layout.label(text="", icon_value=self.get_icon(name))

    def register(self):
        self._icons_preview_collection = pcoll = bpy.utils.previews.new()
        for icon_name in self._icons:
            icon_path = self._icon_dir / f"{icon_name}{ICON_EXT}"
            pcoll.load(icon_name, str(icon_path), "IMAGE")

    def unregister(self):
        bpy.utils.previews.remove(self._icons_preview_collection)
        self._icons_preview_collection = None


# Global icon manager instance
icon_manager = IconManager(ICON_DIR)


def icon(name: str) -> int:
    return icon_manager.get_icon(name)


ICON_GEOM_TOOL = str(ICON_DIR / "sollumz.tool")
ICON_GEOM_GRADIENT = str(ICON_DIR / "sollumz.gradient")


def pre_register_classes(_classes):
    # Before the classes, so the icons are available to code that runs while registering them,
    # like `EnumProperty` items callbacks
    icon_manager.register()


def post_unregister_classes():
    icon_manager.unregister()
