import bpy
from bpy.props import (
    IntProperty,
    StringProperty,
    BoolProperty,
    EnumProperty,
    PointerProperty
)


# Enum for YndNodeSpecialType
special_type_items = [
    ("0", "None", "Normal node"),
    ("2", "Parking Space", "Parking space node"),
    ("10", "Ped Road Crossing", "Pedestrian crossing where vehicles have priority"),
    ("14", "Ped Node Assisted Movement", "Pedestrian node with assisted movement"),
    ("15", "Traffic Light Junction Stop", "Traffic light stop node"),
    ("16", "Stop Sign", "Stop junction node"),
    ("17", "Caution", "Caution node (slow down)"),
    ("18", "Ped Road Crossing No Wait", "Pedestrian crossing with priority"),
    ("19", "Emergency Vehicles Only", "Emergency vehicles only"),
    ("20", "Off Road Junction", "Off-road junction node"),
]


# Enum for YndNodeSpeed
speed_items = [
    ("0", "Slow", "Slow speed"),
    ("1", "Normal", "Normal speed"),
    ("2", "Fast", "Fast speed"),
    ("3", "Faster", "Faster speed"),
]


class YndNodeProperties(bpy.types.PropertyGroup):
    area_id: IntProperty(
        name="Area ID",
        description="Area ID of the node",
        default=0,
        min=0
    )
    
    node_id: IntProperty(
        name="Node ID",
        description="Unique node ID within the area",
        default=0,
        min=0
    )
    
    street_name: StringProperty(
        name="Street Name",
        description="Street name hash or text",
        default=""
    )
    
    # Raw flag values
    flags0: IntProperty(
        name="Flags 0",
        description="Raw flags byte 0",
        default=0,
        min=0,
        max=255
    )
    
    flags1: IntProperty(
        name="Flags 1",
        description="Raw flags byte 1",
        default=0,
        min=0,
        max=255
    )
    
    flags2: IntProperty(
        name="Flags 2",
        description="Raw flags byte 2",
        default=0,
        min=0,
        max=255
    )
    
    flags3: IntProperty(
        name="Flags 3",
        description="Raw flags byte 3",
        default=0,
        min=0,
        max=255
    )
    
    flags4: IntProperty(
        name="Flags 4",
        description="Raw flags byte 4",
        default=0,
        min=0,
        max=255
    )
    
    flags5: IntProperty(
        name="Flags 5",
        description="Raw flags byte 5 (link count flags)",
        default=0,
        min=0,
        max=255
    )
    
    # Decoded flag properties from Flags0
    off_road: BoolProperty(
        name="Off Road",
        description="Node is off-road (Flags0 bit 3)",
        default=False
    )
    
    no_big_vehicles: BoolProperty(
        name="No Big Vehicles",
        description="No big vehicles allowed (Flags0 bit 5)",
        default=False
    )
    
    cannot_go_left: BoolProperty(
        name="Cannot Go Left",
        description="Cannot turn left from this node (Flags0 bit 7)",
        default=False
    )
    
    # Decoded flag properties from Flags1
    slip_road: BoolProperty(
        name="Slip Road",
        description="Node is on a slip road (Flags1 bit 0)",
        default=False
    )
    
    indicate_keep_left: BoolProperty(
        name="Indicate Keep Left",
        description="Indicate to keep left (Flags1 bit 1)",
        default=False
    )
    
    indicate_keep_right: BoolProperty(
        name="Indicate Keep Right",
        description="Indicate to keep right (Flags1 bit 2)",
        default=False
    )
    
    left_turns_only: BoolProperty(
        name="Left Turns Only",
        description="Only left turns allowed (Flags1 bit 7)",
        default=False
    )
    
    special_type: EnumProperty(
        name="Special Type",
        description="Special node type (Flags1 bits 3-7)",
        items=special_type_items,
        default="0"
    )
    
    # Decoded flag properties from Flags2
    no_gps: BoolProperty(
        name="No GPS",
        description="Node not shown on GPS (Flags2 bit 0)",
        default=False
    )
    
    is_junction: BoolProperty(
        name="Is Junction",
        description="Node is a junction (Flags2 bit 2)",
        default=False
    )
    
    is_disabled_unk0: BoolProperty(
        name="Is Disabled (Unk0)",
        description="Node is disabled - unknown type 0 (Flags2 bit 4)",
        default=False
    )
    
    highway: BoolProperty(
        name="Highway",
        description="Node is on a highway (Flags2 bit 6)",
        default=False
    )
    
    is_disabled_unk1: BoolProperty(
        name="Is Disabled (Unk1)",
        description="Node is disabled - unknown type 1 (Flags2 bit 7)",
        default=False
    )
    
    # Decoded flag properties from Flags3
    tunnel: BoolProperty(
        name="Tunnel",
        description="Node is in a tunnel (Flags3 bit 0)",
        default=False
    )
    
    heuristic_value: IntProperty(
        name="Heuristic Value",
        description="Pathfinding heuristic value (Flags3 bits 1-7)",
        default=0,
        min=0,
        max=127
    )
    
    # Decoded flag properties from Flags4
    density: IntProperty(
        name="Density",
        description="Traffic density (Flags4 bits 0-3)",
        default=0,
        min=0,
        max=15
    )
    
    dead_endness: IntProperty(
        name="Dead Endness",
        description="Dead end value (Flags4 bits 4-6)",
        default=0,
        min=0,
        max=7
    )
    
    # Decoded from Flags5 (LinkCountFlags)
    link_count: IntProperty(
        name="Link Count",
        description="Number of links from this node (Flags5 bits 3-7)",
        default=0,
        min=0,
        max=31
    )
    
    speed: EnumProperty(
        name="Speed",
        description="Speed category for this node (Flags5 bits 1-2)",
        items=speed_items,
        default="1"
    )


