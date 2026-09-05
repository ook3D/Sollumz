import bmesh
import bpy
import pytest
from mathutils import Vector

from szio.gta5.cwxml import Navmesh as NavMesh, NavPolygon, YNV
from ..sollumz_properties import SollumType
from ..ynv.authoring import new_navmesh, create_portal, import_neighbors, activate
from ..ynv.navmesh_attributes import NavMeshAttr, mesh_get_navmesh_edge_attributes, mesh_set_navmesh_edge_attributes
from ..ynv.navmesh_topology import (
    NONE,
    NavmeshError,
    polygon_order,
    rebuild_links,
)
from ..ynv.ynvimport import navmesh_to_obj, import_ynv
from ..ynv.ynvexport import navmesh_from_object, export_ynv


@pytest.fixture(autouse=True)
def clean_nav_scene():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    before = set(bpy.data.objects)
    meshes = set(bpy.data.meshes)
    yield
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in set(bpy.data.objects) - before:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in set(bpy.data.meshes) - meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def quad(x, y, z=0, size=10):
    return [(x, y, z), (x + size, y, z), (x + size, y + size, z), (x, y + size, z)]


def test_point_and_portal_properties_preserve_all_byte_values():
    obj = bpy.data.objects.new("raw_types", None)
    for value in range(256):
        obj.sz_nav_link.set_raw_int(value)
        obj.sz_nav_cover_point.set_raw_int(value)
        assert obj.sz_nav_link.get_raw_int() == value
        assert obj.sz_nav_cover_point.get_raw_int() == value


@pytest.mark.parametrize("protected", [None, "imported", "manual_portal"])
def test_vehicle_deleted_starter_polygon(tmp_path, protected):
    obj = make_nav([quad(20, 0), quad(0, 0), quad(0, 0, z=3)])
    if protected == "imported":
        obj = navmesh_to_obj(navmesh_from_object(obj), str(tmp_path / "vehicle.ynv.xml"))
    portal = create_portal(obj, Vector((5, 5, 0)), Vector((5, 5, 3)))
    portal.sz_nav_link.auto_bind = protected != "manual_portal"
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces.remove(bm.faces[0])
    bm.to_mesh(obj.data)
    bm.free()
    bpy.context.view_layer.update()
    path = str(tmp_path / "edited.ynv.xml")
    if protected:
        with pytest.raises(NavmeshError, match="deleted"):
            export_ynv(obj, path)
    else:
        assert export_ynv(obj, path)
        nav = YNV.from_xml_file(path)
        assert len(nav.polygons) == 2
        assert (nav.portals[0].poly_from, nav.portals[0].poly_to) == (0, 1)
        assert "Vehicle" in nav.content_flags
        assert export_ynv(obj, path)


@pytest.mark.parametrize("portal_type", [0, 1, 2, 3, 254, 255])
def test_raw_types_survive_xml_import_export(tmp_path, portal_type):
    from szio.gta5.cwxml import NavPoint

    obj = make_nav([quad(0, 0), quad(0, 0, z=3)])
    create_portal(obj, Vector((5, 5, 0)), Vector((5, 5, 3)))
    bpy.context.view_layer.update()
    nav = navmesh_from_object(obj)
    nav.portals[0].type = portal_type
    point = NavPoint()
    point.type = 254
    point.angle = 0
    point.position = Vector((5, 5, 0))
    nav.points.append(point)
    source = str(tmp_path / "source.ynv.xml")
    nav.write_xml(source)
    imported = import_ynv(source)
    bpy.context.view_layer.update()
    dest = str(tmp_path / "roundtrip.ynv.xml")
    assert export_ynv(imported, dest)
    result = YNV.from_xml_file(dest)
    assert result.portals[0].type == portal_type
    assert result.points[0].type == 254


def make_nav(faces, aid=10000):
    vertices = [v for f in faces for v in f]
    indices, start = [], 0
    for f in faces:
        indices.append(tuple(range(start, start + len(f))))
        start += len(f)
    mesh = bpy.data.meshes.new("test_nav")
    mesh.from_pydata(vertices, [], indices)
    obj = new_navmesh(bpy.context, mesh, aid)
    bpy.context.view_layer.update()
    return obj


def linked_pair():
    left = make_nav([quad(100, 20), quad(140, 0)], 4040)
    right = make_nav([quad(150, 0)], 4041)
    assert rebuild_links([left, right]) == 1
    return left, right


def refs(poly):
    return [
        [tuple(map(int, pair.strip().split(":"))) for pair in line.split(",")]
        for line in poly.edges.strip().splitlines()
    ]


