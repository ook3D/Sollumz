import bpy
from pathlib import Path
from ..shared import SOLLUMZ_TEST_VERSIONING_DATA_DIR
from ...versioning import do_versions
from ...sollumz_properties import LODLevel
from ...yft.properties import VehiclePaintLayer


def data_path(file_name: str) -> Path:
    path = SOLLUMZ_TEST_VERSIONING_DATA_DIR.joinpath(file_name)
    assert path.exists()
    return path


def load_blend_data(data: bpy.types.BlendData, file_name: str):
    with data.libraries.load(str(data_path(file_name))) as (data_from, data_to):
        for attr in dir(data_to):
            setattr(data_to, attr, getattr(data_from, attr))


def test_versioning_paint_layers():
    with bpy.data.temp_data() as temp_data:
        load_blend_data(temp_data, "v250_paint_layer.blend")

        do_versions(temp_data)

        for mat_name, expected_paint_layer, expected_diffuse_color in (
            ("mat_not_paintable_default", VehiclePaintLayer.DEFAULT, (2.0, 5.0, 5.0)),
            ("mat_not_paintable_custom", VehiclePaintLayer.CUSTOM, (1.0, 0.5, 0.25)),
            ("vehicle_paint1 [PRIMARY]", VehiclePaintLayer.PRIMARY, (2.0, 1.0, 1.0)),
            ("vehicle_paint1 [SECONDARY]", VehiclePaintLayer.SECONDARY, (2.0, 2.0, 2.0)),
            ("vehicle_paint1 [WHEEL]", VehiclePaintLayer.WHEEL, (2.0, 4.0, 4.0)),
            ("vehicle_paint1 [INTERIOR TRIM]", VehiclePaintLayer.INTERIOR_TRIM, (2.0, 6.0, 6.0)),
            ("vehicle_paint1 [DASHBOARD]", VehiclePaintLayer.INTERIOR_DASH, (2.0, 7.0, 7.0)),
        ):
            mat = temp_data.materials[mat_name]
            assert mat.sz_paint_layer == expected_paint_layer.name

            x, y, z = mat.node_tree.nodes["matDiffuseColor"].get_vec3()
            assert (x, y, z) == expected_diffuse_color