class YndLinkProperties(bpy.types.PropertyGroup):
    """Properties for links between nodes"""
    
    to_area_id: IntProperty(
        name="To Area ID",
        description="Target node area ID",
        default=0,
        min=0
    )
    
    to_node_id: IntProperty(
        name="To Node ID",
        description="Target node ID",
        default=0,
        min=0
    )
    
    # Raw flag values
    flags0: IntProperty(
        name="Flags 0",
        description="Raw link flags byte 0",
        default=0,
        min=0,
        max=255
    )
    
    flags1: IntProperty(
        name="Flags 1",
        description="Raw link flags byte 1",
        default=0,
        min=0,
        max=255
    )
    
    flags2: IntProperty(
        name="Flags 2",
        description="Raw link flags byte 2",
        default=0,
        min=0,
        max=255
    )
    
    link_length: IntProperty(
        name="Link Length",
        description="Length of the link",
        default=0,
        min=0
    )
    
    # Decoded properties
    lane_count_forward: IntProperty(
        name="Forward Lanes",
        description="Number of forward lanes",
        default=1,
        min=0,
        max=7
    )
    
    lane_count_backward: IntProperty(
        name="Backward Lanes",
        description="Number of backward lanes",
        default=0,
        min=0,
        max=7
    )


class YndFileProperties(bpy.types.PropertyGroup):
    vehicle_node_count: IntProperty(
        name="Vehicle Nodes",
        description="Number of vehicle nodes in the file",
        default=0,
        min=0
    )
    
    ped_node_count: IntProperty(
        name="Ped Nodes",
        description="Number of pedestrian nodes in the file",
        default=0,
        min=0
    )

def decode_node_flags(node_xml, node_props):
    # Store raw flag values
    flags0 = node_xml.flags0.value if hasattr(node_xml.flags0, 'value') else node_xml.flags0
    flags1 = node_xml.flags1.value if hasattr(node_xml.flags1, 'value') else node_xml.flags1
    flags2 = node_xml.flags2.value if hasattr(node_xml.flags2, 'value') else node_xml.flags2
    flags3 = node_xml.flags3.value if hasattr(node_xml.flags3, 'value') else node_xml.flags3
    flags4 = node_xml.flags4.value if hasattr(node_xml.flags4, 'value') else node_xml.flags4
    flags5 = node_xml.flags5.value if hasattr(node_xml.flags5, 'value') else node_xml.flags5
    
    node_props.flags0 = flags0
    node_props.flags1 = flags1
    node_props.flags2 = flags2
    node_props.flags3 = flags3
    node_props.flags4 = flags4
    node_props.flags5 = flags5
    
    # Decode Flags0
    node_props.off_road = (flags0 & 8) > 0  # bit 3
    node_props.no_big_vehicles = (flags0 & 32) > 0  # bit 5
    node_props.cannot_go_left = (flags0 & 128) > 0  # bit 7
    
    # Decode Flags1
    node_props.slip_road = (flags1 & 1) > 0  # bit 0
    node_props.indicate_keep_left = (flags1 & 2) > 0  # bit 1
    node_props.indicate_keep_right = (flags1 & 4) > 0  # bit 2
    node_props.left_turns_only = (flags1 & 128) > 0  # bit 7
    
    # Special type is bits 3-7 of Flags1 (shift right by 3)
    special_value = (flags1 >> 3) & 0x1F  # 5 bits
    node_props.special_type = str(special_value)
    
    # Decode Flags2
    node_props.no_gps = (flags2 & 1) > 0  # bit 0
    node_props.is_junction = (flags2 & 4) > 0  # bit 2
    node_props.is_disabled_unk0 = (flags2 & 16) > 0  # bit 4
    node_props.highway = (flags2 & 64) > 0  # bit 6
    node_props.is_disabled_unk1 = (flags2 & 128) > 0  # bit 7
    
    # Decode Flags3
    node_props.tunnel = (flags3 & 1) > 0  # bit 0
    node_props.heuristic_value = (flags3 >> 1) & 0x7F  # bits 1-7 (7 bits)
    
    # Decode Flags4
    node_props.density = flags4 & 15  # bits 0-3 (4 bits)
    node_props.dead_endness = (flags4 >> 4) & 7  # bits 4-6 (3 bits)
    
    # Decode Flags5 (LinkCountFlags)
    node_props.link_count = (flags5 >> 3) & 0x1F  # bits 3-7 (5 bits)
    speed_value = (flags5 >> 1) & 3  # bits 1-2 (2 bits)
    node_props.speed = str(speed_value)


