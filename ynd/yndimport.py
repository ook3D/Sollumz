import os
import bpy
from mathutils import Vector
from ..cwxml.nodepath import YND
from ..sollumz_properties import SollumType
from ..tools.blenderhelper import create_blender_object, create_empty_object
from ..tools.meshhelper import create_box
from .ynd_helpers import (
    create_node_material,
    create_link_material,
    get_node_color,
    get_link_color,
    validate_node_data
)


def import_ynd(filepath):
    ynd_xml = YND.from_xml_file(filepath)
    ynd_to_obj(ynd_xml, filepath)


def get_value(prop):
    return prop.value if hasattr(prop, 'value') else prop


def ynd_to_obj(node_dict, filepath):
    name = os.path.basename(filepath.replace(YND.file_extension, ""))
    
    ynd_obj = create_empty_object(SollumType.YND, name)
    
    ynd_obj.ynd_file_properties.vehicle_node_count = get_value(node_dict.vehicle_node_count)
    ynd_obj.ynd_file_properties.ped_node_count = get_value(node_dict.ped_node_count)
    
    node_objects = {}
    for node in get_value(node_dict.nodes):
        is_valid, error_msg = validate_node_data(node)
        if not is_valid:
            print(f"Warning: Skipping invalid node {get_value(node.node_id)}: {error_msg}")
            continue
        
        node_obj = node_to_obj(node)
        node_obj.parent = ynd_obj
        node_key = (get_value(node.area_id), get_value(node.node_id))
        node_objects[node_key] = node_obj
    
    junctions = get_value(node_dict.junctions) if hasattr(node_dict, 'junctions') else []
    junction_refs = get_value(node_dict.junction_refs) if hasattr(node_dict, 'junction_refs') else []
    
    if junctions and junction_refs:
        create_junction_heightmaps(junctions, junction_refs, node_objects, ynd_obj)
    
    links_obj = create_links_visualization(get_value(node_dict.nodes), node_objects)
    if links_obj:
        links_obj.parent = ynd_obj
    
    return ynd_obj


def node_to_obj(node):
    mesh = bpy.data.meshes.new("YND Node")
    obj = create_blender_object(SollumType.YND_NODE, f"Node_{get_value(node.node_id)}", mesh)
    
    create_box(mesh, 0.5)
    
    obj.location = Vector(get_value(node.position))
    
    obj.ynd_node_properties.area_id = get_value(node.area_id)
    obj.ynd_node_properties.node_id = get_value(node.node_id)
    obj.ynd_node_properties.street_name = get_value(node.street_name) or ""
    
    from .properties import decode_node_flags
    decode_node_flags(node, obj.ynd_node_properties)
    
    apply_node_material(obj, node)
    
    return obj





def apply_node_material(obj, node):
    mat = create_node_material(node)
    
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def create_links_visualization(nodes, node_objects):
    from .ynd_helpers import create_link_material
    
    link_objects = []
    
    for node in nodes:
        if not get_value(node.links):
            continue
        
        node_key = (get_value(node.area_id), get_value(node.node_id))
        if node_key not in node_objects:
            continue
        
        from_node_obj = node_objects[node_key]
        start_pos = Vector(get_value(node.position))
        from_area_id = get_value(node.area_id)
        from_node_id = get_value(node.node_id)
        
        for link in get_value(node.links):
            target_key = (get_value(link.to_area_id), get_value(link.to_node_id))
            if target_key not in node_objects:
                continue
            
            to_node_obj = node_objects[target_key]
            target_pos = to_node_obj.location
            
            link_obj = create_link_object(link, start_pos, target_pos, from_area_id, from_node_id, from_node_obj, to_node_obj)
            if link_obj:
                link_objects.append(link_obj)
    
    if not link_objects:
        return None
    
    links_parent = create_empty_object(SollumType.YND_LINKS, "Links")
    
    for link_obj in link_objects:
        link_obj.parent = links_parent
    
    return links_parent


def create_link_object(link, start_pos, end_pos, from_area_id, from_node_id, from_node_obj, to_node_obj):
    from .ynd_helpers import create_link_material
    
    mesh = bpy.data.meshes.new("YND Link")
    vertices = [start_pos, end_pos]
    edges = [(0, 1)]
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    
    to_area_id = get_value(link.to_area_id)
    to_node_id = get_value(link.to_node_id)
    link_name = f"Link_{from_area_id}_{from_node_id}_to_{to_area_id}_{to_node_id}"
    obj = create_blender_object(SollumType.YND_LINK, link_name, mesh)
    
    obj.ynd_link_properties.to_area_id = to_area_id
    obj.ynd_link_properties.to_node_id = to_node_id
    obj.ynd_link_properties.flags0 = get_value(link.flags0)
    obj.ynd_link_properties.flags1 = get_value(link.flags1)
    obj.ynd_link_properties.flags2 = get_value(link.flags2)
    obj.ynd_link_properties.link_length = get_value(link.link_length)
    
    flags0 = get_value(link.flags0)
    obj.ynd_link_properties.lane_count_forward = flags0 & 0x7
    obj.ynd_link_properties.lane_count_backward = (flags0 >> 3) & 0x7
    
    mat = create_link_material(link)
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    
    obj["from_node"] = from_node_obj
    obj["to_node"] = to_node_obj
    
    return obj