def test_new_mesh_has_stable_ids_and_corner_attributes():
    obj = make_nav([quad(0, 0)])
    assert polygon_order(obj.data) == [0]
    for attr in (NavMeshAttr.EDGE_DATA_0, NavMeshAttr.EDGE_ADJACENT_POLY, NavMeshAttr.EDGE_ORIGINAL_POLY):
        assert obj.data.attributes[attr].domain == "CORNER"
    nav = navmesh_from_object(obj)
    assert all(component > 0 for component in nav.bb_size)
    assert refs(nav.polygons[0]) == [[NONE, NONE]] * 4


def test_shared_vertex_adjacency_is_directed():
    mesh = bpy.data.meshes.new("shared")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2), (0, 2, 3)])
    obj = new_navmesh(bpy.context, mesh, 10000)
    nav = navmesh_from_object(obj)
    assert refs(nav.polygons[0])[2] == [(10000, 1)] * 2
    assert refs(nav.polygons[1])[0] == [(10000, 0)] * 2


def test_cross_sector_links_reciprocal_and_original_ids_stable():
    left, right = linked_pair()
    a, b = navmesh_from_object(left), navmesh_from_object(right)
    assert refs(a.polygons[1])[1] == [(4041, 0)] * 2
    assert refs(b.polygons[0])[3] == [(4040, 1)] * 2
    assert int(a.polygons[1].flags.split()[2]) & 4


def test_reordering_faces_does_not_renumber_border_references():
    left, right = linked_pair()
    bm = bmesh.new()
    bm.from_mesh(left.data)
    bm.faces.sort(key=lambda f: -f.index)
    bm.to_mesh(left.data)
    bm.free()
    assert polygon_order(left.data) == [1, 0]
    a, b = navmesh_from_object(left), navmesh_from_object(right)
    assert a.polygons[1].vertices[0].x == 140
    assert refs(b.polygons[0])[3] == [(4040, 1)] * 2


def test_append_new_face_keeps_existing_border_indices():
    left, right = linked_pair()
    bm = bmesh.new()
    bm.from_mesh(left.data)
    bm.faces.new([bm.verts.new(v) for v in quad(80, 20)])
    bm.to_mesh(left.data)
    bm.free()
    assert len(navmesh_from_object(left).polygons) == 3
    assert refs(navmesh_from_object(right).polygons[0])[3] == [(4040, 1)] * 2


@pytest.mark.parametrize("change", ["delete", "duplicate"])
def test_ambiguous_or_missing_persistent_ids_are_rejected(change):
    obj = make_nav([quad(0, 0), quad(20, 0)], 4040)
    if change == "delete":
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.faces.remove(bm.faces[0])
        bm.to_mesh(obj.data)
        bm.free()
    else:
        obj.data.attributes[NavMeshAttr.POLY_ID].data[1].value = 1
    with pytest.raises(NavmeshError, match="deleted|duplicated"):
        navmesh_from_object(obj)


def test_unloaded_neighbor_references_are_preserved(tmp_path):
    left, right = linked_pair()
    expected = navmesh_from_object(left)
    # Unload the neighbour while retaining the imported reference values.
    for child in list(right.children_recursive):
        bpy.data.objects.remove(child, do_unlink=True)
    bpy.data.objects.remove(right, do_unlink=True)
    out = tmp_path / "left.ynv.xml"
    assert export_ynv(left, str(out))
    actual = YNV.from_xml_file(str(out))
    assert actual.polygons[1].edges.split() == expected.polygons[1].edges.split()


def test_changed_border_requires_rebuild_with_neighbors():
    left, right = linked_pair()
    left.data.vertices[5].co.y += 1
    with pytest.raises(NavmeshError, match="Border polygon"):
        navmesh_from_object(left)
    with pytest.raises(NavmeshError, match="Load linked sectors"):
        rebuild_links([left])


def test_distinct_current_original_and_unsigned_flags_roundtrip(tmp_path):
    nav = NavMesh()
    nav.area_id = 10000
    nav.content_flags = "Polygons, Vehicle"
    p = NavPolygon()
    p.vertices = [Vector(v) for v in quad(0, 0)]
    p.flags = "5 12 0 128 120 121 0"
    p.edges = "\n".join(["4040:12, 4041:13"] + ["16383:16383, 16383:16383"] * 3)
    p.edges_flags = "\n".join(["2:65535, 3:32768"] + ["0:0, 0:0"] * 3)
    nav.polygons.append(p)
    obj = navmesh_to_obj(nav, str(tmp_path / "raw.ynv.xml"))
    actual = navmesh_from_object(obj).polygons[0]
    assert actual.edges.split() == p.edges.split()
    assert actual.edges_flags.split() == p.edges_flags.split()
    assert actual.flags == p.flags