def encode_node_flags(node_props):
    # Encode Flags0
    flags0 = 0
    if node_props.off_road:
        flags0 |= 8
    if node_props.no_big_vehicles:
        flags0 |= 32
    if node_props.cannot_go_left:
        flags0 |= 128
    
    # Encode Flags1
    flags1 = 0
    if node_props.slip_road:
        flags1 |= 1
    if node_props.indicate_keep_left:
        flags1 |= 2
    if node_props.indicate_keep_right:
        flags1 |= 4
    if node_props.left_turns_only:
        flags1 |= 128
    
    # Encode special type (bits 3-7)
    special_value = int(node_props.special_type)
    flags1 |= (special_value & 0x1F) << 3
    
    # Encode Flags2
    flags2 = 0
    if node_props.no_gps:
        flags2 |= 1
    if node_props.is_junction:
        flags2 |= 4
    if node_props.is_disabled_unk0:
        flags2 |= 16
    if node_props.highway:
        flags2 |= 64
    if node_props.is_disabled_unk1:
        flags2 |= 128
    
    # Encode Flags3
    flags3 = 0
    if node_props.tunnel:
        flags3 |= 1
    flags3 |= (node_props.heuristic_value & 0x7F) << 1
    
    # Encode Flags4
    flags4 = (node_props.density & 15) | ((node_props.dead_endness & 7) << 4)
    
    # Encode Flags5
    speed_value = int(node_props.speed)
    flags5 = ((node_props.link_count & 0x1F) << 3) | ((speed_value & 3) << 1)
    
    return flags0, flags1, flags2, flags3, flags4, flags5


def decode_link_flags(link_xml, link_props):
    # Store raw flag values
    flags0 = link_xml.flags0.value if hasattr(link_xml.flags0, 'value') else link_xml.flags0
    flags1 = link_xml.flags1.value if hasattr(link_xml.flags1, 'value') else link_xml.flags1
    flags2 = link_xml.flags2.value if hasattr(link_xml.flags2, 'value') else link_xml.flags2
    
    link_props.flags0 = flags0
    link_props.flags1 = flags1
    link_props.flags2 = flags2
    
    # Decode lane counts from flags
    # lane counts are typically in flags0
    link_props.lane_count_forward = flags0 & 7  # bits 0-2 (3 bits)
    link_props.lane_count_backward = (flags0 >> 3) & 7  # bits 3-5 (3 bits)


def encode_link_flags(link_props):
    # Encode lane counts into flags0
    flags0 = (link_props.lane_count_forward & 7) | ((link_props.lane_count_backward & 7) << 3)
    
    # Keep other flags as is for now
    flags1 = link_props.flags1
    flags2 = link_props.flags2
    
    return flags0, flags1, flags2


def register():
    bpy.types.Object.ynd_file_properties = PointerProperty(type=YndFileProperties)
    bpy.types.Object.ynd_node_properties = PointerProperty(type=YndNodeProperties)
    bpy.types.Object.ynd_link_properties = PointerProperty(type=YndLinkProperties)


def unregister():
    del bpy.types.Object.ynd_link_properties
    del bpy.types.Object.ynd_node_properties
    del bpy.types.Object.ynd_file_properties
