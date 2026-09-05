import math
import os
from pathlib import Path
import tempfile

import bpy
from mathutils import Vector
from mathutils.geometry import closest_point_on_tri, tessellate_polygon

from szio.gta5.cwxml import Navmesh as NavMesh, NavPolygon, NavPortal as NavLink, NavPoint as NavCoverPoint
from ..sollumz_properties import SollumType
from ..shared.math import wrap_angle
from .navmesh import navmesh_grid_get_cell_bounds, navmesh_is_valid
from .navmesh_attributes import NavMeshAttr
from .navmesh_topology import (
    NONE,
    NavmeshError,
    area_id,
    snapshot,
    local_references,
    validate_preserved_borders,
    match_edges,
    TOLERANCE,
    reference_flags,
)
from .properties import NavLinkType


def _descendants(obj, sollum_type):
    return [child for child in obj.children_recursive if child.sollum_type == sollum_type]


def _position(obj, root):
    world = obj.matrix_world.translation
    return world.copy() if area_id(root) < 10000 else root.matrix_world.inverted() @ world


def bind_position(snap, point, max_distance):
    candidates = []
    for index, vertices in enumerate(snap.vertices):
        if len(vertices) < 3:
            continue
        distances = [
            (
                point - closest_point_on_tri(point, *((vertices[i] for i in tri) if isinstance(tri[0], int) else tri))
            ).length
            for tri in tessellate_polygon([vertices])
        ]
        if distances:
            candidates.append((min(distances), index))
    candidates.sort()
    if not candidates or candidates[0][0] > max_distance:
        raise NavmeshError(
            "Portal endpoint is too far from a polygon in its navmesh. Move it onto a face or increase Binding Distance."
        )
    if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1e-5:
        raise NavmeshError(
            "Portal endpoint lies on an ambiguous polygon boundary. Move it inside a polygon or set its ID manually."
        )
    return candidates[0][1]


def link_from_object(link_obj, root=None, snap=None):
    if root is None:
        root = link_obj.parent
        while root and not navmesh_is_valid(root):
            root = root.parent
    if root is None:
        raise NavmeshError(f"Portal '{link_obj.name}' has no navmesh parent.")
    targets = [c for c in link_obj.children if c.sollum_type == SollumType.NAVMESH_LINK_TARGET]
    if len(targets) != 1:
        raise NavmeshError(f"Portal '{link_obj.name}' must have exactly one target.")
    snap = snap or snapshot(root)
    props = link_obj.sz_nav_link
    result = NavLink()
    result.type = NavLinkType[props.link_type].value
    result.angle = wrap_angle(props.heading)
    result.position_from = _position(link_obj, root)
    result.position_to = _position(targets[0], root)
    if props.auto_bind:
        result.poly_from = bind_position(snap, result.position_from, props.bind_distance)
        result.poly_to = bind_position(snap, result.position_to, props.bind_distance)
    else:
        result.poly_from, result.poly_to = props.poly_from, props.poly_to
    if not (0 <= result.poly_from < len(snap.order) and 0 <= result.poly_to < len(snap.order)):
        raise NavmeshError(f"Portal '{link_obj.name}' references a missing polygon.")
    return result


def cover_point_from_object(obj, root=None):
    root = root or next((p for p in _parents(obj) if navmesh_is_valid(p)), None)
    if root is None:
        raise NavmeshError(f"Cover point '{obj.name}' has no navmesh parent.")
    result = NavCoverPoint()
    result.type = obj.sz_nav_cover_point.get_raw_int()
    transform = obj.matrix_world if area_id(root) < 10000 else root.matrix_world.inverted() @ obj.matrix_world
    result.angle = wrap_angle(transform.to_euler().z - math.pi)
    result.position = _position(obj, root)
    return result


def _parents(obj):
    while obj.parent:
        obj = obj.parent
        yield obj


