from dataclasses import dataclass
from itertools import product
import json
import math

from mathutils import Vector

from .navmesh import navmesh_is_valid, navmesh_get_grid_cell, navmesh_grid_get_cell_index
from .navmesh_attributes import NavMeshAttr, mesh_add_navmesh_attribute, signed_int

NONE = (0x3FFF, 0x3FFF)
MAX_POLYGONS = 0x3FFF
EDGE_ATTRS = (
    NavMeshAttr.EDGE_DATA_0,
    NavMeshAttr.EDGE_DATA_1,
    NavMeshAttr.EDGE_ADJACENT_POLY,
    NavMeshAttr.EDGE_ORIGINAL_POLY,
)
TOLERANCE = 0.01
BORDER_HEIGHT_TOLERANCE = 0.1


class NavmeshError(ValueError):
    """An authoring error which can be reported directly to the user."""


def reference_flags(value, ref):
    return (value | 1) if ref == NONE else (value & ~1)


def pack_reference(ref):
    return signed_int(ref[0] | (ref[1] << 16))


def unpack_reference(value):
    return value & 0xFFFF, (value >> 16) & 0xFFFF


def area_id(obj):
    stored = obj.sz_navmesh.area_id
    if stored >= 0:
        return stored
    x, y = navmesh_get_grid_cell(obj)
    return navmesh_grid_get_cell_index(x, y) if x >= 0 and y >= 0 else 10000


def initialize_mesh(mesh, imported=False):
    if mesh.is_editmode:
        raise NavmeshError("Switch to Object Mode before initializing a navmesh.")
    legacy = [a for a in EDGE_ATTRS if (old := mesh.attributes.get(a)) and old.domain == "EDGE"]
    if legacy:
        if len(mesh.vertices) != len(mesh.loops) or len(mesh.edges) != len(mesh.loops):
            raise NavmeshError("Legacy navmesh topology has changed. Reimport its YNV XML before editing.")
        values = {a: [d.value for d in mesh.attributes[a].data] for a in legacy}
        for a in legacy:
            mesh.attributes.remove(mesh.attributes[a])
            mesh_add_navmesh_attribute(mesh, a)
            for loop in mesh.loops:
                mesh.attributes[a].data[loop.index].value = values[a][loop.vertex_index]
        mesh_add_navmesh_attribute(mesh, NavMeshAttr.EDGE_ORIGINAL_POLY)
        for loop in mesh.loops:
            mesh.attributes[NavMeshAttr.EDGE_ORIGINAL_POLY].data[loop.index].value = (
                mesh.attributes[NavMeshAttr.EDGE_ADJACENT_POLY].data[loop.index].value
            )
        imported = True
    for attr in NavMeshAttr:
        existed = attr in mesh.attributes
        mesh_add_navmesh_attribute(mesh, attr)
        if not existed and attr == NavMeshAttr.EDGE_INITIALIZED:
            for datum in mesh.attributes[attr].data:
                datum.value = 1
        if not existed and attr in (NavMeshAttr.EDGE_ADJACENT_POLY, NavMeshAttr.EDGE_ORIGINAL_POLY):
            for datum in mesh.attributes[attr].data:
                datum.value = pack_reference(NONE)
    if imported:
        for poly in mesh.polygons:
            mesh.attributes[NavMeshAttr.POLY_ID].data[poly.index].value = poly.index + 1
        mesh["sz_navmesh_reserved_count"] = len(mesh.polygons)
    mesh["sz_navmesh_schema"] = 2


def vehicle_can_reindex(obj):
    """New standalone meshes have no external polygon IDs to preserve."""
    from ..sollumz_properties import SollumType

    return (
        area_id(obj) == 10000
        and "sz_navmesh_source_polygons" not in obj.data
        and (
            obj.data.get("sz_navmesh_reserved_count", 0) != len(obj.data.polygons)
            or any(
                datum.value != i + 1
                for i, datum in enumerate(obj.data.attributes[NavMeshAttr.POLY_ID].data)
            )
        )
        and not any(
            child.sollum_type == SollumType.NAVMESH_LINK and not child.sz_nav_link.auto_bind
            for child in obj.children_recursive
        )
    )


