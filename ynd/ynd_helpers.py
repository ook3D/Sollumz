import bpy
from mathutils import Vector

_material_cache = {}


def get_value(prop):
    return prop.value if hasattr(prop, 'value') else prop


def get_node_color(node):
    flags1 = get_value(node.flags1)
    flags2 = get_value(node.flags2)
    flags3 = get_value(node.flags3)
    
    # Check for disabled nodes first (highest priority)
    if flags2 & (1 << 7):  # IsDisabled (bit 7)
        return (1.0, 0.0, 0.0, 1.0)  # Red
    
    # Check for tunnel nodes
    if flags3 & (1 << 0):  # Tunnel (bit 0)
        return (0.5, 0.5, 0.5, 1.0)  # Gray
    
    # Check for junction nodes
    if flags2 & (1 << 2):  # IsJunction (bit 2)
        return (1.0, 1.0, 0.0, 1.0)  # Yellow
    
    # Check for highway nodes
    if flags2 & (1 << 6):  # Highway (bit 6)
        return (0.0, 0.5, 1.0, 1.0)  # Light Blue
    
    # Check special types (bits 3-7 of flags1)
    special_type = (flags1 >> 3) & 0x1F
    if special_type == 2:  # Parking Space
        return (1.0, 0.0, 1.0, 1.0)  # Magenta
    elif special_type == 10:  # Ped Road Crossing
        return (1.0, 0.5, 0.0, 1.0)  # Orange
    elif special_type == 14:  # Ped Node Assisted Movement
        return (0.8, 0.0, 0.8, 1.0)  # Purple
    elif special_type == 15:  # Traffic Light Junction Stop
        return (1.0, 1.0, 0.0, 1.0)  # Yellow
    elif special_type == 16:  # Stop Sign
        return (1.0, 0.2, 0.2, 1.0)  # Light Red
    elif special_type == 17:  # Caution
        return (1.0, 0.8, 0.0, 1.0)  # Amber
    elif special_type == 18:  # Ped Road Crossing No Wait
        return (1.0, 0.6, 0.2, 1.0)  # Light Orange
    elif special_type == 19:  # Emergency Vehicles Only
        return (0.0, 0.0, 1.0, 1.0)  # Blue
    elif special_type == 20:  # Off Road Junction
        return (0.6, 0.4, 0.2, 1.0)  # Brown
    elif special_type != 0:  # Other special types
        return (0.8, 0.8, 0.0, 1.0)  # Light Yellow
    
    # Check for off road nodes
    if flags1 & (1 << 3):  # OffRoad (bit 3 of flags0, but stored in flags1)
        return (0.4, 0.6, 0.2, 1.0)  # Olive Green
    
    # Default: normal vehicle node
    return (0.0, 1.0, 0.0, 1.0)  # Green


def get_link_color(link):
    flags0 = get_value(link.flags0)
    flags1 = get_value(link.flags1)
    
    # Extract lane counts from flags0
    forward_lanes = flags0 & 0x7  # Bits 0-2
    backward_lanes = (flags0 >> 3) & 0x7  # Bits 3-5
    
    # Check for special link types in flags1
    # Bit 6: Shortcut link
    if flags1 & (1 << 6):
        return (0.0, 0.5, 1.0, 1.0)  # Blue - shortcut
    
    # Bit 7: Off-road connection
    if flags1 & (1 << 7):
        return (1.0, 0.5, 0.0, 1.0)  # Orange - off-road
    
    # Color based on directionality
    if forward_lanes > 0 and backward_lanes > 0:
        return (1.0, 1.0, 0.0, 1.0)  # Yellow - bidirectional
    elif forward_lanes > 0:
        return (0.0, 1.0, 0.0, 1.0)  # Green - forward only
    elif backward_lanes > 0:
        return (1.0, 0.0, 0.0, 1.0)  # Red - backward only
    else:
        # No lanes specified, use cyan as default
        return (0.0, 1.0, 1.0, 1.0)  # Cyan - default/unknown