def _validate_neighbors(snap):
    referenced = {
        ref[0] for edge in snap.edges for ref in (edge.adjacent, edge.original) if ref != NONE and ref[0] != snap.area
    }
    neighbors = {}
    for obj in bpy.context.scene.objects:
        if obj == snap.obj or not navmesh_is_valid(obj) or area_id(obj) not in referenced:
            continue
        aid = area_id(obj)
        if aid in neighbors:
            raise NavmeshError(f"Multiple loaded objects have neighbour Area ID {aid}.")
        neighbors[aid] = snapshot(obj)
    snaps = [snap] + list(neighbors.values())
    matches = match_edges(snaps)
    neighbor_slot = {s.area: i for i, s in enumerate(snaps)}
    for i, edge in enumerate(snap.edges):
        for ref in (edge.adjacent, edge.original):
            if ref[0] in neighbors and ref[1] >= len(neighbors[ref[0]].order):
                raise NavmeshError(f"Border polygon {edge.face} references missing polygon {ref}.")
        ref = edge.adjacent
        if ref[0] not in neighbors or edge.special:
            continue
        target_snap = neighbors[ref[0]]
        if edge.face in snap.unchanged and ref[1] in target_snap.unchanged:
            if any(e.face == ref[1] and e.adjacent == (snap.area, edge.face) for e in target_snap.edges):
                continue
        other = matches.get((0, i))
        if other is None or other[0] != neighbor_slot[ref[0]]:
            raise NavmeshError(
                f"Border polygon {edge.face} no longer matches sector {ref[0]}. Rebuild neighbour links."
            )
        target = snaps[other[0]].edges[other[1]]
        if target.face != ref[1] or target.adjacent != (snap.area, edge.face):
            raise NavmeshError(f"Non-reciprocal border link at polygon {edge.face}. Rebuild and export both sectors.")


def polygons_from_object(navmesh_obj, snap=None):
    snap = snap or snapshot(navmesh_obj)
    refs = local_references(snap)
    by_loop = {e.loop: e for e in snap.edges}
    result = []
    has_water = is_dlc = False
    mesh = navmesh_obj.data
    for index, (mesh_face, vertices, loops) in enumerate(zip(snap.order, snap.vertices, snap.loops)):
        poly = NavPolygon()
        poly.vertices = vertices
        data0 = mesh.attributes[NavMeshAttr.POLY_DATA_0].data[mesh_face].value
        data1 = mesh.attributes[NavMeshAttr.POLY_DATA_1].data[mesh_face].value
        data2 = mesh.attributes[NavMeshAttr.POLY_DATA_2].data[mesh_face].value
        flag0, flag1 = data0 & 255, data0 >> 8 & 255
        flag2, flag3 = data1 & 255, data1 >> 8 & 255
        # Recalculate size flags using exported geometry, retaining all other bits.
        polygon_area = (
            abs(
                sum(
                    v.x * vertices[(i + 1) % len(vertices)].y - v.y * vertices[(i + 1) % len(vertices)].x
                    for i, v in enumerate(vertices)
                )
            )
            / 2
        )
        if index not in snap.unchanged:
            flag0 = (flag0 & ~3) | (1 if polygon_area < 2 else 0) | (2 if polygon_area > 40 else 0)
        border = any(r != NONE and r[0] != snap.area for loop in loops for r in refs[loop])
        if index not in snap.unchanged:
            flag2 = (flag2 | 4) if border else (flag2 & ~4)
        centroid = sum(vertices, Vector()) / len(vertices)
        low = Vector(tuple(math.floor(min(v[i] for v in vertices) / 0.25) * 0.25 for i in range(3)))
        high = Vector(tuple(math.floor(max(v[i] for v in vertices) / 0.25) * 0.25 for i in range(3)))
        compressed = [
            min(255, max(0, int((centroid[i] - low[i]) / (high[i] - low[i]) * 256))) if high[i] != low[i] else 0
            for i in range(2)
        ]
        if index in snap.unchanged:
            compressed = snap.source[index]["flags"][4:6]
        poly.flags = " ".join(map(str, (flag0, flag1, flag2, flag3, *compressed, data2 & 255)))
        poly.edges = "\n".join(f"{a[0]}:{a[1]}, {b[0]}:{b[1]}" for a, b in (refs[loop] for loop in loops))
        flag_lines = []
        for loop in loops:
            e = by_loop[loop]
            a, b = refs[loop]
            data0, data1 = e.data0, e.data1
            if index not in snap.unchanged or (a, b) != (e.adjacent, e.original):
                data0, data1 = reference_flags(data0, a), reference_flags(data1, b)
            flag_lines.append(f"{data0 & 65535}:{data0 >> 16 & 65535}, {data1 & 65535}:{data1 >> 16 & 65535}")
        poly.edges_flags = "\n".join(flag_lines)
        result.append(poly)
        has_water |= bool(flag0 & 128)
        is_dlc |= bool(data2 & 1)
    return result, has_water, is_dlc