def polygon_order(mesh, *, allow_reindex=False):
    """Return mesh-face indices in export order without changing any stored ID."""
    attr = mesh.attributes.get(NavMeshAttr.POLY_ID)
    if attr is None:
        raise NavmeshError("Initialize this mesh with Convert to Navmesh before exporting.")
    if allow_reindex:
        if not 1 <= len(mesh.polygons) <= MAX_POLYGONS:
            raise NavmeshError(f"A navmesh must contain 1 to {MAX_POLYGONS} polygons.")
        return list(range(len(mesh.polygons)))
    reserved = int(mesh.get("sz_navmesh_reserved_count", 0))
    slots = {}
    new = []
    for poly in mesh.polygons:
        value = attr.data[poly.index].value
        if value == 0:
            new.append(poly.index)
        elif value < 0 or value > MAX_POLYGONS:
            raise NavmeshError(f"Polygon {poly.index} has an invalid persistent ID.")
        elif value - 1 in slots:
            raise NavmeshError(
                f"Persistent polygon ID {value - 1} is duplicated. Select only the new faces and use Mark as New Polygons."
            )
        else:
            slots[value - 1] = poly.index
    expected = max(reserved, max(slots, default=-1) + 1)
    missing = set(range(expected)) - slots.keys()
    if missing:
        sample = ", ".join(map(str, sorted(missing)[:8]))
        raise NavmeshError(
            f"Persistent polygons were deleted (IDs {sample}). Restore them; renumbering would break sector and portal links."
        )
    order = [slots[i] for i in range(expected)] + new
    if not order or len(order) > MAX_POLYGONS:
        raise NavmeshError(
            f"A navmesh must contain 1ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œ{MAX_POLYGONS} polygons."
        )
    return order


def commit_polygon_ids(mesh, order):
    for exported, face in enumerate(order):
        mesh.attributes[NavMeshAttr.POLY_ID].data[face].value = exported + 1
    mesh["sz_navmesh_reserved_count"] = len(order)


@dataclass
class Edge:
    face: int
    loop: int
    start: Vector
    end: Vector
    adjacent: tuple
    original: tuple
    data0: int
    data1: int

    @property
    def special(self):
        return bool((self.data0 & 2) or ((self.data0 >> 16) & 1))


@dataclass
class Snapshot:
    obj: object
    area: int
    order: list
    vertices: list
    loops: list
    edges: list
    unchanged: set
    source: list