def create_node_material(node):
    color = get_node_color(node)
    
    # Create a cache key based on color
    cache_key = f"YND_Node_{int(color[0]*255)}_{int(color[1]*255)}_{int(color[2]*255)}"
    
    if cache_key in _material_cache:
        return _material_cache[cache_key]
    
    if cache_key in bpy.data.materials:
        mat = bpy.data.materials[cache_key]
        _material_cache[cache_key] = mat
        return mat
    
    mat = bpy.data.materials.new(name=cache_key)
    mat.use_nodes = True
    
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs['Base Color'].default_value = color
        if 'Emission Color' in bsdf.inputs:
            bsdf.inputs['Emission Color'].default_value = color
            bsdf.inputs['Emission Strength'].default_value = 0.2
        elif 'Emission' in bsdf.inputs:
            # Older Blender versions use 'Emission'
            bsdf.inputs['Emission'].default_value = color
            if 'Emission Strength' in bsdf.inputs:
                bsdf.inputs['Emission Strength'].default_value = 0.2
    
    _material_cache[cache_key] = mat
    
    return mat


def create_link_material(link):
    color = get_link_color(link)
    
    # Create a cache key based on color
    cache_key = f"YND_Link_{int(color[0]*255)}_{int(color[1]*255)}_{int(color[2]*255)}"
    
    if cache_key in _material_cache:
        return _material_cache[cache_key]
    
    if cache_key in bpy.data.materials:
        mat = bpy.data.materials[cache_key]
        _material_cache[cache_key] = mat
        return mat
    
    mat = bpy.data.materials.new(name=cache_key)
    mat.use_nodes = True
    
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs['Base Color'].default_value = color
        if 'Emission Color' in bsdf.inputs:
            bsdf.inputs['Emission Color'].default_value = color
            bsdf.inputs['Emission Strength'].default_value = 0.5
        elif 'Emission' in bsdf.inputs:
            # Older Blender versions use 'Emission'
            bsdf.inputs['Emission'].default_value = color
            if 'Emission Strength' in bsdf.inputs:
                bsdf.inputs['Emission Strength'].default_value = 0.5
    
    # Cache the material
    _material_cache[cache_key] = mat
    
    return mat


def validate_node_data(node):
    if not hasattr(node, 'area_id') or get_value(node.area_id) is None:
        return False, "Node missing area_id"
    
    if not hasattr(node, 'node_id') or get_value(node.node_id) is None:
        return False, "Node missing node_id"
    
    if not hasattr(node, 'position') or not get_value(node.position):
        return False, "Node missing position"
    
    try:
        pos = get_value(node.position)
        if len(pos) != 3:
            return False, f"Node position must be 3D vector, got {len(pos)} values"
        
        for i, val in enumerate(pos):
            if not isinstance(val, (int, float)):
                return False, f"Node position[{i}] is not a number: {val}"
            if val != val:  # NaN check
                return False, f"Node position[{i}] is NaN"
            if abs(val) == float('inf'):
                return False, f"Node position[{i}] is infinite"
    except Exception as e:
        return False, f"Invalid node position: {str(e)}"
    
    # Validate flags are in valid range (0-255)
    flag_names = ['flags0', 'flags1', 'flags2', 'flags3', 'flags4', 'flags5']
    for flag_name in flag_names:
        if hasattr(node, flag_name):
            flag_value = get_value(getattr(node, flag_name))
            if not isinstance(flag_value, int) or flag_value < 0 or flag_value > 255:
                return False, f"Node {flag_name} must be 0-255, got {flag_value}"
    
    # Validate links if present
    if hasattr(node, 'links') and get_value(node.links):
        for i, link in enumerate(get_value(node.links)):
            if not hasattr(link, 'to_area_id') or get_value(link.to_area_id) is None:
                return False, f"Link {i} missing to_area_id"
            if not hasattr(link, 'to_node_id') or get_value(link.to_node_id) is None:
                return False, f"Link {i} missing to_node_id"
    
    return True, ""


def clear_material_cache():
    global _material_cache
    _material_cache.clear()
