import heapq

import bmesh
import numpy as np
from bpy.props import EnumProperty, FloatProperty
from bpy.types import Operator
from mathutils.kdtree import KDTree

from ...tools.meshhelper import create_color_attr, get_color_attr_name


def hash01(coords):
    """Deterministic pseudo-random 0..1 per position, to desync wind phase between vertices/leaf
    cards that sit close together but aren't mesh-connected."""
    x = np.sin(coords[:, 0] * 12.9898 + coords[:, 1] * 78.233 + coords[:, 2] * 37.719) * 43758.5453
    return x - np.floor(x)


def compute_geodesic_from_root(bm, coords, root_z_threshold):
    """Mesh-distance (edge-walked) from each vertex to the nearest root vertex, as a numpy array.

    Roots are the vertices within ``root_z_threshold`` of the mesh's lowest point (the trunk base).
    Islands with no root in them get bridged, each in one shot, to the nearest vertex the root-walk
    already solved.
    """
    bm.verts.ensure_lookup_table()
    n = len(bm.verts)
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    min_z = float(coords[:, 2].min())
    root_idx = np.nonzero(coords[:, 2] <= min_z + root_z_threshold)[0]
    if root_idx.size == 0:
        root_idx = np.array([int(np.argmin(coords[:, 2]))])

    INF = float("inf")
    dist = [INF] * n
    heap = []
    for i in root_idx:
        dist[int(i)] = 0.0
        heapq.heappush(heap, (0.0, int(i)))

    def dijkstra():
        while heap:
            d, vi = heapq.heappop(heap)
            if d > dist[vi]:
                continue
            v = bm.verts[vi]
            for e in v.link_edges:
                oi = e.other_vert(v).index
                nd = d + e.calc_length()
                if nd < dist[oi]:
                    dist[oi] = nd
                    heapq.heappush(heap, (nd, oi))

    dijkstra()

    unresolved_idx = [i for i in range(n) if dist[i] == INF]
    if unresolved_idx:
        resolved_idx = [i for i in range(n) if dist[i] != INF]
        if not resolved_idx:
            for i in unresolved_idx:
                dist[i] = 0.0
        else:
            tree = KDTree(len(resolved_idx))
            for i, vi in enumerate(resolved_idx):
                tree.insert(coords[vi], i)
            tree.balance()

            unresolved_set = set(unresolved_idx)
            comp_seen = [False] * n

            for start in unresolved_idx:
                if comp_seen[start]:
                    continue

                comp_seen[start] = True
                comp_verts = [start]
                stack = [start]
                while stack:
                    cur = stack.pop()
                    for e in bm.verts[cur].link_edges:
                        oi = e.other_vert(bm.verts[cur]).index
                        if oi in unresolved_set and not comp_seen[oi]:
                            comp_seen[oi] = True
                            comp_verts.append(oi)
                            stack.append(oi)

                best = None  # (dist, entry_vert_idx, target_vert_idx)
                for vi in comp_verts:
                    _, ri, d = tree.find(coords[vi])
                    if best is None or d < best[0]:
                        best = (d, vi, resolved_idx[ri])

                bridge_d, entry_vi, target_vi = best
                dist[entry_vi] = dist[target_vi] + bridge_d
                heapq.heappush(heap, (dist[entry_vi], entry_vi))

            dijkstra()

    return np.asarray(dist, dtype=np.float64)


def wind_colors(coords, dist, props):
    """Wind channel values in vertex order, as (Color 1 RGB, Color 2 RGB) numpy arrays."""
    max_d = props.max_distance if props.max_distance > 0.0 else float(dist.max(initial=0.0))
    max_d = max(max_d, 1e-6)

    t = np.clip(dist / max_d, 0.0, 1.0)
    looseness = t**props.stiffness_curve
    stiffness = 1.0 - looseness

    if props.phase_mode == "WAVE":
        phase = np.mod(t * props.phase_frequency, 1.0)
    else:
        phase = hash01(coords)

    amplitude = looseness * props.amplitude
    stiffness = stiffness * props.brightness
    phase = phase * props.brightness
    amplitude = amplitude * props.brightness

    color1 = np.stack((stiffness, phase, amplitude), axis=-1)
    color2 = np.stack((stiffness, stiffness, stiffness), axis=-1)
    return color1, color2


