import os
import bpy
from bpy_extras.io_utils import ImportHelper
from ..cwxml.nodepath import YND
from .yndimport import import_ynd
from .. import logger


class SOLLUMZ_OT_import_ynd(bpy.types.Operator, ImportHelper):
    bl_idname = "sollumz.import_ynd"
    bl_label = "Import YND"
    bl_options = {"UNDO", "PRESET"}
    bl_description = "Import a Node Dictionary XML file (.ynd.xml)"
    
    filename_ext = YND.file_extension
    
    filter_glob: bpy.props.StringProperty(
        default=f"*{YND.file_extension}",
        options={"HIDDEN"},
        maxlen=255
    )
    
    def execute(self, context):
        try:
            with logger.use_operator_logger(self):
                import_ynd(self.filepath)
                logger.info(f"Successfully imported '{os.path.basename(self.filepath)}'")
            return {"FINISHED"}
        except Exception as e:
            logger.error(f"Import failed: {str(e)}")
            self.report({"ERROR"}, f"Import failed: {str(e)}")
            return {"CANCELLED"}
