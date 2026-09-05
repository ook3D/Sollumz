import math
from mathutils import Vector


def distance_point_to_line(start: Vector, end: Vector, point: Vector) -> float:
    L = (end - start).length
    if L == 0.0:
        return (point - start).length
    A = (point - start).cross(point - end).length
    return A / L


def wrap_angle(angle: float) -> float:
    "Wraps the angle in radians to [0..2*pi) range."
    return angle % (2 * math.pi)
