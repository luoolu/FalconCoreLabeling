diff --git a/anylabeling/views/labeling/widgets/canvas.py b/anylabeling/views/labeling/widgets/canvas.py
index 54c1ddc6a71ce0065bd3a6156be214a90b0ab74a..556215df96d79ffa13acf51a5764e13f6c6e3b58 undefined
--- a/anylabeling/views/labeling/widgets/canvas.py
+++ b/anylabeling/views/labeling/widgets/canvas.py
@@ -81,50 +81,51 @@ class Canvas(QtWidgets.QWidget):  # pylint: disable=too-many-public-methods, too
         self.h_edge = None
         self.prev_h_edge = None
         self.moving_shape = False
         self.snapping = True
         self.h_shape_is_selected = False
         self._painter = QtGui.QPainter()
         self._cursor = CURSOR_DEFAULT
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
 
         self.is_loading = False
         self.loading_text = self.tr("Loading...")
         self.loading_angle = 0
         self.free_drawing_polygon = False
+        self.pause_drawing_polygon = False
 
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
 
     def fill_drawing(self):
         """Get option to fill shapes by color"""
         return self._fill_drawing
 
diff --git a/anylabeling/views/labeling/widgets/canvas.py b/anylabeling/views/labeling/widgets/canvas.py
index 54c1ddc6a71ce0065bd3a6156be214a90b0ab74a..556215df96d79ffa13acf51a5764e13f6c6e3b58 undefined
--- a/anylabeling/views/labeling/widgets/canvas.py
+++ b/anylabeling/views/labeling/widgets/canvas.py
@@ -937,50 +938,51 @@ class Canvas(QtWidgets.QWidget):  # pylint: disable=too-many-public-methods, too
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
         self.shapes.append(self.current)
         self.store_shapes()
         self.current = None
         self.free_drawing_polygon = False
+        self.pause_drawing_polygon = False
         self.set_hiding(False)
         self.new_shape.emit()
         self.update()
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
diff --git a/anylabeling/views/labeling/widgets/canvas.py b/anylabeling/views/labeling/widgets/canvas.py
index 54c1ddc6a71ce0065bd3a6156be214a90b0ab74a..556215df96d79ffa13acf51a5764e13f6c6e3b58 undefined
--- a/anylabeling/views/labeling/widgets/canvas.py
+++ b/anylabeling/views/labeling/widgets/canvas.py
@@ -1116,50 +1118,67 @@ class Canvas(QtWidgets.QWidget):  # pylint: disable=too-many-public-methods, too
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
         if self.drawing():
             if key == QtCore.Qt.Key_Escape and self.current:
                 self.current = None
                 self.free_drawing_polygon = False
                 self.drawing_polygon.emit(False)
                 self.update()
             elif key == QtCore.Qt.Key_Return and self.can_close_shape():
                 self.finalise()
+            elif (
+                key == QtCore.Qt.Key_Space
+                and self.create_mode == "polygon"
+                and self.current is not None
+            ):
+                if not self.pause_drawing_polygon:
+                    # pause freehand drawing
+                    self.pause_drawing_polygon = True
+                    self.free_drawing_polygon = False
+                    self.drawing_polygon.emit(False)
+                    self.override_cursor(CURSOR_DEFAULT)
+                else:
+                    # resume freehand drawing
+                    self.pause_drawing_polygon = False
+                    self.free_drawing_polygon = True
+                    self.drawing_polygon.emit(True)
+                    self.override_cursor(CURSOR_DRAW)
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
                 index = self.shapes.index(self.selected_shapes[0])
                 if self.shapes_backups[-1][index].points != self.shapes[index].points:
                     self.store_shapes()
                     self.shape_moved.emit()
diff --git a/anylabeling/views/labeling/widgets/canvas.py b/anylabeling/views/labeling/widgets/canvas.py
index 54c1ddc6a71ce0065bd3a6156be214a90b0ab74a..556215df96d79ffa13acf51a5764e13f6c6e3b58 undefined
--- a/anylabeling/views/labeling/widgets/canvas.py
+++ b/anylabeling/views/labeling/widgets/canvas.py
@@ -1179,94 +1198,96 @@ class Canvas(QtWidgets.QWidget):  # pylint: disable=too-many-public-methods, too
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
+            self.pause_drawing_polygon = False
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
+        self.pause_drawing_polygon = False
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
 
     def gen_new_group_id(self):
         """Generate new shape's group_id based on current shapes"""
         max_group_id = 0
         for shape in self.shapes:
             if shape.group_id is not None:
                 max_group_id = max(max_group_id, shape.group_id)
         return max_group_id + 1
 
