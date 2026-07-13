import os
from pathlib import Path

import bpy
from bpy.types import Image


def texture_extension(image: Image) -> str:
    """The file extension of `image`'s filepath (including the dot, e.g. `.png`), or `""`."""
    return os.path.splitext(bpy.path.basename(image.filepath))[1]


def is_dds_image(image: Image) -> bool:
    """Whether `image` is already a DDS texture: packed DDS data, or a `.dds` file path."""
    packed = image.packed_file
    if packed and (data := packed.data):
        return data[:4] == b"DDS "
    return texture_extension(image).lower() == ".dds"


def image_source_path(image: Image) -> Path:
    """The absolute filesystem path of `image`'s source file."""
    return Path(bpy.path.abspath(image.filepath, library=image.library))
