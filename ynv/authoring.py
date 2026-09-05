import math
from pathlib import Path
import xml.etree.ElementTree as ET

import bpy
import bmesh
from bpy.types import Operator
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from mathutils import Vector

from ..sollumz_properties import SollumType
from .navmesh import navmesh_is_valid, navmesh_grid_get_cell_filename, navmesh_grid_get_cell_bounds
from .navmesh_attributes import NavMeshAttr
from .navmesh_topology import (
    NavmeshError,
    area_id,
    initialize_mesh,
    snapshot,
    polygon_order,
    commit_polygon_ids,
    rebuild_links,
    NONE,
)
from .properties import NavLinkTypeEnumItems


def navmesh_parent(obj):
    while obj is not None:
        if navmesh_is_valid(obj):
            return obj
        obj = obj.parent
    return None


def activate(context, obj):
    for selected in context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def ensure_group(root, kind, name):
    group = next((c for c in root.children if c.sollum_type == kind), None)
    if group is None:
        group = bpy.data.objects.new(name, None)
        group.sollum_type = kind
        group.parent = root
        group.empty_display_size = 0
        (root.users_collection[0] if root.users_collection else bpy.context.collection).objects.link(group)
    return group


def new_navmesh(context, mesh, aid):
    from .navmesh_material import get_navmesh_material

    name = navmesh_grid_get_cell_filename(aid % 100, aid // 100) if aid < 10000 else "vehicle_navmesh"
    initialize_mesh(mesh)
    commit_polygon_ids(mesh, polygon_order(mesh))
    obj = bpy.data.objects.new(name, mesh)
    obj.sollum_type = SollumType.NAVMESH
    obj.sz_navmesh.area_id = aid
    context.collection.objects.link(obj)
    # A new mesh has no external dependencies, but its IDs become stable as soon
    # as tools can create portal or sector references to them.
    mesh.materials.clear()
    mesh.materials.append(get_navmesh_material())
    ensure_group(obj, SollumType.NAVMESH_LINK_GROUP, "Portals")
    ensure_group(obj, SollumType.NAVMESH_COVER_POINT_GROUP, "Cover Points")
    return obj


def _cursor_area(context):
    x, y, _ = context.scene.cursor.location
    gx, gy = math.floor((x + 6000) / 150), math.floor((y + 6000) / 150)
    if not (0 <= gx < 100 and 0 <= gy < 100):
        raise NavmeshError("The 3D cursor lies outside the navigation grid.")
    return gy * 100 + gx


def create_portal(root, start, end, link_type="CLIMB_LADDER"):
    group = ensure_group(root, SollumType.NAVMESH_LINK_GROUP, "Portals")
    collection = root.users_collection[0]
    bpy.context.view_layer.update()
    obj = bpy.data.objects.new("Portal", None)
    obj.sollum_type = SollumType.NAVMESH_LINK
    obj.parent = group
    obj.location = group.matrix_world.inverted() @ start
    obj.empty_display_type = "SPHERE"
    obj.empty_display_size = 0.4
    obj.sz_nav_link.link_type = link_type
    obj.sz_nav_link.auto_bind = True
    collection.objects.link(obj)
    bpy.context.view_layer.update()
    target = bpy.data.objects.new("Portal.target", None)
    target.sollum_type = SollumType.NAVMESH_LINK_TARGET
    target.parent = obj
    target.location = obj.matrix_world.inverted() @ end
    target.empty_display_type = "SPHERE"
    target.empty_display_size = 0.3
    collection.objects.link(target)
    return obj


class SOLLUMZ_OT_navmesh_create(Operator):
    bl_idname = "sollumz.navmesh_create"
    bl_label = "Create Navmesh"
    bl_description = "Create an editable navigation plane at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}
    standalone: BoolProperty(name="Vehicle Navmesh", default=False)
    size: FloatProperty(name="Size", default=2.0, min=0.01, max=150, subtype="DISTANCE")

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        try:
            aid = 10000 if self.standalone else _cursor_area(context)
            center = context.scene.cursor.location.copy()
            half = self.size / 2
            if aid < 10000:
                low, high = navmesh_grid_get_cell_bounds(aid % 100, aid // 100)
                center.x = min(high.x - half, max(low.x + half, center.x))
                center.y = min(high.y - half, max(low.y + half, center.y))
            vertices = [center + Vector((x * half, y * half, 0)) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            if self.standalone:
                vertices = [v - center for v in vertices]
            mesh = bpy.data.meshes.new("Navmesh")
            mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
            obj = new_navmesh(context, mesh, aid)
            if self.standalone:
                obj.location = center
            activate(context, obj)
            return {"FINISHED"}
        except NavmeshError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SOLLUMZ_OT_navmesh_convert(Operator):
    bl_idname = "sollumz.navmesh_convert"
    bl_label = "Convert Mesh to Navmesh"
    bl_description = "Create a navmesh copy of the active mesh; map geometry must fit inside one sector"
    bl_options = {"REGISTER", "UNDO"}
    standalone: BoolProperty(name="Vehicle Navmesh", default=False)

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        source = context.active_object
        if source.modifiers:
            self.report({"ERROR"}, "Apply modifiers before converting to a navmesh.")
            return {"CANCELLED"}
        obj = None
        mesh = None
        try:
            aid = 10000 if self.standalone else _cursor_area(context)
            mesh = source.data.copy()
            for attr in list(mesh.attributes):
                if attr.name.startswith(".navmesh."):
                    mesh.attributes.remove(attr)
            for key in list(mesh.keys()):
                if key.startswith("sz_navmesh_"):
                    del mesh[key]
            if not self.standalone:
                mesh.transform(source.matrix_world)
            obj = new_navmesh(context, mesh, aid)
            if self.standalone:
                obj.matrix_world = source.matrix_world
            snapshot(obj)
            activate(context, obj)
            return {"FINISHED"}
        except NavmeshError as exc:
            if obj:
                for child in list(obj.children_recursive):
                    bpy.data.objects.remove(child, do_unlink=True)
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SOLLUMZ_OT_navmesh_create_portal(Operator):
    bl_idname = "sollumz.navmesh_create_portal"
    bl_label = "Create Portal"
    bl_description = (
        "Create a portal at the cursor, or between two selected faces in Edit Mode; move its target to the destination"
    )
    bl_options = {"REGISTER", "UNDO"}
    link_type: EnumProperty(name="Type", items=NavLinkTypeEnumItems)
    height: FloatProperty(name="Target Height", default=2.0, subtype="DISTANCE")
    reverse: BoolProperty(name="Create Return Portal", default=False)

    @classmethod
    def poll(cls, context):
        return navmesh_parent(context.active_object) is not None and context.mode in {"OBJECT", "EDIT_MESH"}

    def execute(self, context):
        root = navmesh_parent(context.active_object)
        start = context.scene.cursor.location.copy()
        end = start + Vector((0, 0, self.height))
        if context.mode == "EDIT_MESH":
            bm = bmesh.from_edit_mesh(root.data)
            selected = [f for f in bm.faces if f.select]
            if len(selected) != 2:
                self.report({"ERROR"}, "Select exactly two faces for the portal endpoints.")
                return {"CANCELLED"}
            if bm.faces.active in selected:
                selected.sort(key=lambda f: f != bm.faces.active)
            start, end = [root.matrix_world @ f.calc_center_median() for f in selected]
            bpy.ops.object.mode_set(mode="OBJECT")
        context.view_layer.update()
        portal = create_portal(root, start, end, self.link_type)
        if self.reverse:
            reverse_type = {"CLIMB_LADDER": "DESCEND_LADDER", "DESCEND_LADDER": "CLIMB_LADDER"}.get(
                self.link_type, self.link_type
            )
            create_portal(root, end, start, reverse_type)
        activate(context, portal)
        return {"FINISHED"}


class SOLLUMZ_OT_navmesh_create_cover(Operator):
    bl_idname = "sollumz.navmesh_create_cover"
    bl_label = "Create Cover Point"
    bl_description = "Create a cover point at the 3D cursor; rotate around Z to set the cover direction"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and navmesh_parent(context.active_object) is not None

    def execute(self, context):
        root = navmesh_parent(context.active_object)
        group = ensure_group(root, SollumType.NAVMESH_COVER_POINT_GROUP, "Cover Points")
        context.view_layer.update()
        obj = bpy.data.objects.new("Cover Point", None)
        obj.sollum_type = SollumType.NAVMESH_COVER_POINT
        obj.parent = group
        obj.location = group.matrix_world.inverted() @ context.scene.cursor.location
        obj.rotation_euler.z = math.pi
        obj.lock_rotation = (True, True, False)
        obj.empty_display_type = "CONE"
        obj.empty_display_size = 0.5
        root.users_collection[0].objects.link(obj)
        activate(context, obj)
        return {"FINISHED"}


class SOLLUMZ_OT_navmesh_initialize(Operator):
    bl_idname = "sollumz.navmesh_initialize"
    bl_label = "Initialize Legacy Navmesh"
    bl_description = (
        "Migrate unchanged meshes from the earlier YNV branch to persistent IDs and directed corner attributes"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and navmesh_is_valid(context.active_object)

    def execute(self, context):
        from .navmesh_topology import remember_borders

        try:
            obj = context.active_object
            if obj.data.get("sz_navmesh_schema", 0) == 2:
                self.report({"INFO"}, "This navmesh is already initialized.")
                return {"FINISHED"}
            initialize_mesh(obj.data, imported=True)
            remember_borders(obj)
            self.report(
                {"INFO"},
                "Initialized. Reimport the source XML to recover original adjacency discarded by the old importer.",
            )
            return {"FINISHED"}
        except NavmeshError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SOLLUMZ_OT_navmesh_mark_new(Operator):
    bl_idname = "sollumz.navmesh_mark_new"
    bl_label = "Mark as New Polygons"
    bl_description = "Clear copied IDs on selected new faces; all original polygons must still be present"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and navmesh_is_valid(context.active_object)

    def execute(self, context):
        mesh = context.active_object.data
        bm = bmesh.from_edit_mesh(mesh)
        layer = bm.faces.layers.int.get(NavMeshAttr.POLY_ID)
        if layer is None:
            self.report({"ERROR"}, "Initialize this navmesh first.")
            return {"CANCELLED"}
        faces = [f for f in bm.faces if f.select]
        remaining = {f[layer] for f in bm.faces if not f.select}
        if any(f[layer] > 0 and f[layer] not in remaining for f in faces):
            self.report(
                {"ERROR"}, "Selection includes an original polygon. Keep the original and select only its new copies."
            )
            return {"CANCELLED"}
        for face in faces:
            face[layer] = 0
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        return {"FINISHED"}


def sector_set(context):
    root = navmesh_parent(context.active_object)
    if root is None:
        raise NavmeshError("Select a navmesh or one of its portals.")
    if area_id(root) >= 10000:
        return [root]
    return [o for o in context.scene.objects if navmesh_is_valid(o) and area_id(o) < 10000]


class SOLLUMZ_OT_navmesh_rebuild_links(Operator):
    bl_idname = "sollumz.navmesh_rebuild_links"
    bl_label = "Rebuild Neighbour Links"
    bl_description = (
        "Rebuild adjacency for loaded map sectors, preserving polygon IDs; export all affected sectors together"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and navmesh_parent(context.active_object) is not None

    def execute(self, context):
        try:
            count = rebuild_links(sector_set(context))
            self.report({"INFO"}, f"Rebuilt {count} border connections. Export the sector set to save both sides.")
            return {"FINISHED"}
        except NavmeshError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SOLLUMZ_OT_navmesh_validate(Operator):
    bl_idname = "sollumz.navmesh_validate"
    bl_label = "Validate Navmeshes"
    bl_description = "Check persistent IDs, geometry, portal bindings and loaded neighbour references"

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and navmesh_parent(context.active_object) is not None

    def execute(self, context):
        from .ynvexport import navmesh_from_object

        try:
            objects = sector_set(context)
            for obj in objects:
                navmesh_from_object(obj)
            self.report({"INFO"}, f"Validated {len(objects)} navmeshes.")
            return {"FINISHED"}
        except NavmeshError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


def import_neighbors(root, directory):
    from .ynvimport import import_ynv

    aid = area_id(root)
    if aid >= 10000:
        raise NavmeshError("Vehicle navmeshes have no map-sector neighbours.")
    x, y = aid % 100, aid // 100
    requested = {
        ny * 100 + nx for nx in range(max(0, x - 1), min(100, x + 2)) for ny in range(max(0, y - 1), min(100, y + 2))
    } - {aid}
    snap = snapshot(root)
    required = {r[0] for e in snap.edges for r in (e.adjacent, e.original) if r != NONE and r[0] != aid}
    requested |= required
    loaded = {area_id(o) for o in bpy.context.scene.objects if navmesh_is_valid(o)}
    requested -= loaded
    folder = Path(bpy.path.abspath(directory))
    if not folder.is_dir():
        raise NavmeshError("Choose a directory containing neighbouring .ynv.xml files.")
    paths = {}
    # File names can differ from the grid convention; read AreaID from headers.
    for path in sorted(folder.glob("*.ynv.xml")):
        try:
            with path.open("rb") as stream:
                for _, elem in ET.iterparse(stream, events=("end",)):
                    if elem.tag == "AreaID":
                        file_area = int(elem.get("value"))
                        if file_area in requested:
                            if file_area in paths:
                                raise NavmeshError(
                                    f"Multiple XML files have Area ID {file_area}. Choose a directory containing one sector set."
                                )
                            paths[file_area] = path
                        break
        except (ET.ParseError, TypeError, ValueError) as exc:
            raise NavmeshError(f"Cannot read sector header in {path.name}: {exc}") from exc
    missing = required - loaded - paths.keys()
    if missing:
        raise NavmeshError(
            f"Missing linked sectors {sorted(missing)}. Export them as CodeWalker .ynv.xml into this directory."
        )
    created = []
    try:
        for path in paths.values():
            created.append(import_ynv(str(path)))
    except Exception:
        for obj in created:
            mesh = obj.data
            for child in list(obj.children_recursive):
                bpy.data.objects.remove(child, do_unlink=True)
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
        raise
    return created


class SOLLUMZ_OT_navmesh_import_neighbors(Operator):
    bl_idname = "sollumz.navmesh_import_neighbors"
    bl_label = "Import Neighbour Sectors"
    bl_description = "Import surrounding and referenced sectors from the Neighbour Directory as CodeWalker XML"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and navmesh_parent(context.active_object) is not None

    def execute(self, context):
        try:
            root = navmesh_parent(context.active_object)
            directory = root.sz_navmesh.neighbor_directory or str(Path(root.sz_navmesh.source_path).parent)
            created = import_neighbors(root, directory)
            self.report({"INFO"}, f"Imported {len(created)} neighbouring sectors.")
            return {"FINISHED"}
        except (NavmeshError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SOLLUMZ_OT_navmesh_export_set(Operator):
    bl_idname = "sollumz.navmesh_export_set"
    bl_label = "Export Sector Set"
    bl_description = "Validate all loaded map sectors, then export their YNV XML files together"
    directory: StringProperty(name="Directory", subtype="DIR_PATH")
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and navmesh_parent(context.active_object) is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import os
        import tempfile
        from .ynvexport import navmesh_from_object, commit_export

        try:
            objects = sector_set(context)
            exports = [(obj, navmesh_from_object(obj)) for obj in objects]
            directory = Path(bpy.path.abspath(self.directory))
            if not directory.is_dir():
                raise NavmeshError("Choose an existing output directory.")
            with tempfile.TemporaryDirectory(dir=directory, prefix=".ynv-export-") as staging:
                staged = []
                for obj, nav in exports:
                    name = (
                        navmesh_grid_get_cell_filename(nav.area_id % 100, nav.area_id // 100)
                        if nav.area_id < 10000
                        else obj.name
                    )
                    path = Path(staging) / (name + ".ynv.xml")
                    nav.write_xml(str(path))
                    staged.append(path)
                for path in staged:
                    os.replace(path, directory / path.name)
            for obj, nav in exports:
                commit_export(obj, nav)
            self.report({"INFO"}, f"Exported {len(exports)} navmeshes.")
            return {"FINISHED"}
        except (NavmeshError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
