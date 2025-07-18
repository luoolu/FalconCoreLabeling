### Summary

- The toolbar now scales icons and width using the screen’s DPI for better visibility across screen resolutions

- The toolbar orientation handler adjusts height and width based on calculated DPI values, ensuring consistent appearance when dock positions change

- Additional toolbars created by the application apply these DPI-aware icon sizes and widths to maintain consistency

- The main window’s menu bar font size adapts to the display DPI so menu items remain clear on all screens

'''
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 3b24340e144c2c39a99df452600321a774012415..dcb7b9c6fe4eb576bca83ec830a96750886b6403 undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -1116,55 +1116,65 @@ class LabelingWidget(LabelDialog):
         # Create a movable dock widget for tools
         self.tools_dock = QtWidgets.QDockWidget(
             self.tr("..."), self.main_window
         )  # Empty title
         self.tools_dock.setObjectName("ToolsDock")
         # Allow moving and detaching, but disable closing
         self.tools_dock.setFeatures(
             QtWidgets.QDockWidget.DockWidgetMovable
             | QtWidgets.QDockWidget.DockWidgetFloatable
         )
 
         # We need visible handle, so don't hide the title bar completely
         # self.tools_dock.setTitleBarWidget(QtWidgets.QWidget())
 
         # Create toolbar widget to place inside dock
         tools_widget = QtWidgets.QWidget()
         tools_layout = QtWidgets.QVBoxLayout()
         tools_layout.setContentsMargins(0, 0, 0, 0)
         tools_layout.setSpacing(0)
 
         # Create toolbar for tools
         self.tools = ToolBar("Tools")
         self.tools.setObjectName("ToolsToolBar")
         self.tools.setOrientation(Qt.Vertical)
         self.tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
-        self.tools.setIconSize(QtCore.QSize(24, 24))
+
+        # Scale icon and dock size based on screen dpi
+        screen = QtWidgets.QApplication.primaryScreen()
+        dpi = screen.logicalDotsPerInch() if screen else 96
+        scale = dpi / 96.0
+        icon_size = int(24 * scale)
+        dock_width = int(icon_size + 16)
+        self._icon_size = icon_size
+        self._dock_width = dock_width
+
+        self.tools.setIconSize(QtCore.QSize(icon_size, icon_size))
 
         # Set initial size constraints for vertical layout
-        self.tools_dock.setMinimumWidth(40)
-        self.tools_dock.setMaximumWidth(40)
+        self.tools_dock.setMinimumWidth(dock_width)
+        self.tools_dock.setMaximumWidth(dock_width)
 
         # Add actions to toolbar
         utils.add_actions(self.tools, self.actions.tool)
 
         # Add toolbar to layout and set as dock widget
         tools_layout.addWidget(self.tools)
         tools_widget.setLayout(tools_layout)
         self.tools_dock.setWidget(tools_widget)
 
         # Apply styling for tools dock with visible handle
         tools_dock_style = (
             "QDockWidget {"
             f"background-color: {AppTheme.get_color('dock_title_bg')};"
             "border: none;"
             "}"
             "QDockWidget::title {"
             "text-align: center;"
             "background-color: " + AppTheme.get_color("dock_title_bg") + ";"
             "color: " + AppTheme.get_color("dock_title_text") + ";"
             "border-radius: 4px;"
             "margin-bottom: 2px;"
             "}"
         )
         self.tools_dock.setStyleSheet(tools_dock_style)
 
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 3b24340e144c2c39a99df452600321a774012415..dcb7b9c6fe4eb576bca83ec830a96750886b6403 undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -1342,52 +1352,52 @@ class LabelingWidget(LabelDialog):
     @pyqtSlot()
     def on_auto_segmentation_requested(self):
         self.canvas.set_auto_labeling(True)
         self.label_instruction.setText(self.get_labeling_instruction())
 
     @pyqtSlot()
     def on_auto_segmentation_disabled(self):
         self.canvas.set_auto_labeling(False)
         self.label_instruction.setText(self.get_labeling_instruction())
 
     def menu(self, title, actions=None):
         menu = self.parent.parent.menuBar().addMenu(title)
         if actions:
             utils.add_actions(menu, actions)
         return menu
 
     def central_widget(self):
         """Return the central widget for the application."""
         return self.main_window.centralWidget()
 
     def toolbar(self, title, actions=None):
         toolbar = ToolBar(title)
         toolbar.setObjectName(f"{title}ToolBar")
         toolbar.setOrientation(Qt.Vertical)
         toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
-        toolbar.setIconSize(QtCore.QSize(24, 24))
-        toolbar.setMaximumWidth(40)
+        toolbar.setIconSize(QtCore.QSize(self._icon_size, self._icon_size))
+        toolbar.setMaximumWidth(self._dock_width)
         if actions:
             utils.add_actions(toolbar, actions)
         return toolbar
 
     def statusBar(self):
         return self.parent.parent.statusBar()
 
     def no_shape(self):
         return len(self.label_list) == 0
 
     def populate_mode_actions(self):
         tool = self.actions.tool
         menu = self.actions.menu
         self.tools.clear()
         utils.add_actions(self.tools, tool)
 
         self.canvas.menus[0].clear()
         utils.add_actions(self.canvas.menus[0], menu)
         self.menus.edit.clear()
         actions = (
             self.actions.create_mode,
             self.actions.create_rectangle_mode,
             self.actions.create_cirle_mode,
             self.actions.create_line_mode,
             self.actions.create_point_mode,
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 3b24340e144c2c39a99df452600321a774012415..dcb7b9c6fe4eb576bca83ec830a96750886b6403 undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -3532,54 +3542,51 @@ class LabelingWidget(LabelDialog):
                 logger.warning("✗ Failed to restore dock state - incompatible layout")
                 # Reset to default layout
                 self.reset_dock_layout()
                 return
 
         except Exception as e:
             logger.warning(f"✗ Error restoring dock state: {e}")
             # If there was an error, delete the invalid state
             if (
                 "ui" in config
                 and isinstance(config["ui"], dict)
                 and "dock_state" in config["ui"]
             ):
                 del config["ui"]["dock_state"]
                 save_config(config)
                 logger.info("Invalid dock state removed from config")
 
     def on_tools_dock_location_changed(self):
         """Handle tools dock location changes to adjust toolbar orientation."""
         # Get the current dock area of the tools dock
         area = self.main_window.dockWidgetArea(self.tools_dock)
 
         # If dock is moved to top or bottom areas, use horizontal layout
         if area == Qt.TopDockWidgetArea or area == Qt.BottomDockWidgetArea:
             self.tools.setOrientation(Qt.Horizontal)
-            # Adjust dock height for horizontal layout - including space for title bar
-            self.tools_dock.setMinimumHeight(65)  # Increased to accommodate title bar
-            self.tools_dock.setMaximumHeight(65)
+            height = int(self._icon_size + 30)
+            self.tools_dock.setMinimumHeight(height)
+            self.tools_dock.setMaximumHeight(height)
             # Reset width constraints
             self.tools_dock.setMinimumWidth(0)
             self.tools_dock.setMaximumWidth(16777215)  # Qt's QWIDGETSIZE_MAX
         else:  # Otherwise (left, right, or floating), use vertical layout
             self.tools.setOrientation(Qt.Vertical)
-            # Adjust dock width for vertical layout
-            self.tools_dock.setMinimumWidth(40)
-            self.tools_dock.setMaximumWidth(40)
+            self.tools_dock.setMinimumWidth(self._dock_width)
+            self.tools_dock.setMaximumWidth(self._dock_width)
             # Reset height constraints
             self.tools_dock.setMinimumHeight(0)
             self.tools_dock.setMaximumHeight(16777215)  # Qt's QWIDGETSIZE_MAX
 
             # If floating, provide more reasonable dimensions
             if not area:  # Qt returns 0 for floating docks
                 self.tools_dock.setMinimumWidth(0)
                 self.tools_dock.setMaximumWidth(16777215)
-                # Set a good default size for the floating toolbox
-                self.tools_dock.resize(40, 300)
-                # Ensure the toolbar is vertical in floating mode
+                self.tools_dock.resize(self._dock_width, 300)
                 self.tools.setOrientation(Qt.Vertical)
 
         # Force toolbar to update its layout
         self.tools.update()
 
         # Save the dock state
         self.save_dock_state()

'''

