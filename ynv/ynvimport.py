import bpy
from bpy.types import Object, Mesh
import math
from ..shared.math import wrap_angle
from szio.gta5.cwxml import (
    YNV,
    NavPoint as NavCoverPoint,
    NavPortal as NavLink,
    NavPolygon,
)
from ..sollumz_properties import SollumType
from .navmesh_attributes import NavMeshAttr
from typing import Sequence


def cover_points_to_obj(points: Sequence[NavCoverPoint]) -> Object:
    pobj = bpy.data.objects.new("Cover Points", None)
    pobj.sollum_type = SollumType.NAVMESH_COVER_POINT_GROUP
    pobj.empty_display_size = 0

    for idx, point in enumerate(points):
        obj = bpy.data.objects.new(f"Cover Point {idx}", None)
        obj.sollum_type = SollumType.NAVMESH_COVER_POINT
        obj.parent = pobj
        obj.empty_display_size = 0.5
        obj.empty_display_type = "CONE"
        obj.location = point.position
        # flip rotation so the cone display is more intuitive
        obj.rotation_euler = (0, 0, wrap_angle(math.pi + point.angle))
        obj.lock_rotation = (True, True, False)
        obj.sz_nav_cover_point.set_raw_int(point.type)
        bpy.context.collection.objects.link(obj)

    return pobj


def links_to_obj(links: Sequence[NavLink]) -> Object:
    pobj = bpy.data.objects.new("Links", None)
    pobj.sollum_type = SollumType.NAVMESH_LINK_GROUP
    pobj.empty_display_size = 0

    for idx, link in enumerate(links):
        from_obj = bpy.data.objects.new(f"Link {idx}", None)
        from_obj.sollum_type = SollumType.NAVMESH_LINK
        from_obj.parent = pobj
        from_obj.empty_display_size = 0.65
        from_obj.empty_display_type = "SPHERE"
        from_obj.location = link.position_from
        from_obj.sz_nav_link.set_raw_int(link.type)
        from_obj.sz_nav_link.heading = link.angle
        from_obj.sz_nav_link.poly_from = link.poly_from
        from_obj.sz_nav_link.poly_to = link.poly_to

        to_obj = bpy.data.objects.new(f"Link {idx}.target", None)
        to_obj.sollum_type = SollumType.NAVMESH_LINK_TARGET
        to_obj.parent = from_obj
        to_obj.empty_display_size = 0.45
        to_obj.empty_display_type = "SPHERE"
        to_obj.location = link.position_to - link.position_from

        bpy.context.collection.objects.link(from_obj)
        bpy.context.collection.objects.link(to_obj)

    return pobj


def polygons_to_mesh(name: str, polygons: Sequence[NavPolygon]) -> Mesh:
    from .navmesh_topology import initialize_mesh, NavmeshError, pack_reference, NONE
    from .navmesh_attributes import signed_int

    vertices, faces, records = [], [], []
    for index, poly in enumerate(polygons):
        flags = [int(v) for v in poly.flags.split()]
        if len(flags) != 7 or any(v < 0 or v > 255 for v in flags):
            raise NavmeshError(f"Polygon {index}: expected seven flag bytes.")
        verts = list(poly.vertices)
        if len(verts) < 2 or (len(verts) == 2 and not flags[6] & 1):
            raise NavmeshError(f"Polygon {index}: invalid vertex count.")

        def parse_pairs(text, default):
            lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
            if not lines:
                return [default] * len(verts)
            if len(lines) != len(verts):
                raise NavmeshError(f"Polygon {index}: edge count does not match its vertices.")
            result = []
            for line in lines:
                pairs = [tuple(map(int, part.strip().split(":"))) for part in line.split(",")]
                if len(pairs) != 2 or any(len(pair) != 2 for pair in pairs):
                    raise NavmeshError(f"Polygon {index}: malformed edge record.")
                result.append(pairs)
            return result

        refs = parse_pairs(poly.edges, (NONE, NONE))
        edge_flags = parse_pairs(poly.edges_flags, ((0, 0), (0, 0)))
        if len(verts) == 2:
            verts.append(verts[-1].copy())
            refs.append(refs[-1])
            edge_flags.append(edge_flags[-1])
        face = list(range(len(vertices), len(vertices) + len(verts)))
        vertices.extend(verts)
        faces.append(face)
        records.append((flags, refs, edge_flags))
    mesh = bpy.data.meshes.new(name)
    try:
        mesh.from_pydata(vertices, [], faces)
        initialize_mesh(mesh, imported=True)
        for poly, (flags, refs, edge_flags) in zip(mesh.polygons, records):
            for attr, value in zip(
                (NavMeshAttr.POLY_DATA_0, NavMeshAttr.POLY_DATA_1, NavMeshAttr.POLY_DATA_2),
                (flags[0] | flags[1] << 8, flags[2] | flags[3] << 8, flags[6]),
            ):
                mesh.attributes[attr].data[poly.index].value = value
            for loop, (current, original), (data0, data1) in zip(poly.loop_indices, refs, edge_flags):
                values = {
                    NavMeshAttr.EDGE_ADJACENT_POLY: pack_reference(current),
                    NavMeshAttr.EDGE_ORIGINAL_POLY: pack_reference(original),
                    NavMeshAttr.EDGE_DATA_0: signed_int(data0[0] | data0[1] << 16),
                    NavMeshAttr.EDGE_DATA_1: signed_int(data1[0] | data1[1] << 16),
                }
                for attr, value in values.items():
                    mesh.attributes[attr].data[loop].value = value
        import json

        mesh["sz_navmesh_source_polygons"] = json.dumps(
            [
                {"vertices": [list(v) for v in poly.vertices], "flags": list(map(int, poly.flags.split()))}
                for poly in polygons
            ]
        )
        from .navmesh_material import get_navmesh_material

        mesh.materials.append(get_navmesh_material())
        return mesh
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise


def navmesh_to_obj(navmesh, filepath):
    from pathlib import Path

    name = Path(filepath).name.removesuffix(YNV.file_extension)
    mesh = polygons_to_mesh(name, navmesh.polygons)
    mesh_obj = bpy.data.objects.new(name, mesh)
    mesh_obj.sollum_type = SollumType.NAVMESH
    mesh_obj.empty_display_size = 0
    mesh_obj.sz_navmesh.area_id = int(navmesh.area_id)
    mesh["sz_navmesh_source_area"] = int(navmesh.area_id)
    mesh_obj.sz_navmesh.source_path = str(Path(filepath).resolve())
    mesh_obj.sz_navmesh.neighbor_directory = str(Path(filepath).resolve().parent)
    bpy.context.collection.objects.link(mesh_obj)

    links_obj = links_to_obj(navmesh.portals)
    links_obj.parent = mesh_obj
    bpy.context.collection.objects.link(links_obj)

    cover_points_obj = cover_points_to_obj(navmesh.points)
    cover_points_obj.parent = mesh_obj
    bpy.context.collection.objects.link(cover_points_obj)

    from .navmesh_topology import remember_borders

    remember_borders(mesh_obj)
    return mesh_obj


def import_ynv(filepath):
    before_objects, before_meshes = set(bpy.data.objects), set(bpy.data.meshes)
    try:
        ynv_xml = YNV.from_xml_file(filepath)
        return navmesh_to_obj(ynv_xml, filepath)
    except Exception:
        for obj in set(bpy.data.objects) - before_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in set(bpy.data.meshes) - before_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise
