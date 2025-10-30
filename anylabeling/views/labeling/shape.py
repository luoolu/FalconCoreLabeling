import copy
import math

from PyQt5 import QtCore, QtGui

from . import utils

# TODO(unknown):
# - [opt] Store paths instead of creating new ones at each paint.


DEFAULT_LINE_COLOR = QtGui.QColor(0, 255, 0, 128)  # bf hovering
DEFAULT_FILL_COLOR = QtGui.QColor(100, 100, 100, 0)  # hovering
DEFAULT_SELECT_LINE_COLOR = QtGui.QColor(255, 255, 255)  # selected
DEFAULT_SELECT_FILL_COLOR = QtGui.QColor(0, 255, 0, 155)  # selected
DEFAULT_VERTEX_FILL_COLOR = QtGui.QColor(0, 255, 0, 255)  # hovering
DEFAULT_HVERTEX_FILL_COLOR = QtGui.QColor(255, 255, 255, 255)  # hovering


class Shape:
    """Shape data type"""

    # Render handles as squares
    P_SQUARE = 0

    # Render handles as circles
    P_ROUND = 1

    # Flag for the handles we would move if dragging
    MOVE_VERTEX = 0

    # Flag for all other handles on the current shape
    NEAR_VERTEX = 1

    # The following class variables influence the drawing of all shape objects.
    line_color = DEFAULT_LINE_COLOR
    fill_color = DEFAULT_FILL_COLOR
    select_line_color = DEFAULT_SELECT_LINE_COLOR
    select_fill_color = DEFAULT_SELECT_FILL_COLOR
    vertex_fill_color = DEFAULT_VERTEX_FILL_COLOR
    hvertex_fill_color = DEFAULT_HVERTEX_FILL_COLOR
    line_width = 3
    fill_opacity = DEFAULT_FILL_COLOR.alpha()
    point_type = P_ROUND
    point_size = 4
    scale = 1.5

    def __init__(
        self,
        labels=None,
        text="",
        line_color=None,
        shape_type=None,
        flags=None,
        group_id=None,
    ):
        if labels is not None:
            self.labels = list(labels)
        elif labels is not None:
            if isinstance(labels, list):
                self.labels = labels
            else:
                self.labels = [l.strip() for l in str(labels).split(",") if l.strip()]
        else:
            self.labels = []
        self.text = text
        self.group_id = group_id
        self.points = []
        self.fill = False
        self.selected = False
        self.shape_type = shape_type
        self.flags = flags
        self.other_data = {}
        self._path_cache = None
        self._path_cache_dirty = True

        self._highlight_index = None
        self._highlight_mode = self.NEAR_VERTEX
        self._highlight_settings = {
            self.NEAR_VERTEX: (4, self.P_ROUND),
            self.MOVE_VERTEX: (1.5, self.P_SQUARE),
        }

        self._vertex_fill_color = None

        self._closed = False

        if line_color is not None:
            # Override the class line_color attribute
            # with an object attribute. Currently this
            # is used for drawing the pending line a different color.
            self.line_color = line_color

        self.shape_type = shape_type

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop("_path_cache", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._path_cache = None

    @property
    def label(self):
        """Return labels as comma separated string"""
        return ", ".join(self.labels)

    @label.setter
    def label(self, text):
        if isinstance(text, list):
            self.labels = [str(t).strip() for t in text if str(t).strip()]
        else:
            self.labels = [t.strip() for t in str(text).split(",") if t.strip()]

    @property
    def primary_label(self):
        """Return first label if exists"""
        return self.labels[0] if self.labels else ""
    @property
    def shape_type(self):
        """Get shape type (polygon, rectangle, point, line, ...)"""
        return self._shape_type

    @shape_type.setter
    def shape_type(self, value):
        """Set shape type"""
        if value is None:
            value = "polygon"
        if value not in [
            "polygon",
            "rectangle",
            "point",
            "line",
            "circle",
            "linestrip",
        ]:
            raise ValueError(f"Unexpected shape_type: {value}")
        self._shape_type = value

    def close(self):
        """Close the shape"""
        self._closed = True
        self.invalidate_path_cache()

    def invalidate_path_cache(self):
        """Mark cached painter path as dirty."""
        self._path_cache = None
        self._path_cache_dirty = True

    def add_point(self, point):
        """Add a point"""
        if self.points and point == self.points[0]:
            self.close()
        else:
            self.points.append(point)
            self.invalidate_path_cache()

    def can_add_point(self):
        """Check if shape supports more points"""
        return self.shape_type in ["polygon", "linestrip"]

    def pop_point(self):
        """Remove and return the last point of the shape"""
        if self.points:
            point = self.points.pop()
            self.invalidate_path_cache()
            return point
        return None

    def insert_point(self, i, point):
        """Insert a point to a specific index"""
        self.points.insert(i, point)
        self.invalidate_path_cache()

    def remove_point(self, i):
        """Remove point from a specific index"""
        self.points.pop(i)
        self.invalidate_path_cache()

    def is_closed(self):
        """Check if the shape is closed"""
        return self._closed

    def set_open(self):
        """Set shape to open - (_close=False)"""
        self._closed = False
        self.invalidate_path_cache()

    def get_rect_from_line(self, pt1, pt2):
        """Get rectangle from diagonal line"""
        x1, y1 = pt1.x(), pt1.y()
        x2, y2 = pt2.x(), pt2.y()
        return QtCore.QRectF(x1, y1, x2 - x1, y2 - y1)

    def paint(self, painter: QtGui.QPainter):  # noqa: max-complexity: 18
        """Paint shape using QPainter"""
        if not self.points:
            return

        color = self.select_line_color if self.selected else self.line_color
        pen = QtGui.QPen(color)
        pen.setWidth(max(1, int(round(self.line_width / self.scale))))
        painter.setPen(pen)

        base_path = self.make_path()
        line_path = QtGui.QPainterPath(base_path)
        vrtx_path = QtGui.QPainterPath()

        highlight_idx = self._highlight_index

        if self.shape_type == "point":
            assert len(self.points) == 1
            self.draw_vertex(vrtx_path, 0)
        elif self.shape_type == "rectangle":
            if self.selected or highlight_idx is not None:
                for i in range(len(self.points)):
                    self.draw_vertex(vrtx_path, i)
        elif self.shape_type == "circle":
            if self.selected or highlight_idx is not None:
                for i in range(len(self.points)):
                    self.draw_vertex(vrtx_path, i)
        elif self.shape_type == "linestrip":
            if self.selected:
                for i in range(len(self.points)):
                    self.draw_vertex(vrtx_path, i)
            elif highlight_idx is not None:
                self.draw_vertex(vrtx_path, highlight_idx)
        else:
            # polygon / other shapes
            drawn_indices = set()

            def add_vertex(idx):
                if idx is None or idx < 0 or idx >= len(self.points):
                    return
                if idx in drawn_indices:
                    return
                self.draw_vertex(vrtx_path, idx)
                drawn_indices.add(idx)

            add_vertex(0)
            if self.selected:
                for idx in range(len(self.points)):
                    add_vertex(idx)
            else:
                add_vertex(highlight_idx)

        painter.drawPath(line_path)
        painter.drawPath(vrtx_path)
        if self._vertex_fill_color is not None:
            painter.fillPath(vrtx_path, self._vertex_fill_color)
        if self.fill:
            fill_color = self.select_fill_color if self.selected else self.fill_color
            fill_color.setAlpha(self.fill_opacity)
            painter.fillPath(line_path, fill_color)

    def draw_vertex(self, path, i):
        """Draw a vertex"""
        d = self.point_size / self.scale
        shape = self.point_type
        point = self.points[i]
        if i == self._highlight_index:
            size, shape = self._highlight_settings[self._highlight_mode]
            d *= size
        if self._highlight_index is not None:
            self._vertex_fill_color = self.hvertex_fill_color
        else:
            self._vertex_fill_color = self.vertex_fill_color
        if shape == self.P_SQUARE:
            path.addRect(point.x() - d / 2, point.y() - d / 2, d, d)
        elif shape == self.P_ROUND:
            path.addEllipse(point, d / 2.0, d / 2.0)
        else:
            print("Unsupported vertex shape")

    def nearest_vertex(self, point, epsilon):
        """Find the index of the nearest vertex to a point
        Only consider if the distance is smaller than epsilon
        """
        min_distance = float("inf")
        min_i = None
        for i, p in enumerate(self.points):
            dist = utils.distance(p - point)
            if dist <= epsilon and dist < min_distance:
                min_distance = dist
                min_i = i
        return min_i

    def nearest_edge(self, point, epsilon):
        """Comparing squared distance will speed up the calculation
        and avoid using sqrt in calculation
        if d1 < d2 then d1^2 < d2^2
        """
        min_dist_squared = epsilon**2
        post_i = None
        for i in range(len(self.points)):
            line = [self.points[i - 1], self.points[i]]
            dist = utils.squared_distance_to_line(point, line)
            dist_squared = dist**2
            if dist_squared <= min_dist_squared:
                min_dist_squared = dist_squared
                post_i = i
        return post_i

    def nearest_edge_with_holes(self, point, epsilon):
        """Find nearest edge on outer ring or any hole.

        Returns:
            tuple | int | None: (index, hole_idx) if a hole edge is closest;
                                 index (int) for outer ring if closest;
                                 None if nothing within epsilon.
        """
        # Outer ring first (keep backward-compatible precision)
        best_dist_sq = (epsilon ** 2)
        best = None  # int for outer, (idx, hole_idx) for hole

        for i in range(len(self.points)):
            line = [self.points[i - 1], self.points[i]]
            d = utils.squared_distance_to_line(point, line)
            dsq = d ** 2
            if dsq <= best_dist_sq:
                best_dist_sq = dsq
                best = i

        # Check holes
        try:
            holes = None
            if isinstance(self.other_data, dict):
                holes = self.other_data.get("holes")
            if holes:
                for hole_idx, hole in enumerate(holes):
                    if not hole or len(hole) < 2:
                        continue
                    # Normalize to QPointF sequence
                    qpts = []
                    for hp in hole:
                        if hasattr(hp, 'x'):
                            qpts.append(hp)
                        else:
                            qpts.append(QtCore.QPointF(float(hp[0]), float(hp[1])))
                    for j in range(len(qpts)):
                        line = [qpts[j - 1], qpts[j]]
                        d = utils.squared_distance_to_line(point, line)
                        dsq = d ** 2
                        if dsq <= best_dist_sq:
                            best_dist_sq = dsq
                            best = (j, hole_idx)
        except Exception:
            pass

        return best

    def insert_point_into_hole(self, hole_idx, index, point):
        """Insert a point into the specified hole ring at index.

        Args:
            hole_idx (int): which hole ring
            index (int): insertion index (like Shape.insert_point semantics)
            point (QPointF): point to insert
        """
        if not isinstance(self.other_data, dict):
            return
        holes = self.other_data.get("holes")
        if not holes or hole_idx < 0 or hole_idx >= len(holes):
            return
        ring = holes[hole_idx]
        if not isinstance(ring, list):
            return

        # Keep the same element type as the ring currently uses
        if len(ring) > 0 and hasattr(ring[0], 'x'):
            insert_val = point
        else:
            insert_val = [float(point.x()), float(point.y())]

        # Index semantics mirroring outer ring behavior
        if index < 0:
            index = 0
        if index > len(ring):
            index = len(ring)
        ring.insert(index, insert_val)
        self.invalidate_path_cache()

    def contains_point(self, point):
        """Check if shape contains a point"""
        # Special handling: background invert polygon should be selectable even through holes
        try:
            if (
                self.shape_type == "polygon"
                and self.is_closed()
                and isinstance(self.other_data, dict)
                and self.other_data.get("select_through_holes")
            ):
                if not self.points:
                    return False
                outer_path = QtGui.QPainterPath(self.points[0])
                for p in self.points[1:]:
                    outer_path.lineTo(p)
                outer_path.closeSubpath()
                return outer_path.contains(point)
        except Exception:
            # Fallback to default behavior if anything goes wrong
            pass
        return self.make_path().contains(point)

    def get_circle_rect_from_line(self, line):
        """Computes parameters to draw with `QPainterPath::addEllipse`"""
        if len(line) != 2:
            return None
        (c, _) = line
        r = line[0] - line[1]
        d = math.sqrt(math.pow(r.x(), 2) + math.pow(r.y(), 2))
        rectangle = QtCore.QRectF(c.x() - d, c.y() - d, 2 * d, 2 * d)
        return rectangle

    def make_path(self):
        """Create a path from shape"""
        if not self.points:
            return QtGui.QPainterPath()
        if not self._path_cache_dirty and self._path_cache is not None:
            return self._path_cache

        if self.shape_type == "rectangle":
            path = QtGui.QPainterPath()
            if len(self.points) == 2:
                rectangle = self.get_rect_from_line(*self.points)
                if rectangle is not None:
                    path.addRect(rectangle)
        elif self.shape_type == "circle":
            path = QtGui.QPainterPath()
            if len(self.points) == 2:
                rectangle = self.get_circle_rect_from_line(self.points)
                if rectangle is not None:
                    path.addEllipse(rectangle)
        else:
            # polygon / linestrip 共用此分支
            # points 至少有 1 个，提前保障
            path = QtGui.QPainterPath(self.points[0])
            for p in self.points[1:]:
                path.lineTo(p)

            # 对封闭多边形：闭合并支持洞（OddEven 填充规则）
            if self.shape_type == "polygon" and self.is_closed():
                path.closeSubpath()
                holes = None
                if isinstance(self.other_data, dict):
                    holes = self.other_data.get("holes")
                if holes:
                    path.setFillRule(QtCore.Qt.OddEvenFill)
                    try:
                        for hole in holes:
                            if not hole or len(hole) < 3:
                                continue
                            # hole 点既可为 [x, y] 数组，也可为 QPointF
                            first = hole[0]
                            if hasattr(first, 'x'):
                                path.moveTo(first)
                                iterable = hole[1:]
                            else:
                                path.moveTo(QtCore.QPointF(float(first[0]), float(first[1])))
                                iterable = hole[1:]
                            for hp in iterable:
                                if hasattr(hp, 'x'):
                                    path.lineTo(hp)
                                else:
                                    path.lineTo(QtCore.QPointF(float(hp[0]), float(hp[1])))
                            path.closeSubpath()
                    except Exception:
                        pass

        self._path_cache = path
        self._path_cache_dirty = False
        return path

    def bounding_rect(self):
        """Return bounding rectangle of the shape"""
        return self.make_path().boundingRect()

    def move_by(self, offset):
        """Move all points by an offset"""
        self.points = [p + offset for p in self.points]
        self.invalidate_path_cache()
        # 若存在洞（holes），整形拖动时需同步平移洞坐标
        try:
            if isinstance(self.other_data, dict) and self.other_data.get("holes"):
                moved_holes = []
                for hole in self.other_data.get("holes"):
                    if not hole:
                        moved_holes.append(hole)
                        continue
                    new_hole = []
                    for hp in hole:
                        if hasattr(hp, 'x'):
                            new_hole.append(hp + offset)
                        else:
                            # 兼容 [x, y] 形式
                            try:
                                new_hole.append([
                                    float(hp[0]) + float(offset.x()),
                                    float(hp[1]) + float(offset.y()),
                                ])
                            except Exception:
                                # 数据异常则跳过该点
                                continue
                    moved_holes.append(new_hole)
                self.other_data["holes"] = moved_holes
                self.invalidate_path_cache()
        except Exception:
            # 忽略洞平移中的异常，避免影响拖动
            pass

    def move_vertex_by(self, i, offset):
        """Move a specific vertex by an offset"""
        self.points[i] = self.points[i] + offset
        self.invalidate_path_cache()

    def highlight_vertex(self, i, action):
        """Highlight a vertex appropriately based on the current action

        Args:
            i (int): The vertex index
            action (int): The action
            (see Shape.NEAR_VERTEX and Shape.MOVE_VERTEX)
        """
        self._highlight_index = i
        self._highlight_mode = action

    def highlight_clear(self):
        """Clear the highlighted point"""
        self._highlight_index = None

    def copy(self):
        """Copy shape"""
        return copy.deepcopy(self)

    def __len__(self):
        return len(self.points)

    def __getitem__(self, key):
        return self.points[key]

    def __setitem__(self, key, value):
        if isinstance(key, int) and key >= len(self.points):
            self.points.extend([None] * (key + 1 - len(self.points)))
        self.points[key] = value