'''
diff --git a/anylabeling/views/mainwindow.py b/anylabeling/views/mainwindow.py
index 74c67a7ff84ebafbab773936469fa845fecc9e2e..3d63e0ab2528d3d019c8c827c6218f0d9e001854 undefined
--- a/anylabeling/views/mainwindow.py
+++ b/anylabeling/views/mainwindow.py
@@ -1,45 +1,46 @@
 """
 Main application window
 """
 
 import json
 import urllib.request
 from pathlib import Path
 
 from PyQt5.QtWidgets import (
     QAction,
     QApplication,
     QMainWindow,
     QProgressDialog,
     QStatusBar,
     QToolBar,
     QVBoxLayout,
     QWidget,
     QMessageBox,
     QPushButton,
 )
+from PyQt5.QtGui import QFont
 from PyQt5.QtCore import Qt, QTimer, QEvent
 
 from ..app_info import __appdescription__, __appname__
 from .labeling.label_wrapper import LabelingWrapper
 
 
 # ----------------------------------------------------------------------
 # 辅助：下载文件（带进度条）
 # ----------------------------------------------------------------------
 def download_with_progress(parent, url: str, dst: Path, title: str):
     dlg = QProgressDialog(f"Downloading {title}…", "Cancel", 0, 100, parent)
     dlg.setWindowTitle("Downloading")
     dlg.setWindowModality(Qt.ApplicationModal)
     dlg.setMinimumDuration(0)
     dlg.show()
 
     def hook(blocknum, blocksize, totalsize):
         percent = int(blocknum * blocksize * 100 / totalsize) if totalsize > 0 else 0
         dlg.setValue(percent)
         QApplication.processEvents()
         if dlg.wasCanceled():
             raise Exception("Download canceled by user")
 
     dst.parent.mkdir(parents=True, exist_ok=True)
     urllib.request.urlretrieve(url, dst, hook)
diff --git a/anylabeling/views/mainwindow.py b/anylabeling/views/mainwindow.py
index 74c67a7ff84ebafbab773936469fa845fecc9e2e..3d63e0ab2528d3d019c8c827c6218f0d9e001854 undefined
--- a/anylabeling/views/mainwindow.py
+++ b/anylabeling/views/mainwindow.py
@@ -49,51 +50,57 @@ def download_with_progress(parent, url: str, dst: Path, title: str):
 # ----------------------------------------------------------------------
 # 主窗体
 # ----------------------------------------------------------------------
 class MainWindow(QMainWindow):
     """Main window"""
 
     # ------------------------------------------------------------------
     # 初始化
     # ------------------------------------------------------------------
     def __init__(
         self,
         app,
         config=None,
         filename=None,
         output=None,
         output_file=None,
         output_dir=None,
     ):
         super().__init__()
         self.app = app
         self.config = config
         self.setContentsMargins(0, 0, 0, 0)
         self.setWindowTitle(__appname__)
 
         # ---------- 菜单栏 ----------
-        self.menuBar()
+        menubar = self.menuBar()
+        screen = QApplication.primaryScreen()
+        dpi = screen.logicalDotsPerInch() if screen else 96
+        scale = dpi / 96.0
+        font = menubar.font()
+        font.setPointSizeF(font.pointSizeF() * scale)
+        menubar.setFont(font)
 
         # ---------- “Segment All” QAction ----------
         self.segment_action = QAction("Segment All", self)
         self.segment_action.setToolTip("使用 SAM-2 自动分割整张图像")
         self.segment_action.triggered.connect(self.segment_all_instances)
 
         # 兼容旧引用
         self.segment_button = self.segment_action
 
         # ---------- 主视图区 ----------
         lay = QVBoxLayout()
         lay.setContentsMargins(10, 10, 10, 10)
         self.labeling_widget = LabelingWrapper(
             self,
             config=config,
             filename=filename,
             output=output,
             output_file=output_file,
             output_dir=output_dir,
         )
         lay.addWidget(self.labeling_widget)
         container = QWidget()
         container.setLayout(lay)
         self.setCentralWidget(container)
 
diff --git a/anylabeling/views/mainwindow.py b/anylabeling/views/mainwindow.py
index 74c67a7ff84ebafbab773936469fa845fecc9e2e..3d63e0ab2528d3d019c8c827c6218f0d9e001854 undefined
--- a/anylabeling/views/mainwindow.py
+++ b/anylabeling/views/mainwindow.py
@@ -353,33 +360,25 @@ class MainWindow(QMainWindow):
                     js["shapes"].append(
                         {
                             "label": shp.primary_label,
                             "points": pts,
                             "type": shp.shape_type,
                             "line_color": None,
                             "fill_color": None,
                         }
                     )
             json_path = Path(image_path).with_suffix(".json")
             with open(json_path, "w", encoding="utf-8") as f:
                 json.dump(js, f, ensure_ascii=False, indent=2)
 
             self.labeling_widget.viewer.load_shapes(shapes, replace=False)
             QMessageBox.information(self, "完成",
                                     f"分割完成（{size}），已保存：\n{json_path}")
 
         except Exception as e:
             traceback.print_exc()
             QMessageBox.critical(self, "异常", str(e))
 
         finally:
             QApplication.restoreOverrideCursor()
             self.statusBar().clearMessage()
             self.segment_action.setEnabled(True)
-
-
-
-
-
-
-
-

'''