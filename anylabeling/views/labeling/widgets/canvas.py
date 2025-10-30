"""This module defines Canvas widget - the core component for drawing image labels"""

import imgviz
import logging
import math
from time import time
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QWheelEvent

logger = logging.getLogger(__name__)

from anylabeling.services.auto_labeling.types import AutoLabelingMode

from .. import utils
from ..shape import Shape

CURSOR_DEFAULT = QtCore.Qt.ArrowCursor
CURSOR_POINT = QtCore.Qt.PointingHandCursor
CURSOR_DRAW = QtCore.Qt.CrossCursor
CURSOR_MOVE = QtCore.Qt.ClosedHandCursor
CURSOR_GRAB = QtCore.Qt.OpenHandCursor

MOVE_SPEED = 5.0

LABEL_COLORMAP = imgviz.label_colormap()


class Canvas(QtWidgets.QWidget):  # pylint: disable=too-many-public-methods, too-many-instance-attributes
    """Canvas widget to handle label drawing"""

    zoom_request = QtCore.pyqtSignal(int, QtCore.QPoint)
    scroll_request = QtCore.pyqtSignal(int, int)
    new_shape = QtCore.pyqtSignal()
    selection_changed = QtCore.pyqtSignal(list)
    shape_moved = QtCore.pyqtSignal()
    drawing_polygon = QtCore.pyqtSignal(bool)
    vertex_selected = QtCore.pyqtSignal(bool)
    auto_labeling_marks_updated = QtCore.pyqtSignal(list)
    circle_selection_completed = QtCore.pyqtSignal(list)  # 圈选完成信号
    manual_shared_edge_status = QtCore.pyqtSignal(str)
    CREATE, EDIT = 0, 1

    # polygon, rectangle, line, point, or circle_select
    _create_mode = "polygon"

    _fill_drawing = False

    def __init__(self, *args, **kwargs):
        self.epsilon = kwargs.pop("epsilon", 10.0)
        self.double_click = kwargs.pop("double_click", "close")
        if self.double_click not in [None, "close"]:
            raise ValueError(
                f"Unexpected value for double_click event: {self.double_click}"
            )
        self.num_backups = kwargs.pop("num_backups", 10)
        self.parent = kwargs.pop("parent")
        super().__init__(*args, **kwargs)
        # Initialise local state.
        self.mode = self.EDIT
        self.is_auto_labeling = False
        self.auto_labeling_mode: AutoLabelingMode = None
        self.shapes = []
        self.shapes_backups = []
        self.current = None
        self.selected_shapes = []  # save the selected shapes here
        self.selected_shapes_copy = []
        # self.line represents:
        #   - create_mode == 'polygon': edge from last point to current
        #   - create_mode == 'rectangle': diagonal line of the rectangle
        #   - create_mode == 'line': the line
        #   - create_mode == 'point': the point
        self.line = Shape()
        self.prev_point = QtCore.QPoint()
        self.prev_move_point = QtCore.QPoint()
        self.offsets = QtCore.QPointF(), QtCore.QPointF()
        self.scale = 1.0
        self.pixmap = QtGui.QPixmap()
        self.visible = {}
        self._hide_backround = False
        self.hide_backround = False
        self.h_hape = None
        self.prev_h_shape = None
        self.h_vertex = None
        self.prev_h_vertex = None
        self.h_edge = None
        self.prev_h_edge = None
        self.moving_shape = False
        self.snapping = True
        self.h_shape_is_selected = False
        self._painter = QtGui.QPainter()
        self._cursor = CURSOR_DEFAULT
        
        # Circle selection variables
        self.circle_selection_mode = False
        self.circle_selection_center = None
        self.circle_selection_radius = 0
        # Menus:
        # 0: right-click without selection and dragging of shapes
        # 1: right-click with selection and dragging of shapes
        self.menus = (QtWidgets.QMenu(), QtWidgets.QMenu())
        # Set widget options.
        self.setMouseTracking(True)
        # Need to throttle mouse move until further inspection
        self._last_update_time = time()
        self._update_interval = 0.016  # ~60Hz update rate
        self.setFocusPolicy(QtCore.Qt.WheelFocus)
        self.show_cross_line = True
        self.show_shape_groups = True
        self.show_texts = True
        
        # Shared edge functionality
        self.shared_edges_enabled = True  # Default: enabled
        self.shared_edge_snap_distance = 10.0  # pixels - distance for snapping during drawing
        self.edge_snapping_enabled = False  # Default: disabled (controlled separately from shared edges)
        self._snap_to_edge = None  # Current edge being snapped to: (shape, edge_idx, snap_point)
        self._snap_edge_points = []  # Points along the edge to snap to
        self._drawing_along_edge = False  # Flag indicating if drawing along an existing edge
        print(f"[CANVAS INIT] shared_edges_enabled={self.shared_edges_enabled}, edge_snapping_enabled={self.edge_snapping_enabled}")
        logger.info(f"[CANVAS INIT] shared_edges_enabled={self.shared_edges_enabled}, edge_snapping_enabled={self.edge_snapping_enabled}")


        self.is_loading = False
        self.loading_text = self.tr("Loading...")
        self.loading_angle = 0
        self.free_drawing_polygon = False
        self.pause_drawing_polygonp = False
        
        # ==================== 手动共边模式状态 ====================
        self.manual_shared_edge_mode = False
        self.manual_edge_step = 0
        self.manual_edge_target_shape = None
        self.manual_edge_source_shape = None
        self.manual_edge_target_points = []  # [start_idx, end_idx]
        self.manual_edge_source_points = []  # [start_idx, end_idx]
        self.manual_shared_edge_guides = []
        self.manual_edge_hover_index = None
        self.manual_edge_highlight_points = []
        # Lasso-based manual shared edge state
        self._manual_lasso_mode = False
        self._manual_lasso_target = None
        self._manual_lasso_source = None
        # One-time flag to suppress label dialog on next new_shape signal
        self._suppress_label_dialog_once = False

    def set_loading(self, is_loading: bool, loading_text: str = None):
        """Set loading state"""
        self.is_loading = is_loading
        if loading_text:
            self.loading_text = loading_text
        self.update()

    def set_auto_labeling_mode(self, mode: AutoLabelingMode):
        """Set auto labeling mode"""
        if mode == AutoLabelingMode.NONE:
            self.is_auto_labeling = False
            self.auto_labeling_mode = mode
        else:
            self.is_auto_labeling = True
            self.auto_labeling_mode = mode
            self.create_mode = mode.shape_type
            self.parent.toggle_draw_mode(
                False, mode.shape_type, disable_auto_labeling=False
            )

    def set_circle_selection_mode(self, enabled=True):
        """Enable or disable circle selection mode"""
        self.circle_selection_mode = enabled
        if enabled:
            self.mode = self.CREATE
            self.create_mode = "circle_select"
            self.set_editing(False)
        else:
            self.circle_selection_center = None
            self.circle_selection_radius = 0
            # 确保彻底清理圈选的临时绘制与交互状态
            try:
                if self.create_mode == "circle_select":
                    self.current = None
                self.free_drawing_polygon = False
                self.left_button_down = False
            except Exception:
                pass
            self.set_editing(True)
        self.update()

    def fill_drawing(self):
        """Get option to fill shapes by color"""
        return self._fill_drawing

    def set_fill_drawing(self, value):
        """Set shape filling option"""
        self._fill_drawing = value

    @property
    def create_mode(self):
        """Create mode for canvas - Modes: polygon, rectangle, circle,..."""
        return self._create_mode

    @create_mode.setter
    def create_mode(self, value):
        """Set create mode for canvas"""
        if value not in [
            "polygon",
            "rectangle",
            "circle",
            "line",
            "point",
            "linestrip",
            "circle_select",
        ]:
            raise ValueError(f"Unsupported create_mode: {value}")
        self._create_mode = value

    def store_shapes(self):
        """Store shapes for restoring later (Undo feature)"""
        shapes_backup = []
        for shape in self.shapes:
            shapes_backup.append(shape.copy())
        if len(self.shapes_backups) > self.num_backups:
            self.shapes_backups = self.shapes_backups[-self.num_backups - 1 :]
        self.shapes_backups.append(shapes_backup)

    @property
    def is_shape_restorable(self):
        """Check if shape can be restored from backup"""
        # We save the state AFTER each edit (not before) so for an
        # edit to be undoable, we expect the CURRENT and the PREVIOUS state
        # to be in the undo stack.
        if len(self.shapes_backups) < 2:
            return False
        return True

    def restore_shape(self):
        """Restore/Undo a shape"""
        # This does _part_ of the job of restoring shapes.
        # The complete process is also done in app.py::undoShapeEdit
        # and app.py::load_shapes and our own Canvas::load_shapes function.
        if not self.is_shape_restorable:
            return
        self.shapes_backups.pop()  # latest

        # The application will eventually call Canvas.load_shapes which will
        # push this right back onto the stack.
        shapes_backup = self.shapes_backups.pop()
        self.shapes = shapes_backup
        self.selected_shapes = []
        for shape in self.shapes:
            shape.selected = False
        self.update()

    def enterEvent(self, _):
        """Mouse enter event"""
        self.override_cursor(self._cursor)

    def leaveEvent(self, _):
        """Mouse leave event"""
        self.un_highlight()
        self.restore_cursor()

    def focusOutEvent(self, _):
        """Window out of focus event"""
        self.restore_cursor()

    def is_visible(self, shape):
        """Check if a shape is visible"""
        return self.visible.get(shape, True)

    def drawing(self):
        """Check if user is drawing (mode==CREATE)"""
        return self.mode == self.CREATE

    def editing(self):
        """Check if user is editing (mode==EDIT)"""
        return self.mode == self.EDIT

    def set_auto_labeling(self, value=True):
        """Set auto labeling mode"""
        self.is_auto_labeling = value
        if self.auto_labeling_mode is None:
            self.auto_labeling_mode = AutoLabelingMode.NONE
            self.parent.toggle_draw_mode(False, "rectangle", disable_auto_labeling=True)

    def get_mode(self):
        """Get current mode"""
        if self.is_auto_labeling and self.auto_labeling_mode != AutoLabelingMode.NONE:
            return self.tr("Auto Labeling")
        if self.mode == self.CREATE:
            return self.tr("Drawing")
        elif self.mode == self.EDIT:
            return self.tr("Editing")
        else:
            return self.tr("Unknown")

    def set_editing(self, value=True):
        """Set editing mode. Editing is set to False, user is drawing"""
        self.mode = self.EDIT if value else self.CREATE
        if not value:  # Create
            self.un_highlight()
            # When entering create mode for manual shared-edge (lasso), keep both
            # selected polygons highlighted for better user observation
            if not getattr(self, "_manual_lasso_mode", False):
                self.deselect_shape()

    def un_highlight(self):
        """Unhighlight shape/vertex/edge"""
        if self.h_hape:
            self.h_hape.highlight_clear()
            self.update()
        self.prev_h_shape = self.h_hape
        self.prev_h_vertex = self.h_vertex
        self.prev_h_edge = self.h_edge
        self.h_hape = self.h_vertex = self.h_edge = None

    def selected_vertex(self):
        """Check if selected a vertex"""
        return self.h_vertex is not None

    def selected_edge(self):
        """Check if selected an edge"""
        return self.h_edge is not None

    # QT Overload
    def mouseMoveEvent(self, ev):  # noqa: C901
        """Update line with last point and current coordinates"""
        if self.is_loading:
            return
        try:
            pos = self.transform_pos(ev.localPos())
        except AttributeError:
            return

        self.prev_move_point = pos

        # Throttle the update call for now until more optimization is made
        current_time = time()
        if current_time - self._last_update_time > self._update_interval:
            # Update will better optimize the the call to repaint
            self.update()
            self._last_update_time = current_time

        self.restore_cursor()

        # Polygon / special-mode drawing.
        if self.drawing():
            # Handle lasso-like selection for circle_select mode using a temp polygon
            if self.create_mode == "circle_select":
                self.override_cursor(CURSOR_DRAW)
                if not self.current:
                    return
                if self.out_off_pixmap(pos):
                    pos = self.intersection_point(self.current[-1], pos)
                # emulate freehand polygon drawing
                self.line.shape_type = "polygon"
                self.line[0] = self.current[-1]
                self.line[1] = pos
                if not self.close_enough(pos, self.current[-1]):
                    self.current.add_point(pos)
                    self.line[0] = self.current[-1]
                self.repaint()
                self.current.highlight_clear()
                return

            # Normal shape drawing modes
            self.line.shape_type = self.create_mode

            self.override_cursor(CURSOR_DRAW)
            if not self.current:
                return

            if self.out_off_pixmap(pos):
                # Don't allow the user to draw outside the pixmap.
                # Project the point to the pixmap's edges.
                pos = self.intersection_point(self.current[-1], pos)
            elif (
                self.snapping
                and len(self.current) > 1
                and self.create_mode == "polygon"
                and self.close_enough(pos, self.current[0])
            ):
                # Attract line to starting point and
                # colorise to alert the user.
                pos = self.current[0]
                self.override_cursor(CURSOR_POINT)
                self.current.highlight_vertex(0, Shape.NEAR_VERTEX)
            
            if self.create_mode in ["polygon", "linestrip"]:
                # Check for edge snapping when enabled
                if self.edge_snapping_enabled and self.create_mode == "polygon":
                    nearest_edge = self.find_nearest_edge(pos)
                    if nearest_edge:
                        shape, edge_idx, snap_point = nearest_edge
                        self._snap_to_edge = nearest_edge
                        pos = snap_point  # Snap cursor to the nearest point on edge
                        self._snap_edge_points = self.get_edge_points_for_snapping(shape, edge_idx)
                    else:
                        self._snap_to_edge = None
                        self._snap_edge_points = []
                else:
                    self._snap_to_edge = None
                    self._snap_edge_points = []
                
                self.line[0] = self.current[-1]
                self.line[1] = pos
                if self.create_mode == "polygon" and self.free_drawing_polygon:
                    if not self.close_enough(pos, self.current[-1]):
                        self.current.add_point(pos)
                        self.line[0] = self.current[-1]
            elif self.create_mode == "rectangle":
                self.line.points = [self.current[0], pos]
                self.line.close()
            elif self.create_mode == "circle":
                self.line.points = [self.current[0], pos]
                self.line.shape_type = "circle"
            elif self.create_mode == "line":
                self.line.points = [self.current[0], pos]
                self.line.close()
            elif self.create_mode == "point":
                self.line.points = [self.current[0]]
                self.line.close()
            self.repaint()
            self.current.highlight_clear()
            return

        # Polygon copy moving.
        if QtCore.Qt.RightButton & ev.buttons():
            if self.selected_shapes_copy and self.prev_point:
                self.override_cursor(CURSOR_MOVE)
                self.bounded_move_shapes(self.selected_shapes_copy, pos)
                self.repaint()
            elif self.selected_shapes:
                self.selected_shapes_copy = [s.copy() for s in self.selected_shapes]
                self.repaint()
            return

        # Polygon/Vertex moving.
        if QtCore.Qt.LeftButton & ev.buttons():
            if self.selected_vertex():
                self.bounded_move_vertex(pos)
                self.repaint()
                self.moving_shape = True
            elif self.selected_shapes and self.prev_point:
                self.override_cursor(CURSOR_MOVE)
                self.bounded_move_shapes(self.selected_shapes, pos)
                self.repaint()
                self.moving_shape = True
            return

        # Just hovering over the canvas, 2 possibilities:
        # - Highlight shapes
        # - Highlight vertex
        # Update shape/vertex fill and tooltip value accordingly.
        # 圈选模式下不显示工具提示，避免遮挡
        if self.create_mode != "circle_select":
            self.setToolTip(self.tr("Image"))
        else:
            self.setToolTip("")
        for shape in reversed([s for s in self.shapes if self.is_visible(s)]):
            # Look for a nearby vertex to highlight. If that fails,
            # check if we happen to be inside a shape.
            index = shape.nearest_vertex(pos, self.epsilon / self.scale)
            # 支持洞：若命中外环失败，再尝试洞边最近边
            index_edge = shape.nearest_edge(pos, self.epsilon / self.scale)
            hole_edge = None
            if index_edge is None and getattr(shape, 'shape_type', None) == 'polygon':
                try:
                    hole_edge = shape.nearest_edge_with_holes(pos, self.epsilon / self.scale)
                except Exception:
                    hole_edge = None
            if index is not None:
                if self.selected_vertex():
                    self.h_hape.highlight_clear()
                self.prev_h_vertex = self.h_vertex = index
                self.prev_h_shape = self.h_hape = shape
                self.prev_h_edge = self.h_edge
                self.h_edge = None
                shape.highlight_vertex(index, shape.MOVE_VERTEX)
                self.override_cursor(CURSOR_POINT)
                if self.create_mode != "circle_select":
                    self.setToolTip(self.tr("Click & drag to move point"))
                self.setStatusTip(self.toolTip())
                self.update()
                break
            if (index_edge is not None or (isinstance(hole_edge, tuple))) and shape.can_add_point():
                if self.selected_vertex():
                    self.h_hape.highlight_clear()
                self.prev_h_vertex = self.h_vertex
                self.h_vertex = None
                self.prev_h_shape = self.h_hape = shape
                # 记录外环或洞边的索引，用于插点
                if isinstance(hole_edge, tuple):
                    self.prev_h_edge = self.h_edge = (hole_edge[0], hole_edge[1])  # (edge_idx, hole_idx)
                else:
                    self.prev_h_edge = self.h_edge = index_edge
                self.override_cursor(CURSOR_POINT)
                if self.create_mode != "circle_select":
                    self.setToolTip(self.tr("Click to create point"))
                self.setStatusTip(self.toolTip())
                self.update()
                break
            if shape.contains_point(pos):
                if self.selected_vertex():
                    self.h_hape.highlight_clear()
                self.prev_h_vertex = self.h_vertex
                self.h_vertex = None
                self.prev_h_shape = self.h_hape = shape
                self.prev_h_edge = self.h_edge
                self.h_edge = None
                if self.create_mode != "circle_select":
                    self.setToolTip(
                        self.tr("Click & drag to move shape '%s'") % shape.label
                    )
                self.setStatusTip(self.toolTip())
                self.override_cursor(CURSOR_GRAB)
                self.update()
                break
        else:  # Nothing found, clear highlights, reset state.
            self.un_highlight()
        self.vertex_selected.emit(self.h_vertex is not None)

    def add_point_to_edge(self):
        """Add a point to current shape"""
        shape = self.prev_h_shape
        index = self.prev_h_edge
        point = self.prev_move_point
        if shape is None or index is None or point is None:
            return
        shape.insert_point(index, point)
        shape.highlight_vertex(index, shape.MOVE_VERTEX)
        self.h_hape = shape
        self.h_vertex = index
        self.h_edge = None
        self.moving_shape = True

    def remove_selected_point(self):
        """Remove a point from current shape"""
        shape = self.prev_h_shape
        index = self.prev_h_vertex
        if shape is None or index is None:
            return
        shape.remove_point(index)
        shape.highlight_clear()
        self.h_hape = shape
        self.prev_h_vertex = None
        self.moving_shape = True  # Save changes

    # QT Overload
    def mousePressEvent(self, ev):
        """Mouse press event"""
        if self.is_loading:
            return
        pos = self.transform_pos(ev.localPos())
        
        # 处理手动共边模式的点击
        
        if ev.button() == QtCore.Qt.LeftButton:
            self.left_button_down = True
            if self.drawing():
                if self.current:
                    # Add point to existing shape.
                    if self.create_mode == "polygon":
                        # 如果点击位置与起始点重合，则直接闭合
                        if self.close_enough(pos, self.current[0]):
                            self.current.add_point(QtCore.QPointF(self.current[0]))
                            self._drawing_along_edge = False
                            self.finalise()
                            return

                        # If snapping to an edge, check if we should follow the edge
                        if self._snap_to_edge and self.shared_edges_enabled:
                            shape, edge_idx, snap_point = self._snap_to_edge
                            
                            # Check if clicking near an endpoint of the snapped edge
                            edge_points = self.get_edge_points_for_snapping(shape, edge_idx)
                            near_endpoint = False
                            endpoint_to_add = None
                            
                            for ep in edge_points:
                                if utils.distance(snap_point - ep) < (self.epsilon / self.scale):
                                    near_endpoint = True
                                    endpoint_to_add = ep
                                    break
                            
                            if near_endpoint and endpoint_to_add:
                                # Start following the edge
                                self.current.add_point(endpoint_to_add)
                                self._drawing_along_edge = True
                            else:
                                # Add the snapped point
                                self.current.add_point(snap_point)
                        else:
                            self.current.add_point(self.line[1])
                        
                        self.line[0] = self.current[-1]
                        if self.current.is_closed():
                            self._drawing_along_edge = False
                            self.finalise()
                    elif self.create_mode in ["rectangle", "circle", "line"]:
                        assert len(self.current.points) == 1
                        self.current.points = self.line.points
                        self.finalise()
                    elif self.create_mode == "linestrip":
                        self.current.add_point(self.line[1])
                        self.line[0] = self.current[-1]
                        if int(ev.modifiers()) == QtCore.Qt.ControlModifier:
                            self.finalise()
                elif not self.out_off_pixmap(pos):
                    # Create new drawing state.
                    if self.create_mode == "circle_select":
                        # 圈选：使用临时 polygon 形状作为可视化，不会落盘
                        self.current = Shape(shape_type="polygon")
                        self.current.add_point(pos)
                        self.free_drawing_polygon = True
                        self.line.points = [pos, pos]
                        self.set_hiding()
                        self.drawing_polygon.emit(True)
                        self.update()
                    else:
                        self.current = Shape(shape_type=self.create_mode)
                        self.current.add_point(pos)
                        if self.create_mode == "polygon":
                            self.free_drawing_polygon = True
                        if self.create_mode == "point":
                            self.finalise()
                        else:
                            if self.create_mode == "circle":
                                self.current.shape_type = "circle"
                            self.line.points = [pos, pos]
                            self.set_hiding()
                            self.drawing_polygon.emit(True)
                            self.update()
            elif self.editing():
                if self.selected_edge():
                    # 支持对洞边插点：self.h_edge 可能为 (edge_idx, hole_idx)
                    edge = self.h_edge
                    if isinstance(edge, tuple) and len(edge) == 2 and isinstance(edge[1], int):
                        # 在洞边插点
                        shape = self.prev_h_shape
                        index = edge[0]
                        point = self.prev_move_point
                        shape.insert_point_into_hole(edge[1], index, point)
                        # 高亮该新点，进入移动模式
                        self.h_hape = shape
                        self.h_vertex = None
                        self.h_edge = None
                        self.moving_shape = True
                    else:
                        # 特例：反选背景形状在边线上点击视为"选择以便拖动"，而不是插点
                        try:
                            is_bg_invert = (
                                self.h_hape is not None
                                and getattr(self.h_hape, 'shape_type', None) == 'polygon'
                                and isinstance(getattr(self.h_hape, 'other_data', None), dict)
                                and self.h_hape.other_data.get('select_through_holes') is True
                            )
                        except Exception:
                            is_bg_invert = False

                        if is_bg_invert:
                            group_mode = int(ev.modifiers()) == QtCore.Qt.ControlModifier
                            base = list(self.selected_shapes) if group_mode else []
                            new_selection = []
                            for s in base + [self.h_hape]:
                                if s not in new_selection:
                                    new_selection.append(s)
                            self.selection_changed.emit(new_selection)
                            # 不插点，直接进入拖动准备
                        else:
                            self.add_point_to_edge()
                elif (
                    self.selected_vertex()
                    and int(ev.modifiers()) == QtCore.Qt.ShiftModifier
                ):
                    # Delete point if: left-click + SHIFT on a point
                    self.remove_selected_point()

                group_mode = int(ev.modifiers()) == QtCore.Qt.ControlModifier
                self.select_shape_point(pos, multiple_selection_mode=group_mode)
                self.prev_point = pos
                self.repaint()
        elif ev.button() == QtCore.Qt.RightButton and self.editing():
            group_mode = int(ev.modifiers()) == QtCore.Qt.ControlModifier
            if not self.selected_shapes or (
                self.h_hape is not None and self.h_hape not in self.selected_shapes
            ):
                self.select_shape_point(pos, multiple_selection_mode=group_mode)
                self.repaint()
            self.prev_point = pos

    # QT Overload
    def mouseReleaseEvent(self, ev):
        """
        Mouse release event

        自由手绘多边形改进：
        - 在 polygon 创建模式下，松开左键后 **不再** 把 `self.free_drawing_polygon`
          设为 False；这样首击后即可放手，自由移动鼠标持续绘制。
        - 其他形状模式、编辑模式和右键逻辑保持不变。
        """
        if self.is_loading:
            return

        # ---------- 右键释放：菜单 / 复制移动 ----------
        if ev.button() == QtCore.Qt.RightButton:
            menu = self.menus[len(self.selected_shapes_copy) > 0]
            self.restore_cursor()
            if (
                    not menu.exec_(self.mapToGlobal(ev.pos()))
                    and self.selected_shapes_copy
            ):
                # 取消移动，删除影子拷贝
                self.selected_shapes_copy = []
                self.repaint()
            return  # 右键逻辑到此结束

        # ---------- 左键释放 ----------
        if ev.button() == QtCore.Qt.LeftButton:
            self.left_button_down = False

            # 处理圈选模式的完成：仅当"闭合"时才触发
            if self.create_mode == "circle_select":
                if self.current and len(self.current.points) >= 3 and self.close_enough(self.current[-1], self.current[0]):
                    # 近似圆（外接框）
                    xs = [p.x() for p in self.current.points]
                    ys = [p.y() for p in self.current.points]
                    cx = (min(xs) + max(xs)) / 2.0
                    cy = (min(ys) + max(ys)) / 2.0
                    rx = (max(xs) - min(xs)) / 2.0
                    ry = (max(ys) - min(ys)) / 2.0
                    self.circle_selection_center = QtCore.QPointF(cx, cy)
                    self.circle_selection_radius = float(max(rx, ry))
                    self.complete_circle_selection()
                    # 清理临时绘制
                    self.current = None
                    self.free_drawing_polygon = False
                    self.update()
                # 未闭合：什么也不做，保持圈选继续
                return

            # 关键改动：不再在此处关闭 freehand
            # -------------------------------------------------
            # 旧实现曾有：
            # if self.drawing() and self.create_mode == "polygon" and self.free_drawing_polygon:
            #     self.free_drawing_polygon = False
            #     return
            # 现已移除，让 freehand 状态持续，直到双击 finalize
            # -------------------------------------------------

            # 不再在鼠标释放时自动取消选中，避免"第一次点击后立刻被取消"的问题。
            # 选中/取消选中的逻辑仅在 mousePressEvent 中处理。

            # ------- 结束形状/顶点移动并记录 undo -------
            if self.moving_shape and self.h_hape:
                try:
                    index = self.shapes.index(self.h_hape)
                    # 增强安全检查：确保备份存在且索引有效
                    should_store = False
                    if (self.shapes_backups and 
                        len(self.shapes_backups) > 0 and 
                        index < len(self.shapes)):
                        
                        # 确保备份中有对应的形状
                        last_backup = self.shapes_backups[-1]
                        if (len(last_backup) > index and 
                            index >= 0 and 
                            hasattr(last_backup[index], 'points') and
                            hasattr(self.shapes[index], 'points')):
                            
                            # 比较点是否发生变化
                            try:
                                if last_backup[index].points != self.shapes[index].points:
                                    should_store = True
                            except (AttributeError, TypeError):
                                # 如果比较失败，保守地存储状态
                                should_store = True
                    
                    if should_store:
                        self.store_shapes()
                        self.shape_moved.emit()
                        
                except (ValueError, IndexError, AttributeError) as e:
                    # 如果索引无效或形状不存在，安全地忽略
                    logger.warning("处理形状移动时发生错误: %s", str(e))
                finally:
                    self.moving_shape = False

    def end_move(self, copy):
        """End of move"""
        assert self.selected_shapes and self.selected_shapes_copy
        assert len(self.selected_shapes_copy) == len(self.selected_shapes)
        if copy:
            for i, shape in enumerate(self.selected_shapes_copy):
                self.shapes.append(shape)
                self.selected_shapes[i].selected = False
                self.selected_shapes[i] = shape
        else:
            for i, shape in enumerate(self.selected_shapes_copy):
                self.selected_shapes[i].points = shape.points
        
        # Apply polygon clipping and shared edge snapping after moving polygons
        print(f"[END_MOVE] shared_edges_enabled={self.shared_edges_enabled}")
        logger.info(f"[END_MOVE] shared_edges_enabled={self.shared_edges_enabled}")
        if self.shared_edges_enabled:
            for shape in self.selected_shapes[:]:  # Use slice to avoid modification during iteration
                if hasattr(shape, 'shape_type') and shape.shape_type == 'polygon' and shape.is_closed():
                    # Get all other shapes excluding the currently selected ones
                    other_shapes = [s for s in self.shapes 
                                   if s not in self.selected_shapes
                                   and hasattr(s, 'shape_type')
                                   and s.shape_type == 'polygon'
                                   and s.is_closed()]
                    
                    if not other_shapes:
                        continue
                    
                    # Apply multi-polygon clipping
                    print(f"[END_MOVE] Clipping moved shape against {len(other_shapes)} other shapes")
                    clipped_result = self.clip_polygon_by_multiple_polygons(shape, other_shapes)
                    
                    if clipped_result is None:
                        # Shape is completely inside other shapes - remove it
                        print(f"[END_MOVE] Shape completely clipped, removing")
                        if shape in self.shapes:
                            self.shapes.remove(shape)
                        if shape in self.selected_shapes:
                            self.selected_shapes.remove(shape)
                    elif len(clipped_result) >= 3:
                        # Update the shape with clipped points
                        print(f"[END_MOVE] Updating shape with {len(clipped_result)} clipped points")
                        shape.points = clipped_result
                        if hasattr(shape, "invalidate_path_cache"):
                            shape.invalidate_path_cache()
                    else:
                        # Too few points after clipping - remove it
                        print(f"[END_MOVE] Shape has only {len(clipped_result)} points after clipping, removing")
                        if shape in self.shapes:
                            self.shapes.remove(shape)
                        if shape in self.selected_shapes:
                            self.selected_shapes.remove(shape)
        
        self.selected_shapes_copy = []
        self.repaint()
        self.store_shapes()
        return True

    def hide_background_shapes(self, value):
        """Set hide background - hide other shapes when some shapes are selected"""
        self.hide_backround = value
        if self.selected_shapes:
            # Only hide other shapes if there is a current selection.
            # Otherwise the user will not be able to select a shape.
            self.set_hiding(True)
            self.update()

    def set_hiding(self, enable=True):
        """Set background hiding"""
        self._hide_backround = self.hide_backround if enable else False

    def can_close_shape(self):
        """Check if a shape can be closed (number of points > 2)"""
        return self.drawing() and self.current and len(self.current) > 2

    # QT Overload
    def mouseDoubleClickEvent(self, _):
        """Mouse double click event"""
        if self.is_loading:
            return
        # We need at least 4 points here, since the mousePress handler
        # adds an extra one before this handler is called.
        if (
            self.double_click == "close"
            and self.can_close_shape()
            and len(self.current) >= 3
        ):
            if not self.current.is_closed():
                start_pt = self.current[0]
                if self._points_close(self.current[-1], start_pt):
                    self.current[-1] = QtCore.QPointF(start_pt)
                else:
                    self.current.add_point(QtCore.QPointF(start_pt))
            self.finalise()

    def select_shapes(self, shapes):
        """Select some shapes"""
        self.set_hiding()
        self.selection_changed.emit(shapes)
        self.update()

    def select_shape_point(self, point, multiple_selection_mode):
        """Select the first shape created which contains this point."""
        if self.selected_vertex():  # A vertex is marked for selection.
            index, shape = self.h_vertex, self.h_hape
            shape.highlight_vertex(index, shape.MOVE_VERTEX)
        else:
            for shape in reversed(self.shapes):
                if self.is_visible(shape) and shape.contains_point(point):
                    self.set_hiding()
                    # 检测是否为"反选背景形状"：需要让洞内命中的内轮廓也一起选中
                    select_through = (
                        getattr(shape, 'shape_type', None) == 'polygon'
                        and isinstance(getattr(shape, 'other_data', None), dict)
                        and shape.other_data.get('select_through_holes') is True
                    )

                    shapes_to_add = [shape]
                    if select_through:
                        # 收集同一点命中的其它形状（忽略其它背景形状）
                        for s in reversed(self.shapes):
                            if s is shape:
                                continue
                            if not self.is_visible(s):
                                continue
                            # 忽略其它"背景反选形状"
                            if (
                                getattr(s, 'shape_type', None) == 'polygon'
                                and isinstance(getattr(s, 'other_data', None), dict)
                                and s.other_data.get('select_through_holes') is True
                            ):
                                continue
                            if s.contains_point(point):
                                shapes_to_add.append(s)
                                # 一般情况下唯一命中，但如有重叠则全部加入

                    # 计算新的选中集合
                    if multiple_selection_mode:
                        base = list(self.selected_shapes)
                    else:
                        base = []
                    # 去重并保持顺序
                    new_selection = []
                    for s in base + shapes_to_add:
                        if s not in new_selection:
                            new_selection.append(s)

                    self.selection_changed.emit(new_selection)
                    self.h_shape_is_selected = shape in self.selected_shapes
                    self.calculate_offsets(point)
                    return
        self.deselect_shape()

    def calculate_offsets(self, point):
        """Calculate offsets of a point to pixmap borders"""
        left = self.pixmap.width() - 1
        right = 0
        top = self.pixmap.height() - 1
        bottom = 0
        for s in self.selected_shapes:
            rect = s.bounding_rect()
            if rect.left() < left:
                left = rect.left()
            if rect.right() > right:
                right = rect.right()
            if rect.top() < top:
                top = rect.top()
            if rect.bottom() > bottom:
                bottom = rect.bottom()

        x1 = left - point.x()
        y1 = top - point.y()
        x2 = right - point.x()
        y2 = bottom - point.y()
        self.offsets = QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2)

    def bounded_move_vertex(self, pos):
        """Move a vertex. Adjust position to be bounded by pixmap border"""
        index, shape = self.h_vertex, self.h_hape
        point = shape[index]
        if self.out_off_pixmap(pos):
            pos = self.intersection_point(point, pos)
        shape.move_vertex_by(index, pos - point)

    def bounded_move_shapes(self, shapes, pos):
        """Move shapes. Adjust position to be bounded by pixmap border"""
        if self.out_off_pixmap(pos):
            return False  # No need to move
        o1 = pos + self.offsets[0]
        if self.out_off_pixmap(o1):
            pos -= QtCore.QPoint(min(0, int(o1.x())), min(0, int(o1.y())))
        o2 = pos + self.offsets[1]
        if self.out_off_pixmap(o2):
            pos += QtCore.QPoint(
                min(0, int(self.pixmap.width() - o2.x())),
                min(0, int(self.pixmap.height() - o2.y())),
            )
        # XXX: The next line tracks the new position of the cursor
        # relative to the shape, but also results in making it
        # a bit "shaky" when nearing the border and allows it to
        # go outside of the shape's area for some reason.
        # self.calculateOffsets(self.selectedShapes, pos)
        dp = pos - self.prev_point
        if dp:
            for shape in shapes:
                shape.move_by(dp)
            self.prev_point = pos
            return True
        return False

    def deselect_shape(self):
        """Deselect all shapes"""
        if self.selected_shapes:
            self.set_hiding(False)
            self.selection_changed.emit([])
            self.h_shape_is_selected = False
            self.update()

    def delete_selected(self):
        """Remove selected shapes"""
        deleted_shapes = []
        if self.selected_shapes:
            # 复制列表，避免迭代时修改导致异常；且仅在形状仍存在时删除
            for shape in list(self.selected_shapes):
                if shape in self.shapes:
                    self.shapes.remove(shape)
                    deleted_shapes.append(shape)
            self.store_shapes()
            # 清空选择并恢复背景显示，避免剩余轮廓被隐藏
            self.selected_shapes = []
            self.set_hiding(False)
            self.update()
        return deleted_shapes

    def complete_circle_selection(self):
        """完成圈选操作，找到圈内的所有形状"""
        if not self.circle_selection_center or self.circle_selection_radius <= 0:
            return

        selected_shapes = []
        center_x = self.circle_selection_center.x()
        center_y = self.circle_selection_center.y()
        radius = self.circle_selection_radius

        # 查找圈内的所有形状
        for shape in self.shapes:
            if self.is_visible(shape) and self.is_shape_inside_circle(shape, center_x, center_y, radius):
                selected_shapes.append(shape)

        # 发送圈选完成信号
        self.circle_selection_completed.emit(selected_shapes)

        # 重置圈选状态
        self.circle_selection_center = None
        self.circle_selection_radius = 0
        self.current = None
        self.update()

    def is_shape_inside_circle(self, shape, center_x, center_y, radius):
        """更精确地判断形状是否应被圈选。

        策略（由严到松）：
        1) 形状质心在圆内 → 选中
        2) 圆心在形状内（shape.contains_point）→ 选中
        3) 多边形（>=4点）：至少一半顶点在圆内 → 选中
        n<=3（矩形使用2点表示、线、点）：要求质心在圆内或全部顶点在圆内
        目的：避免"只触碰一个点却选中大量形状"的误选。
        """
        try:
            if not hasattr(shape, 'points') or not shape.points:
                return False

            cx, cy = float(center_x), float(center_y)
            r2 = float(radius) * float(radius)

            def point_in_circle(px, py):
                dx = float(px) - cx
                dy = float(py) - cy
                return (dx * dx + dy * dy) <= r2

            # 1) 质心判断
            pts = shape.points
            n = len(pts)
            if n >= 3:
                # 多边形质心（面积法），退化时用平均法
                try:
                    area = 0.0
                    cx_poly = 0.0
                    cy_poly = 0.0
                    for i in range(n):
                        x1 = float(pts[i].x())
                        y1 = float(pts[i].y())
                        x2 = float(pts[(i + 1) % n].x())
                        y2 = float(pts[(i + 1) % n].y())
                        cross = x1 * y2 - x2 * y1
                        area += cross
                        cx_poly += (x1 + x2) * cross
                        cy_poly += (y1 + y2) * cross
                    if abs(area) > 1e-6:
                        area *= 0.5
                        cx_poly /= (6.0 * area)
                        cy_poly /= (6.0 * area)
                    else:
                        # 面积近似为 0：退化为平均点
                        cx_poly = sum(float(p.x()) for p in pts) / n
                        cy_poly = sum(float(p.y()) for p in pts) / n
                except Exception:
                    cx_poly = sum(float(p.x()) for p in pts) / n
                    cy_poly = sum(float(p.y()) for p in pts) / n
            else:
                # 点/线/矩形(2点)等：使用点的平均
                cx_poly = sum(float(p.x()) for p in pts) / n
                cy_poly = sum(float(p.y()) for p in pts) / n

            if point_in_circle(cx_poly, cy_poly):
                return True

            # 2) 圆心是否在形状内
            try:
                if shape.contains_point(QtCore.QPointF(cx, cy)):
                    return True
            except Exception:
                pass

            # 3) 顶点比例判断
            inside_count = 0
            for point in pts:
                if point_in_circle(point.x(), point.y()):
                    inside_count += 1

            if n >= 4:
                # 多边形：至少一半顶点在圆内
                return inside_count >= (n / 2.0)
            else:
                # n<=3：必须"全部在内"，避免单点触碰误选
                return inside_count == n
        except Exception:
            return False

    def delete_shape(self, shape):
        """Remove a specific shape"""
        if shape in self.selected_shapes:
            self.selected_shapes.remove(shape)
        if shape in self.shapes:
            self.shapes.remove(shape)
        self.store_shapes()
        # 删除单个形状后同样确保不处于隐藏其他轮廓的状态
        if not self.selected_shapes:
            self.set_hiding(False)
        self.update()

    def duplicate_selected_shapes(self):
        """Duplicate selected shapes"""
        if self.selected_shapes:
            self.selected_shapes_copy = [s.copy() for s in self.selected_shapes]
            self.bounded_shift_shapes(self.selected_shapes_copy)
            self.end_move(copy=True)
        return self.selected_shapes

    def bounded_shift_shapes(self, shapes):
        """
        Shift shapes by an offset. Adjust positions to be bounded
        by pixmap borders
        """
        # Try to move in one direction, and if it fails in another.
        # Give up if both fail.
        point = shapes[0][0]
        offset = QtCore.QPointF(2.0, 2.0)
        self.offsets = QtCore.QPointF(), QtCore.QPointF()
        self.prev_point = point
        if not self.bounded_move_shapes(shapes, point - offset):
            self.bounded_move_shapes(shapes, point + offset)

    # QT Overload
    def paintEvent(self, event):  # noqa: C901
        """Paint event for canvas"""
        if self.pixmap is None or self.pixmap.width() == 0 or self.pixmap.height() == 0:
            super().paintEvent(event)
            return

        p = self._painter
        p.begin(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

        p.scale(self.scale, self.scale)
        p.translate(self.offset_to_center())

        p.drawPixmap(0, 0, self.pixmap)
        Shape.scale = self.scale

        # Draw loading/waiting screen
        if self.is_loading:
            # Draw a semi-transparent rectangle
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0, 20))
            p.drawRect(self.pixmap.rect())

            # Draw a spinning wheel
            p.setPen(QtGui.QColor(255, 255, 255))
            p.setBrush(Qt.NoBrush)
            p.save()
            p.translate(self.pixmap.width() / 2, self.pixmap.height() / 2 - 50)
            p.rotate(self.loading_angle)
            p.drawEllipse(-20, -20, 40, 40)
            p.drawLine(0, 0, 0, -20)
            p.restore()
            self.loading_angle += 30
            if self.loading_angle >= 360:
                self.loading_angle = 0

            # Draw the loading text
            min_dim = max(1, min(self.pixmap.width(), self.pixmap.height()))
            font_size = int(min_dim * 0.042)
            font_size = max(26, min(font_size, 72))
            font = QtGui.QFont("Arial", font_size)
            font.setBold(True)
            p.setFont(font)

            metrics = QtGui.QFontMetrics(font)
            text_rect = metrics.boundingRect(self.loading_text)
            padding_h = max(font_size // 2, 24)
            padding_v = max(font_size // 3, 16)
            margins = QtCore.QMargins(padding_h, padding_v, padding_h, padding_v)
            text_rect = text_rect.marginsAdded(margins)
            center_point = QtCore.QPoint(
                self.pixmap.width() // 2,
                self.pixmap.height() // 2 + font_size,
            )
            text_rect.moveCenter(center_point)

            p.save()
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0, 160))
            p.drawRoundedRect(text_rect, 12, 12)
            p.restore()

            p.setPen(QtGui.QColor(255, 255, 255))
            p.drawText(text_rect, Qt.AlignCenter, self.loading_text)
            p.end()
            self.update()
            return

        # Draw groups (用户要求与正常显示一致，不显示白色虚线框和中心点)
        if self.show_shape_groups and False:
            pen = QtGui.QPen(QtGui.QColor("#AAAAAA"), 2, Qt.SolidLine)
            p.setPen(pen)
            grouped_shapes = {}
            for shape in self.shapes:
                if shape.group_id is None:
                    continue
                if shape.group_id not in grouped_shapes:
                    grouped_shapes[shape.group_id] = []
                grouped_shapes[shape.group_id].append(shape)

            for group_id in grouped_shapes:
                shapes = grouped_shapes[group_id]
                min_x = float("inf")
                min_y = float("inf")
                max_x = 0
                max_y = 0
                for shape in shapes:
                    rect = shape.bounding_rect()
                    min_x = min(min_x, rect.x())
                    min_y = min(min_y, rect.y())
                    max_x = max(max_x, rect.x() + rect.width())
                    max_y = max(max_y, rect.y() + rect.height())
                    # 兼容字符串型 group_id：对其做稳定哈希映射到调色板索引
                    try:
                        idx = int(group_id)
                    except Exception:
                        idx = abs(hash(str(group_id)))
                    group_color = LABEL_COLORMAP[idx % len(LABEL_COLORMAP)]
                    pen.setStyle(Qt.SolidLine)
                    pen.setWidth(max(1, int(round(4.0 / Shape.scale))))
                    pen.setColor(QtGui.QColor(*group_color))
                    p.setPen(pen)
                    cx = rect.x() + rect.width() / 2
                    cy = rect.y() + rect.height() / 2
                    circle_radius = max(1, int(round(3.0 / Shape.scale)))
                    p.drawEllipse(
                        QtCore.QRectF(
                            cx - circle_radius,
                            cy - circle_radius,
                            2 * circle_radius,
                            2 * circle_radius,
                        )
                    )
                # 旧版会画白色虚线包围框，这里直接去掉

        for shape in self.shapes:
            if (shape.selected or not self._hide_backround) and self.is_visible(shape):
                shape.fill = True  # 始终填充 Shape，不再依赖选中或悬停
                shape.paint(p)
        if self.current:
            self.current.paint(p)
            self.line.paint(p)
        if self.selected_shapes_copy:
            for s in self.selected_shapes_copy:
                s.paint(p)

        if (
            self.fill_drawing()
            and self.create_mode == "polygon"
            and self.current is not None
            and len(self.current.points) >= 2
        ):
            drawing_shape = self.current.copy()
            drawing_shape.add_point(self.line[1])
            drawing_shape.fill = True
            drawing_shape.paint(p)

        # Draw texts
        if self.show_texts:
            p.setFont(
                QtGui.QFont("Arial", int(max(6.0, int(round(8.0 / Shape.scale)))))
            )
            pen = QtGui.QPen(QtGui.QColor("#00FF00"), 8, Qt.SolidLine)
            p.setPen(pen)
            for shape in self.shapes:
                text = shape.text
                if text:
                    bbox = shape.bounding_rect()
                    fm = QtGui.QFontMetrics(p.font())
                    rect = fm.boundingRect(text)
                    p.fillRect(
                        int(rect.x() + bbox.x() - 3),
                        int(rect.y() + bbox.y() - 3),
                        int(rect.width()),
                        int(rect.height()),
                        QtGui.QColor("#00FF00"),
                    )
                    p.drawText(
                        int(bbox.x()),
                        int(bbox.y()),
                        text,
                    )
            pen = QtGui.QPen(QtGui.QColor("#000000"), 8, Qt.SolidLine)
            p.setPen(pen)
            for shape in self.shapes:
                text = shape.text
                if text:
                    bbox = shape.bounding_rect()
                    p.drawText(
                        int(bbox.x()),
                        int(bbox.y()),
                        text,
                    )

        # Draw mouse coordinates
        if self.show_cross_line:
            pen = QtGui.QPen(
                QtGui.QColor("#00FF00"),
                max(1, int(round(2.0 / Shape.scale))),
                Qt.DashLine,
            )
            p.setPen(pen)
            p.setOpacity(0.5)
            p.drawLine(
                QtCore.QPointF(self.prev_move_point.x(), 0),
                QtCore.QPointF(self.prev_move_point.x(), self.pixmap.height()),
            )
            p.drawLine(
                QtCore.QPointF(0, self.prev_move_point.y()),
                QtCore.QPointF(self.pixmap.width(), self.prev_move_point.y()),
            )

        # 移除圈选模式下的红色圆圈叠加层绘制（保留圈选逻辑，不做任何额外绘制）
        
        # Draw snap-to-edge visual feedback when drawing
        if self.shared_edges_enabled and self._snap_to_edge and self.drawing():
            shape, edge_idx, snap_point = self._snap_to_edge
            
            # Highlight the edge being snapped to with a thicker cyan line
            p1 = shape.points[edge_idx]
            p2 = shape.points[(edge_idx + 1) % len(shape.points)]
            pen = QtGui.QPen(QtGui.QColor(0, 255, 255, 180), max(4, int(round(5.0 / Shape.scale))), Qt.SolidLine)
            p.setPen(pen)
            p.drawLine(p1, p2)
            
            # Draw endpoint markers (larger cyan circles)
            endpoint_size = max(3, int(round(8.0 / Shape.scale)))
            p.setBrush(QtGui.QColor(0, 255, 255, 200))
            p.drawEllipse(p1, endpoint_size, endpoint_size)
            p.drawEllipse(p2, endpoint_size, endpoint_size)
            
            # Draw the snap point with a bright white circle
            snap_size = max(2, int(round(5.0 / Shape.scale)))
            p.setBrush(QtGui.QColor(255, 255, 255, 255))
            p.drawEllipse(snap_point, snap_size, snap_size)
            
            # Draw a small cross at the snap point for better visibility
            cross_size = max(8, int(round(12.0 / Shape.scale)))
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 255), max(2, int(round(2.0 / Shape.scale))), Qt.SolidLine)
            p.setPen(pen)
            p.drawLine(
                QtCore.QPointF(snap_point.x() - cross_size, snap_point.y()),
                QtCore.QPointF(snap_point.x() + cross_size, snap_point.y())
            )
            p.drawLine(
                QtCore.QPointF(snap_point.x(), snap_point.y() - cross_size),
                QtCore.QPointF(snap_point.x(), snap_point.y() + cross_size)
            )
        
        # 绘制手动共边模式的可视化反馈

        p.end()

    def transform_pos(self, point):
        """Convert from widget-logical coordinates to painter-logical ones."""
        return point / self.scale - self.offset_to_center()

    def offset_to_center(self):
        """Calculate offset to the center"""
        if self.pixmap is None:
            return QtCore.QPointF()
        s = self.scale
        area = super().size()
        w, h = self.pixmap.width() * s, self.pixmap.height() * s
        area_width, area_height = area.width(), area.height()
        x = (area_width - w) / (2 * s) if area_width > w else 0
        y = (area_height - h) / (2 * s) if area_height > h else 0
        return QtCore.QPointF(x, y)

    def out_off_pixmap(self, p):
        """Check if a position is out of pixmap"""
        if self.pixmap is None:
            return True
        w, h = self.pixmap.width(), self.pixmap.height()
        return not (0 <= p.x() <= w - 1 and 0 <= p.y() <= h - 1)

    def finalise(self):
        """Finish drawing for a shape"""
        assert self.current
        if self.is_auto_labeling and self.auto_labeling_mode != AutoLabelingMode.NONE:
            self.current.label = self.auto_labeling_mode.edit_mode
        # TODO(vietanhdev): Temporrally fix. Need to refactor
        if self.current.label is None:
            self.current.label = ""
        self.current.close()
        # 如果处于手动共边（套索）模式，则优先处理并不添加该临时多边形
        if self._manual_lasso_mode and self.current.shape_type == "polygon":
            try:
                success = self._apply_manual_shared_edge_lasso(
                    self.current,
                    self._manual_lasso_target,
                    self._manual_lasso_source,
                )
            except Exception as e:
                logger.error("Manual lasso shared edge failed: %s", str(e), exc_info=True)
                success = False
            # 退出套索模式
            self._exit_manual_lasso_mode()
            # 清理当前形状并返回（不添加临时套索）
            self._snap_to_edge = None
            self._snap_edge_points = []
            self._drawing_along_edge = False
            self.current = None
            self.free_drawing_polygon = False
            self.pause_drawing_polygon = False
            self.set_hiding(False)
            if success:
                # Suppress label dialog for this emit; we didn't create a new shape,
                # only modified existing ones for shared edge.
                self._suppress_label_dialog_once = True
                self.new_shape.emit()
                try:
                    self.shape_moved.emit()
                except Exception:
                    pass
                if hasattr(self.parent, "paint_canvas"):
                    try:
                        self.parent.paint_canvas()
                    except Exception:
                        pass
            # Leave the flag reset responsibility to label_widget.new_shape handler
            self.update()
            if self.is_auto_labeling:
                self.update_auto_labeling_marks()
            return

        # Apply polygon clipping and shared edge snapping for polygons
        should_add_current = True
        original_points = list(self.current.points)
        clipped_result = original_points[:]
        
        print(f"[FINALISE] shared_edges_enabled={self.shared_edges_enabled}, shape_type={self.current.shape_type}")
        logger.info(f"[FINALISE] shared_edges_enabled={self.shared_edges_enabled}, shape_type={self.current.shape_type}")
        
        if self.shared_edges_enabled and self.current.shape_type == "polygon":
            # Get all existing polygons (exclude current)
            existing_polygons = [s for s in self.shapes 
                                if hasattr(s, 'shape_type') 
                                and s.shape_type == 'polygon' 
                                and s.is_closed()]
            
            print(f"[FINALISE] Found {len(existing_polygons)} existing polygons")
            print(f"[FINALISE] Current polygon has {len(self.current.points)} points")
            logger.info(f"Finalise: Found {len(existing_polygons)} existing polygons")
            logger.info(f"Finalise: Current polygon has {len(self.current.points)} points")
            
            # Only apply clipping if there are existing polygons
            if existing_polygons:
                # Apply multi-polygon clipping - remove parts of new polygon that are inside ANY existing polygon
                clipped_result = self.clip_polygon_by_multiple_polygons(self.current, existing_polygons)
                
                if clipped_result is None:
                    # New polygon is completely inside existing polygons - don't add it
                    print(f"[FINALISE] Polygon completely inside existing polygons")
                    logger.info(f"Finalise: Polygon completely inside existing polygons")
                    clipped_result = None
                elif len(clipped_result) < 3:
                    # Clipped polygon has too few points
                    print(f"[FINALISE] Clipped polygon has only {len(clipped_result)} points")
                    logger.info(f"Finalise: Clipped polygon has only {len(clipped_result)} points")
                    clipped_result = None
                else:
                    # Update the current polygon with clipped points
                    print(f"[FINALISE] Updating polygon with {len(clipped_result)} clipped points")
                    logger.info(f"Finalise: Updating polygon with {len(clipped_result)} clipped points")
                    self.current.points = clipped_result
                    if hasattr(self.current, "invalidate_path_cache"):
                        self.current.invalidate_path_cache()
            
            if clipped_result is None or len(clipped_result) < 3:
                cleaned = self._clean_polygon_points(original_points, dup_tol=1.2, col_tol=1.2)
                if len(cleaned) < 3:
                    cleaned = original_points

                if len(cleaned) >= 3:
                    logger.warning(
                        "Finalise fallback: clipping removed polygon; retaining outline with %s points",
                        len(cleaned),
                    )
                    self.current.points = cleaned
                    if hasattr(self.current, "invalidate_path_cache"):
                        self.current.invalidate_path_cache()
                else:
                    logger.warning("Finalise abort: polygon degenerated after clipping (len=%s)", len(cleaned))
                    should_add_current = False
            else:
                # No existing polygons - just add the new one normally
                logger.debug("Finalise: No existing polygons, adding new one normally")
                pass
        
        # Add the polygon to shapes if it should be added
        if should_add_current:
            # Sort tl -> br for rectangle
            if self.current.shape_type == "rectangle":
                x_min = min(self.current.points[0].x(), self.current.points[1].x())
                y_min = min(self.current.points[0].y(), self.current.points[1].y())
                x_max = max(self.current.points[0].x(), self.current.points[1].x())
                y_max = max(self.current.points[0].y(), self.current.points[1].y())
                self.current.points = [
                    QtCore.QPointF(x_min, y_min),
                    QtCore.QPointF(x_max, y_max),
                ]
                if hasattr(self.current, "invalidate_path_cache"):
                    self.current.invalidate_path_cache()
            self.shapes.append(self.current)
        
        # Clear snapping state
        self._snap_to_edge = None
        self._snap_edge_points = []
        self._drawing_along_edge = False
        
        self.store_shapes()
        self.current = None
        self.free_drawing_polygon = False
        self.pause_drawing_polygon = False
        self.set_hiding(False)
        self.new_shape.emit()
        self.update()
        try:
            self.shape_moved.emit()
        except Exception:
            pass
        if hasattr(self.parent, "paint_canvas"):
            try:
                self.parent.paint_canvas()
            except Exception:
                pass
        if self.is_auto_labeling:
            self.update_auto_labeling_marks()

    def update_auto_labeling_marks(self):
        """Update the auto labeling marks"""
        marks = []
        for shape in self.shapes:
            if shape.label == AutoLabelingMode.ADD:
                if shape.shape_type == AutoLabelingMode.POINT:
                    marks.append(
                        {
                            "type": "point",
                            "data": [
                                int(shape.points[0].x()),
                                int(shape.points[0].y()),
                            ],
                            "label": 1,
                        }
                    )
                elif shape.shape_type == AutoLabelingMode.RECTANGLE:
                    marks.append(
                        {
                            "type": "rectangle",
                            "data": [
                                int(shape.points[0].x()),
                                int(shape.points[0].y()),
                                int(shape.points[1].x()),
                                int(shape.points[1].y()),
                            ],
                            "label": 1,
                        }
                    )
            elif shape.label == AutoLabelingMode.REMOVE:
                if shape.shape_type == AutoLabelingMode.POINT:
                    marks.append(
                        {
                            "type": "point",
                            "data": [
                                int(shape.points[0].x()),
                                int(shape.points[0].y()),
                            ],
                            "label": 0,
                        }
                    )
                elif shape.shape_type == AutoLabelingMode.RECTANGLE:
                    marks.append(
                        {
                            "type": "rectangle",
                            "data": [
                                int(shape.points[0].x()),
                                int(shape.points[0].y()),
                                int(shape.points[1].x()),
                                int(shape.points[1].y()),
                            ],
                            "label": 0,
                        }
                    )

        self.auto_labeling_marks_updated.emit(marks)

    def close_enough(self, p1, p2):
        """Check if 2 points are close enough (by an threshold epsilon)"""
        # d = distance(p1 - p2)
        # m = (p1-p2).manhattanLength()
        # print "d %.2f, m %d, %.2f" % (d, m, d - m)
        # divide by scale to allow more precision when zoomed in
        return utils.distance(p1 - p2) < (self.epsilon / self.scale)

    def intersection_point(self, p1, p2):
        """Cycle through each image edge in clockwise fashion,
        and find the one intersecting the current line segment.
        """
        size = self.pixmap.size()
        points = [
            (0, 0),
            (size.width() - 1, 0),
            (size.width() - 1, size.height() - 1),
            (0, size.height() - 1),
        ]
        # x1, y1 should be in the pixmap, x2, y2 should be out of the pixmap
        x1 = min(max(p1.x(), 0), size.width() - 1)
        y1 = min(max(p1.y(), 0), size.height() - 1)
        x2, y2 = p2.x(), p2.y()
        _, i, (x, y) = min(self.intersecting_edges((x1, y1), (x2, y2), points))
        x3, y3 = points[i]
        x4, y4 = points[(i + 1) % 4]
        x1, y1 = int(x1), int(y1)
        x2, y2 = int(x2), int(y2)
        x3, y3 = int(x3), int(y3)
        x4, y4 = int(x4), int(y4)
        if (x, y) == (x1, y1):
            # Handle cases where previous point is on one of the edges.
            if x3 == x4:
                return QtCore.QPoint(x3, min(max(0, y2), max(y3, y4)))
            # y3 == y4
            return QtCore.QPoint(min(max(0, x2), max(x3, x4)), y3)
        return QtCore.QPoint(int(x), int(y))

    def intersecting_edges(self, point1, point2, points):
        """Find intersecting edges.

        For each edge formed by `points', yield the intersection
        with the line segment `(x1,y1) - (x2,y2)`, if it exists.
        Also return the distance of `(x2,y2)' to the middle of the
        edge along with its index, so that the one closest can be chosen.
        """
        (x1, y1) = point1
        (x2, y2) = point2
        for i in range(4):
            x3, y3 = points[i]
            x4, y4 = points[(i + 1) % 4]
            denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
            nua = (x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)
            nub = (x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)
            if denom == 0:
                # This covers two cases:
                #   nua == nub == 0: Coincident
                #   otherwise: Parallel
                continue
            ua, ub = nua / denom, nub / denom
            if 0 <= ua <= 1 and 0 <= ub <= 1:
                x = x1 + ua * (x2 - x1)
                y = y1 + ua * (y2 - y1)
                m = QtCore.QPointF((x3 + x4) / 2, (y3 + y4) / 2)
                d = utils.distance(m - QtCore.QPointF(x2, y2))
                yield d, i, (x, y)

    # These two, along with a call to adjustSize are required for the
    # scroll area.
    # QT Overload
    def sizeHint(self):
        """Get size hint"""
        return self.minimumSizeHint()

    # QT Overload
    def minimumSizeHint(self):
        """Get minimum size hint"""
        if self.pixmap:
            return self.scale * self.pixmap.size()
        return super().minimumSizeHint()

    # QT Overload
    def wheelEvent(self, ev: QWheelEvent):
        """Mouse wheel event"""
        mods = ev.modifiers()
        delta = ev.angleDelta()
        if QtCore.Qt.ControlModifier == int(mods):
            # with Ctrl/Command key
            # zoom
            self.zoom_request.emit(delta.y(), ev.pos())
        else:
            # scroll
            self.scroll_request.emit(delta.x(), QtCore.Qt.Horizontal)
            self.scroll_request.emit(delta.y(), QtCore.Qt.Vertical)
        ev.accept()

    def move_by_keyboard(self, offset):
        """Move selected shapes by an offset (using keyboard)"""
        if self.selected_shapes:
            self.bounded_move_shapes(self.selected_shapes, self.prev_point + offset)
            self.repaint()
            self.moving_shape = True

    # QT Overload
    def keyPressEvent(self, ev):
        """Key press event"""
        modifiers = ev.modifiers()
        key = ev.key()
        
        # 处理手动共边模式的Esc键
        
        if self.drawing():
            if key == QtCore.Qt.Key_Escape and self.current:
                self.current = None
                self.free_drawing_polygon = False
                self.drawing_polygon.emit(False)
                # Clear snapping state
                self._snap_to_edge = None
                self._snap_edge_points = []
                self._drawing_along_edge = False
                self.update()
            elif key == QtCore.Qt.Key_Return and self.can_close_shape():
                self.finalise()
            elif (
                    key == QtCore.Qt.Key_Space
                    and self.create_mode == "polygon"
                    and self.current is not None
            ):
                if not self.pause_drawing_polygon:
                    # pause freehand drawing
                    self.pause_drawing_polygon = True
                    self.free_drawing_polygon = False
                    self.drawing_polygon.emit(False)
                    self.override_cursor(CURSOR_DEFAULT)
                else:
                    # resume freehand drawing
                    self.pause_drawing_polygon = False
                    self.free_drawing_polygon = True
                    self.drawing_polygon.emit(True)
                    self.override_cursor(CURSOR_DRAW)
            elif modifiers == QtCore.Qt.AltModifier:
                self.snapping = False
        elif self.editing():
            if key == QtCore.Qt.Key_Up:
                self.move_by_keyboard(QtCore.QPointF(0.0, -MOVE_SPEED))
            elif key == QtCore.Qt.Key_Down:
                self.move_by_keyboard(QtCore.QPointF(0.0, MOVE_SPEED))
            elif key == QtCore.Qt.Key_Left:
                self.move_by_keyboard(QtCore.QPointF(-MOVE_SPEED, 0.0))
            elif key == QtCore.Qt.Key_Right:
                self.move_by_keyboard(QtCore.QPointF(MOVE_SPEED, 0.0))

    # QT Overload
    def keyReleaseEvent(self, ev):
        """Key release event"""
        modifiers = ev.modifiers()
        if self.drawing():
            if int(modifiers) == 0:
                self.snapping = True
        elif self.editing():
            if self.moving_shape and self.selected_shapes:
                try:
                    index = self.shapes.index(self.selected_shapes[0])
                    # 安全检查：确保备份存在且索引有效
                    should_store = False
                    if (self.shapes_backups and 
                        len(self.shapes_backups) > 0 and 
                        index < len(self.shapes) and 
                        index >= 0):
                        
                        last_backup = self.shapes_backups[-1]
                        if (len(last_backup) > index and 
                            hasattr(last_backup[index], 'points') and
                            hasattr(self.shapes[index], 'points')):
                            
                            try:
                                if last_backup[index].points != self.shapes[index].points:
                                    should_store = True
                            except (AttributeError, TypeError):
                                should_store = True
                    
                    if should_store:
                        self.store_shapes()
                        self.shape_moved.emit()
                        
                except (ValueError, IndexError, AttributeError) as e:
                    logger.warning("键盘释放事件处理形状移动时发生错误: %s", str(e))
                finally:
                    self.moving_shape = False

    def set_last_label(self, text, flags):
        """Set label and flags for last shape"""
        assert text
        if self.is_auto_labeling:
            self.shapes[-1].label = self.auto_labeling_mode.edit_mode
        else:
            self.shapes[-1].label = text
        self.shapes[-1].flags = flags
        self.shapes_backups.pop()
        self.store_shapes()
        return self.shapes[-1]

    def undo_last_line(self):
        """Undo last line"""
        assert self.shapes
        self.current = self.shapes.pop()
        self.current.set_open()
        if self.create_mode in ["polygon", "linestrip"]:
            self.line.points = [self.current[-1], self.current[0]]
        elif self.create_mode in ["rectangle", "line", "circle"]:
            self.current.points = self.current.points[0:1]
        elif self.create_mode == "point":
            self.current = None
        self.drawing_polygon.emit(True)

    def undo_last_point(self):
        """Undo last point"""
        if not self.current or self.current.is_closed():
            return
        self.current.pop_point()
        if len(self.current) > 0:
            self.line[0] = self.current[-1]
        else:
            self.current = None
            self.free_drawing_polygon = False
            self.pause_drawing_polygon = False
            self.drawing_polygon.emit(False)
        self.update()

    def load_pixmap(self, pixmap, clear_shapes=True):
        """Load pixmap"""
        self.pixmap = pixmap
        if clear_shapes:
            self.shapes = []
        self.update()

    def load_shapes(self, shapes, replace=True):
        """Load shapes"""
        if replace:
            self.shapes = list(shapes)
        else:
            self.shapes.extend(shapes)
        self.store_shapes()
        self.current = None
        self.h_hape = None
        self.h_vertex = None
        self.h_edge = None
        self.update()

    def set_shape_visible(self, shape, value):
        """Set visibility for a shape"""
        self.visible[shape] = value
        self.update()

    def override_cursor(self, cursor):
        """Override cursor"""
        self.restore_cursor()
        self._cursor = cursor
        QtWidgets.QApplication.setOverrideCursor(cursor)

    def restore_cursor(self):
        """Restore override cursor"""
        QtWidgets.QApplication.restoreOverrideCursor()

    def reset_state(self):
        """Clear shapes and pixmap"""
        self.restore_cursor()
        self.pixmap = None
        self.shapes_backups = []
        self.free_drawing_polygon = False
        self.pause_drawing_polygon = False
        self.update()

    def set_show_cross_line(self, enabled):
        """Set cross line visibility"""
        self.show_cross_line = enabled
        self.update()

    def set_show_groups(self, enabled):
        """Set showing shape groups"""
        self.show_shape_groups = enabled
        self.update()

    def set_show_texts(self, enabled):
        """Set showing texts"""
        self.show_texts = enabled
        self.update()
    
    def set_shared_edges_enabled(self, enabled):
        """Enable or disable shared edge functionality (clipping and vertex reuse)"""
        print(f"[CONFIG] set_shared_edges_enabled called with: {enabled}")
        logger.info(f"[CONFIG] set_shared_edges_enabled called with: {enabled}")
        self.shared_edges_enabled = enabled
        print(f"[CONFIG] shared_edges_enabled is now: {self.shared_edges_enabled}")
        logger.info(f"[CONFIG] shared_edges_enabled is now: {self.shared_edges_enabled}")
        self.update()
    
    def set_edge_snapping_enabled(self, enabled):
        """Enable or disable edge snapping when drawing"""
        self.edge_snapping_enabled = enabled
        if not enabled:
            # Clear snapping state
            self._snap_to_edge = None
            self._snap_edge_points = []
            self._drawing_along_edge = False
        self.update()
    
    def find_edge_intersections(self, new_polygon, existing_shapes):
        """Find intersection points between a new polygon and existing closed polygons.
        
        Returns:
            List of intersection info: [(existing_shape, point_C, point_D, seg_idx_new, seg_idx_existing), ...]
            where point_C and point_D are the two intersection points forming a shared edge
        """
        if not self.shared_edges_enabled:
            return []
        
        intersections = []
        new_points = new_polygon.points if hasattr(new_polygon, 'points') else new_polygon
        
        if len(new_points) < 2:
            return []
        
        for existing_shape in existing_shapes:
            if not hasattr(existing_shape, 'shape_type') or existing_shape.shape_type != 'polygon':
                continue
            if not existing_shape.is_closed():
                continue
            if len(existing_shape.points) < 3:
                continue
            
            # Find all intersection points between the two polygons
            shape_intersections = []
            
            # Check each edge of the new polygon against each edge of the existing polygon
            for i in range(len(new_points)):
                p1 = new_points[i]
                p2 = new_points[(i + 1) % len(new_points)] if i < len(new_points) - 1 else new_points[0]
                
                for j in range(len(existing_shape.points)):
                    p3 = existing_shape.points[j]
                    p4 = existing_shape.points[(j + 1) % len(existing_shape.points)]
                    
                    # Check if these two line segments intersect
                    intersection = self._line_segment_intersection(p1, p2, p3, p4)
                    if intersection:
                        shape_intersections.append((intersection, i, j))
            
            # If we have exactly 2 intersections, we have a shared edge
            if len(shape_intersections) == 2:
                point_c, idx_new_c, idx_exist_c = shape_intersections[0]
                point_d, idx_new_d, idx_exist_d = shape_intersections[1]
                intersections.append((existing_shape, point_c, point_d, idx_new_c, idx_exist_c, idx_new_d, idx_exist_d))
        
        return intersections
    
    def _line_segment_intersection(self, p1, p2, p3, p4, tolerance=2.0):
        """Calculate the intersection point of two line segments.
        
        Returns the intersection point (QPointF) if segments intersect, None otherwise.
        Includes tolerance for near-intersections.
        """
        try:
            x1, y1 = p1.x(), p1.y()
            x2, y2 = p2.x(), p2.y()
            x3, y3 = p3.x(), p3.y()
            x4, y4 = p4.x(), p4.y()
            
            denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if abs(denom) < 1e-10:
                return None  # Parallel lines
            
            t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
            u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
            
            # Check if intersection is within both line segments (with tolerance)
            if -tolerance/100.0 <= t <= 1.0 + tolerance/100.0 and -tolerance/100.0 <= u <= 1.0 + tolerance/100.0:
                x = x1 + t * (x2 - x1)
                y = y1 + t * (y2 - y1)
                return QtCore.QPointF(x, y)
        except Exception:
            pass
        
        return None
    
    def snap_polygon_to_shared_edges(self, polygon_shape, existing_shapes):
        """Snap a polygon's edges to align with existing polygon edges where they intersect.
        
        This modifies polygon_shape.points in place to create perfect shared edges.
        """
        if not self.shared_edges_enabled:
            return
        
        intersections = self.find_edge_intersections(polygon_shape, existing_shapes)
        
        for intersection_info in intersections:
            existing_shape, point_c, point_d, idx_new_c, idx_exist_c, idx_new_d, idx_exist_d = intersection_info
            
            # Get the segment from the existing shape between the two intersection points
            # We need to determine which points from the existing shape form the shared edge
            points_between = self._get_points_between_on_polygon(
                existing_shape.points, idx_exist_c, idx_exist_d,
                existing_shape, polygon_shape
            )
            
            # Replace the corresponding segment in the new polygon with points from existing polygon
            # This ensures perfect alignment
            self._replace_polygon_segment(polygon_shape, idx_new_c, idx_new_d, points_between)
    
    def _get_points_between_on_polygon(self, points, start_edge_idx, end_edge_idx, 
                                        clip_polygon=None, subject_polygon=None):
        """Get all points on a polygon between two edge indices.
        
        This extracts the vertices that form the shared edge segment.
        选择靠近subject多边形（正在绘制的多边形）中心的那一侧的边。
        
        Args:
            points: List of polygon points
            start_edge_idx: Index of the edge where the first intersection occurs
            end_edge_idx: Index of the edge where the second intersection occurs
            clip_polygon: Shape对象，已存在的多边形（提供共边顶点的多边形）
            subject_polygon: Shape对象，正在绘制的多边形（被选中的多边形）
            
        Returns:
            List of points along the path closer to subject polygon center
        """
        result = []
        n = len(points)
        
        if n < 2:
            return result
        
        # The edge at start_edge_idx goes from points[start_edge_idx] to points[(start_edge_idx+1)%n]
        # The edge at end_edge_idx goes from points[end_edge_idx] to points[(end_edge_idx+1)%n]
        
        # We want to include all vertices between these two edges
        # Determine which direction is shorter
        
        # Forward path: from start_edge_idx+1 to end_edge_idx (inclusive)
        if end_edge_idx >= start_edge_idx:
            forward_dist = end_edge_idx - start_edge_idx
        else:
            forward_dist = (n - start_edge_idx) + end_edge_idx
        
        # Backward path: from start_edge_idx to end_edge_idx+1 (going backwards)
        backward_dist = n - forward_dist
        
        # 如果提供了clip和subject多边形，使用基于中心距离的选择逻辑
        # 否则回退到较短路径的选择（向后兼容）
        if clip_polygon is not None and subject_polygon is not None:
            entry_vertex = (start_edge_idx + 1) % n
            exit_vertex = end_edge_idx
            use_forward = self._choose_exterior_path(
                clip_polygon,
                subject_polygon,
                entry_vertex,
                exit_vertex,
                forward_dist,
                backward_dist,
                clip_polygon.points[start_edge_idx],
                clip_polygon.points[end_edge_idx]
            )
        else:
            # 回退到较短路径的选择（向后兼容旧代码）
            use_forward = (forward_dist <= backward_dist)
        
        # Choose the path based on the decision
        if use_forward:
            # Go forward: include points from (start_edge_idx+1) to end_edge_idx
            i = (start_edge_idx + 1) % n
            while True:
                result.append(points[i])
                if i == end_edge_idx:
                    break
                i = (i + 1) % n
                if len(result) > n:  # Safety check
                    break
        else:
            # Go backward: include points from start_edge_idx to (end_edge_idx+1) backwards
            i = start_edge_idx
            while True:
                result.append(points[i])
                if i == (end_edge_idx + 1) % n:
                    break
                i = (i - 1 + n) % n
                if len(result) > n:  # Safety check
                    break
        
        return result
    
    def _replace_polygon_segment(self, polygon, start_idx, end_idx, new_points):
        """Replace a segment of a polygon with new points from the shared edge.
        
        This removes the points between start_idx and end_idx and replaces them
        with points from the existing polygon's edge.
        
        Args:
            polygon: The new polygon being drawn
            start_idx: Start index of the segment to replace
            end_idx: End index of the segment to replace  
            new_points: Points from the existing polygon's edge to use as replacement
        """
        if not new_points or len(polygon.points) < 2:
            return
        
        # Ensure indices are valid
        n = len(polygon.points)
        start_idx = start_idx % n
        end_idx = end_idx % n
        
        # Build the new points list
        result_points = []
        
        # Determine the path direction
        if end_idx > start_idx:
            # Simple case: segment doesn't wrap around
            # Keep points [0, start_idx], insert new points, keep points [end_idx+1, n-1]
            result_points.extend(polygon.points[:start_idx + 1])
            result_points.extend(new_points)
            result_points.extend(polygon.points[end_idx + 1:])
        else:
            # Segment wraps around: [start_idx, n-1] + [0, end_idx]
            # Keep points [end_idx+1, start_idx], insert new points
            result_points.extend(polygon.points[end_idx + 1:start_idx + 1])
            result_points.extend(new_points)
        
        # Update the polygon's points
        polygon.points = result_points
    
    # Shared edge highlighting methods removed - no visual feedback needed
    def _are_points_collinear(self, p1, p2, p3, tolerance):
        """Check if three points are approximately collinear."""
        # Calculate the distance from p3 to the line p1-p2
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length_sq = dx * dx + dy * dy
        
        if length_sq < 1e-10:
            # p1 and p2 are the same point
            return utils.distance(p1 - p3) < tolerance
        
        # Calculate perpendicular distance
        t = ((p3.x() - p1.x()) * dx + (p3.y() - p1.y()) * dy) / length_sq
        proj_x = p1.x() + t * dx
        proj_y = p1.y() + t * dy
        
        dist = math.sqrt((p3.x() - proj_x)**2 + (p3.y() - proj_y)**2)
        return dist < tolerance
    
    def find_nearest_edge(self, point):
        """Find the nearest edge of existing polygons to the given point.
        
        Returns:
            tuple or None: (shape, edge_start_idx, snap_point) if found, None otherwise
        """
        if not self.shared_edges_enabled:
            return None
        
        min_distance = self.shared_edge_snap_distance / self.scale
        nearest_edge = None
        
        for shape in self.shapes:
            if not hasattr(shape, 'shape_type') or shape.shape_type != 'polygon':
                continue
            if not shape.is_closed():
                continue
            if len(shape.points) < 3:
                continue
            
            # Check each edge of the polygon
            for i in range(len(shape.points)):
                p1 = shape.points[i]
                p2 = shape.points[(i + 1) % len(shape.points)]
                
                # Calculate the perpendicular distance from point to the edge
                snap_point, distance = self._point_to_line_segment_distance(point, p1, p2)
                
                if distance < min_distance:
                    min_distance = distance
                    nearest_edge = (shape, i, snap_point)
        
        return nearest_edge
    
    def _point_to_line_segment_distance(self, point, line_start, line_end):
        """Calculate the shortest distance from a point to a line segment.
        
        Returns:
            tuple: (closest_point_on_segment, distance)
        """
        # Vector from line_start to line_end
        dx = line_end.x() - line_start.x()
        dy = line_end.y() - line_start.y()
        
        # Length squared of the line segment
        length_sq = dx * dx + dy * dy
        
        if length_sq < 1e-10:
            # Line segment is actually a point
            dist = utils.distance(point - line_start)
            return line_start, dist
        
        # Parameter t represents the projection of point onto the line
        # t = 0 means projection is at line_start
        # t = 1 means projection is at line_end
        t = ((point.x() - line_start.x()) * dx + (point.y() - line_start.y()) * dy) / length_sq
        
        # Clamp t to [0, 1] to stay within the line segment
        t = max(0, min(1, t))
        
        # Calculate the closest point on the line segment
        closest_x = line_start.x() + t * dx
        closest_y = line_start.y() + t * dy
        closest_point = QtCore.QPointF(closest_x, closest_y)
        
        # Calculate distance
        distance = utils.distance(point - closest_point)
        
        return closest_point, distance
    
    def get_edge_points_for_snapping(self, shape, edge_idx):
        """Get all points along an edge that should be used for snapping.
        
        This includes the edge endpoints and any intermediate vertices.
        """
        points = []
        start_idx = edge_idx
        end_idx = (edge_idx + 1) % len(shape.points)
        
        points.append(shape.points[start_idx])
        points.append(shape.points[end_idx])
        
        return points
    
    def clip_polygon_by_multiple_polygons(self, subject_polygon, clip_polygons):
        """Clip subject polygon by multiple clip polygons - 一次性处理所有共边.
        
        关键改进：一次性遍历subject多边形，同时处理与所有clip多边形的交叉点，
        确保顺时针/逆时针方向的鲁棒性，避免迭代裁剪导致的顶点混乱。
        
        Args:
            subject_polygon: The polygon to be clipped (new/moved polygon)
            clip_polygons: List of polygons to clip against (existing polygons)
            
        Returns:
            list of QPointF: The clipped polygon points, or None if completely inside
        """
        if not clip_polygons:
            return list(subject_polygon.points)
        
        print(f"\n[MULTI-CLIP] ============ 开始多多边形裁剪 ============")
        print(f"[MULTI-CLIP] Subject: {len(subject_polygon.points)} 个顶点")
        print(f"[MULTI-CLIP] Clip多边形数量: {len(clip_polygons)}")
        logger.info(f"clip_polygon_by_multiple_polygons: Clipping against {len(clip_polygons)} polygons")
        
        # Check if subject is completely inside any clip polygon
        for idx, clip_poly in enumerate(clip_polygons):
            if self._polygon_contains_polygon(clip_poly, subject_polygon):
                print(f"[MULTI-CLIP] Subject完全在clip#{idx}内部，返回None")
                logger.info(f"clip_polygon_by_multiple_polygons: Subject completely inside clip polygon {idx}")
                return None
        
        # === 第一步：收集所有交叉点，按subject边排序 ===
        # all_intersections: [(subject_edge, clip_idx, clip_edge, intersection_point, distance_from_edge_start)]
        all_intersections = []
        
        for clip_idx, clip_poly in enumerate(clip_polygons):
            if not self._polygons_intersect(subject_polygon, clip_poly):
                print(f"[MULTI-CLIP] Clip#{clip_idx} 不相交，跳过")
                continue
            
            # 查找与这个clip多边形的所有交叉点
            for i in range(len(subject_polygon.points)):
                edge_start = subject_polygon.points[i]
                edge_end = subject_polygon.points[(i + 1) % len(subject_polygon.points)]
                
                for j in range(len(clip_poly.points)):
                    c1 = clip_poly.points[j]
                    c2 = clip_poly.points[(j + 1) % len(clip_poly.points)]
                    
                    intersection = self._line_segment_intersection(edge_start, edge_end, c1, c2)
                    if intersection:
                        # 计算交点到边起点的距离，用于同一边上多个交点的排序
                        dist_sq = self._distance_squared(intersection, edge_start)
                        all_intersections.append((i, clip_idx, j, intersection, dist_sq))
                        print(f"[MULTI-CLIP]   找到交点: subj_edge={i}, clip#{clip_idx}_edge={j}, dist={dist_sq:.1f}")
        
        # 如果没有交叉点，返回原始多边形
        if not all_intersections:
            print(f"[MULTI-CLIP] 无交叉点，返回原始多边形")
            return list(subject_polygon.points)
        
        # === 关键：按subject边的顺序排序，同一边上按距离排序 ===
        all_intersections.sort(key=lambda x: (x[0], x[4]))
        
        print(f"\n[MULTI-CLIP] === 排序后的交叉点列表 ({len(all_intersections)}个) ===")
        for idx, (subj_edge, clip_idx, clip_edge, pt, dist) in enumerate(all_intersections):
            print(f"[MULTI-CLIP]   [{idx}] subj_edge={subj_edge}, clip#{clip_idx}, clip_edge={clip_edge}, dist={dist:.1f}")
        
        # === 第二步：构建交叉点映射，便于查找 ===
        # intersection_map: subject_edge -> [(clip_idx, clip_edge, point), ...]
        intersection_map = {}
        for subj_edge, clip_idx, clip_edge, pt, dist_sq in all_intersections:
            if subj_edge not in intersection_map:
                intersection_map[subj_edge] = []
            intersection_map[subj_edge].append((clip_idx, clip_edge, pt))
        
        # === 第三步：一次性遍历subject多边形，构建结果 ===
        result_points = []
        # 小工具：按像素容差追加点，避免重复点/极短边
        def _append_with_tol(lst, pt, tol=1.2):
            if not lst:
                lst.append(pt)
                return True
            last = lst[-1]
            if math.hypot(pt.x() - last.x(), pt.y() - last.y()) <= tol:
                return False
            lst.append(pt)
            return True
        inside_clips = set()  # 当前在哪些clip内部
        entry_info = {}  # clip_idx -> (entry_clip_edge, entry_point)
        
        print(f"\n[MULTI-CLIP] === 开始遍历subject多边形 ===")
        
        for i in range(len(subject_polygon.points)):
            current = subject_polygon.points[i]
            next_point = subject_polygon.points[(i + 1) % len(subject_polygon.points)]
            
            # 检查当前点在哪些clip内部
            current_inside_which = set()
            for clip_idx, clip_poly in enumerate(clip_polygons):
                if clip_poly.contains_point(current):
                    current_inside_which.add(clip_idx)
            
            # 只有当前点不在任何clip内部时才添加
            if not current_inside_which:
                result_points.append(current)
                print(f"[MULTI-CLIP] [{i}] 添加顶点 (外部)")
            else:
                print(f"[MULTI-CLIP] [{i}] 跳过顶点 (在clip {current_inside_which} 内)")
            
            # 处理这条边上的交叉点
            if i in intersection_map:
                edge_intersections = intersection_map[i]
                print(f"[MULTI-CLIP] [{i}] 边上有 {len(edge_intersections)} 个交叉点")
                
                for clip_idx, clip_edge, int_point in edge_intersections:
                    clip_poly = clip_polygons[clip_idx]
                    
                    # === 改进的entry/exit判断 ===
                    # 不仅依赖inside_clips状态，还要检查边的起点和终点
                    # 这样可以正确处理同一条边上多个交叉点的情况
                    edge_start_inside = clip_poly.contains_point(current)
                    edge_end_inside = clip_poly.contains_point(next_point)
                    
                    # 判断是entry还是exit
                    is_entry = (not edge_start_inside) and edge_end_inside
                    is_exit = edge_start_inside and (not edge_end_inside)
                    
                    if is_entry:
                        # === ENTRY: 从外到内 ===
                        print(f"[MULTI-CLIP]     → ENTRY clip#{clip_idx} (边起点外，终点内)")
                        
                        # 检查是否已经在这个clip内（可能前面有其他entry未配对）
                        if clip_idx in entry_info:
                            print(f"[MULTI-CLIP]       ⚠️ 警告：clip#{clip_idx}已有未配对的entry，覆盖之")
                        
                        inside_clips.add(clip_idx)
                        entry_info[clip_idx] = (clip_edge, int_point)
                        # 添加entry交点，保证与subject外部边的连续性
                        _append_with_tol(result_points, int_point)
                        
                    elif is_exit:
                        # === EXIT: 从内到外 ===
                        print(f"[MULTI-CLIP]     ← EXIT clip#{clip_idx} (边起点内，终点外)")
                        
                        if clip_idx not in entry_info:
                            print(f"[MULTI-CLIP]       ⚠️ 警告：exit但没有对应的entry! 跳过")
                            inside_clips.discard(clip_idx)
                            continue
                        
                        if clip_idx in entry_info:
                            entry_clip_edge, entry_point = entry_info[clip_idx]
                            exit_clip_edge = clip_edge
                            
                            # === 添加共边：沿clip多边形边界 ===
                            print(f"[MULTI-CLIP]       共边: entry_edge={entry_clip_edge} -> exit_edge={exit_clip_edge}")
                            
                            entry_end_vertex = (entry_clip_edge + 1) % len(clip_poly.points)
                            exit_start_vertex = exit_clip_edge % len(clip_poly.points)
                            
                            # 计算两个方向的距离
                            n_clip = len(clip_poly.points)
                            if exit_start_vertex >= entry_end_vertex:
                                forward_dist = exit_start_vertex - entry_end_vertex + 1
                            else:
                                forward_dist = n_clip - entry_end_vertex + exit_start_vertex + 1
                            
                            if entry_end_vertex >= exit_start_vertex:
                                backward_dist = entry_end_vertex - exit_start_vertex + 1
                            else:
                                backward_dist = n_clip - exit_start_vertex + entry_end_vertex + 1
                            
                            print(f"[MULTI-CLIP]       路径选择: forward={forward_dist}, backward={backward_dist}")
                            
                            # 使用基于中心距离的路径选择
                            use_forward = self._choose_exterior_path(
                                clip_poly,
                                subject_polygon,
                                entry_end_vertex,
                                exit_start_vertex,
                                forward_dist,
                                backward_dist,
                                entry_point,
                                int_point,
                            )
                            
                            # 添加共边顶点
                            if entry_end_vertex == exit_start_vertex:
                                # 特殊情况：entry和exit在相邻边上，路径为：entry点 -> pivot顶点 -> exit点
                                _append_with_tol(result_points, clip_poly.points[entry_end_vertex])
                                print(f"[MULTI-CLIP]       添加单个顶点: {entry_end_vertex}")
                            else:
                                # 沿选定方向添加顶点
                                vertices_added = []
                                if use_forward:
                                    idx = entry_end_vertex
                                    count = 0
                                    while True:
                                        _append_with_tol(result_points, clip_poly.points[idx])
                                        vertices_added.append(idx)
                                        if idx == exit_start_vertex:
                                            break
                                        idx = (idx + 1) % n_clip
                                        count += 1
                                        if count > n_clip:
                                            print(f"[MULTI-CLIP]       ❌ 错误：正向路径无限循环！")
                                            break
                                else:
                                    idx = entry_end_vertex
                                    count = 0
                                    while True:
                                        _append_with_tol(result_points, clip_poly.points[idx])
                                        vertices_added.append(idx)
                                        if idx == exit_start_vertex:
                                            break
                                        idx = (idx - 1 + n_clip) % n_clip
                                        count += 1
                                        if count > n_clip:
                                            print(f"[MULTI-CLIP]       ❌ 错误：反向路径无限循环！")
                                            break
                                
                                print(f"[MULTI-CLIP]       添加共边顶点: {vertices_added}")
                            # 添加exit交点，闭合共边段
                            _append_with_tol(result_points, int_point)
                            
                            # 清除entry信息
                            del entry_info[clip_idx]
                        
                        # 标记已离开这个clip
                        inside_clips.discard(clip_idx)
                    
                    else:
                        # === 既不是entry也不是exit ===
                        # 这种情况包括：
                        # 1. 边的起点和终点都在clip外部（切线接触）
                        # 2. 边的起点和终点都在clip内部（不应该出现，因为这条边不应该有交叉点）
                        if edge_start_inside and edge_end_inside:
                            print(f"[MULTI-CLIP]     ⚠️ 异常：边起点和终点都在clip#{clip_idx}内，但有交叉点（可能是边界点）")
                        else:
                            print(f"[MULTI-CLIP]     ○ 切线：边起点和终点都在clip#{clip_idx}外（切线接触，忽略）")
        
        print(f"\n[MULTI-CLIP] === 遍历完成 ===")
        print(f"[MULTI-CLIP] 结果多边形: {len(result_points)} 个顶点")
        
        # 检查是否有未配对的entry
        if entry_info:
            print(f"[MULTI-CLIP] ⚠️ 警告：有 {len(entry_info)} 个未配对的entry: {list(entry_info.keys())}")
        
        # 最终清理：去相邻近重复点和近似共线点，避免伪边
        cleaned_points = self._clean_polygon_points(result_points, dup_tol=1.2, col_tol=1.2)
        logger.info(f"clip_polygon_by_multiple_polygons: Final result has {len(result_points)} points; cleaned -> {len(cleaned_points)}")
        
        if len(cleaned_points) < 3:
            print(f"[MULTI-CLIP] 结果顶点少于3个，返回None")
            return None
        
        print(f"[MULTI-CLIP] ============ 完成 ============\n")
        return cleaned_points
    
    def clip_polygon_by_polygon(self, subject_polygon, clip_polygon):
        """Clip subject polygon by clip polygon - removes parts inside clip_polygon.
        
        This implements polygon difference operation (subject - clip).
        
        Args:
            subject_polygon: The polygon to be clipped (new/moved polygon)
            clip_polygon: The polygon to clip against (existing polygon)
            
        Returns:
            list of QPointF: The clipped polygon points, or None if completely inside
        """
        if not hasattr(subject_polygon, 'points') or not hasattr(clip_polygon, 'points'):
            logger.debug("clip_polygon_by_polygon: Missing points attribute")
            return None
        
        if len(subject_polygon.points) < 3 or len(clip_polygon.points) < 3:
            logger.debug(f"clip_polygon_by_polygon: Not enough points - subject:{len(subject_polygon.points)}, clip:{len(clip_polygon.points)}")
            return None
        
        print(f"[CLIP DEBUG] clip_polygon: subject={len(subject_polygon.points)}pts, clip={len(clip_polygon.points)}pts")
        logger.debug(f"clip_polygon_by_polygon: Starting - subject has {len(subject_polygon.points)} points, clip has {len(clip_polygon.points)} points")
        
        # Check if subject is completely outside clip (no clipping needed)
        if not self._polygons_intersect(subject_polygon, clip_polygon):
            print(f"[CLIP DEBUG] Polygons don't intersect, returning original")
            logger.debug("clip_polygon_by_polygon: Polygons don't intersect, returning original")
            return list(subject_polygon.points)
        
        print(f"[CLIP DEBUG] Polygons DO intersect")
        logger.debug("clip_polygon_by_polygon: Polygons intersect")
        
        # Check if subject is completely inside clip (remove it)
        if self._polygon_contains_polygon(clip_polygon, subject_polygon):
            logger.debug("clip_polygon_by_polygon: Subject completely inside clip, returning None")
            return None
        
        # Find all intersections first to track entry/exit points
        intersections = []  # [(subject_edge_idx, clip_edge_idx, intersection_point)]
        intersection_tolerance = 2.0  # pixels - merge intersections closer than this
        
        for i in range(len(subject_polygon.points)):
            s1 = subject_polygon.points[i]
            s2 = subject_polygon.points[(i + 1) % len(subject_polygon.points)]
            
            for j in range(len(clip_polygon.points)):
                c1 = clip_polygon.points[j]
                c2 = clip_polygon.points[(j + 1) % len(clip_polygon.points)]
                
                intersection = self._line_segment_intersection(s1, s2, c1, c2)
                if intersection:
                    # Check if this intersection is too close to an existing one on the same edge
                    is_duplicate = False
                    for existing_i, existing_j, existing_pt in intersections:
                        if existing_i == i:  # Same subject edge
                            dist = math.sqrt(
                                (intersection.x() - existing_pt.x()) ** 2 +
                                (intersection.y() - existing_pt.y()) ** 2
                            )
                            if dist < intersection_tolerance:
                                print(f"[CLIP DEBUG] Skipping duplicate intersection on edge {i} (dist={dist:.2f})")
                                is_duplicate = True
                                break
                    
                    if not is_duplicate:
                        intersections.append((i, j, intersection))
        
        print(f"[CLIP DEBUG] Found {len(intersections)} intersection points (after deduplication)")
        
        # Initialize result_points
        result_points = []
        
        # Use shared edge approach for any even number of intersections
        try:
            if len(intersections) >= 2 and len(intersections) % 2 == 0:
                print(f"[CLIP DEBUG] === SHARED EDGE MODE (Multi-Intersection) ===")
                
                # Sort intersections by subject edge index for proper ordering
                intersections_sorted = sorted(intersections, key=lambda x: (x[0], x[2].x(), x[2].y()))
                
                print(f"[CLIP DEBUG] Processing {len(intersections_sorted)} intersections")
                for idx, (subj_edge, clip_edge, pt) in enumerate(intersections_sorted):
                    print(f"[CLIP DEBUG]   Int {idx}: subj_edge={subj_edge}, clip_edge={clip_edge}")
                
                # Build result by walking around subject polygon
                # Track which intersection we're at
                intersection_map = {}  # subject_edge -> [(clip_edge, point), ...]
                for subj_edge, clip_edge, pt in intersections_sorted:
                    if subj_edge not in intersection_map:
                        intersection_map[subj_edge] = []
                    intersection_map[subj_edge].append((clip_edge, pt))
                
                # IMPORTANT: Sort intersections on each edge by distance from edge start
                # This ensures we process them in the correct order
                for subj_edge in intersection_map:
                    edge_start = subject_polygon.points[subj_edge]
                    # Sort by distance from edge start point
                    intersection_map[subj_edge].sort(
                        key=lambda x: (x[1].x() - edge_start.x())**2 + (x[1].y() - edge_start.y())**2
                    )
                    print(f"[CLIP DEBUG] Edge {subj_edge}: sorted {len(intersection_map[subj_edge])} intersections by distance")
                
                n_clip = len(clip_polygon.points)
                
                # CRITICAL FIX: 正确初始化 currently_inside 状态
                # 检查第一个点是否在clip多边形内部，这对于起始点在内部的情况至关重要
                first_point = subject_polygon.points[0]
                first_point_inside = clip_polygon.contains_point(first_point)
                currently_inside = first_point_inside
                
                if first_point_inside:
                    print(f"[CLIP DEBUG] ⚠️ 起始点(vertex 0)在clip多边形内部，currently_inside初始化为True")
                else:
                    print(f"[CLIP DEBUG] ✓ 起始点(vertex 0)在clip多边形外部，currently_inside初始化为False")
                
                entry_clip_edge = None
                entry_point = None
                
                for i in range(len(subject_polygon.points)):
                    current = subject_polygon.points[i]
                    next_point = subject_polygon.points[(i + 1) % len(subject_polygon.points)]
                    
                    current_inside = clip_polygon.contains_point(current)
                    next_inside = clip_polygon.contains_point(next_point)
                    
                    # Add current point if outside
                    if not current_inside and not currently_inside:
                        result_points.append(current)
                        print(f"[CLIP DEBUG] Added subject vertex {i} (outside)")
                    
                    # Check if this edge has intersections
                    if i in intersection_map:
                        edge_intersections = intersection_map[i]
                        print(f"[CLIP DEBUG] Edge {i} has {len(edge_intersections)} intersections")
                        
                        # CRITICAL: Handle state transitions robustly
                        # One edge should have at most ONE meaningful transition
                        # If multiple intersections exist, we need to pick the RIGHT one
                        
                        # Determine what transition (if any) this edge should have
                        if not current_inside and next_inside:
                            # Entry transition: outside -> inside
                            # Use the FIRST intersection (closest to current point)
                            if len(edge_intersections) > 0:
                                clip_edge, int_point = edge_intersections[0]
                                print(f"[CLIP DEBUG] ENTRY at edge {i}, clip_edge={clip_edge} (using first of {len(edge_intersections)} intersections)")
                                entry_clip_edge = clip_edge
                                entry_point = int_point
                                currently_inside = True
                                
                        elif current_inside and next_inside:
                            # Both inside - skip this edge
                            # We're traversing inside the clip polygon, don't add points
                            print(f"[CLIP DEBUG] Edge {i}: both endpoints inside, skipping")
                            
                        elif not current_inside and not next_inside:
                            # Both outside - this edge stays in result
                            # Current point already added above
                            print(f"[CLIP DEBUG] Edge {i}: both endpoints outside, keeping")
                            
                        elif current_inside and not next_inside:
                            # Exit transition: inside -> outside
                            # Use the LAST intersection (closest to next point)
                            if len(edge_intersections) > 0:
                                clip_edge, int_point = edge_intersections[-1]
                                print(f"[CLIP DEBUG] EXIT at edge {i}, clip_edge={clip_edge} (using last of {len(edge_intersections)} intersections)")
                                exit_clip_edge = clip_edge
                                exit_point = int_point
                                
                                # CRITICAL FIX: 处理起始点在内部的情况
                                # 如果entry_clip_edge为None，说明起始点在内部，这是第一个exit
                                if entry_clip_edge is None:
                                    # 起始点在内部，直接添加exit交叉点即可
                                    # 不需要添加共边路径，因为没有对应的entry
                                    result_points.append(exit_point)
                                    print(f"[CLIP DEBUG] ⚠️ 起始点在内部，直接添加exit交叉点（无对应entry）")
                                    currently_inside = False
                                    # 不设置entry，继续等待下一个entry-exit对
                                    
                                # Add clip polygon's vertices between entry and exit
                                # This is the SHARED EDGE that replaces subject's overlapping part
                                elif entry_clip_edge is not None:
                                    # The entry and exit points are ON clip polygon edges
                                    # We need to include clip vertices between these edges
                                    
                                    # CRITICAL FIX: Always add the precise entry intersection point first
                                    # This prevents the "spike" artifact when intersection is not at a vertex
                                    result_points.append(entry_point)
                                    print(f"[CLIP DEBUG] Added entry intersection point")
                                    
                                    # Strategy: Include vertices from entry edge's END to exit edge's START
                                    # This gives us the clip polygon boundary segment
                                    entry_end_vertex = (entry_clip_edge + 1) % n_clip
                                    exit_start_vertex = exit_clip_edge % n_clip  # Start of exit edge
                                    
                                    print(f"[CLIP DEBUG] Entry edge {entry_clip_edge}, Exit edge {exit_clip_edge}")
                                    print(f"[CLIP DEBUG] Checking clip vertices from {entry_end_vertex} to {exit_start_vertex}")
                                    
                                    # Determine if we need to include intermediate clip vertices
                                    # Only include vertices that are strictly between entry and exit points
                                    need_vertices = True
                                    
                                    # If entry and exit are on the same edge or adjacent edges, 
                                    # we might not need intermediate vertices
                                    if entry_clip_edge == exit_clip_edge:
                                        # Same edge - no intermediate vertices needed
                                        need_vertices = False
                                        print(f"[CLIP DEBUG]   Entry and exit on same edge, no intermediate vertices")
                                    elif entry_end_vertex == exit_start_vertex:
                                        # Adjacent edges - check if we should include the shared vertex
                                        # Only include if the vertex is far enough from both intersection points
                                        shared_vertex = clip_polygon.points[entry_end_vertex]
                                        dist_to_entry = math.sqrt(
                                            (shared_vertex.x() - entry_point.x())**2 + 
                                            (shared_vertex.y() - entry_point.y())**2
                                        )
                                        dist_to_exit = math.sqrt(
                                            (shared_vertex.x() - exit_point.x())**2 + 
                                            (shared_vertex.y() - exit_point.y())**2
                                        )
                                        vertex_threshold = 3.0  # pixels - only include if both distances > threshold
                                        
                                        if dist_to_entry > vertex_threshold and dist_to_exit > vertex_threshold:
                                            result_points.append(shared_vertex)
                                            print(f"[CLIP DEBUG]   Added shared vertex {entry_end_vertex} (dist_entry={dist_to_entry:.1f}, dist_exit={dist_to_exit:.1f})")
                                        else:
                                            print(f"[CLIP DEBUG]   Skipped shared vertex {entry_end_vertex} (too close: {dist_to_entry:.1f}, {dist_to_exit:.1f})")
                                        need_vertices = False
                                    else:
                                        # Multiple vertices between entry and exit
                                        # Calculate distances for both directions
                                        # Forward: entry_end -> exit_start (clockwise)
                                        if exit_start_vertex > entry_end_vertex:
                                            forward_dist = exit_start_vertex - entry_end_vertex + 1
                                        else:
                                            forward_dist = n_clip - entry_end_vertex + exit_start_vertex + 1
                                        
                                        # Backward: entry_end -> exit_start (counter-clockwise)  
                                        if entry_end_vertex > exit_start_vertex:
                                            backward_dist = entry_end_vertex - exit_start_vertex + 1
                                        else:
                                            backward_dist = n_clip - exit_start_vertex + entry_end_vertex + 1
                                        
                                        print(f"[CLIP DEBUG]   Path options: forward={forward_dist}, backward={backward_dist}, n_clip={n_clip}")
                                        
                                        # 选择靠近subject多边形中心的路径
                                        use_forward = self._choose_exterior_path(
                                            clip_polygon,
                                            subject_polygon,
                                            entry_end_vertex,
                                            exit_start_vertex,
                                            forward_dist,
                                            backward_dist,
                                            entry_point,
                                            exit_point,
                                        )
                                        
                                        if use_forward:
                                            print(f"[CLIP DEBUG]   ✓ Using FORWARD path ({forward_dist} vertices)")
                                            idx = entry_end_vertex
                                            count = 0
                                            while True:
                                                result_points.append(clip_polygon.points[idx])
                                                print(f"[CLIP DEBUG]     Added clip vertex {idx}")
                                                if idx == exit_start_vertex:
                                                    break
                                                idx = (idx + 1) % n_clip
                                                count += 1
                                                if count > n_clip:  # Safety check
                                                    print(f"[CLIP DEBUG]   ❌ ERROR: Infinite loop in forward path!")
                                                    break
                                        else:
                                            print(f"[CLIP DEBUG]   ✓ Using BACKWARD path ({backward_dist} vertices)")
                                            idx = entry_end_vertex
                                            count = 0
                                            while True:
                                                result_points.append(clip_polygon.points[idx])
                                                print(f"[CLIP DEBUG]     Added clip vertex {idx}")
                                                if idx == exit_start_vertex:
                                                    break
                                                idx = (idx - 1 + n_clip) % n_clip
                                                count += 1
                                                if count > n_clip:  # Safety check
                                                    print(f"[CLIP DEBUG]   ❌ ERROR: Infinite loop in backward path!")
                                                    break
                                
                                    # CRITICAL FIX: Always add the precise exit intersection point last
                                    # This prevents the "spike" artifact when intersection is not at a vertex
                                    result_points.append(exit_point)
                                    print(f"[CLIP DEBUG] Added exit intersection point")
                                    print(f"[CLIP DEBUG] Shared edge complete (using precise intersection points)")
                                
                                currently_inside = False
                                # Reset entry info for next entry-exit pair
                                entry_clip_edge = None
                                entry_point = None
                
                # CRITICAL FIX: 处理起始点在内部的闭合情况
                # 如果循环结束时仍有未匹配的entry（起始点在内部，绕回来时也在内部）
                if entry_clip_edge is not None and first_point_inside:
                    print(f"[CLIP DEBUG] ⚠️ 检测到未匹配的entry（起始点在内部，需要闭合共边路径）")
                    
                    # 找到回到起始点的exit交叉点
                    # 最后一条边（从最后一个点回到第一个点）应该有这个exit
                    last_edge_idx = len(subject_polygon.points) - 1
                    if last_edge_idx in intersection_map:
                        edge_intersections = intersection_map[last_edge_idx]
                        if len(edge_intersections) > 0:
                            # 使用最后一个交叉点作为exit（最接近起始点）
                            clip_edge, int_point = edge_intersections[-1]
                            print(f"[CLIP DEBUG] 找到闭合exit点在边{last_edge_idx}, clip_edge={clip_edge}")
                            
                            # 添加entry点
                            result_points.append(entry_point)
                            print(f"[CLIP DEBUG] Added entry intersection point (closing)")
                            
                            # 添加clip路径（如果需要）
                            entry_end_vertex = (entry_clip_edge + 1) % n_clip
                            exit_start_vertex = clip_edge % n_clip
                            
                            if entry_clip_edge != clip_edge:
                                # 计算路径
                                if exit_start_vertex > entry_end_vertex:
                                    forward_dist = exit_start_vertex - entry_end_vertex + 1
                                else:
                                    forward_dist = n_clip - entry_end_vertex + exit_start_vertex + 1
                                
                                if entry_end_vertex > exit_start_vertex:
                                    backward_dist = entry_end_vertex - exit_start_vertex + 1
                                else:
                                    backward_dist = n_clip - exit_start_vertex + entry_end_vertex + 1
                                
                                use_forward = self._choose_exterior_path(
                                    clip_polygon,
                                    subject_polygon,
                                    entry_end_vertex,
                                    exit_start_vertex,
                                    forward_dist,
                                    backward_dist,
                                    entry_point,
                                    int_point,
                                )
                                
                                if use_forward:
                                    idx = entry_end_vertex
                                    while idx != exit_start_vertex:
                                        result_points.append(clip_polygon.points[idx])
                                        idx = (idx + 1) % n_clip
                                    result_points.append(clip_polygon.points[exit_start_vertex])
                                else:
                                    idx = entry_end_vertex
                                    while idx != exit_start_vertex:
                                        result_points.append(clip_polygon.points[idx])
                                        idx = (idx - 1 + n_clip) % n_clip
                                    result_points.append(clip_polygon.points[exit_start_vertex])
                            
                            # 添加exit点
                            result_points.append(int_point)
                            print(f"[CLIP DEBUG] Added exit intersection point (closing)")
            
            else:
                # General case: just walk around and add outside points + intersections
                inside_count = 0
                outside_count = 0
                
                for i in range(len(subject_polygon.points)):
                    current = subject_polygon.points[i]
                    next_point = subject_polygon.points[(i + 1) % len(subject_polygon.points)]
                    
                    current_inside = clip_polygon.contains_point(current)
                    next_inside = clip_polygon.contains_point(next_point)
                    
                    if current_inside:
                        inside_count += 1
                    else:
                        outside_count += 1
                    
                    # Case 1: Current point is outside
                    if not current_inside:
                        result_points.append(current)
                        
                        # If next is inside, find the intersection point where we enter
                        if next_inside:
                            # Find intersection on this edge
                            for j in range(len(clip_polygon.points)):
                                c1 = clip_polygon.points[j]
                                c2 = clip_polygon.points[(j + 1) % len(clip_polygon.points)]
                                
                                intersection = self._line_segment_intersection(current, next_point, c1, c2)
                                if intersection:
                                    # Add intersection point
                                    result_points.append(intersection)
                                    break
                    
                    # Case 2: Current point is inside, next is outside
                    elif not next_inside:
                        # Find intersection point where we exit
                        for j in range(len(clip_polygon.points)):
                            c1 = clip_polygon.points[j]
                            c2 = clip_polygon.points[(j + 1) % len(clip_polygon.points)]
                            
                            intersection = self._line_segment_intersection(current, next_point, c1, c2)
                            if intersection:
                                # Add intersection point
                                result_points.append(intersection)
                                break
                    
                    # Case 3: Both inside - skip (we're inside clip polygon)
                    # Case 4: Both outside - already added current, continue
        except Exception as e:
            print(f"[CLIP DEBUG] ERROR in clipping: {e}")
            logger.error(f"Error in clip_polygon_by_polygon: {e}", exc_info=True)
            # Fallback: return original points
            return list(subject_polygon.points)
        
        print(f"[CLIP DEBUG] Result has {len(result_points)} points before cleanup")
        logger.debug(f"clip_polygon_by_polygon: result has {len(result_points)} points before cleanup")
        
        # Remove duplicate consecutive points
        if result_points:
            cleaned_points = [result_points[0]]
            for i in range(1, len(result_points)):
                prev = cleaned_points[-1]
                curr = result_points[i]
                # Check if points are different (not duplicate)
                if abs(curr.x() - prev.x()) > 1e-6 or abs(curr.y() - prev.y()) > 1e-6:
                    cleaned_points.append(curr)
            result_points = cleaned_points
        
        logger.debug(f"clip_polygon_by_polygon: result has {len(result_points)} points after cleanup")
        
        if len(result_points) < 3:
            logger.debug("clip_polygon_by_polygon: Result has less than 3 points, returning None")
            return None
        
        logger.debug("clip_polygon_by_polygon: Returning clipped polygon")
        return result_points
    
    def _calculate_polygon_center(self, polygon):
        """计算多边形的中心点（质心）
        
        Args:
            polygon: Shape对象，包含points属性
            
        Returns:
            QtCore.QPointF: 多边形的中心点
        """
        if not polygon.points or len(polygon.points) == 0:
            return QtCore.QPointF(0, 0)
        
        sum_x = 0.0
        sum_y = 0.0
        for point in polygon.points:
            sum_x += point.x()
            sum_y += point.y()
        
        n = len(polygon.points)
        return QtCore.QPointF(sum_x / n, sum_y / n)
    
    def _distance_squared(self, p1, p2):
        """计算两点之间的距离的平方（避免开方运算）
        
        Args:
            p1: QtCore.QPointF 第一个点
            p2: QtCore.QPointF 第二个点
            
        Returns:
            float: 距离的平方
        """
        dx = p1.x() - p2.x()
        dy = p1.y() - p2.y()
        return dx * dx + dy * dy
    
    def _points_close(self, p1, p2, tol=4.0):
        """Return True if two points are within the given pixel tolerance."""
        if p1 is None or p2 is None:
            return False
        return self._distance_squared(p1, p2) <= tol * tol

    def _point_coords(self, pt):
        """将点统一为浮点坐标，支持QPointF或[x, y]形式."""
        if hasattr(pt, "x"):
            return float(pt.x()), float(pt.y())
        if isinstance(pt, (tuple, list)) and len(pt) >= 2:
            return float(pt[0]), float(pt[1])
        raise TypeError(f"Unsupported point type: {type(pt)}")

    def _polygon_orientation(self, points):
        """计算点序列的有向面积（正值=逆时针，负值=顺时针）."""
        if not points or len(points) < 3:
            return 0.0
        area = 0.0
        n_pts = len(points)
        for i in range(n_pts):
            x1, y1 = self._point_coords(points[i])
            x2, y2 = self._point_coords(points[(i + 1) % n_pts])
            area += x1 * y2 - x2 * y1
        return area / 2.0
    
    def _choose_exterior_path(
        self,
        clip_polygon,
        subject_polygon,
        entry_vertex,
        exit_vertex,
        forward_dist,
        backward_dist,
        entry_point=None,
        exit_point=None,
    ):
        """综合“质心距离 + 绘制方向”选择更贴近subject的共边路径。"""
        print(f"[PATH CHOICE] ===== 共边方向选择 (增强版) =====")
        print(f"[PATH CHOICE] forward_dist={forward_dist}, backward_dist={backward_dist}")
        
        subject_center = self._calculate_polygon_center(subject_polygon)
        print(f"[PATH CHOICE] subject_center = ({subject_center.x():.1f}, {subject_center.y():.1f})")
        subject_orientation = self._polygon_orientation(subject_polygon.points)
        subject_sign = 1 if subject_orientation >= 0 else -1
        print(f"[PATH CHOICE] subject_orientation={subject_orientation:.3f} (sign={subject_sign})")
        
        n_clip = len(clip_polygon.points)
        entry_vertex = entry_vertex % max(1, n_clip)
        exit_vertex = exit_vertex % max(1, n_clip)
        
        entry_pt = entry_point if entry_point is not None else clip_polygon.points[entry_vertex]
        exit_pt = exit_point if exit_point is not None else clip_polygon.points[exit_vertex]
        
        def collect_vertices(start_idx, step, count):
            pts = []
            if n_clip == 0:
                return pts
            idx = start_idx
            steps = max(1, count)
            for _ in range(steps):
                pts.append(clip_polygon.points[idx])
                idx = (idx + step) % n_clip
            return pts
        
        forward_points = collect_vertices(entry_vertex, 1, forward_dist)
        backward_points = collect_vertices(entry_vertex, -1, backward_dist)
        
        def average_dist_sq(points):
            if not points:
                return float("inf")
            total = 0.0
            for pt in points:
                total += self._distance_squared(pt, subject_center)
            return total / len(points)
        
        forward_avg_dist = average_dist_sq(forward_points)
        backward_avg_dist = average_dist_sq(backward_points)
        print(f"[PATH CHOICE] 正向 avg-dist={math.sqrt(forward_avg_dist):.2f}, 反向 avg-dist={math.sqrt(backward_avg_dist):.2f}")
        
        def orientation_score(path_pts):
            seq = [entry_pt]
            seq.extend(path_pts)
            seq.append(exit_pt)
            seq.append(subject_center)
            return self._polygon_orientation(seq)
        
        forward_orientation = orientation_score(forward_points)
        backward_orientation = orientation_score(backward_points)
        forward_sign = 0 if abs(forward_orientation) < 1e-6 else (1 if forward_orientation > 0 else -1)
        backward_sign = 0 if abs(backward_orientation) < 1e-6 else (1 if backward_orientation > 0 else -1)
        print(f"[PATH CHOICE] forward_orientation={forward_orientation:.3f} (sign={forward_sign})")
        print(f"[PATH CHOICE] backward_orientation={backward_orientation:.3f} (sign={backward_sign})")
        
        forward_matches = forward_sign != 0 and forward_sign == subject_sign
        backward_matches = backward_sign != 0 and backward_sign == subject_sign
        
        if forward_matches and not backward_matches:
            print("[PATH CHOICE] → 根据方向选择正向路径")
            return True
        if backward_matches and not forward_matches:
            print("[PATH CHOICE] → 根据方向选择反向路径")
            return False
        
        if forward_avg_dist < backward_avg_dist * 0.999:
            print("[PATH CHOICE] → 平均距离更小，选择正向路径")
            return True
        if backward_avg_dist < forward_avg_dist * 0.999:
            print("[PATH CHOICE] → 平均距离更小，选择反向路径")
            return False
        
        print("[PATH CHOICE] → 距离/方向难分胜负，回退到路径长度比较")
        return forward_dist <= backward_dist
    
    def _is_polygon_ccw(self, points):
        """Check if a polygon's vertices are in counter-clockwise order.
        
        Uses the shoelace formula to calculate signed area.
        Positive area = counter-clockwise, negative = clockwise.
        """
        if len(points) < 3:
            return True
        
        area = 0.0
        for i in range(len(points)):
            p1 = points[i]
            p2 = points[(i + 1) % len(points)]
            area += (p2.x() - p1.x()) * (p2.y() + p1.y())
        
        return area < 0  # In screen coordinates, CCW has negative area
    
    def _polygons_intersect(self, poly1, poly2):
        """Check if two polygons intersect (have any overlapping area or edges crossing)."""
        # Quick bounding box check first
        if not self._bounding_boxes_intersect(poly1, poly2):
            return False
        
        # Check if any edges intersect
        for i in range(len(poly1.points)):
            p1 = poly1.points[i]
            p2 = poly1.points[(i + 1) % len(poly1.points)]
            
            for j in range(len(poly2.points)):
                p3 = poly2.points[j]
                p4 = poly2.points[(j + 1) % len(poly2.points)]
                
                if self._line_segment_intersection(p1, p2, p3, p4):
                    return True
        
        # Check if one polygon is inside the other
        if poly1.contains_point(poly2.points[0]) or poly2.contains_point(poly1.points[0]):
            return True
        
        return False
    
    def _bounding_boxes_intersect(self, poly1, poly2):
        """Check if the bounding boxes of two polygons intersect."""
        rect1 = poly1.bounding_rect()
        rect2 = poly2.bounding_rect()
        return rect1.intersects(rect2)
    
    def _polygon_contains_polygon(self, outer, inner):
        """Check if inner polygon is completely inside outer polygon."""
        # All points of inner must be inside outer
        for point in inner.points:
            if not outer.contains_point(point):
                return False
        return True
    
    def _point_inside_edge(self, point, edge_start, edge_end):
        """Check if a point is on the "inside" side of an edge.
        
        For a counter-clockwise polygon, inside is to the left of the edge.
        """
        # Cross product to determine which side of the line the point is on
        dx = edge_end.x() - edge_start.x()
        dy = edge_end.y() - edge_start.y()
        
        px = point.x() - edge_start.x()
        py = point.y() - edge_start.y()
        
        cross = dx * py - dy * px
        
        # Positive cross product means point is on the left (inside for CCW polygon)
        return cross > 0
    
    def _line_intersection_2d(self, p1, p2, p3, p4):
        """Calculate the intersection point of two line segments.
        
        Returns the intersection point or None if lines don't intersect.
        """
        x1, y1 = p1.x(), p1.y()
        x2, y2 = p2.x(), p2.y()
        x3, y3 = p3.x(), p3.y()
        x4, y4 = p4.x(), p4.y()
        
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-10:
            return None
        
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        
        # Check if intersection is within the line segment p1-p2
        if 0 <= t <= 1:
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            return QtCore.QPointF(x, y)
        
        return None

    def gen_new_group_id(self):
        """Generate a new numeric group_id.

        - 兼容字符串 group_id：仅从可解析为整数的 group_id 中取最大值。
        - 若不存在可解析为整数的分组，则从 1 开始。
        """
        max_group_id = 0
        for shape in self.shapes:
            if shape.group_id is not None:
                try:
                    gi = int(shape.group_id)
                    if gi > max_group_id:
                        max_group_id = gi
                except Exception:
                    # 非数字分组忽略
                    pass
        return max_group_id + 1

    def merge_group_ids(self, group_ids, new_group_id):
        """Merge multiple shapes' group_id into a new one"""
        for shape in self.shapes:
            if shape.group_id in group_ids:
                shape.group_id = new_group_id

    def group_selected_shapes(self):
        """Group selected shapes"""
        if len(self.selected_shapes) == 0:
            return

        # List all group ids for selected shapes
        group_ids = set()
        has_non_group_shape = False
        for shape in self.selected_shapes:
            if shape.group_id is not None:
                group_ids.add(shape.group_id)
            else:
                has_non_group_shape = True

        # 规则：如果已存在分组，则复用其中一个已有分组（不再做数值比较，兼容字符串）；
        # 否则生成新的数字分组。
        new_group_id = None
        if len(group_ids) > 0:
            new_group_id = next(iter(group_ids))
        else:
            new_group_id = self.gen_new_group_id()

        # Merge group ids
        if len(group_ids) > 1:
            self.merge_group_ids(group_ids=group_ids, new_group_id=new_group_id)
        # Assign new_group_id to non-group shapes
        if has_non_group_shape:
            for shape in self.selected_shapes:
                if shape.group_id is None:
                    shape.group_id = new_group_id

        self.update()

    def ungroup_selected_shapes(self):
        """Ungroup selected shapes"""
        if len(self.selected_shapes) == 0:
            return

        # List all group ids for selected shapes
        group_ids = set()
        for shape in self.selected_shapes:
            if shape.group_id is not None:
                group_ids.add(shape.group_id)

        for group_id in group_ids:
            for shape in self.shapes:
                if shape.group_id == group_id:
                    shape.group_id = None

        self.update()
    
    # ==================== 手动指定共边段功能 ====================
    
    def start_manual_shared_edge_mode(self):
        if self.manual_shared_edge_mode:
            return True
        if not self.selected_shapes or len(self.selected_shapes) != 1:
            self.manual_shared_edge_status.emit(self.tr("❌ 请选择一个多边形作为目标多边形"))
            return False
        target_shape = self.selected_shapes[0]
        if not hasattr(target_shape, "shape_type") or target_shape.shape_type != "polygon":
            self.manual_shared_edge_status.emit(self.tr("❌ 手动共边仅支持多边形"))
            return False
        if len(target_shape.points) < 3:
            self.manual_shared_edge_status.emit(self.tr("❌ 目标多边形顶点数不足"))
            return False
        self.manual_shared_edge_mode = True
        self.manual_edge_step = 1
        self.manual_edge_target_shape = target_shape
        self.manual_edge_source_shape = None
        self.manual_edge_target_points = []
        self.manual_edge_source_points = []
        self.manual_shared_edge_guides = []
        self.manual_edge_hover_index = None
        self.manual_edge_highlight_points = []
        self.set_editing(True)
        self.override_cursor(CURSOR_POINT)
        target_shape.selected = True
        self.manual_shared_edge_status.emit(self.tr("步骤 1/4：在目标多边形上点击共边起点"))
        logger.info("[手动共边] 模式启动，目标形状ID=%s", getattr(target_shape, "id", "unknown"))
        self.update()
        return True
    
    def cancel_manual_shared_edge_mode(self):
        if not self.manual_shared_edge_mode:
            return
        logger.info("[手动共边] 模式取消")
        self.manual_shared_edge_mode = False
        self.manual_edge_step = 0
        self.manual_edge_target_shape = None
        self.manual_edge_source_shape = None
        self.manual_edge_target_points = []
        self.manual_edge_source_points = []
        self.manual_shared_edge_guides = []
        self.manual_edge_hover_index = None
        self.manual_edge_highlight_points = []
        self.restore_cursor()
        self.update()
        self.manual_shared_edge_status.emit(self.tr("已取消手动共边"))
        
        # 取消选中状态
        for shape in self.shapes:
            shape.selected = False
        self.selected_shapes = []
    
    def _draw_arrow(self, painter, p1, p2, color):
        """
        绘制箭头指示方向
        
        Args:
            painter: QPainter对象
            p1: 起点
            p2: 终点
            color: 箭头颜色
        """
        # 计算方向向量
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.sqrt(dx*dx + dy*dy)
        
        if length < 1:
            return
        
        # 单位方向向量
        ux = dx / length
        uy = dy / length
        
        # 箭头大小（根据缩放调整）
        arrow_size = max(8, int(round(12.0 / self.scale)))
        
        # 箭头两侧的点
        angle = math.pi / 6  # 30度
        
        # 左侧点
        left_x = p2.x() - arrow_size * (ux * math.cos(angle) - uy * math.sin(angle))
        left_y = p2.y() - arrow_size * (uy * math.cos(angle) + ux * math.sin(angle))
        
        # 右侧点
        right_x = p2.x() - arrow_size * (ux * math.cos(angle) + uy * math.sin(angle))
        right_y = p2.y() - arrow_size * (uy * math.cos(angle) - ux * math.sin(angle))
        
        # 绘制箭头
        painter.setPen(QtGui.QPen(color, max(2, int(round(3.0 / self.scale))), Qt.SolidLine))
        painter.setBrush(color)
        
        arrow = QtGui.QPolygonF([
            p2,
            QtCore.QPointF(left_x, left_y),
            QtCore.QPointF(right_x, right_y)
        ])
        painter.drawPolygon(arrow)
    
    def _draw_enhanced_arrow(self, painter, p1, p2, color, size_multiplier=1.0):
        """
        绘制增强版箭头（带阴影效果，更大更明显）
        
        Args:
            painter: QPainter对象
            p1: 起点
            p2: 终点
            color: 箭头颜色
            size_multiplier: 大小倍数（1.0=正常，1.5=放大50%）
        """
        # 计算方向向量
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.sqrt(dx*dx + dy*dy)
        
        if length < 1:
            return
        
        # 单位方向向量
        ux = dx / length
        uy = dy / length
        
        # 箭头大小（根据缩放和倍数调整）
        base_arrow_size = max(10, int(round(16.0 / self.scale)))
        arrow_size = base_arrow_size * size_multiplier
        
        # 箭头角度（稍微宽一点，更明显）
        angle = math.pi / 5  # 36度
        
        # 左侧点
        left_x = p2.x() - arrow_size * (ux * math.cos(angle) - uy * math.sin(angle))
        left_y = p2.y() - arrow_size * (uy * math.cos(angle) + ux * math.sin(angle))
        
        # 右侧点
        right_x = p2.x() - arrow_size * (ux * math.cos(angle) + uy * math.sin(angle))
        right_y = p2.y() - arrow_size * (uy * math.cos(angle) - ux * math.sin(angle))
        
        # 绘制三层箭头（阴影效果）
        # 第1层：白色阴影（最大）
        shadow_size = arrow_size * 1.3
        shadow_left_x = p2.x() - shadow_size * (ux * math.cos(angle) - uy * math.sin(angle))
        shadow_left_y = p2.y() - shadow_size * (uy * math.cos(angle) + ux * math.sin(angle))
        shadow_right_x = p2.x() - shadow_size * (ux * math.cos(angle) + uy * math.sin(angle))
        shadow_right_y = p2.y() - shadow_size * (uy * math.cos(angle) - ux * math.sin(angle))
        
        shadow_color = QtGui.QColor(255, 255, 255, 100)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(shadow_color)
        shadow_arrow = QtGui.QPolygonF([
            p2,
            QtCore.QPointF(shadow_left_x, shadow_left_y),
            QtCore.QPointF(shadow_right_x, shadow_right_y)
        ])
        painter.drawPolygon(shadow_arrow)
        
        # 第2层：半透明颜色（中等大小）
        glow_size = arrow_size * 1.15
        glow_left_x = p2.x() - glow_size * (ux * math.cos(angle) - uy * math.sin(angle))
        glow_left_y = p2.y() - glow_size * (uy * math.cos(angle) + ux * math.sin(angle))
        glow_right_x = p2.x() - glow_size * (ux * math.cos(angle) + uy * math.sin(angle))
        glow_right_y = p2.y() - glow_size * (uy * math.cos(angle) - ux * math.sin(angle))
        
        glow_color = QtGui.QColor(color.red(), color.green(), color.blue(), 150)
        painter.setBrush(glow_color)
        glow_arrow = QtGui.QPolygonF([
            p2,
            QtCore.QPointF(glow_left_x, glow_left_y),
            QtCore.QPointF(glow_right_x, glow_right_y)
        ])
        painter.drawPolygon(glow_arrow)
        
        # 第3层：实心颜色箭头（核心）
        painter.setPen(QtGui.QPen(color, max(2, int(round(3.0 / self.scale))), Qt.SolidLine))
        painter.setBrush(color)
        
        arrow = QtGui.QPolygonF([
            p2,
            QtCore.QPointF(left_x, left_y),
            QtCore.QPointF(right_x, right_y)
        ])
        painter.drawPolygon(arrow)
        
        # 添加白色高光（箭头尖端）
        highlight_size = arrow_size * 0.4
        painter.setBrush(QtGui.QColor(255, 255, 255, 200))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(p2, int(highlight_size), int(highlight_size))
    
    def _find_nearest_vertex(self, shape, pos, max_distance=None):
        """
        查找形状上距离指定位置最近的顶点
        
        Args:
            shape: 形状对象
            pos: 位置（QtCore.QPointF）
            max_distance: 最大距离（像素），None则根据缩放级别自动调整
        
        Returns:
            int or None: 顶点索引，如果没有找到则返回None
        """
        if not shape or not shape.points:
            return None
        
        # 根据缩放级别自动调整容差
        if max_distance is None:
            max_distance = max(20.0, 30.0 / self.scale)  # 缩放越大，容差越小
        
        min_dist = float('inf')
        nearest_idx = None
        
        for i, point in enumerate(shape.points):
            dist = math.sqrt((point.x() - pos.x())**2 + (point.y() - pos.y())**2)
            if dist < min_dist:
                min_dist = dist
                nearest_idx = i
        
        # 只有在距离足够近时才返回顶点
        if min_dist <= max_distance:
            logger.info(f"[手动共边] 找到顶点{nearest_idx}，距离={min_dist:.1f}像素")
            return nearest_idx
        else:
            logger.info(f"[手动共边] 未找到足够近的顶点，最近距离={min_dist:.1f}像素 > 阈值{max_distance:.1f}像素")
            return None
    
    def _apply_manual_shared_edge(self):
        """应用手动共边对齐"""
        return

    # ==================== 手动共边（绘制区域/Lasso） ====================
    def start_manual_shared_edge_polygon_mode(self, target_shape, source_shape):
        """进入手动共边（绘制区域）模式。

        Args:
            target_shape (Shape): 目标多边形（将被修改）
            source_shape (Shape): 来源多边形（提供共边段）
        """
        try:
            if not target_shape or not source_shape:
                self.manual_shared_edge_status.emit(self.tr("❌ 需要选中两个多边形"))
                return False
            if target_shape == source_shape:
                self.manual_shared_edge_status.emit(self.tr("❌ 无效：两个目标相同"))
                return False
            for s in (target_shape, source_shape):
                if not hasattr(s, 'shape_type') or s.shape_type != 'polygon' or not s.is_closed():
                    self.manual_shared_edge_status.emit(self.tr("❌ 手动共边仅支持封闭多边形"))
                    return False
        except Exception:
            pass

        self._manual_lasso_mode = True
        self._manual_lasso_target = target_shape
        self._manual_lasso_source = source_shape
        self.manual_shared_edge_status.emit(self.tr("手动共边：请绘制一个闭合区域（套索），完成后自动应用"))
        # 切换到绘制模式由外层触发；此处仅设置提示
        return True

    def _exit_manual_lasso_mode(self):
        self._manual_lasso_mode = False
        self._manual_lasso_target = None
        self._manual_lasso_source = None
        # 不改变整体的 manual_shared_edge_mode，lasso 模式与上面的点选模式互不干扰
        self.update()

    def _param_t_on_segment(self, a, b, p):
        """计算点 p 在有向线段 a->b 上的参数 t (0..1)，用于排序。"""
        ax, ay = float(a.x()), float(a.y())
        bx, by = float(b.x()), float(b.y())
        px, py = float(p.x()), float(p.y())
        vx, vy = (bx - ax), (by - ay)
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return 0.0
        t = 0.0
        try:
            t = ((px - ax) * vx + (py - ay) * vy) / denom
        except Exception:
            t = 0.0
        if t < 0.0:
            return 0.0
        if t > 1.0:
            return 1.0
        return t

    def _compute_intersections_with_polygon(self, shape, polygon_points):
        """计算 shape 与 polygon 的交点。

        Returns list of dicts with keys:
            - point: QPointF 交点
            - shape_edge: int 形状边起点索引
            - poly_edge: int 套索边起点索引
            - poly_t: float 交点在该套索边上的参数 0..1
        """
        res = []
        if not shape or not polygon_points or len(polygon_points) < 3:
            return res
        try:
            for i in range(len(shape.points)):
                s1 = shape.points[i]
                s2 = shape.points[(i + 1) % len(shape.points)]
                for j in range(len(polygon_points)):
                    p1 = polygon_points[j]
                    p2 = polygon_points[(j + 1) % len(polygon_points)]
                    ip = self._line_segment_intersection(s1, s2, p1, p2)
                    if ip is not None:
                        res.append({
                            'point': ip,
                            'shape_edge': i,
                            'poly_edge': j,
                            'poly_t': self._param_t_on_segment(p1, p2, ip)
                        })
        except Exception:
            return res
        # 去重（紧邻顶点的重复交点）使用像素级容差
        dedup = []
        def _close(a,b,eps=1.2):
            return math.hypot(a.x()-b.x(), a.y()-b.y()) <= eps
        for item in res:
            found = False
            for d in dedup:
                if _close(item['point'], d['point']):
                    found = True
                    break
            if not found:
                dedup.append(item)
        return dedup

    def _edge_index_for_insertion(self, shape, pt):
        """找到 shape 中离 pt 最近的边索引（返回起点索引），用于插入顶点。"""
        if not shape or not shape.points:
            return 0
        # 使用最小距离方法
        best_i = 0
        best_d = float('inf')
        for i in range(len(shape.points)):
            a = shape.points[i]
            b = shape.points[(i + 1) % len(shape.points)]
            # 点到线段的平方距离
            d = utils.squared_distance_to_line(pt, [a, b])
            dd = d * d
            if dd < best_d:
                best_d = dd
                best_i = i
        return best_i

    def _insert_point_on_shape_edge(self, shape, edge_index, pt):
        """在 shape 的第 edge_index 条边之后插入点，返回新顶点索引。

        若交点接近边的端点（像素级容差），则复用端点，不新增顶点，避免产生极短边。
        """
        n = len(shape.points)
        if n == 0:
            shape.insert_point(0, pt)
            return 0
        a_idx = edge_index % n
        b_idx = (edge_index + 1) % n
        a = shape.points[a_idx]
        b = shape.points[b_idx]
        # 使用像素级容差判断是否贴近端点
        tol = 1.2
        if math.hypot(pt.x() - a.x(), pt.y() - a.y()) <= tol:
            return a_idx
        if math.hypot(pt.x() - b.x(), pt.y() - b.y()) <= tol:
            return b_idx
        # 否则插入到边后
        ins_idx = (edge_index + 1)
        if ins_idx < 0:
            ins_idx = 0
        if ins_idx > n:
            ins_idx = n
        shape.insert_point(ins_idx, pt)
        return ins_idx

    def _extract_path_on_polygon(self, polygon_points, start_edge, start_point, end_edge, end_point, forward=True):
        """从多边形点列中提取沿边界的路径。

        不修改 polygon_points，仅根据 start_edge/end_edge 以及方向收集中间顶点。
        返回点序列，包含起点与终点。
        """
        n = len(polygon_points)
        if n == 0:
            return [start_point, end_point]
        # 特殊情况：起止点位于同一条边上，路径应只包含两点，
        # 否则会错误地绕完整个多边形，产生多余的长边。
        if (start_edge % n) == (end_edge % n):
            return [start_point, end_point]
        result = [start_point]
        if forward:
            i = (start_edge + 1) % n
            while True:
                result.append(polygon_points[i])
                if i == end_edge:
                    break
                i = (i + 1) % n
                if len(result) > n + 4:
                    break
        else:
            i = start_edge
            while True:
                result.append(polygon_points[i])
                if i == (end_edge + 1) % n:
                    break
                i = (i - 1 + n) % n
                if len(result) > n + 4:
                    break
        result.append(end_point)
        return result

    def _extract_arc_on_shape(self, shape_points, start_idx, end_idx, forward=True):
        """提取 shape 顶点序列上从 start_idx 到 end_idx 的弧段，包含端点。"""
        n = len(shape_points)
        res = [shape_points[start_idx]]
        if forward:
            i = start_idx
            while True:
                i = (i + 1) % n
                res.append(shape_points[i])
                if i == end_idx:
                    break
                if len(res) > n + 2:
                    break
        else:
            i = start_idx
            while True:
                i = (i - 1 + n) % n
                res.append(shape_points[i])
                if i == end_idx:
                    break
                if len(res) > n + 2:
                    break
        return res

    def _path_midpoint(self, pts):
        if not pts:
            return None
        if len(pts) == 1:
            return pts[0]
        # 取中间两点平均，尽量不位于顶点，避免边界效应
        m = len(pts) // 2
        p1 = pts[m - 1]
        p2 = pts[m]
        return QtCore.QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)

    def _path_length(self, pts):
        try:
            if not pts or len(pts) < 2:
                return 0.0
            total = 0.0
            for i in range(1, len(pts)):
                total += math.hypot(pts[i].x() - pts[i-1].x(), pts[i].y() - pts[i-1].y())
            return total
        except Exception:
            return 0.0

    def _is_path_inside_polygon(self, path_pts, polygon_shape):
        try:
            mp = self._path_midpoint(path_pts)
            if mp is None:
                return False
            return polygon_shape.contains_point(mp)
        except Exception:
            return False

    def _inside_ratio(self, pts, polygon_shape):
        """计算点列在多边形内的比例，作为路径“是否在内”的稳健判据。"""
        try:
            if not pts:
                return 0.0
            inside = 0
            total = 0
            for p in pts:
                total += 1
                if polygon_shape.contains_point(p):
                    inside += 1
            if total == 0:
                return 0.0
            return float(inside) / float(total)
        except Exception:
            return 0.0

    def _dedup_consecutive(self, pts, tol=1e-6):
        if not pts:
            return []
        res = [pts[0]]
        for p in pts[1:]:
            q = res[-1]
            if abs(p.x()-q.x()) > tol or abs(p.y()-q.y()) > tol:
                res.append(p)
        # 去除首尾重复
        if len(res) > 1:
            if abs(res[0].x()-res[-1].x()) <= tol and abs(res[0].y()-res[-1].y()) <= tol:
                res = res[:-1]
        return res

    def _clean_polygon_points(self, pts, dup_tol=1.2, col_tol=1.2):
        """清理多边形点列：
        - 移除相邻近重复点（像素级容差）
        - 移除近似共线的冗余点
        保留首点（作为环起点），避免退化为不足三点。
        """
        if not pts:
            return []
        # 先去相邻近重复
        res = self._dedup_consecutive(pts, tol=dup_tol)
        # 再做一遍，防止因首次删除导致的新相邻近重复
        res = self._dedup_consecutive(res, tol=dup_tol)
        if len(res) < 4:
            return res
        # 移除共线点（环结构，保留 index 0 作为起点）
        changed = True
        # 限制迭代次数，避免极端数据循环
        guard = 0
        while changed and len(res) >= 4 and guard < 3:
            changed = False
            guard += 1
            n = len(res)
            to_remove = []
            for i in range(1, n):  # 跳过 0，保留起点
                prev = res[(i - 1) % n]
                curr = res[i]
                nxt = res[(i + 1) % n]
                if self._are_points_collinear(prev, curr, nxt, tolerance=col_tol):
                    to_remove.append(i)
            if to_remove:
                changed = True
                res = [p for idx, p in enumerate(res) if idx not in to_remove]
        # 首尾近重复再检查一次
        if len(res) > 1:
            if abs(res[0].x()-res[-1].x()) <= dup_tol and abs(res[0].y()-res[-1].y()) <= dup_tol:
                res = res[:-1]
        # 移除极短边（小于dup_tol）
        if len(res) >= 4:
            cleaned = []
            for i in range(len(res)):
                a = res[i]
                b = res[(i + 1) % len(res)]
                if math.hypot(a.x()-b.x(), a.y()-b.y()) <= dup_tol:
                    # 跳过 b
                    continue
                cleaned.append(a)
            # 末尾补齐若最后一条也短
            if cleaned and (math.hypot(cleaned[0].x()-cleaned[-1].x(), cleaned[0].y()-cleaned[-1].y()) <= dup_tol):
                cleaned.pop()
            if len(cleaned) >= 3:
                res = cleaned
        return res

    def _apply_manual_shared_edge_lasso(self, lasso_polygon, target_shape, source_shape):
        """根据套索区域，将 target 的圈内边段替换为 source 的圈内边段。

        规则（与示意一致）：
        - Lasso 与 target 相交两次，得到 B、E；
        - 与 source 相交两次，得到 C、D；
        - 新 target = B → (Lasso B→C) → (Source C→D 内侧弧) → (Lasso D→E) → (Target E→B 外侧弧)
        """
        if not lasso_polygon or lasso_polygon.shape_type != 'polygon' or not lasso_polygon.is_closed():
            self.manual_shared_edge_status.emit(self.tr("❌ 请绘制闭合区域"))
            return False

        lp = list(lasso_polygon.points)
        # 交点
        ints_t = self._compute_intersections_with_polygon(target_shape, lp)
        ints_s = self._compute_intersections_with_polygon(source_shape, lp)

        if len(ints_t) != 2 or len(ints_s) != 2:
            self.manual_shared_edge_status.emit(self.tr("❌ 套索与两个多边形的交点不是各2个，请调整区域"))
            return False

        # 按套索路径顺序排序（poly_edge, poly_t）
        def sort_key(it):
            return (it['poly_edge'], it['poly_t'])
        seq = sorted([
            {**it, 'which': 'T'} for it in ints_t
        ] + [
            {**it, 'which': 'S'} for it in ints_s
        ], key=sort_key)

        # 找到顺序 T,S,S,T 的起点（循环数组）
        pattern = ['T', 'S', 'S', 'T']
        idx0 = None
        n4 = len(seq)
        for i in range(n4):
            ok = True
            for k in range(4):
                if seq[(i + k) % n4]['which'] != pattern[k]:
                    ok = False
                    break
            if ok:
                idx0 = i
                break
        # 也可能是 S,T,T,S（反方向），则互换目标/来源在序列中的意义
        reverse_pattern = ['S', 'T', 'T', 'S']
        reversed_case = False
        if idx0 is None:
            for i in range(n4):
                ok = True
                for k in range(4):
                    if seq[(i + k) % n4]['which'] != reverse_pattern[k]:
                        ok = False
                        break
                if ok:
                    idx0 = i
                    reversed_case = True
                    break

        if idx0 is None:
            self.manual_shared_edge_status.emit(self.tr("❌ 匹配交点顺序失败，请重新绘制区域"))
            return False

        ordered = [seq[(idx0 + k) % n4] for k in range(4)]
        # B, C, D, E 分别对应
        # 若 reversed_case=True，含义调换，但构造路径时仍使用 B=Target1, C=Source1, D=Source2, E=Target2
        if reversed_case:
            # 序列是 S, T, T, S，需要转换为 B(=T1),C(=S1),D(=S2),E(=T2)
            # 即交换 role 只是命名，不改变 target/source 真实对象
            # 重新映射：位置1是T => B；位置0和3为S => C 和 E 需要对调；为简单起见，直接重新收集
            # 取出按哪类
            t_items = [x for x in ordered if x['which'] == 'T']
            s_items = [x for x in ordered if x['which'] == 'S']
            # 按原顺序：ordered[0]=S -> C，ordered[1]=T -> B，ordered[2]=T -> E，ordered[3]=S -> D
            B = ordered[1]
            C = ordered[0]
            D = ordered[3]
            E = ordered[2]
        else:
            B, C, D, E = ordered  # 已为 T,S,S,T

        # 套索上的连接段
        path_BC = self._extract_path_on_polygon(lp, B['poly_edge'], B['point'], C['poly_edge'], C['point'], forward=True)
        path_DE = self._extract_path_on_polygon(lp, D['poly_edge'], D['point'], E['poly_edge'], E['point'], forward=True)

        # 在 target/source 中插入交点，获取索引
        b_edge = self._edge_index_for_insertion(target_shape, B['point'])
        b_idx = self._insert_point_on_shape_edge(target_shape, b_edge, B['point'])
        # 第二个点重新定位边索引再插入，避免因前一次插入导致索引漂移
        e_edge = self._edge_index_for_insertion(target_shape, E['point'])
        e_idx = self._insert_point_on_shape_edge(target_shape, e_edge, E['point'])

        c_edge = self._edge_index_for_insertion(source_shape, C['point'])
        c_idx = self._insert_point_on_shape_edge(source_shape, c_edge, C['point'])
        d_edge = self._edge_index_for_insertion(source_shape, D['point'])
        d_idx = self._insert_point_on_shape_edge(source_shape, d_edge, D['point'])

        # 由于插入顺序，索引可能相对环方向。统一转为当前顶点序列
        # 提取 target 的内外两条弧：B->E 与 E->B
        arc_t_be_forward = self._extract_arc_on_shape(target_shape.points, b_idx, e_idx, forward=True)
        arc_t_eb_forward = self._extract_arc_on_shape(target_shape.points, e_idx, b_idx, forward=True)
        # 哪条在套索内部？采用“点在多边形内比例”更稳健地判定
        ratio_be = self._inside_ratio(arc_t_be_forward, lasso_polygon)
        ratio_eb = self._inside_ratio(arc_t_eb_forward, lasso_polygon)
        len_be = self._path_length(arc_t_be_forward)
        len_eb = self._path_length(arc_t_eb_forward)
        # 如果两者在“是否在套索内”的比例差异很小，则用长度作为判据：
        # 较短的那条更可能是套索内部的“待替换段”，较长的那条应是外侧保留弧。
        if abs(ratio_be - ratio_eb) < 0.15:
            if len_be <= len_eb:
                arc_target_inside_BtoE = arc_t_be_forward
                arc_target_outside_EtoB = arc_t_eb_forward
            else:
                arc_target_inside_BtoE = list(reversed(arc_t_eb_forward))
                arc_target_outside_EtoB = arc_t_be_forward
        else:
            if ratio_be > ratio_eb:
                arc_target_inside_BtoE = arc_t_be_forward
                arc_target_outside_EtoB = arc_t_eb_forward
            else:
                arc_target_inside_BtoE = list(reversed(arc_t_eb_forward))
                arc_target_outside_EtoB = arc_t_be_forward

        # 提取 source 的 C->D 与 D->C，选择在套索内的方向
        arc_s_cd_forward = self._extract_arc_on_shape(source_shape.points, c_idx, d_idx, forward=True)
        arc_s_dc_forward = self._extract_arc_on_shape(source_shape.points, d_idx, c_idx, forward=True)
        inside_cd = self._is_path_inside_polygon(arc_s_cd_forward, lasso_polygon)
        if inside_cd:
            arc_source_inside_CtoD = arc_s_cd_forward
        else:
            arc_source_inside_CtoD = list(reversed(arc_s_dc_forward))
        # 规范方向：确保 arc_source_inside_CtoD 起点接近 C，终点接近 D
        if arc_source_inside_CtoD:
            if math.hypot(arc_source_inside_CtoD[0].x()-C['point'].x(), arc_source_inside_CtoD[0].y()-C['point'].y()) > \
               math.hypot(arc_source_inside_CtoD[-1].x()-C['point'].x(), arc_source_inside_CtoD[-1].y()-C['point'].y()):
                arc_source_inside_CtoD = list(reversed(arc_source_inside_CtoD))

        # 组装新的 target：B -> C（直连）-> (Source C..D) -> D -> E（直连） -> (Target outside E..B)
        # 不使用套索路径点，按直线连接 BC 与 DE。
        def rm_first(pt_list):
            return pt_list[1:] if len(pt_list) > 1 else []

        d_tol = 1.2

        new_pts = []
        # 起点 B
        new_pts.append(B['point'])
        # 直连到 C
        if math.hypot(B['point'].x()-C['point'].x(), B['point'].y()-C['point'].y()) > d_tol:
            new_pts.append(C['point'])
        else:
            # B 与 C 极近，则只保留 C，避免短边
            new_pts[-1] = C['point']
        # 沿 source 的 C..D 共边段
        new_pts.extend(rm_first(self._dedup_consecutive(arc_source_inside_CtoD, tol=d_tol)))
        # 直连 D -> E
        if math.hypot(D['point'].x()-E['point'].x(), D['point'].y()-E['point'].y()) > d_tol:
            new_pts.append(E['point'])
        else:
            # 若 D 与 E 极近，则用 E 作为终点
            if new_pts:
                new_pts[-1] = E['point']
            else:
                new_pts.append(E['point'])
        # 接上 target 的外侧弧 E..B（去掉起点E避免重复）
        # 规范方向：确保 outside_arc 起点接近 E，终点接近 B
        outside_arc = self._dedup_consecutive(arc_target_outside_EtoB, tol=d_tol)
        if outside_arc:
            start_dist_E = math.hypot(outside_arc[0].x()-E['point'].x(), outside_arc[0].y()-E['point'].y())
            end_dist_E = math.hypot(outside_arc[-1].x()-E['point'].x(), outside_arc[-1].y()-E['point'].y())
            if start_dist_E > end_dist_E:
                outside_arc = list(reversed(outside_arc))
        new_pts.extend(rm_first(outside_arc))

        # 最终清理
        new_pts = self._clean_polygon_points(new_pts, dup_tol=d_tol, col_tol=1.2)
        if len(new_pts) < 3:
            self.manual_shared_edge_status.emit(self.tr("❌ 生成的多边形点数不足"))
            return False

        target_shape.points = new_pts
        if hasattr(target_shape, "invalidate_path_cache"):
            target_shape.invalidate_path_cache()
        target_shape.close()
        # 保存历史，刷新
        self.store_shapes()
        self.update()
        self.manual_shared_edge_status.emit(self.tr("✅ 手动共边已完成"))
        return True
