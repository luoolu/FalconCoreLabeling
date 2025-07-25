import copy
import math

from PyQt5 import QtCore, QtGui

from . import utils

# TODO(unknown):
# - [opt] Store paths instead of creating new ones at each paint.


DEFAULT_LINE_COLOR = QtGui.QColor(0, 255, 0, 128)  # bf hovering
DEFAULT_FILL_COLOR = QtGui.QColor(100, 100, 100, 100)  # hovering
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
    line_width = 2
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

    def add_point(self, point):
        """Add a point"""
        if self.points and point == self.points[0]:
            self.close()
        else:
            self.points.append(point)

    def can_add_point(self):
        """Check if shape supports more points"""
        return self.shape_type in ["polygon", "linestrip"]

    def pop_point(self):
        """Remove and return the last point of the shape"""
        if self.points:
            return self.points.pop()
        return None

    def insert_point(self, i, point):
        """Insert a point to a specific index"""
        self.points.insert(i, point)

    def remove_point(self, i):
        """Remove point from a specific index"""
        self.points.pop(i)

    def is_closed(self):
        """Check if the shape is closed"""
        return self._closed

    def set_open(self):
        """Set shape to open - (_close=False)"""
        self._closed = False

    def get_rect_from_line(self, pt1, pt2):
        """Get rectangle from diagonal line"""
        x1, y1 = pt1.x(), pt1.y()
        x2, y2 = pt2.x(), pt2.y()
        return QtCore.QRectF(x1, y1, x2 - x1, y2 - y1)

    def paint(self, painter: QtGui.QPainter):  # noqa: max-complexity: 18
        """Paint shape using QPainter"""
        if self.points:
            color = self.select_line_color if self.selected else self.line_color
            pen = QtGui.QPen(color)
            # Try using integer sizes for smoother drawing(?)
            pen.setWidth(max(1, int(round(self.line_width / self.scale))))
            painter.setPen(pen)

            line_path = QtGui.QPainterPath()
            vrtx_path = QtGui.QPainterPath()

            if self.shape_type == "rectangle":
                assert len(self.points) in [1, 2]
                if len(self.points) == 2:
                    rectangle = self.get_rect_from_line(*self.points)
                    line_path.addRect(rectangle)
                if self.selected:
                    for i in range(len(self.points)):
                        self.draw_vertex(vrtx_path, i)
            elif self.shape_type == "circle":
                assert len(self.points) in [1, 2]
                if len(self.points) == 2:
                    rectangle = self.get_circle_rect_from_line(self.points)
                    line_path.addEllipse(rectangle)
                if self.selected:
                    for i in range(len(self.points)):
                        self.draw_vertex(vrtx_path, i)
            elif self.shape_type == "linestrip":
                line_path.moveTo(self.points[0])
                if self.selected:
                    self.draw_vertex(vrtx_path, 0)

                # Small improvement to start at the 2nd point and technically correct
                for i, p in enumerate(self.points[1:], start=1):
                    line_path.lineTo(p)
                    if self.selected:
                        self.draw_vertex(vrtx_path, i)

            elif self.shape_type == "point":
                assert len(self.points) == 1
                self.draw_vertex(vrtx_path, 0)
            else:
                line_path.moveTo(self.points[0])
                # Uncommenting the following line will draw 2 paths
                # for the 1st vertex, and make it non-filled, which
                # may be desirable.
                self.draw_vertex(vrtx_path, 0)

                # Small improvement to start at the 2nd point and technically correct
                for i, p in enumerate(self.points[1:], start=1):
                    line_path.lineTo(p)
                    if self.selected:
                        self.draw_vertex(vrtx_path, i)

                if self.is_closed():
                    # Properly close the path
                    line_path.closeSubpath()

            painter.drawPath(line_path)
            painter.drawPath(vrtx_path)
            if self._vertex_fill_color is not None:
                painter.fillPath(vrtx_path, self._vertex_fill_color)
            if self.fill:
                color = self.select_fill_color if self.selected else self.fill_color
                color.setAlpha(self.fill_opacity)
                painter.fillPath(line_path, color)

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

    def contains_point(self, point):
        """Check if shape contains a point"""
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
        if self.shape_type == "rectangle":
            path = QtGui.QPainterPath()
            if len(self.points) == 2:
                rectangle = self.get_rect_from_line(*self.points)
                path.addRect(rectangle)
        elif self.shape_type == "circle":
            path = QtGui.QPainterPath()
            if len(self.points) == 2:
                rectangle = self.get_circle_rect_from_line(self.points)
                path.addEllipse(rectangle)
        else:
            path = QtGui.QPainterPath(self.points[0])
            for p in self.points[1:]:
                path.lineTo(p)
        return path

    def bounding_rect(self):
        """Return bounding rectangle of the shape"""
        return self.make_path().boundingRect()

    def move_by(self, offset):
        """Move all points by an offset"""
        self.points = [p + offset for p in self.points]

    def move_vertex_by(self, i, offset):
        """Move a specific vertex by an offset"""
        self.points[i] = self.points[i] + offset

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
