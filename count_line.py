"""Counting-line geometry used by TyreTrack."""
import math

def segment_bounds(width, height, ratio, angle, length):
    cx = (width - 1) / 2.0
    half = max(1.0, min(1.0, float(length)) * (width - 1) / 2.0)
    return cx-half, cx+half, float(ratio)*(height-1), math.tan(math.radians(float(angle)))

def crosses_segment(previous_side, current_side, previous_x, current_x, left, right):
    denominator = previous_side-current_side
    if abs(denominator) < 1e-9:
        return False
    fraction = previous_side/denominator
    if not 0 <= fraction <= 1:
        return False
    intersection_x = previous_x + fraction*(current_x-previous_x)
    return left <= intersection_x <= right


def count_point(box, width, height):
    """The exact visible red-dot centre, shared by drawing and counting."""
    x1,y1,x2,y2=(int(v) for v in box)
    x1=max(0,min(width-1,x1));x2=max(0,min(width-1,x2))
    y1=max(0,min(height-1,y1));y2=max(0,min(height-1,y2))
    return (x1+x2)//2,(y1+y2)//2
