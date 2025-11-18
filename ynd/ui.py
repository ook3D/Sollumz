import bpy
from ..sollumz_properties import SollumType
from ..sollumz_ui import SOLLUMZ_PT_OBJECT_PANEL


def draw_ynd_file_properties(self, context):
    obj = context.active_object
    if obj and obj.sollum_type == SollumType.YND:
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        layout.label(text="Node Counts")
        layout.prop(obj.ynd_file_properties, "vehicle_node_count")
        layout.prop(obj.ynd_file_properties, "ped_node_count")


def draw_ynd_node_properties(self, context):
    obj = context.active_object
    if obj and obj.sollum_type == SollumType.YND_NODE:
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        props = obj.ynd_node_properties
        
        # Basic identification
        layout.label(text="Node Identification")
        layout.prop(props, "area_id")
        layout.prop(props, "node_id")
        layout.prop(props, "street_name")
        
        layout.separator()
        
        # Decoded flags, organized by category
        layout.label(text="Node Type")
        layout.prop(props, "special_type")
        layout.prop(props, "speed")
        
        layout.separator()
        
        layout.label(text="Road Properties")
        row = layout.row()
        row.prop(props, "off_road", toggle=True)
        row.prop(props, "highway", toggle=True)
        
        row = layout.row()
        row.prop(props, "is_junction", toggle=True)
        row.prop(props, "tunnel", toggle=True)
        
        row = layout.row()
        row.prop(props, "slip_road", toggle=True)
        row.prop(props, "no_gps", toggle=True)
        
        layout.separator()
        
        layout.label(text="Vehicle Restrictions")
        row = layout.row()
        row.prop(props, "no_big_vehicles", toggle=True)
        row.prop(props, "cannot_go_left", toggle=True)
        row = layout.row()
        row.prop(props, "left_turns_only", toggle=True)
        
        layout.separator()
        
        layout.label(text="Navigation Hints")
        row = layout.row()
        row.prop(props, "indicate_keep_left", toggle=True)
        row.prop(props, "indicate_keep_right", toggle=True)
        
        layout.separator()
        
        layout.label(text="Status")
        row = layout.row()
        row.prop(props, "is_disabled_unk0", toggle=True)
        row.prop(props, "is_disabled_unk1", toggle=True)
        
        layout.separator()
        
        layout.label(text="Pathfinding Data")
        layout.prop(props, "heuristic_value")
        layout.prop(props, "density")
        layout.prop(props, "dead_endness")
        layout.prop(props, "link_count")
        
        layout.separator()
        
        # Raw flags
        box = layout.box()
        box.label(text="Raw Flags (Advanced)")
        col = box.column(align=True)
        col.prop(props, "flags0")
        col.prop(props, "flags1")
        col.prop(props, "flags2")
        col.prop(props, "flags3")
        col.prop(props, "flags4")
        col.prop(props, "flags5")


def draw_ynd_link_properties(self, context):
    obj = context.active_object
    if obj and obj.sollum_type == SollumType.YND_LINK:
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        props = obj.ynd_link_properties
        
        # Target node
        layout.label(text="Target Node")
        layout.prop(props, "to_area_id")
        layout.prop(props, "to_node_id")
        
        layout.separator()
        
        # Link properties
        layout.label(text="Link Properties")
        layout.prop(props, "link_length")
        
        layout.separator()
        
        # Lane information
        layout.label(text="Lane Configuration")
        layout.prop(props, "lane_count_forward")
        layout.prop(props, "lane_count_backward")
        
        layout.separator()
        
        # Raw flags
        box = layout.box()
        box.label(text="Raw Flags (Advanced)")
        col = box.column(align=True)
        col.prop(props, "flags0")
        col.prop(props, "flags1")
        col.prop(props, "flags2")


SOLLUMZ_PT_OBJECT_PANEL.append(draw_ynd_file_properties)
SOLLUMZ_PT_OBJECT_PANEL.append(draw_ynd_node_properties)
SOLLUMZ_PT_OBJECT_PANEL.append(draw_ynd_link_properties)