def test_dlc_two_vertex_polygon_roundtrip(tmp_path):
    nav = NavMesh()
    nav.area_id = 10000
    nav.content_flags = "Polygons, Vehicle, Unknown16"
    p = NavPolygon()
    p.vertices = [Vector((0, 0, 0)), Vector((0, 2, 0))]
    p.flags = "0 0 0 0 0 0 1"
    p.edges = "16383:16383, 16383:16383\n16383:16383, 16383:16383"
    p.edges_flags = "0:0, 0:0\n0:0, 0:0"
    nav.polygons.append(p)
    obj = navmesh_to_obj(nav, str(tmp_path / "stitch.ynv.xml"))
    actual = navmesh_from_object(obj).polygons[0]
    assert len(actual.vertices) == 2
    assert actual.edges.split() == p.edges.split()


def test_portal_binding_and_polygon_portal_lists(tmp_path):
    obj = make_nav([quad(0, 0), quad(0, 0, z=3)])
    create_portal(obj, Vector((5, 5, 0)), Vector((5, 5, 3)))
    bpy.context.view_layer.update()
    nav = navmesh_from_object(obj)
    assert (nav.portals[0].poly_from, nav.portals[0].poly_to) == (0, 1)
    assert [p.portals for p in nav.polygons] == ["0", "0"]
    path = tmp_path / "portal.ynv.xml"
    assert export_ynv(obj, str(path))
    loaded = YNV.from_xml_file(str(path))
    assert loaded.polygons[0].portals == "0"
    assert loaded.portals[0].type == 1


def test_invalid_portal_does_not_overwrite_existing_file(tmp_path):
    obj = make_nav([quad(0, 0)])
    create_portal(obj, Vector((5, 5, 0)), Vector((5, 5, 20)))
    bpy.context.view_layer.update()
    path = tmp_path / "preserve.ynv.xml"
    path.write_text("previous export")
    with pytest.raises(NavmeshError, match="too far"):
        export_ynv(obj, str(path))
    assert path.read_text() == "previous export"


def test_portal_positions_follow_parent_transform_for_map():
    obj = make_nav([quad(10, 10), quad(10, 10, z=3)], 4040)
    create_portal(obj, Vector((15, 15, 0)), Vector((15, 15, 3)))
    obj.location.x = 5
    bpy.context.view_layer.update()
    nav = navmesh_from_object(obj)
    assert nav.portals[0].position_from.x == 20
    assert nav.polygons[0].vertices[0].x == 15


def test_import_neighbors_uses_area_id_and_skips_loaded(tmp_path):
    left, right = linked_pair()
    expected = navmesh_from_object(right)
    expected.write_xml(str(tmp_path / "arbitrary-name.ynv.xml"))
    for c in list(right.children_recursive):
        bpy.data.objects.remove(c, do_unlink=True)
    bpy.data.objects.remove(right, do_unlink=True)
    result = import_neighbors(left, str(tmp_path))
    assert len(result) == 1
    assert result[0].sz_navmesh.area_id == 4041
    assert import_neighbors(left, str(tmp_path)) == []
    navmesh_from_object(left)


def test_malformed_import_rolls_back_objects(tmp_path):
    path = tmp_path / "bad.ynv.xml"
    path.write_text('<NavMesh><AreaID value="10000"/><Polygons><Item><Flags>0</Flags></Item></Polygons></NavMesh>')
    before = set(bpy.data.objects)
    with pytest.raises(NavmeshError):
        import_ynv(str(path))
    assert set(bpy.data.objects) == before


def test_create_navmesh_portal_and_cover_operators():
    bpy.context.scene.cursor.location = (5, 5, 0)
    assert bpy.ops.sollumz.navmesh_create(standalone=True) == {"FINISHED"}
    root = bpy.context.active_object
    assert root.sz_navmesh.area_id == 10000
    assert bpy.ops.sollumz.navmesh_create_cover() == {"FINISHED"}
    assert navmesh_from_object(root).points[0].position.length < 1e-5
    activate(bpy.context, root)
    assert bpy.ops.sollumz.navmesh_create_portal() == {"FINISHED"}
    assert bpy.context.active_object.sollum_type == SollumType.NAVMESH_LINK
    assert len(bpy.context.active_object.children) == 1