def navmesh_from_object(obj):
    snap = snapshot(obj)
    if snap.area < 10000 and any(
        other != obj and navmesh_is_valid(other) and area_id(other) == snap.area for other in bpy.context.scene.objects
    ):
        raise NavmeshError(f"More than one loaded navmesh has Area ID {snap.area}.")
    validate_preserved_borders(snap)
    _validate_neighbors(snap)
    used_areas = {snap.area, NONE[0]}
    for edge_refs in local_references(snap).values():
        used_areas.update(r[0] for r in edge_refs)
    if len(used_areas) > 32:
        raise NavmeshError("A YNV can reference at most 32 area IDs, including itself and the unlinked sentinel.")
    nav = NavMesh()
    nav.area_id = snap.area
    nav.polygons, has_water, is_dlc = polygons_from_object(obj, snap)
    for portal in _descendants(obj, SollumType.NAVMESH_LINK):
        nav.portals.append(link_from_object(portal, obj, snap))
    portal_lists = [[] for _ in nav.polygons]
    if len(nav.portals) > 65535:
        raise NavmeshError("Too many portals for a YNV file.")
    for index, portal in enumerate(nav.portals):
        for face in {portal.poly_from, portal.poly_to}:
            portal_lists[face].append(index)
    for index, links in enumerate(portal_lists):
        if len(links) > 7:
            raise NavmeshError(f"Polygon {index} has more than seven portals. Split the polygon or remove links.")
        nav.polygons[index].portals = " ".join(map(str, links))
    for point in _descendants(obj, SollumType.NAVMESH_COVER_POINT):
        nav.points.append(cover_point_from_object(point, obj))
    if len(nav.points) > 8191:
        raise NavmeshError("A YNV supports at most 8191 cover points.")
    positions = [v for verts in snap.vertices for v in verts]
    positions += [p.position for p in nav.points]
    positions += [v for p in nav.portals for v in (p.position_from, p.position_to)]
    low = Vector(tuple(min(v[i] for v in positions) for i in range(3)))
    high = Vector(tuple(max(v[i] for v in positions) for i in range(3)))
    if snap.area < 10000:
        cell_low, cell_high = navmesh_grid_get_cell_bounds(snap.area % 100, snap.area // 100)
        if any(
            v.x < cell_low.x - TOLERANCE
            or v.x > cell_high.x + TOLERANCE
            or v.y < cell_low.y - TOLERANCE
            or v.y > cell_high.y + TOLERANCE
            for v in positions
        ):
            raise NavmeshError(
                "A portal or cover point lies outside its sector. CW XML portals must belong to one sector."
            )
        low.x, low.y = cell_low.x, cell_low.y
        high.x, high.y = cell_high.x, cell_high.y
    for axis in range(3):
        high[axis] = max(high[axis], low[axis] + 0.01)
    nav.bb_min, nav.bb_max, nav.bb_size = low, high, high - low
    flags = ["Polygons"]
    if nav.portals:
        flags.append("Portals")
    if snap.area >= 10000:
        flags.append("Vehicle")
    if has_water:
        flags.append("Unknown8")
    if is_dlc:
        flags.append("Unknown16")
    nav.content_flags = ", ".join(flags)
    return nav


def commit_export(obj, nav):
    """Persist the references actually written, including defaults on new corners."""
    from .navmesh_topology import commit_polygon_ids, polygon_order, pack_reference, remember_borders
    from .navmesh_attributes import signed_int

    mesh = obj.data
    order = polygon_order(mesh)
    for face, poly in zip(order, nav.polygons):
        loop_indices = list(mesh.polygons[face].loop_indices)

        def pairs(text):
            return [
                [tuple(map(int, part.split(":"))) for part in line.split(",")] for line in text.strip().splitlines()
            ]

        refs, flags = pairs(poly.edges), pairs(poly.edges_flags)
        if len(loop_indices) > len(refs):
            refs.append(refs[-1])
            flags.append(flags[-1])
        for loop, (current, original), (first, second) in zip(loop_indices, refs, flags):
            values = {
                NavMeshAttr.EDGE_INITIALIZED: 1,
                NavMeshAttr.EDGE_ADJACENT_POLY: pack_reference(current),
                NavMeshAttr.EDGE_ORIGINAL_POLY: pack_reference(original),
                NavMeshAttr.EDGE_DATA_0: signed_int(first[0] | first[1] << 16),
                NavMeshAttr.EDGE_DATA_1: signed_int(second[0] | second[1] << 16),
            }
            for attr, value in values.items():
                mesh.attributes[attr].data[loop].value = value
    commit_polygon_ids(mesh, order)
    remember_borders(obj)


def export_ynv(obj, filepath):
    nav = navmesh_from_object(obj)
    path = Path(filepath)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".ynv.xml", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        nav.write_xml(str(temporary))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    commit_export(obj, nav)
    return True
