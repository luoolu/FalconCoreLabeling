## PPL-XPL图像标注 优化
当PPL-XPL标注同步按钮启用时，打开文件夹，通过上一张下一张切换叠加放在canvas上的的图像，
不应该影响标注；目前的情况是标注第一个目标时是正常的，第二个目标或第二次标注时，切换下一张图像，会清空标注;
需求：当PPL-XPL标注同步按钮启用时，打开文件夹，通过上一张下一张切换叠加放在canvas上的的图像，canvas上的标注
应该是不受影响的，不应该清空，应该继续保留之前的标注;即应该是把多张PPL-XPL图像当成一张图像来标注，只是支持切换观察；
前提：
-PPL-XPL标注同步按钮启用
-切换的下一张图像尺寸跟切换图像前的尺寸保持一致，否则给出提示;

'''
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -89,51 +89,53 @@ class LabelingWidget(LabelDialog):
         self.label_file = None
         self.other_data = {}
 
         # see configs/anylabeling_config.yaml for valid configuration
         if config is None:
             config = get_config()
         self._config = config
         self.sync_pplxpl = self._config.get("pplxpl_sync", False)
 
         # set default shape colors
         Shape.line_color = QtGui.QColor(*self._config["shape"]["line_color"])
         Shape.fill_color = QtGui.QColor(*self._config["shape"]["fill_color"])
         Shape.select_line_color = QtGui.QColor(
             *self._config["shape"]["select_line_color"]
         )
         Shape.select_fill_color = QtGui.QColor(
             *self._config["shape"]["select_fill_color"]
         )
         Shape.vertex_fill_color = QtGui.QColor(
             *self._config["shape"]["vertex_fill_color"]
         )
         Shape.hvertex_fill_color = QtGui.QColor(
             *self._config["shape"]["hvertex_fill_color"]
         )
         Shape.line_width = self._config["shape"].get("line_width", 2)
-        Shape.fill_opacity = self._config["shape"].get("fill_opacity", Shape.fill_color.alpha())
+        Shape.fill_opacity = self._config["shape"].get(
+            "fill_opacity", Shape.fill_color.alpha()
+        )
         Shape.fill_color.setAlpha(Shape.fill_opacity)
         Shape.select_fill_color.setAlpha(Shape.fill_opacity)
 
         # Set point size from config file
         Shape.point_size = self._config["shape"]["point_size"]
 
         super(LabelDialog, self).__init__()
 
         # Whether we need to save or not.
         self.dirty = False
 
         self._no_selection_slot = False
 
         self._copied_shapes = None
 
         # Initialize the QSettings object early
         self.settings = QtCore.QSettings("anylabeling", "anylabeling")
 
         # Initialize a QMainWindow for dock widget functionality
         self.main_window = QtWidgets.QMainWindow()
         self.main_window.setDockOptions(
             QtWidgets.QMainWindow.AllowNestedDocks | QtWidgets.QMainWindow.AnimatedDocks
         )
         # Set central widget for the main window
         self.main_window.setCentralWidget(QtWidgets.QWidget())
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -1657,62 +1659,67 @@ class LabelingWidget(LabelDialog):
             self.label_dialog.add_label_history(lb)
 
         # Update unique label list
         for lb in shape.labels:
             if not self.unique_label_list.find_items_by_label(lb):
                 unique_label_item = self.unique_label_list.create_item_from_label(lb)
                 self.unique_label_list.addItem(unique_label_item)
                 rgb = self._get_rgb_by_label(lb)
                 self.unique_label_list.set_item_label(unique_label_item, lb, rgb)
 
         self._update_shape_color(shape)
         if shape.group_id is None:
             color = shape.fill_color.getRgb()[:3]
             item.setText(
                 '{} <font color="#{:02x}{:02x}{:02x}">●</font>'.format(
                     html.escape(shape.label), *color
                 )
             )
         else:
             item.setText(f"{shape.label} ({shape.group_id})")
         self.set_dirty()
 
     def edit_image_label(self):
         text, flags, _ = self.label_dialog.pop_up(
             text=", ".join(
-                self.label_file.image_labels if self.label_file else self.other_data.get("image_labels", [])),
+                self.label_file.image_labels
+                if self.label_file
+                else self.other_data.get("image_labels", [])
+            ),
             flags={},
             group_id=None,
         )
         if text is None:
             return
         labels = [t.strip() for t in text.split(",") if t.strip()]
         for lb in labels:
             if not self.validate_label(lb):
                 self.error_message(
                     self.tr("Invalid label"),
-                    self.tr("Invalid label '{}' with validation type '{}'").format(lb, self._config["validate_label"]),
+                    self.tr("Invalid label '{}' with validation type '{}'").format(
+                        lb, self._config["validate_label"]
+                    ),
                 )
                 return
         self.other_data["image_labels"] = labels
         if self.label_file:
             self.label_file.image_labels = labels
         for lb in labels:
             if not self.unique_label_list.find_items_by_label(lb):
                 item = self.unique_label_list.create_item_from_label(lb)
                 self.unique_label_list.addItem(item)
                 rgb = self._get_rgb_by_label(lb)
                 self.unique_label_list.set_item_label(item, lb, rgb)
         self.set_dirty()
 
     def file_search_changed(self):
         self.import_image_folder(
             self.last_open_dir,
             pattern=self.file_search.text(),
             load=False,
         )
 
     def file_selection_changed(self):
         items = self.file_list_widget.selectedItems()
         if not items:
             return
         item = items[0]
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -2332,60 +2339,55 @@ class LabelingWidget(LabelDialog):
             if self.image_data:
                 self.image_path = filename
             self.label_file = None
             self.other_data = {}
             self.other_data["image_labels"] = []
         image = QtGui.QImage.fromData(self.image_data)
 
         if image.isNull():
             formats = [
                 f"*.{fmt.data().decode()}"
                 for fmt in QtGui.QImageReader.supportedImageFormats()
             ]
             self.error_message(
                 self.tr("Error opening file"),
                 self.tr(
                     "<p>Make sure <i>{0}</i> is a valid image file.<br/>"
                     "Supported image formats: {1}</p>"
                 ).format(filename, ",".join(formats)),
             )
             self.status(self.tr("Error reading %s") % filename)
             return False
         self.image = image
         self.filename = filename
         new_size = (self.image.width(), self.image.height())
         same_size = prev_size is None or prev_size == new_size
-        if (
-            not same_size
-            and self._config.get("keep_prev_loc", True)
-        ):
+        if not same_size and self._config.get("keep_prev_loc", True):
             QtWidgets.QMessageBox.warning(
                 self,
                 self.tr("Different Image Size"),
-                self.tr(
-                    "Cannot keep previous location because image sizes differ."
-                ),
+                self.tr("Cannot keep previous location because image sizes differ."),
             )
         if self._config["keep_prev"]:
             prev_shapes = self.canvas.shapes
         if self.sync_pplxpl:
             pixmap = self._load_pplxpl_overlay(osp.dirname(filename))
             if pixmap is not None:
                 self.image = pixmap.toImage()
                 self.canvas.load_pixmap(pixmap)
             else:
                 self.canvas.load_pixmap(QtGui.QPixmap.fromImage(image))
         else:
             self.canvas.load_pixmap(QtGui.QPixmap.fromImage(image))
         flags = dict.fromkeys(self._config["flags"] or [], False)
         if self.label_file:
             self.load_labels(self.label_file.shapes)
             if self.label_file.flags is not None:
                 flags.update(self.label_file.flags)
         self.load_flags(flags)
         if self._config["keep_prev"] and self.no_shape():
             self.load_shapes(prev_shapes, replace=False)
             self.set_dirty()
         else:
             self.set_clean()
         self.canvas.setEnabled(True)
         # set zoom values
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -2556,88 +2558,93 @@ class LabelingWidget(LabelDialog):
         keep_prev = self._config["keep_prev"]
         if QtWidgets.QApplication.keyboardModifiers() == (
             Qt.ControlModifier | Qt.ShiftModifier
         ):
             self._config["keep_prev"] = True
             save_config(self._config)
 
         if not self.may_continue():
             return
 
         if len(self.image_list) <= 0:
             return
 
         if self.filename is None:
             return
 
         # Save dock state before changing images
         self.save_dock_state()
 
         current_index = self.image_list.index(self.filename)
         if current_index - 1 >= 0:
             filename = self.image_list[current_index - 1]
             if filename:
                 if self.sync_pplxpl:
                     self._copy_view_state(self.filename, filename)
-                self.load_file(filename)
+                    self.filename = filename
+                    self._update_canvas_image(filename)
+                else:
+                    self.load_file(filename)
 
         self._config["keep_prev"] = keep_prev
         save_config(self._config)
 
     def open_next_image(self, _value=False, load=True):
         keep_prev = self._config["keep_prev"]
         if QtWidgets.QApplication.keyboardModifiers() == (
             Qt.ControlModifier | Qt.ShiftModifier
         ):
             self._config["keep_prev"] = True
             save_config(self._config)
 
         if not self.may_continue():
             return
 
         if len(self.image_list) <= 0:
             return
 
         filename = None
         if self.filename is None:
             filename = self.image_list[0]
         else:
             current_index = self.image_list.index(self.filename)
             if current_index + 1 < len(self.image_list):
                 filename = self.image_list[current_index + 1]
             else:
                 filename = self.image_list[-1]
         prev_filename = self.filename
         self.filename = filename
 
         # Save dock state before changing images
         self.save_dock_state()
 
         if self.filename and load:
             if self.sync_pplxpl and prev_filename:
                 self._copy_view_state(prev_filename, self.filename)
-            self.load_file(self.filename)
+                self._update_canvas_image(self.filename)
+            else:
+                self.load_file(self.filename)
 
         self._config["keep_prev"] = keep_prev
         save_config(self._config)
 
     def open_file(self, _value=False):
         if not self.may_continue():
             return
         path = osp.dirname(str(self.filename)) if self.filename else "."
         formats = [
             f"*.{fmt.data().decode()}"
             for fmt in QtGui.QImageReader.supportedImageFormats()
         ]
         filters = self.tr("Image & Label files (%s)") % " ".join(
             formats + [f"*{LabelFile.suffix}"]
         )
         file_dialog = FileDialogPreview(self)
         file_dialog.setFileMode(FileDialogPreview.ExistingFile)
         file_dialog.setNameFilter(filters)
         file_dialog.setWindowTitle(
             self.tr("%s - Choose Image or Label file") % __appname__,
         )
         file_dialog.setWindowFilePath(path)
         file_dialog.setViewMode(FileDialogPreview.Detail)
         if file_dialog.exec_():
             filename = file_dialog.selectedFiles()[0]
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -2810,102 +2817,134 @@ class LabelingWidget(LabelDialog):
         )
 
     def current_path(self):
         return osp.dirname(str(self.filename)) if self.filename else "."
 
     def toggle_keep_prev_mode(self):
         self._config["keep_prev"] = not self._config["keep_prev"]
         save_config(self._config)
 
     def toggle_auto_use_last_label(self):
         self._config["auto_use_last_label"] = not self._config["auto_use_last_label"]
         save_config(self._config)
 
     def toggle_pplxpl_sync(self):
         """Toggle PPL-XPL label synchronization."""
         self.sync_pplxpl = not self.sync_pplxpl
         self._config["pplxpl_sync"] = self.sync_pplxpl
         save_config(self._config)
 
     def _copy_view_state(self, src, dst):
         """Copy zoom and scroll state from src file to dst file."""
         if src in self.zoom_values:
             self.zoom_values[dst] = self.zoom_values[src]
         for orientation in self.scroll_values:
             if src in self.scroll_values[orientation]:
-                self.scroll_values[orientation][dst] = self.scroll_values[orientation][src]
+                self.scroll_values[orientation][dst] = self.scroll_values[orientation][
+                    src
+                ]
+
+    def _update_canvas_image(self, filename):
+        """Load a new image onto the canvas without altering shapes."""
+        image_data = LabelFile.load_image_file(filename)
+        image = QtGui.QImage.fromData(image_data) if image_data else QtGui.QImage()
+
+        pixmap = None
+        if self.sync_pplxpl:
+            pixmap = self._load_pplxpl_overlay(osp.dirname(filename))
+        if pixmap is None:
+            pixmap = QtGui.QPixmap.fromImage(image)
+
+        if pixmap.isNull():
+            return False
+
+        self.image = pixmap.toImage()
+        self.image_path = filename
+        self.image_data = image_data
+        self.canvas.load_pixmap(pixmap, clear_shapes=False)
+        self.paint_canvas()
+        self.prev_image_size = (self.image.width(), self.image.height())
+        return True
 
     def _load_pplxpl_overlay(self, folder):
         """Return a QPixmap stacking all images in a folder."""
-        exts = [f".{fmt.data().decode().lower()}" for fmt in QtGui.QImageReader.supportedImageFormats()]
-        files = [osp.join(folder, f) for f in os.listdir(folder)
-                 if osp.isfile(osp.join(folder, f)) and f.lower().endswith(tuple(exts))]
+        exts = [
+            f".{fmt.data().decode().lower()}"
+            for fmt in QtGui.QImageReader.supportedImageFormats()
+        ]
+        files = [
+            osp.join(folder, f)
+            for f in os.listdir(folder)
+            if osp.isfile(osp.join(folder, f)) and f.lower().endswith(tuple(exts))
+        ]
         if not files:
             return None
         files = natsort.os_sorted(files)
         images = [QtGui.QImage(f) for f in files if QtGui.QImage(f).isNull() is False]
         if not images:
             return None
         w, h = images[0].width(), images[0].height()
         for img in images[1:]:
             if img.width() != w or img.height() != h:
                 QtWidgets.QMessageBox.warning(
                     self,
                     self.tr("Image size mismatch"),
-                    self.tr("Images in folder have different sizes. Using the first image only."),
+                    self.tr(
+                        "Images in folder have different sizes. Using the first image only."
+                    ),
                 )
                 return QtGui.QPixmap.fromImage(images[0])
         arrs = [opencv.qt_img_to_rgb_cv_img(img) for img in images]
         stack = np.mean(arrs, axis=0).astype(np.uint8)
         return QtGui.QPixmap.fromImage(opencv.cv_img_to_qt_img(stack))
 
     def _get_current_shapes_and_flags(self):
         """Return current shapes and flags formatted for saving."""
 
         def format_shape(s):
             data = s.other_data.copy()
             data.update(
                 {
                     "labels": s.labels,
                     "text": s.text,
                     "points": [(p.x(), p.y()) for p in s.points],
                     "group_id": s.group_id,
                     "shape_type": s.shape_type,
                     "flags": s.flags,
                 }
             )
             return data
 
         shapes = [
             format_shape(item.shape())
             for item in self.label_list
             if item.shape().primary_label
-               not in [
-                   AutoLabelingMode.OBJECT,
-                   AutoLabelingMode.ADD,
-                   AutoLabelingMode.REMOVE,
-               ]
+            not in [
+                AutoLabelingMode.OBJECT,
+                AutoLabelingMode.ADD,
+                AutoLabelingMode.REMOVE,
+            ]
         ]
         flags = {}
         for i in range(self.flag_widget.count()):
             item = self.flag_widget.item(i)
             key = item.text()
             flag = item.checkState() == Qt.Checked
             flags[key] = flag
         return shapes, flags
 
     def sync_annotations_to_folder(self):
         """Apply current annotations to all images in the opened folder."""
         if not self.sync_pplxpl or not self.image_list:
             return
 
         shapes, flags = self._get_current_shapes_and_flags()
 
         for img in self.image_list:
             label_path = osp.splitext(img)[0] + ".json"
             if self.output_dir:
                 label_file_without_path = osp.basename(label_path)
                 label_path = osp.join(self.output_dir, label_file_without_path)
 
             if self._config["store_data"]:
                 img_data = LabelFile.load_image_file(img)
                 image = QtGui.QImage.fromData(img_data) if img_data else QtGui.QImage()
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -3225,51 +3264,53 @@ class LabelingWidget(LabelDialog):
         for lb in [t.strip() for t in text.split(",") if t.strip()]:
             if not self.validate_label(lb):
                 self.error_message(
                     self.tr("Invalid label"),
                     self.tr("Invalid label '{}' with validation type '{}'").format(
                         lb, self._config["validate_label"]
                     ),
                 )
                 return
 
         # Add to label history
         for lb in [t.strip() for t in text.split(",") if t.strip()]:
             self.label_dialog.add_label_history(lb)
 
         # Update label for the object
         updated_shapes = False
         for shape in self.canvas.shapes:
             if shape.primary_label == AutoLabelingMode.OBJECT:
                 updated_shapes = True
                 shape.label = text
                 shape.flags = flags
                 shape.group_id = group_id
                 # Update unique label list
                 for lb in shape.labels:
                     if not self.unique_label_list.find_items_by_label(lb):
-                        unique_label_item = self.unique_label_list.create_item_from_label(lb)
+                        unique_label_item = (
+                            self.unique_label_list.create_item_from_label(lb)
+                        )
                         self.unique_label_list.addItem(unique_label_item)
                         rgb = self._get_rgb_by_label(lb)
                         self.unique_label_list.set_item_label(
                             unique_label_item, lb, rgb
                         )
 
                 # Update label list
                 self._update_shape_color(shape)
                 item = self.label_list.find_item_by_shape(shape)
                 if shape.group_id is None:
                     color = shape.fill_color.getRgb()[:3]
                     item.setText(
                         '{} <font color="#{:02x}{:02x}{:02x}">●</font>'.format(
                             html.escape(shape.label), *color
                         )
                     )
                 else:
                     item.setText(f"{shape.label} ({shape.group_id})")
 
         # Clean up auto labeling objects
         self.clear_auto_labeling_marks()
 
         # Update shape colors
         for shape in self.canvas.shapes:
             self._update_shape_color(shape)
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -3408,50 +3449,51 @@ class LabelingWidget(LabelDialog):
         else:  # system
             if "DARK_MODE" in os.environ:
                 del os.environ["DARK_MODE"]
 
         # Save the theme setting to config
         self._config["theme"] = theme
         save_config(self._config)
 
         # Show dialog to restart application
         msg_box = QMessageBox()
         msg_box.setText(
             self.tr("Please restart the application to apply the theme change.")
         )
         msg_box.exec_()
 
     def switch_label_set(self, name):
         """Switch current label list to the specified set"""
         if "label_sets" not in self._config:
             return
         if name not in self._config["label_sets"]:
             return
         self._config["labels"] = self._config["label_sets"][name]
         save_config(self._config)
         self.update_unique_label_list()
         self.update_label_dialog_labels()
+
     def save_dock_state(self, force=False):
         """Save dock state to config with error handling.
 
         Args:
             force (bool): If True, save regardless of how much time has passed since the last save
         """
         try:
             # Use a minimum time interval between saves to prevent too frequent saving
             current_time = QtCore.QDateTime.currentMSecsSinceEpoch()
             if not force and hasattr(self, "_last_dock_save_time"):
                 time_since_last_save = current_time - self._last_dock_save_time
                 if time_since_last_save < 2000:  # Less than 2 seconds since last save
                     return  # Skip this save to prevent excessive config writes
 
             config = get_config()
 
             # Make sure UI configuration exists
             if "ui" not in config or not isinstance(config["ui"], dict):
                 config["ui"] = {}
 
             # Get QByteArray state and convert to Base64 string
             byte_state = self.main_window.saveState()
             if byte_state.isEmpty():
                 logger.warning("Cannot save empty dock state")
                 return
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index c22eac5b02d7a3c97d3ebd9fd853f9b049089820..3d9635a5465d05749d4051d367b85829fcfab013 100755
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -3590,26 +3632,26 @@ class LabelingWidget(LabelDialog):
                 self.tools.setOrientation(Qt.Vertical)
 
         # Force toolbar to update its layout
         self.tools.update()
 
         # Save the dock state
         self.save_dock_state()
 
     def update_toolbar_scale(self):
         """Scale toolbar icon size based on current window width."""
         if not hasattr(self, "_base_icon_size"):
             return
 
         # Scale relative to a 1024px wide window and clamp the factor
         window_scale = self.main_window.width() / 1024
         window_scale = max(0.5, min(1.5, window_scale))
 
         icon_size = int(self._base_icon_size * window_scale)
         if icon_size == self._icon_size:
             return
 
         self._icon_size = icon_size
         self._dock_width = int(icon_size + 16)
 
         self.tools.setIconSize(QtCore.QSize(icon_size, icon_size))
-        self.on_tools_dock_location_changed()
+        self.on_tools_dock_location_changed()

'''

'''
def _update_canvas_image(self, filename):
    """Load a new image onto the canvas without altering shapes."""
    image_data = LabelFile.load_image_file(filename)
    image = QtGui.QImage.fromData(image_data) if image_data else QtGui.QImage()

    pixmap = None
    if self.sync_pplxpl:
        pixmap = self._load_pplxpl_overlay(osp.dirname(filename))
    if pixmap is None:
        pixmap = QtGui.QPixmap.fromImage(image)

    if pixmap.isNull():
        return False

    self.image = pixmap.toImage()
    self.image_path = filename
    self.image_data = image_data
    self.canvas.load_pixmap(pixmap, clear_shapes=False)
    self.paint_canvas()
    self.prev_image_size = (self.image.width(), self.image.height())
    return True

'''
