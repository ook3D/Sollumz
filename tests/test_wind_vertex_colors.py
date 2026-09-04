import bmesh
import bpy
import numpy as np
import pytest
from numpy.testing import assert_allclose

from ..editor_tools.vertex_paint.wind import compute_geodesic_from_root
from ..tools.meshhelper import get_color_attr_name


def _grid_object(context, name="tree"):
    """A 4-vertex vertical strip: (0,0,0)-(0,0,1)-(0,0,2)-(0,0,3), each edge 1 unit long."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (0.0, 0.5, 1.0), (0.0, 0.0, 2.0), (0.0, 0.5, 3.0)],
        [],
        [(0, 1, 2), (1, 2, 3)],
    )
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    return obj


def _geodesic(obj, root_z_threshold=0.05):
    mesh = obj.data
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(-1, 3)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        return coords, compute_geodesic_from_root(bm, coords, root_z_threshold)
    finally:
        bm.free()


def test_geodesic_grows_away_from_root(context):
    obj = _grid_object(context)
    try:
        _, dist = _geodesic(obj)
        assert dist[0] == 0.0  # lowest vertex is the root
        assert dist[1] < dist[3]  # further up the strip is further from the root
        assert all(np.isfinite(dist))
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)


def test_geodesic_bridges_disconnected_island(context):
    """A leaf card not welded to the trunk must still get a finite distance."""
    obj = _grid_object(context)
    mesh = obj.data
    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        island = [bm.verts.new(co) for co in ((5.0, 0.0, 3.0), (5.0, 1.0, 3.0), (5.0, 0.0, 4.0))]
        bm.faces.new(island)
        bm.to_mesh(mesh)
        bm.free()

        _, dist = _geodesic(obj)
        assert all(np.isfinite(dist))
        assert dist[4] > dist[3]  # bridged island is further out than the tip it bridged from
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)


@pytest.mark.parametrize("phase_mode", ("HASH", "WAVE"))
def test_ops_bake_wind_vertex_colors(context, phase_mode):
    obj = _grid_object(context)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        assert bpy.ops.sollumz.bake_wind_vertex_colors(phase_mode=phase_mode, brightness=1.0) == {"FINISHED"}

        mesh = obj.data
        for color_idx in (0, 1):
            attr = mesh.color_attributes[get_color_attr_name(color_idx)]
            assert attr.domain == "CORNER"
            assert attr.data_type == "BYTE_COLOR"

        colors = np.empty((len(mesh.loops), 4), dtype=np.float32)
        mesh.color_attributes[get_color_attr_name(0)].data.foreach_get("color_srgb", colors.ravel())

        loop_vert = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", loop_vert)

        # Root vertex is fully stiff with no sway, and shared vertices agree on every corner
        root_loops = loop_vert == 0
        assert_allclose(colors[root_loops, 0], 1.0, atol=1 / 255)  # stiffness
        assert_allclose(colors[root_loops, 2], 0.0, atol=1 / 255)  # amplitude
        for vi in np.unique(loop_vert):
            corners = colors[loop_vert == vi]
            assert_allclose(corners, corners[0])

        # Stiffness falls off away from the root, amplitude rises
        assert colors[loop_vert == 3, 0][0] < colors[loop_vert == 1, 0][0]
        assert colors[loop_vert == 3, 2][0] > colors[loop_vert == 1, 2][0]
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