def test_sector_set_export_validates_all_before_writing(tmp_path):
    left, right = linked_pair()
    activate(bpy.context, left)
    assert bpy.ops.sollumz.navmesh_export_set(directory=str(tmp_path)) == {"FINISHED"}
    paths = sorted(tmp_path.glob("*.ynv.xml"))
    assert len(paths) == 2
    saved = {p.name: p.read_bytes() for p in paths}
    right.data.attributes[NavMeshAttr.POLY_ID].data[0].value = 2
    with pytest.raises(RuntimeError, match="deleted"):
        bpy.ops.sollumz.navmesh_export_set(directory=str(tmp_path))
    assert {p.name: p.read_bytes() for p in paths} == saved


def test_nonmanifold_adjacency_is_rejected():
    obj = make_nav([quad(0, 0), quad(10, 0), quad(10, 0)])
    with pytest.raises(NavmeshError, match="Ambiguous"):
        navmesh_from_object(obj)


def test_new_concave_polygon_is_rejected():
    obj = make_nav([[(0, 0, 0), (4, 0, 0), (1, 1, 0), (4, 4, 0), (0, 4, 0)]])
    with pytest.raises(NavmeshError, match="concave"):
        navmesh_from_object(obj)


def test_empty_active_face_access_is_safe():
    obj = make_nav([quad(0, 0)])
    activate(bpy.context, obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.active = None
    assert obj.data.sz_navmesh_poly_access.active_poly == -1
    assert obj.data.sz_navmesh_poly_access.active_poly_attributes.is_water is False
    bpy.ops.object.mode_set(mode="OBJECT")


def test_unlinked_edges_encode_engine_sentinel_high_bit():
    obj = make_nav([quad(0, 0)])
    exported = navmesh_from_object(obj)
    assert exported.polygons[0].edges_flags.splitlines() == ["1:0, 1:0"] * 4


def test_rebuild_preserves_unloaded_outer_neighbors():
    a = make_nav([quad(140, 0)], 4040)
    b = make_nav([quad(150, 0), quad(290, 0)], 4041)
    c = make_nav([quad(300, 0)], 4042)
    assert rebuild_links([a, b, c]) == 2
    for child in list(c.children_recursive):
        bpy.data.objects.remove(child, do_unlink=True)
    bpy.data.objects.remove(c, do_unlink=True)
    assert rebuild_links([a, b]) == 1
    assert refs(navmesh_from_object(b).polygons[1])[1] == [(4042, 0)] * 2


def test_rebuild_rejects_misaligned_border_without_partial_mutation():
    left, right = linked_pair()
    before = [d.value for d in left.data.attributes[NavMeshAttr.EDGE_ADJACENT_POLY].data]
    right.location.y = 2
    bpy.context.view_layer.update()
    with pytest.raises(NavmeshError, match="no matching edge"):
        rebuild_links([left, right])
    assert [d.value for d in left.data.attributes[NavMeshAttr.EDGE_ADJACENT_POLY].data] == before


def test_duplicate_loaded_sector_ids_are_rejected():
    a = make_nav([quad(10, 10)], 4040)
    make_nav([quad(30, 30)], 4040)
    with pytest.raises(NavmeshError, match="More than one"):
        navmesh_from_object(a)


def test_edit_mode_flag_tools_leave_blenders_bmesh_valid():
    obj = make_nav([quad(0, 0, size=1)])
    activate(bpy.context, obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces.active = bm.faces[0]
    assert bpy.ops.sollumz.navmesh_polys_update_flags() == {"FINISHED"}
    assert obj.data.sz_navmesh_poly_access.active_poly_attributes.is_small
    assert bpy.ops.sollumz.navmesh_polys_select_similar() == {"FINISHED"}
    assert bmesh.from_edit_mesh(obj.data).is_valid
    bpy.ops.object.mode_set(mode="OBJECT")


def test_corner_editor_preserves_other_side_on_shared_edge():
    mesh = bpy.data.meshes.new("shared")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2), (0, 2, 3)])
    obj = new_navmesh(bpy.context, mesh, 10000)
    activate(bpy.context, obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.edges.index_update()
    bm.faces.active = bm.faces[0]
    edge = next(e for e in bm.edges if len(e.link_faces) == 2)
    attrs = mesh_get_navmesh_edge_attributes(mesh, edge.index)
    attrs.data01 = 123
    mesh_set_navmesh_edge_attributes(mesh, edge.index, attrs)
    bm.faces.active = bm.faces[1]
    assert mesh_get_navmesh_edge_attributes(mesh, edge.index).data01 == 0
    bm.faces.active = bm.faces[0]
    assert mesh_get_navmesh_edge_attributes(mesh, edge.index).data01 == 123
    bpy.ops.object.mode_set(mode="OBJECT")


def test_new_polygon_connects_reciprocally_to_unchanged_imported_face(tmp_path):
    original = make_nav([quad(0, 0)])
    xml = navmesh_from_object(original)
    imported = navmesh_to_obj(xml, str(tmp_path / "imported.ynv.xml"))
    bm = bmesh.new()
    bm.from_mesh(imported.data)
    bm.faces.new([bm.verts.new(v) for v in quad(10, 0)])
    bm.to_mesh(imported.data)
    bm.free()
    nav = navmesh_from_object(imported)
    assert refs(nav.polygons[0])[1] == [(10000, 1)] * 2
    assert refs(nav.polygons[1])[3] == [(10000, 0)] * 2


def test_rebuild_restores_imported_boundary_flag_without_neighbor(tmp_path):
    obj = make_nav([quad(0, 0)], 4040)
    source = navmesh_from_object(obj)
    assert int(source.polygons[0].flags.split()[2]) & 4
    imported = navmesh_to_obj(source, str(tmp_path / "boundary.ynv.xml"))
    bpy.data.objects.remove(obj, do_unlink=True)
    # Simulate the previous rebuild clearing a flag on an unlinked boundary.
    datum = imported.data.attributes[NavMeshAttr.POLY_DATA_1].data[0]
    datum.value &= ~4
    rebuild_links([imported])
    assert imported.data.attributes[NavMeshAttr.POLY_DATA_1].data[0].value & 4
    assert int(navmesh_from_object(imported).polygons[0].flags.split()[2]) & 4


@pytest.mark.parametrize("position,expected", [(0, True), (10, False)])
def test_new_unlinked_boundary_flags(position, expected):
    obj = make_nav([quad(position, 10)], 4040)
    rebuild_links([obj])
    assert bool(obj.data.attributes[NavMeshAttr.POLY_DATA_1].data[0].value & 4) == expected
    assert bool(int(navmesh_from_object(obj).polygons[0].flags.split()[2]) & 4) == expected


@pytest.mark.parametrize("edit", [False, True])
def test_imported_ambiguous_edges_preserve_references_unless_edited(tmp_path, edit):
    obj = make_nav([quad(0, 0), quad(10, 0)])
    source = navmesh_from_object(obj)
    source.polygons.append(NavPolygon.from_xml(source.polygons[1].to_xml()))
    imported = navmesh_to_obj(source, str(tmp_path / "overlapping.ynv.xml"))
    if edit:
        # Keep the shared edge coincident, but change the candidate polygon.
        imported.data.vertices[9].co.x += 1
        with pytest.raises(NavmeshError, match="Ambiguous edge"):
            navmesh_from_object(imported)
        with pytest.raises(NavmeshError, match="Ambiguous edge"):
            rebuild_links([imported])
    else:
        dest = str(tmp_path / "preserved.ynv.xml")
        assert export_ynv(imported, dest)
        result = YNV.from_xml_file(dest)
        assert [refs(p) for p in result.polygons] == [refs(p) for p in source.polygons]
        assert [p.edges_flags.split() for p in result.polygons] == [p.edges_flags.split() for p in source.polygons]
        rebuild_links([imported])
        rebuilt = navmesh_from_object(imported)
        assert [refs(p) for p in rebuilt.polygons] == [refs(p) for p in source.polygons]
        assert [p.edges_flags.split() for p in rebuilt.polygons] == [p.edges_flags.split() for p in source.polygons]


def test_linked_sector_area_id_cannot_be_changed():
    left, _ = linked_pair()
    left.sz_navmesh.area_id = 10000
    with pytest.raises(NavmeshError, match="Area ID changed"):
        navmesh_from_object(left)


def test_repeated_export_does_not_restore_copied_external_references(tmp_path):
    obj = make_nav([quad(0, 0)])
    for attr in (NavMeshAttr.EDGE_ADJACENT_POLY, NavMeshAttr.EDGE_ORIGINAL_POLY):
        obj.data.attributes[attr].data[0].value = 4040 | (12 << 16)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    original = bm.faces[0]
    copied = bmesh.ops.duplicate(bm, geom=[original])["geom"]
    for vert in copied:
        if isinstance(vert, bmesh.types.BMVert):
            vert.co.x += 20
    for face in bm.faces:
        if face != original:
            face[bm.faces.layers.int[NavMeshAttr.POLY_ID]] = 0
    bm.to_mesh(obj.data)
    bm.free()
    path = tmp_path / "repeated.ynv.xml"
    export_ynv(obj, str(path))
    first = YNV.from_xml_file(str(path))
    assert refs(first.polygons[1]) == [[NONE, NONE]] * 4
    export_ynv(obj, str(path))
    second = YNV.from_xml_file(str(path))
    assert first.polygons[1].edges == second.polygons[1].edges
