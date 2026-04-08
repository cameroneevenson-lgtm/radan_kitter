from __future__ import annotations

import math
import os
from typing import Callable, Dict, Iterable, List, Optional, Tuple


def _format_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc or "").strip()
    return f"{name}: {text}" if text else name


def _iter_dxf_entities(doc) -> Iterable:
    msp = doc.modelspace()
    for entity in msp:
        yield entity


def _layer_aci_color(doc, layer_name: str) -> Optional[int]:
    try:
        if not layer_name:
            return None
        layer = doc.layers.get(layer_name)
        if layer is None:
            return None
        if not layer.dxf.hasattr("color"):
            return None
        return abs(int(layer.dxf.color))
    except Exception:
        return None


def _entity_effective_color_key(entity, doc) -> Optional[str]:
    try:
        if hasattr(entity, "dxf") and entity.dxf.hasattr("true_color"):
            true_color = int(entity.dxf.true_color) & 0xFFFFFF
            if true_color > 0:
                return f"rgb:{true_color:06X}"
    except Exception:
        pass

    aci: Optional[int] = None
    try:
        if hasattr(entity, "dxf") and entity.dxf.hasattr("color"):
            aci = int(entity.dxf.color)
    except Exception:
        aci = None

    if aci in (None, 0, 256):
        layer_name = ""
        try:
            layer_name = str(entity.dxf.layer or "")
        except Exception:
            layer_name = ""
        layer_aci = _layer_aci_color(doc, layer_name)
        if layer_aci is not None:
            aci = layer_aci

    if aci is None:
        return None
    return f"aci:{abs(int(aci))}"


def _polyline_points(entity) -> Optional[List[Tuple[float, float]]]:
    entity_type = entity.dxftype()
    try:
        if entity_type == "LINE":
            start = entity.dxf.start
            end = entity.dxf.end
            return [(float(start.x), float(start.y)), (float(end.x), float(end.y))]
        if entity_type == "LWPOLYLINE":
            return [(float(x), float(y)) for (x, y, *_rest) in entity.get_points()]
        if entity_type == "POLYLINE":
            points = []
            for vertex in entity.vertices():
                points.append((float(vertex.dxf.location.x), float(vertex.dxf.location.y)))
            return points
    except Exception:
        return None
    return None