def create_junction_heightmaps(junctions, junction_refs, node_objects, ynd_parent):
    node_to_junction = {}
    for ref in junction_refs:
        area_id = get_value(ref.area_id)
        node_id = get_value(ref.node_id)
        junction_id = get_value(ref.junction_id)
        node_to_junction[(area_id, node_id)] = junction_id
    
    for junction_id, junction in enumerate(junctions):
        node_key = None
        for key, junc_id in node_to_junction.items():
            if junc_id == junction_id:
                node_key = key
                break

        node_obj = node_objects.get(node_key) if node_key else None
        
        junction_obj = create_junction_heightmap(junction, junction_id, node_obj, ynd_parent)


def create_junction_heightmap(junction, junction_id, node_obj, ynd_parent):
    if not hasattr(junction, 'heightmap') or not hasattr(junction.heightmap, 'data'):
        return None
    
    heightmap_data = junction.heightmap.data
    if not heightmap_data:
        return None
    
    size_x = get_value(junction.size_x)
    size_y = get_value(junction.size_y)
    min_z = get_value(junction.min_z)
    max_z = get_value(junction.max_z)
    position = get_value(junction.position)
    
    dim_x = size_x
    dim_y = size_y
    
    expected_size = dim_x * dim_y
    if len(heightmap_data) != expected_size:
        print(f"Warning: Junction {junction_id} heightmap size mismatch. Expected {expected_size}, got {len(heightmap_data)}")
        return None
    
    mesh = bpy.data.meshes.new("Junction Heightmap")
    vertices = []
    faces = []
    colors = []
    
    # Calculate cell size (size is total dimension in world units, divide by number of cells)
    # The grid has dim_x * dim_y cells, so we need dim_x+1 * dim_y+1 vertices
    cell_size_x = size_x / (dim_x - 1) if dim_x > 1 else size_x
    cell_size_y = size_y / (dim_y - 1) if dim_y > 1 else size_y
    
    for y in range(dim_y):
        for x in range(dim_x):
            idx = y * dim_x + x
            height_byte = heightmap_data[idx]
            
            if max_z > min_z:
                normalized_height = height_byte / 255.0
                height = min_z + (normalized_height * (max_z - min_z))
            else:
                height = min_z
            
            # Calculate vertex position
            # Position is the bottom left corner of the heightmap
            vert_x = x * cell_size_x
            vert_y = y * cell_size_y
            vertices.append((vert_x, vert_y, height))
            
            # Calculate color based on height (blue = low, red = high)
            color_value = height_byte / 255.0
            color = (color_value, 0.0, 1.0 - color_value, 1.0)  # Red to Blue gradient
            colors.append(color)
    
    # create faces
    for y in range(dim_y - 1):
        for x in range(dim_x - 1):
            # Create quad face
            v0 = y * dim_x + x
            v1 = y * dim_x + (x + 1)
            v2 = (y + 1) * dim_x + (x + 1)
            v3 = (y + 1) * dim_x + x
            faces.append((v0, v1, v2, v3))

    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    
    if colors and mesh.vertices:
        color_layer = mesh.vertex_colors.new(name="HeightColor")
        for poly in mesh.polygons:
            for loop_idx in poly.loop_indices:
                loop = mesh.loops[loop_idx]
                vert_idx = loop.vertex_index
                if vert_idx < len(colors):
                    color_layer.data[loop_idx].color = colors[vert_idx]
    
    if node_obj:
        obj_name = f"Junction_{junction_id}_{node_obj.name}"
    else:
        obj_name = f"Junction_{junction_id}"
    
    obj = create_blender_object(SollumType.YND_NODE, obj_name, mesh)
    
    obj.location = Vector((position[0], position[1], 0))
    obj.parent = ynd_parent
    
    apply_junction_material(obj)
    
    return obj


def apply_junction_material(obj):
    mat = bpy.data.materials.new(name="YND_Junction_Material")
    mat.use_nodes = True
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    
    nodes.clear()
    
    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    bsdf_node = nodes.new(type='ShaderNodeBsdfPrincipled')
    vertex_color_node = nodes.new(type='ShaderNodeVertexColor')
    
    vertex_color_node.layer_name = "HeightColor"
    
    output_node.location = (300, 0)
    bsdf_node.location = (0, 0)
    vertex_color_node.location = (-300, 0)
    
    links.new(vertex_color_node.outputs['Color'], bsdf_node.inputs['Base Color'])
    links.new(bsdf_node.outputs['BSDF'], output_node.inputs['Surface'])
    
    bsdf_node.inputs['Alpha'].default_value = 0.5
    mat.blend_method = 'BLEND'
    mat.show_transparent_back = False
    
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