def snapshot(obj):
    if not navmesh_is_valid(obj):
        raise NavmeshError("Select a navigation mesh.")
    mesh = obj.data
    if mesh.is_editmode:
        raise NavmeshError("Switch to Object Mode before validating or exporting navigation meshes.")
    if any(m.show_viewport for m in obj.modifiers):
        raise NavmeshError(
            f"Apply modifiers on '{obj.name}' before exporting; persistent polygon IDs must remain unambiguous."
        )
    if mesh.get("sz_navmesh_schema", 0) != 2:
        raise NavmeshError(
            f"'{obj.name}' uses legacy navmesh attributes. Use Initialize Legacy Navmesh or reimport its XML."
        )
    for attr in NavMeshAttr:
        data = mesh.attributes.get(attr)
        if data is None or data.domain != attr.domain or data.data_type != attr.type:
            raise NavmeshError(
                f"'{obj.name}' has missing or incompatible navmesh attributes. Reimport its XML or convert a mesh copy."
            )
    reindex = vehicle_can_reindex(obj)
    order = polygon_order(mesh, allow_reindex=reindex)
    area = area_id(obj)
    if ("sz_navmesh_source_area" in mesh and mesh["sz_navmesh_source_area"] != area) or (
        "sz_navmesh_linked_area" in obj and obj["sz_navmesh_linked_area"] != area
    ):
        raise NavmeshError("An imported sector Area ID changed. Restore it or convert a copy into a new navmesh.")
    vertices, loops, edges = [], [], []
    source = json.loads(mesh.get("sz_navmesh_source_polygons", "[]"))
    unchanged = set()
    for face_id, mesh_face in enumerate(order):
        poly = mesh.polygons[mesh_face]
        pverts = [
            obj.matrix_world @ mesh.vertices[i].co if area < 10000 else mesh.vertices[i].co.copy()
            for i in poly.vertices
        ]
        ploops = list(poly.loop_indices)
        data2 = mesh.attributes[NavMeshAttr.POLY_DATA_2].data[mesh_face].value
        if data2 & 1 and len(pverts) == 3 and (pverts[-1] - pverts[-2]).length < 1e-7:
            pverts.pop()
            ploops.pop()
        if face_id < len(source):
            saved = source[face_id]["vertices"]
            if len(saved) == len(pverts) and all((v - Vector(old)).length < 1e-6 for v, old in zip(pverts, saved)):
                unchanged.add(face_id)
        if len(pverts) < 3 and not (data2 & 1 and len(pverts) == 2):
            raise NavmeshError(f"Polygon {face_id} must have at least three vertices.")
        if len(pverts) > 15:
            raise NavmeshError(f"Polygon {face_id} has more than 15 vertices. Split it before export.")
        if any(not math.isfinite(c) for v in pverts for c in v):
            raise NavmeshError(f"Polygon {face_id} has a non-finite coordinate.")
        if area < 10000:
            from .navmesh import navmesh_grid_get_cell_bounds

            low, high = navmesh_grid_get_cell_bounds(area % 100, area // 100)
            if any(
                v.x < low.x - TOLERANCE
                or v.x > high.x + TOLERANCE
                or v.y < low.y - TOLERANCE
                or v.y > high.y + TOLERANCE
                for v in pverts
            ):
                raise NavmeshError(f"Polygon {face_id} in '{obj.name}' extends outside sector {area}.")
        if len(pverts) >= 3 and face_id not in unchanged:
            turns = [
                (pverts[(i + 1) % len(pverts)] - v)
                .cross(pverts[(i + 2) % len(pverts)] - pverts[(i + 1) % len(pverts)])
                .z
                for i, v in enumerate(pverts)
            ]
            if max(turns) > 1e-6 and min(turns) < -1e-6:
                raise NavmeshError(f"Polygon {face_id} is concave. Split it into convex faces.")
            if max(turns) <= 1e-6 and min(turns) < -1e-6:
                raise NavmeshError(f"Polygon {face_id} faces downward. Recalculate its normal upward before export.")
            if max(abs(t) for t in turns) < 1e-8 and not data2 & 1:
                raise NavmeshError(f"Polygon {face_id} has no walkable area.")
        vertices.append(pverts)
        loops.append(ploops)
        for i, loop in enumerate(ploops):
            read = lambda attr: mesh.attributes[attr].data[loop].value
            is_new = reindex or mesh.attributes[NavMeshAttr.POLY_ID].data[mesh_face].value == 0 or not read(
                NavMeshAttr.EDGE_INITIALIZED
            )
            adjacent = NONE if is_new else unpack_reference(read(NavMeshAttr.EDGE_ADJACENT_POLY))
            original = NONE if is_new else unpack_reference(read(NavMeshAttr.EDGE_ORIGINAL_POLY))
            for ref in (adjacent, original):
                if ref != NONE and not (0 <= ref[0] <= 10000 and 0 <= ref[1] < MAX_POLYGONS):
                    raise NavmeshError(f"Polygon {face_id} has an invalid adjacency {ref}.")
            edges.append(
                Edge(
                    face_id,
                    loop,
                    pverts[i],
                    pverts[(i + 1) % len(pverts)],
                    adjacent,
                    original,
                    0 if is_new else read(NavMeshAttr.EDGE_DATA_0),
                    0 if is_new else read(NavMeshAttr.EDGE_DATA_1),
                )
            )
    return Snapshot(obj, area, order, vertices, loops, edges, unchanged, source)


def match_edges(snapshots, tolerance=TOLERANCE, *, preserve_unchanged=False):
    """Match opposite directed edges by position, also across unwelded faces."""
    buckets = {}
    all_edges = [(si, ei, edge) for si, snap in enumerate(snapshots) for ei, edge in enumerate(snap.edges)]

    def key(v):
        return math.floor(v.x / tolerance), math.floor(v.y / tolerance), math.floor(v.z / BORDER_HEIGHT_TOLERANCE)

    for si, ei, edge in all_edges:
        buckets.setdefault(key(edge.start), []).append((si, ei, edge))
    matches = {}
    by_area = {snap.area: snap for snap in snapshots}
    for si, ei, edge in all_edges:
        if (edge.start - edge.end).length < tolerance:
            continue
        candidates = []
        target = key(edge.end)
        for offset in product((-1, 0, 1), repeat=3):
            for sj, ej, other in buckets.get(tuple(a + b for a, b in zip(target, offset)), ()):
                if (si, edge.face) == (sj, other.face):
                    continue

                def close(a, b):
                    delta = a - b
                    if si == sj:
                        return delta.length <= tolerance
                    return (
                        abs(delta.x) <= tolerance
                        and abs(delta.y) <= tolerance
                        and abs(delta.z) <= BORDER_HEIGHT_TOLERANCE
                    )

                if close(edge.end, other.start) and close(edge.start, other.end):
                    candidates.append((sj, ej))
        if len(candidates) > 1:
            snap = snapshots[si]
            if (
                preserve_unchanged
                and edge.face in snap.unchanged
                and all(snapshots[sj].edges[ej].face in snapshots[sj].unchanged for sj, ej in candidates)
                and all(
                    ref == NONE or ref[0] not in by_area or ref[1] in by_area[ref[0]].unchanged
                    for ref in (edge.adjacent, edge.original)
                )
            ):
                continue
            raise NavmeshError(
                f"Ambiguous edge on '{snapshots[si].obj.name}', polygon {edge.face}; more than one adjacent face."
            )
        if candidates:
            matches[si, ei] = candidates[0]
    return matches


def border_signature(snap):
    return [
        [e.face, list(e.start), list(e.end), list(e.adjacent), list(e.original)]
        for e in snap.edges
        if any(r != NONE and r[0] != snap.area for r in (e.adjacent, e.original))
    ]


def remember_borders(obj):
    snap = snapshot(obj)
    obj.sz_navmesh.border_snapshot = json.dumps(border_signature(snap))
    obj["sz_navmesh_linked_area"] = snap.area


def validate_preserved_borders(snap):
    baseline = json.loads(snap.obj.sz_navmesh.border_snapshot or "[]")
    by_face = {}
    for edge in snap.edges:
        by_face.setdefault(edge.face, []).append(edge)
    for face, start, end, adjacent, original in baseline:
        if not any(
            (e.start - Vector(start)).length <= TOLERANCE
            and (e.end - Vector(end)).length <= TOLERANCE
            and e.adjacent == tuple(adjacent)
            and e.original == tuple(original)
            for e in by_face.get(face, [])
        ):
            raise NavmeshError(
                f"Border polygon {face} in '{snap.obj.name}' changed. Load the linked sectors and rebuild their links before exporting."
            )


def polygon_lies_along_edge(snap, face, references):
    """Identify boundary edges, including unlinked ones (IdentifyEdgePolys)."""
    if snap.area < 10000:
        from .navmesh import navmesh_grid_get_cell_bounds

        low, high = navmesh_grid_get_cell_bounds(snap.area % 100, snap.area // 100)
    else:
        vertices = [v for polygon in snap.vertices for v in polygon]
        low = Vector(tuple(min(v[i] for v in vertices) for i in range(3)))
        high = Vector(tuple(max(v[i] for v in vertices) for i in range(3)))
    vertices = snap.vertices[face]
    return any(
        ref[0] != snap.area
        and any(
            abs(start[axis] - bound[axis]) <= TOLERANCE
            and abs(end[axis] - bound[axis]) <= TOLERANCE
            for axis in (0, 1) for bound in (low, high)
        )
        for start, end, ref in zip(vertices, vertices[1:] + vertices[:1], references)
    )


def local_references(snap):
    matches = match_edges([snap], preserve_unchanged=True)
    result = {}
    for i, edge in enumerate(snap.edges):
        adjacent, original = edge.adjacent, edge.original
        match = matches.get((0, i))
        preserve = (
            edge.face in snap.unchanged
            and (match is None or snap.edges[match[1]].face in snap.unchanged)
            and all(r == NONE or r[0] != snap.area or r[1] in snap.unchanged for r in (adjacent, original))
        )
        if not edge.special and not preserve:
            local = (snap.area, snap.edges[match[1]].face) if match else NONE
            if adjacent == NONE or adjacent[0] == snap.area:
                adjacent = local
            if original == NONE or original[0] == snap.area:
                original = local
        for ref in (adjacent, original):
            if ref != NONE and ref[0] == snap.area and ref[1] >= len(snap.order):
                raise NavmeshError(f"Polygon {edge.face} references missing polygon {ref[1]}.")
        result[edge.loop] = adjacent, original
    return result


def rebuild_links(objects):
    """Prepare all changes before updating any mesh; never renumber old faces."""
    snaps = [snapshot(obj) for obj in objects]
    if not snaps:
        raise NavmeshError("No navigation meshes are loaded.")
    if len({s.area for s in snaps}) != len(snaps):
        raise NavmeshError("More than one loaded navmesh has the same Area ID. Select one sector set at a time.")
    matches = match_edges(snaps, preserve_unchanged=True)
    areas = {s.area for s in snaps}
    by_area = {s.area: s for s in snaps}
    changes = []
    for si, snap in enumerate(snaps):
        baseline = json.loads(snap.obj.sz_navmesh.border_snapshot or "[]")
        for face, start, end, current, original in baseline:
            untouched = any(
                e.face == face
                and (e.start - Vector(start)).length <= TOLERANCE
                and (e.end - Vector(end)).length <= TOLERANCE
                and e.adjacent == tuple(current)
                and e.original == tuple(original)
                for e in snap.edges
            )
            required = {r[0] for r in (current, original) if tuple(r) != NONE and r[0] != snap.area}
            if not untouched and (missing := required - areas):
                raise NavmeshError(f"Load linked sectors {sorted(missing)} before rebuilding '{snap.obj.name}'.")
        for ei, edge in enumerate(snap.edges):
            if edge.special:
                continue
            found = matches.get((si, ei))
            if (
                edge.face in snap.unchanged
                and (found is None or snaps[found[0]].edges[found[1]].face in snaps[found[0]].unchanged)
                and all(
                    r == NONE or r[0] not in by_area or r[1] in by_area[r[0]].unchanged
                    for r in (edge.adjacent, edge.original)
                )
            ):
                continue
            external = [r for r in (edge.adjacent, edge.original) if r != NONE and r[0] != snap.area]
            if external and all(r[0] not in areas for r in external):
                continue
            if (
                external
                and edge.face in snap.unchanged
                and all(r[0] not in by_area or r[1] in by_area[r[0]].unchanged for r in external)
            ):
                continue
            if found:
                other, index = found
                ref = snaps[other].area, snaps[other].edges[index].face
                data1 = edge.data1 | (4 << 16) if ref[0] != snap.area else edge.data1 & ~(4 << 16)
            else:
                if any(r != NONE and r[0] != snap.area and r[0] in areas for r in (edge.adjacent, edge.original)):
                    raise NavmeshError(
                        f"Border polygon {edge.face} in '{snap.obj.name}' has no matching edge in its loaded neighbour. Align both border edges before rebuilding."
                    )
                ref = NONE
                data1 = edge.data1 & ~(4 << 16)
                if any(r != NONE and r[0] != snap.area and r[0] not in areas for r in (edge.adjacent, edge.original)):
                    raise NavmeshError(f"Load the sector referenced by polygon {edge.face} before rebuilding.")
            changes.append(
                (snap.obj.data, edge.loop, ref, reference_flags(edge.data0, ref), reference_flags(data1, ref))
            )
    for mesh, loop, ref, data0, data1 in changes:
        mesh.attributes[NavMeshAttr.EDGE_INITIALIZED].data[loop].value = 1
        for attr in (NavMeshAttr.EDGE_ADJACENT_POLY, NavMeshAttr.EDGE_ORIGINAL_POLY):
            mesh.attributes[attr].data[loop].value = pack_reference(ref)
        mesh.attributes[NavMeshAttr.EDGE_DATA_0].data[loop].value = signed_int(data0)
        mesh.attributes[NavMeshAttr.EDGE_DATA_1].data[loop].value = signed_int(data1)
    for snap in snaps:
        commit_polygon_ids(snap.obj.data, snap.order)
        for face, mesh_face in enumerate(snap.order):
            if face in snap.unchanged:
                border = bool(snap.source[face]["flags"][2] & 4)
            else:
                border = polygon_lies_along_edge(snap, face, [
                    unpack_reference(snap.obj.data.attributes[NavMeshAttr.EDGE_ADJACENT_POLY].data[loop].value)
                    for loop in snap.loops[face]
                ])
            datum = snap.obj.data.attributes[NavMeshAttr.POLY_DATA_1].data[mesh_face]
            datum.value = datum.value | 4 if border else datum.value & ~4
        remember_borders(snap.obj)
        snap.obj.data.update()
    return sum(1 for (si, _), (sj, _) in matches.items() if si != sj) // 2