def _segments_from_points(
    points: List[Tuple[float, float]],
    closed: bool,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    segments = []
    for index in range(len(points) - 1):
        segments.append((points[index], points[index + 1]))
    if closed and len(points) >= 3:
        segments.append((points[-1], points[0]))
    return segments


def _arc_span_deg(start_deg: float, end_deg: float) -> float:
    span = (float(end_deg) - float(start_deg)) % 360.0
    if span <= 1e-9:
        span = 360.0
    return span


def _points_close(a: Tuple[float, float], b: Tuple[float, float], tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _dedupe_consecutive_points(points: List[Tuple[float, float]], tol: float) -> List[Tuple[float, float]]:
    if not points:
        return []
    out = [points[0]]
    for point in points[1:]:
        if not _points_close(out[-1], point, tol):
            out.append(point)
    return out


def _seg_len(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _bbox_from_points(points: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _stitch_open_paths_to_closed_loops(
    open_polys: List[List[Tuple[float, float]]],
) -> List[List[Tuple[float, float]]]:
    segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    cloud: List[Tuple[float, float]] = []
    for poly in open_polys:
        if len(poly) < 2:
            continue
        cloud.extend(poly)
        segments.extend(_segments_from_points(poly, closed=False))
    segments = [(a, b) for (a, b) in segments if _seg_len(a, b) > 1e-9]
    if not segments:
        return []

    if len(cloud) >= 2:
        x0, y0, x1, y1 = _bbox_from_points(cloud)
        diag = math.hypot(x1 - x0, y1 - y0)
    else:
        diag = 0.0
    tol = max(1e-4, 1e-4 * diag)

    loops: List[List[Tuple[float, float]]] = []
    unused = list(segments)
    while unused:
        a, b = unused.pop()
        path: List[Tuple[float, float]] = [a, b]

        made_progress = True
        while made_progress and unused:
            made_progress = False
            for index, (p0, p1) in enumerate(unused):
                if _points_close(path[-1], p0, tol):
                    path.append(p1)
                elif _points_close(path[-1], p1, tol):
                    path.append(p0)
                elif _points_close(path[0], p1, tol):
                    path.insert(0, p0)
                elif _points_close(path[0], p0, tol):
                    path.insert(0, p1)
                else:
                    continue
                unused.pop(index)
                made_progress = True
                break

        if len(path) < 4:
            continue
        if not _points_close(path[0], path[-1], tol):
            continue
        path = path[:-1]
        path = _dedupe_consecutive_points(path, tol)
        if len(path) >= 3:
            loops.append(path)
    return loops


def _point_to_segment_distance(
    point: Tuple[float, float],
    a: Tuple[float, float],
    b: Tuple[float, float],
) -> float:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    px, py = float(point[0]), float(point[1])
    vx = bx - ax
    vy = by - ay
    den = (vx * vx) + (vy * vy)
    if den <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * vx + (py - ay) * vy) / den
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    cx = ax + t * vx
    cy = ay + t * vy
    return math.hypot(px - cx, py - cy)


def _seg_angle_deg(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    angle = abs(angle) % 180.0
    return angle


def _bbox_aspect(x0: float, y0: float, x1: float, y1: float) -> float:
    w = max(1e-9, x1 - x0)
    h = max(1e-9, y1 - y0)
    return w / h if w >= h else h / w


def _circle_points(cx: float, cy: float, radius: float, steps: int = 48) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    n = max(12, int(steps))
    for index in range(n):
        t = 2.0 * math.pi * (index / n)
        out.append((cx + radius * math.cos(t), cy + radius * math.sin(t)))
    return out


def _arc_points(
    cx: float,
    cy: float,
    radius: float,
    start_deg: float,
    end_deg: float,
    steps: int = 24,
) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if radius <= 1e-9:
        return out
    span = _arc_span_deg(start_deg, end_deg)
    n = max(6, int(max(6.0, float(steps) * (span / 360.0))))
    for index in range(n + 1):
        t_deg = float(start_deg) + (float(span) * (float(index) / float(n)))
        t = math.radians(t_deg)
        out.append((cx + radius * math.cos(t), cy + radius * math.sin(t)))
    return out


def _poly_area_abs(points: List[Tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    n = len(points)
    for index in range(n):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(0.5 * total)


def _poly_perimeter(points: List[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(_seg_len(a, b) for (a, b) in _segments_from_points(points, closed=True))


def _poly_area_signed(points: List[Tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    n = len(points)
    for index in range(n):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % n]
        total += x1 * y2 - x2 * y1
    return 0.5 * total


def _convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[float, float]] = []
    for point in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: List[Tuple[float, float]] = []
    for point in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def _point_in_poly(point: Tuple[float, float], poly: List[Tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    n = len(poly)
    if n < 3:
        return False
    for index in range(n):
        x1, y1 = poly[index]
        x2, y2 = poly[(index + 1) % n]
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            x_hit = (x2 - x1) * (y - y1) / max(1e-12, (y2 - y1)) + x1
            if x < x_hit:
                inside = not inside
    return inside


def compute_dxf_features(
    dxf_path: str,
    *,
    dxf_signal_cols: List[str],
    nan_fn: Callable[[], float],
    safe_float_fn: Callable[[object], float],
    safe_int_fn: Callable[[object], float],
    clamp01_fn: Callable[[float], float],
    ezdxf_module,
) -> Dict[str, float]:
    out = {key: nan_fn() for key in dxf_signal_cols}

    if not dxf_path or not os.path.exists(dxf_path) or ezdxf_module is None:
        return out

    try:
        doc = ezdxf_module.readfile(dxf_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to read DXF '{dxf_path}': {_format_error(exc)}") from exc

    entities = list(_iter_dxf_entities(doc))
    out["dxf_entity_count"] = safe_int_fn(len(entities))

    arc_entity_count = 0
    all_line_seg_lens: List[float] = []

    colors: set[str] = set()
    for entity in entities:
        color_key = _entity_effective_color_key(entity, doc)
        if color_key is None:
            continue
        colors.add(color_key)
    out["dxf_color_count"] = safe_int_fn(len(colors))

    closed_loops: List[List[Tuple[float, float]]] = []
    open_polys: List[List[Tuple[float, float]]] = []
    cloud_points: List[Tuple[float, float]] = []
    entity_samples: List[List[Tuple[float, float]]] = []
    total_geom_len = 0.0
    arc_geom_len = 0.0

    for entity in entities:
        entity_type = entity.dxftype()
        if entity_type == "CIRCLE":
            try:
                center = entity.dxf.center
                radius = abs(float(entity.dxf.radius))
                if radius > 1e-9:
                    circle_pts = _circle_points(float(center.x), float(center.y), radius)
                    cloud_points.extend(circle_pts)
                    entity_samples.append(circle_pts)
                    closed_loops.append(circle_pts)
                    circ_len = 2.0 * math.pi * radius
                    total_geom_len += circ_len
                    arc_geom_len += circ_len
            except Exception:
                pass
            continue

        if entity_type == "ARC":
            arc_entity_count += 1
            try:
                center = entity.dxf.center
                radius = abs(float(entity.dxf.radius))
                if radius > 1e-9:
                    start_a = float(entity.dxf.start_angle)
                    end_a = float(entity.dxf.end_angle)
                    span_deg = _arc_span_deg(start_a, end_a)
                    arc_len = math.radians(span_deg) * radius
                    total_geom_len += arc_len
                    arc_geom_len += arc_len
                    arc_pts = _arc_points(float(center.x), float(center.y), radius, start_a, end_a)
                    if arc_pts:
                        cloud_points.extend(arc_pts)
                        entity_samples.append(arc_pts)
            except Exception:
                pass
            continue

        points = _polyline_points(entity)
        if not points or len(points) < 2:
            continue
        cloud_points.extend(points)
        entity_samples.append(points)
        is_closed = False
        try:
            if entity_type == "LWPOLYLINE":
                is_closed = bool(entity.closed)
            elif entity_type == "POLYLINE":
                is_closed = bool(entity.is_closed)
        except Exception:
            is_closed = False

        segs_here = _segments_from_points(points, closed=is_closed and len(points) >= 3)
        for (a, b) in segs_here:
            seg_len = _seg_len(a, b)
            total_geom_len += seg_len
            if seg_len > 1e-9:
                all_line_seg_lens.append(seg_len)

        if is_closed and len(points) >= 3:
            closed_loops.append(points)
        else:
            open_polys.append(points)

    out["dxf_arc_length_ratio"] = safe_float_fn(arc_geom_len / total_geom_len) if total_geom_len > 1e-9 else safe_float_fn(0.0)
    out["dxf_arc_count"] = safe_int_fn(arc_entity_count)
    out["dxf_bbox_aspect_ratio"] = safe_float_fn(0.0)
    out["dxf_fill_ratio"] = safe_float_fn(0.0)
    out["dxf_edge_length_cv"] = safe_float_fn(0.0)
    out["dxf_edge_band_entity_ratio"] = safe_float_fn(0.0)
    if len(cloud_points) >= 2:
        bx0, by0, bx1, by1 = _bbox_from_points(cloud_points)
        out["dxf_bbox_aspect_ratio"] = safe_float_fn(_bbox_aspect(bx0, by0, bx1, by1))
    if all_line_seg_lens:
        mean_len = sum(all_line_seg_lens) / max(1, len(all_line_seg_lens))
        if mean_len > 1e-9:
            var = sum((x - mean_len) ** 2 for x in all_line_seg_lens) / max(1, len(all_line_seg_lens))
            out["dxf_edge_length_cv"] = safe_float_fn(math.sqrt(var) / mean_len)

    if not closed_loops and open_polys:
        closed_loops = _stitch_open_paths_to_closed_loops(open_polys)

    if not closed_loops and len(cloud_points) >= 3:
        hull = _convex_hull(cloud_points)
        hull_area = _poly_area_abs(hull)
        hull_perim = _poly_perimeter(hull)
        if hull_area > 1e-9 and hull_perim > 1e-9:
            out["dxf_perimeter_area_ratio"] = safe_float_fn(hull_perim / hull_area)
            out["dxf_internal_void_area_ratio"] = safe_float_fn(0.0)
            out["dxf_has_interior_polylines"] = safe_int_fn(0)
            out["dxf_exterior_notch_count"] = safe_int_fn(0)
            hx0, hy0, hx1, hy1 = _bbox_from_points(hull)
            hbbox_area = max(0.0, hx1 - hx0) * max(0.0, hy1 - hy0)
            if hbbox_area > 1e-9:
                out["dxf_fill_ratio"] = safe_float_fn(clamp01_fn(hull_area / hbbox_area))
            return out

    if not closed_loops and len(cloud_points) >= 2:
        x0, y0, x1, y1 = _bbox_from_points(cloud_points)
        w = max(0.0, x1 - x0)
        h = max(0.0, y1 - y0)
        bbox_area = w * h
        bbox_perim = 2.0 * (w + h)
        if bbox_area > 1e-9 and bbox_perim > 1e-9:
            out["dxf_perimeter_area_ratio"] = safe_float_fn(bbox_perim / bbox_area)
            out["dxf_internal_void_area_ratio"] = safe_float_fn(0.0)
            out["dxf_has_interior_polylines"] = safe_int_fn(0)
            out["dxf_exterior_notch_count"] = safe_int_fn(0)
            return out

    if not closed_loops:
        out["dxf_perimeter_area_ratio"] = safe_float_fn(0.0)
        out["dxf_internal_void_area_ratio"] = safe_float_fn(0.0)
        out["dxf_has_interior_polylines"] = safe_int_fn(0)
        out["dxf_exterior_notch_count"] = safe_int_fn(0)
        return out

    loop_areas = [(float(_poly_area_abs(loop)), loop) for loop in closed_loops]
    loop_areas.sort(key=lambda item: item[0], reverse=True)
    outer_area, outer_loop = loop_areas[0]
    outer_perim = _poly_perimeter(outer_loop)
    interior_loop_areas: List[float] = []
    if outer_loop:
        for area_i, loop_i in loop_areas[1:]:
            if area_i <= 1e-9 or len(loop_i) < 3:
                continue
            cx_i = sum(pt[0] for pt in loop_i) / float(len(loop_i))
            cy_i = sum(pt[1] for pt in loop_i) / float(len(loop_i))
            if not _point_in_poly((cx_i, cy_i), outer_loop):
                continue
            if outer_area > 1e-9 and abs(area_i - outer_area) / outer_area <= 0.01:
                continue
            interior_loop_areas.append(float(area_i))
    if outer_area > 1e-9:
        out["dxf_perimeter_area_ratio"] = safe_float_fn(outer_perim / outer_area)
        void_area = sum(interior_loop_areas)
        out["dxf_internal_void_area_ratio"] = safe_float_fn(void_area / outer_area)
        bx0, by0, bx1, by1 = _bbox_from_points(outer_loop)
        bbox_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
        out["dxf_bbox_aspect_ratio"] = safe_float_fn(_bbox_aspect(bx0, by0, bx1, by1))
        if bbox_area > 1e-9:
            out["dxf_fill_ratio"] = safe_float_fn(clamp01_fn(outer_area / bbox_area))
    else:
        hull = _convex_hull(cloud_points)
        hull_area = _poly_area_abs(hull)
        hull_perim = _poly_perimeter(hull)
        if hull_area > 1e-9 and hull_perim > 1e-9:
            out["dxf_perimeter_area_ratio"] = safe_float_fn(hull_perim / hull_area)
            if len(hull) >= 2:
                hx0, hy0, hx1, hy1 = _bbox_from_points(hull)
                hbbox_area = max(0.0, hx1 - hx0) * max(0.0, hy1 - hy0)
                out["dxf_bbox_aspect_ratio"] = safe_float_fn(_bbox_aspect(hx0, hy0, hx1, hy1))
                if hbbox_area > 1e-9:
                    out["dxf_fill_ratio"] = safe_float_fn(clamp01_fn(hull_area / hbbox_area))
        else:
            out["dxf_perimeter_area_ratio"] = safe_float_fn(0.0)
        out["dxf_internal_void_area_ratio"] = safe_float_fn(0.0)

    interior_count = int(len(interior_loop_areas))
    if outer_loop:
        open_inside = 0
        for poly in open_polys:
            if len(poly) < 2:
                continue
            if len(poly) >= 3:
                cx = sum(pt[0] for pt in poly) / len(poly)
                cy = sum(pt[1] for pt in poly) / len(poly)
            else:
                cx = 0.5 * (poly[0][0] + poly[-1][0])
                cy = 0.5 * (poly[0][1] + poly[-1][1])
            if _point_in_poly((cx, cy), outer_loop):
                open_inside += 1
        interior_count += open_inside
    out["dxf_has_interior_polylines"] = safe_int_fn(interior_count)

    x0, y0, x1, y1 = _bbox_from_points(outer_loop)
    bbox_diag = math.hypot(x1 - x0, y1 - y0)
    short_thr = 0.03 * bbox_diag if bbox_diag > 1e-9 else 0.0
    notch_count = 0
    segs = _segments_from_points(outer_loop, closed=True)
    outer_edge_lens = [_seg_len(a, b) for (a, b) in segs if _seg_len(a, b) > 1e-9]
    if outer_edge_lens:
        mean_len = sum(outer_edge_lens) / max(1, len(outer_edge_lens))
        if mean_len > 1e-9:
            var = sum((x - mean_len) ** 2 for x in outer_edge_lens) / max(1, len(outer_edge_lens))
            out["dxf_edge_length_cv"] = safe_float_fn(math.sqrt(var) / mean_len)
    n = len(segs)
    for index in range(n):
        a0, b0 = segs[index - 1]
        a1, b1 = segs[index]
        l0 = _seg_len(a0, b0)
        l1 = _seg_len(a1, b1)
        if short_thr <= 0 or (l0 > short_thr and l1 > short_thr):
            continue
        ang0 = _seg_angle_deg(a0, b0)
        ang1 = _seg_angle_deg(a1, b1)
        diff = abs(ang1 - ang0) % 180.0
        diff = min(diff, 180.0 - diff)
        if 70.0 <= diff <= 110.0:
            notch_count += 1
    if notch_count == 0:
        signed_area = _poly_area_signed(outer_loop)
        if abs(signed_area) > 1e-12 and len(outer_loop) >= 4:
            ccw = signed_area > 0.0
            concave_ortho = 0
            m = len(outer_loop)
            for index in range(m):
                p_prev = outer_loop[index - 1]
                p_cur = outer_loop[index]
                p_next = outer_loop[(index + 1) % m]
                v1x = p_cur[0] - p_prev[0]
                v1y = p_cur[1] - p_prev[1]
                v2x = p_next[0] - p_cur[0]
                v2y = p_next[1] - p_cur[1]
                if math.hypot(v1x, v1y) <= 1e-9 or math.hypot(v2x, v2y) <= 1e-9:
                    continue
                cross = v1x * v2y - v1y * v2x
                is_concave = (cross < -1e-12) if ccw else (cross > 1e-12)
                if not is_concave:
                    continue
                ang0 = _seg_angle_deg(p_prev, p_cur)
                ang1 = _seg_angle_deg(p_cur, p_next)
                diff = abs(ang1 - ang0) % 180.0
                diff = min(diff, 180.0 - diff)
                if 70.0 <= diff <= 110.0:
                    concave_ortho += 1
            if concave_ortho >= 2:
                notch_count = max(notch_count, concave_ortho // 2)
    out["dxf_exterior_notch_count"] = safe_int_fn(notch_count)

    if bbox_diag > 1e-9 and segs and entity_samples:
        band_w = 0.03 * bbox_diag
        near_entities = 0
        used_entities = 0
        for points in entity_samples:
            if not points:
                continue
            sample_points = points
            if len(sample_points) > 24:
                step = max(1, int(len(sample_points) / 24))
                sample_points = sample_points[::step]
            near_pts = 0
            valid_pts = 0
            for point in sample_points:
                valid_pts += 1
                is_near = False
                for (a, b) in segs:
                    if _point_to_segment_distance(point, a, b) <= band_w:
                        is_near = True
                        break
                if is_near:
                    near_pts += 1
            if valid_pts <= 0:
                continue
            used_entities += 1
            if (float(near_pts) / float(valid_pts)) >= 0.5:
                near_entities += 1
        if used_entities > 0:
            out["dxf_edge_band_entity_ratio"] = safe_float_fn(clamp01_fn(float(near_entities) / float(used_entities)))

    for col in (
        "dxf_perimeter_area_ratio",
        "dxf_internal_void_area_ratio",
        "dxf_bbox_aspect_ratio",
        "dxf_fill_ratio",
        "dxf_edge_length_cv",
        "dxf_edge_band_entity_ratio",
    ):
        value = safe_float_fn(out.get(col))
        out[col] = float(value) if math.isfinite(value) else 0.0
    out["dxf_internal_void_area_ratio"] = safe_float_fn(clamp01_fn(float(out["dxf_internal_void_area_ratio"])))
    out["dxf_fill_ratio"] = safe_float_fn(clamp01_fn(float(out["dxf_fill_ratio"])))
    out["dxf_edge_band_entity_ratio"] = safe_float_fn(clamp01_fn(float(out["dxf_edge_band_entity_ratio"])))

    return out
