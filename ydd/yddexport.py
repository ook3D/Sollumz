from ..resources.drawable import *
from ..tools.meshhelper import *
from ..tools.utils import *
from ..ydr.ydrexport import drawable_from_object
from ..tools import jenkhash
from ..sollumz_properties import DrawableType


def get_hash(item):
    return jenkhash.Generate(item[1].name.split(".")[0])


def drawable_dict_from_object(exportop, obj, filepath):

    drawable_dict = DrawableDictionary()

    bones = None
    for child in obj.children:
        if child.sollum_type == DrawableType.DRAWABLE and child.type == 'ARMATURE' and len(child.pose.bones) > 0:
            bones = child.pose.bones
            break

    for child in obj.children:
        if child.sollum_type == DrawableType.DRAWABLE:
            drawable = drawable_from_object(exportop, child, filepath, bones)
            drawable_dict[drawable.name] = drawable

    drawable_dict.sort(key=get_hash)

    return drawable_dict


def export_ydd(exportop, obj, filepath):
    drawable_dict_from_object(exportop, obj, filepath).write_xml(filepath)