def write_wind_colors(mesh, coords, dist, props):
    """Write the wind channels onto mesh Color 1 and Color 2 attributes."""
    color1, color2 = wind_colors(coords, dist, props)

    loop_vert = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_vert)

    for color_idx, values in ((0, color1), (1, color2)):
        name = get_color_attr_name(color_idx)
        attr = mesh.color_attributes.get(name)
        if attr is None:
            create_color_attr(mesh, color_idx)
            attr = mesh.color_attributes[name]

        loop_colors = np.ones((len(loop_vert), 4), dtype=np.float32)
        loop_colors[:, :3] = values[loop_vert]
        attr.data.foreach_set("color_srgb", loop_colors.ravel())

    mesh.update()


class SOLLUMZ_OT_bake_wind_vertex_colors(Operator):
    bl_idname = "sollumz.bake_wind_vertex_colors"
    bl_label = "Bake Wind Vertex Colors"
    bl_description = (
        "Bake tree shader wind vertex colors (stiffness, sway amplitude, phase) on the selected "
        "meshes, derived from each vertex's mesh-distance to the trunk root"
    )
    bl_options = {"REGISTER", "UNDO"}

    root_z_threshold: FloatProperty(
        name="Root Threshold",
        description="Vertices within this height of the mesh's lowest point are treated as the trunk root",
        default=0.05,
        min=0.0,
    )
    max_distance: FloatProperty(
        name="Falloff Distance",
        description=(
            "Mesh-distance at which stiffness reaches zero and amplitude reaches its max. "
            "0 = auto (the furthest vertex found)"
        ),
        default=0.0,
        min=0.0,
    )
    stiffness_curve: FloatProperty(
        name="Falloff Curve",
        description="Exponent on the normalized distance. >1 keeps branches stiffer for longer, <1 loosens them sooner",
        default=1.0,
        min=0.01,
        max=8.0,
    )
    amplitude: FloatProperty(
        name="Amplitude",
        description=(
            "Sway amplitude at full looseness (tip). The shader shares one amplitude channel for "
            "horizontal and vertical sway"
        ),
        default=0.5,
        min=0.0,
        max=1.0,
    )
    brightness: FloatProperty(
        name="Brightness",
        description=(
            "Uniform multiplier over all 3 channels. Lowering this also weakens the baked stiffness "
            "and compresses the phase, not just how the colors look"
        ),
        default=1.0,
        min=0.0,
        max=1.0,
    )
    phase_mode: EnumProperty(
        name="Phase Mode",
        items=(
            ("HASH", "Position Hash", "Decorrelate phase per-vertex so nearby disconnected leaf cards don't sway in sync"),
            ("WAVE", "Distance Wave", "Derive phase from distance-to-root: a wave traveling outward along each branch"),
        ),
        default="HASH",
    )
    phase_frequency: FloatProperty(
        name="Wave Frequency",
        description="Number of wave cycles between the root and the falloff distance. Only used in Distance Wave mode",
        default=4.0,
        min=0.0,
    )

    @classmethod
    def poll(cls, context):
        # Edit mode keeps the mesh in its own edit-mode copy, so the bake wouldn't stick
        if context.mode not in {"OBJECT", "PAINT_VERTEX"}:
            cls.poll_message_set("Must be in object or vertex paint mode")
            return False
        return any(obj.type == "MESH" for obj in cls._target_objects(context))

    @staticmethod
    def _target_objects(context):
        objs = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not objs and (obj := context.active_object) and obj.type == "MESH":
            # In vertex paint mode the painted object isn't necessarily "selected"
            objs = [obj]
        return objs

    def execute(self, context):
        num_baked = 0
        for obj in self._target_objects(context):

            mesh = obj.data
            num_verts = len(mesh.vertices)
            if num_verts == 0:
                continue

            bad_attrs = [
                name
                for color_idx in (0, 1)
                if (name := get_color_attr_name(color_idx)) in mesh.color_attributes
                and mesh.color_attributes[name].domain != "CORNER"
            ]
            if bad_attrs:
                self.report(
                    {"WARNING"},
                    f"Skipped '{obj.name}', color attributes {bad_attrs} are not on the face corner domain",
                )
                continue

            coords = np.empty(num_verts * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", coords)
            coords = coords.reshape(num_verts, 3)

            bm = bmesh.new()
            bm.from_mesh(mesh)
            try:
                dist = compute_geodesic_from_root(bm, coords, self.root_z_threshold)
            finally:
                bm.free()

            write_wind_colors(mesh, coords, dist, self)
            num_baked += 1

        if num_baked == 0:
            self.report({"WARNING"}, "No meshes to bake")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Baked wind vertex colors on {num_baked} object(s)")
        return {"FINISHED"}
