import functools
import html
import math
import os
import os.path as osp
import re
import webbrowser
import weakref

import imgviz
import natsort
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWhatsThis,
    QMessageBox,
)

from anylabeling.services.auto_labeling.types import AutoLabelingMode

from anylabeling.app_info import __appname__
from anylabeling.config import get_config, save_config
from anylabeling.views.labeling import utils
from anylabeling.views.labeling.utils.opencv import (
    cv_img_to_qt_img,
    qt_img_to_rgb_cv_img,
)
import numpy as np
from anylabeling.views.labeling.utils import opencv
from anylabeling.views.labeling.label_file import LabelFile, LabelFileError
from anylabeling.views.labeling.logger import logger
from anylabeling.views.labeling.shape import Shape
from anylabeling.views.labeling.widgets import (
    AutoLabelingWidget,
    BrightnessContrastDialog,
    Canvas,
    FileDialogPreview,
    LabelDialog,
    LabelListWidget,
    LabelListWidgetItem,
    ToolBar,
    UniqueLabelQListWidget,
    ZoomWidget,
)
from .widgets.export_dialog import ExportDialog
from anylabeling.styles import AppTheme

LABEL_COLORMAP = imgviz.label_colormap()

# Green for the first label
LABEL_COLORMAP[2] = LABEL_COLORMAP[1]
LABEL_COLORMAP[1] = [0, 180, 33]


class LabelingWidget(LabelDialog):
    """The main widget for labeling images"""

    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = 0, 1, 2
    next_files_changed = QtCore.pyqtSignal(list)

    # Keep weak references to all active labeling widgets so that
    # global settings such as mask opacity can be propagated.
    _instances = weakref.WeakSet()

    def __init__(
        self,
        parent=None,
        config=None,
        filename=None,
        output=None,
        output_file=None,
        output_dir=None,
    ):
        self.parent = parent
        # Register this widget so global settings can be synced
        LabelingWidget._instances.add(self)
        if output is not None:
            logger.warning("argument output is deprecated, use output_file instead")
            if output_file is None:
                output_file = output

        self.filename = None
        self.image_path = None
        self.image_data = None
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
        Shape.fill_opacity = self._config["shape"].get(
            "fill_opacity", Shape.fill_color.alpha()
        )
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
        self.main_window.centralWidget().setLayout(QtWidgets.QVBoxLayout())
        self.main_window.centralWidget().layout().setContentsMargins(0, 0, 0, 0)

        # Main widgets and related state.
        self.label_dialog = LabelDialog(
            parent=self,
            labels=self._config["labels"],
            sort_labels=self._config["sort_labels"],
            show_text_field=self._config["show_label_text_field"],
            completion=self._config["label_completion"],
            fit_to_content=self._config["fit_to_content"],
            flags=self._config["label_flags"],
        )

        self.label_list = LabelListWidget()
        self.last_open_dir = None

        features = (
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetFloatable
            | QtWidgets.QDockWidget.DockWidgetMovable
        )

        # Apply dock title styling
        dock_title_style = (
            "QDockWidget::title {"
            "text-align: center;"
            "border-radius: 4px;"
            "margin-bottom: 2px;"
            f"background-color: {AppTheme.get_color('dock_title_bg')};"
            f"color: {AppTheme.get_color('dock_title_text')};"
            "}"
        )

        # Create right sidebar with shape text editor
        shape_text_widget = QtWidgets.QWidget()
        shape_text_layout = QVBoxLayout()
        shape_text_layout.setContentsMargins(0, 0, 0, 0)
        self.shape_text_label = QLabel("Object Text")
        self.shape_text_label.setStyleSheet(
            "QLabel {"
            "text-align: center;"
            "padding: 0px;"
            "font-size: 11px;"
            "margin-bottom: 5px;"
            "}"
        )
        self.shape_text_edit = QPlainTextEdit()
        shape_text_layout.addWidget(self.shape_text_label, 0, Qt.AlignCenter)
        shape_text_layout.addWidget(self.shape_text_edit)
        shape_text_widget.setLayout(shape_text_layout)

        # Add shape text widget to dock
        self.shape_text_dock = QtWidgets.QDockWidget(
            self.tr("Text Editor"), self.main_window
        )
        self.shape_text_dock.setObjectName("TextEditor")
        self.shape_text_dock.setFeatures(features)
        self.shape_text_dock.setWidget(shape_text_widget)
        self.shape_text_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_text_dock)

        # Text Editor Actions - created after dock is initialized
        # Set shortcut for the text editor toggle view action
        self.shape_text_dock.toggleViewAction().setShortcut(
            QtCore.Qt.CTRL + QtCore.Qt.Key_T
        )

        # Create dock widgets with movable feature enabled
        self.flag_dock = QtWidgets.QDockWidget(self.tr("Flags"), self.main_window)
        self.flag_dock.setObjectName("Flags")
        self.flag_dock.setFeatures(features)
        self.flag_widget = QtWidgets.QListWidget()
        if config["flags"]:
            self.load_flags(dict.fromkeys(config["flags"], False))
        else:
            self.flag_dock.hide()
        self.flag_dock.setWidget(self.flag_widget)
        self.flag_widget.itemChanged.connect(self.set_dirty)
        self.flag_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)

        self.label_list.item_selection_changed.connect(self.label_selection_changed)
        self.label_list.item_double_clicked.connect(self.edit_label)
        self.label_list.item_changed.connect(self.label_item_changed)
        self.label_list.item_dropped.connect(self.label_order_changed)
        self.shape_dock = QtWidgets.QDockWidget(self.tr("Objects"), self.main_window)
        self.shape_dock.setObjectName("Objects")
        self.shape_dock.setFeatures(features)
        self.shape_dock.setWidget(self.label_list)
        self.shape_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_dock)

        self.unique_label_list = UniqueLabelQListWidget()
        self.unique_label_list.setToolTip(
            self.tr("Select label to start annotating for it. Press 'Esc' to deselect.")
        )
        self.update_unique_label_list()
        self.label_dock = QtWidgets.QDockWidget(self.tr("Labels"), self.main_window)
        self.label_dock.setObjectName("Labels")
        self.label_dock.setFeatures(features)
        self.label_dock.setWidget(self.unique_label_list)
        self.label_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)

        self.file_search = QtWidgets.QLineEdit()
        self.file_search.setPlaceholderText(self.tr("Search Filename"))
        self.file_search.textChanged.connect(self.file_search_changed)
        self.file_list_widget = QtWidgets.QListWidget()
        self.file_list_widget.itemSelectionChanged.connect(self.file_selection_changed)
        file_list_layout = QtWidgets.QVBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.setSpacing(0)
        file_list_layout.addWidget(self.file_search)
        file_list_layout.addWidget(self.file_list_widget)
        self.file_dock = QtWidgets.QDockWidget(self.tr("Files"), self.main_window)
        self.file_dock.setObjectName("Files")
        self.file_dock.setFeatures(features)
        file_list_widget = QtWidgets.QWidget()
        file_list_widget.setLayout(file_list_layout)
        self.file_dock.setWidget(file_list_widget)
        self.file_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)

        self.zoom_widget = ZoomWidget()
        self.line_width_spinbox = QtWidgets.QSpinBox()
        self.line_width_spinbox.setRange(1, 10)
        self.line_width_spinbox.setValue(Shape.line_width)
        self.line_width_spinbox.setToolTip(self.tr("Line Width"))
        self.line_width_spinbox.valueChanged.connect(self.line_width_changed)

        self.fill_opacity_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.fill_opacity_slider.setRange(0, 255)
        self.fill_opacity_slider.setValue(Shape.fill_opacity)
        self.fill_opacity_slider.setToolTip(self.tr("Mask Opacity"))
        self.fill_opacity_slider.valueChanged.connect(self.fill_opacity_changed)
        self.setAcceptDrops(True)

        self.canvas = self.label_list.canvas = Canvas(
            parent=self,
            epsilon=self._config["epsilon"],
            double_click=self._config["canvas"]["double_click"],
            num_backups=self._config["canvas"]["num_backups"],
        )
        self.canvas.zoom_request.connect(self.zoom_request)

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidget(self.canvas)
        scroll_area.setWidgetResizable(True)
        self.scroll_bars = {
            Qt.Vertical: scroll_area.verticalScrollBar(),
            Qt.Horizontal: scroll_area.horizontalScrollBar(),
        }
        self.canvas.scroll_request.connect(self.scroll_request)

        self.canvas.new_shape.connect(self.new_shape)
        self.canvas.shape_moved.connect(self.set_dirty)
        self.canvas.selection_changed.connect(self.shape_selection_changed)
        self.canvas.drawing_polygon.connect(self.toggle_drawing_sensitive)

        self._central_widget = scroll_area

        # Actions
        create_action = functools.partial(utils.new_action, self)
        shortcuts = self._config["shortcuts"]
        open_ = create_action(
            self.tr("&Open"),
            self.open_file,
            shortcuts["open"],
            "open",
            self.tr("Open image or label file"),
        )
        opendir = create_action(
            self.tr("&Open Dir"),
            self.open_folder_dialog,
            shortcuts["open_dir"],
            "open",
            self.tr("Open Dir"),
        )
        open_next_image = create_action(
            self.tr("&Next Image"),
            self.open_next_image,
            shortcuts["open_next"],
            "next",
            self.tr("Open next (hold Ctrl+Shift to copy labels)"),
            enabled=False,
        )
        open_prev_image = create_action(
            self.tr("&Prev Image"),
            self.open_prev_image,
            shortcuts["open_prev"],
            "prev",
            self.tr("Open prev (hold Ctrl+Shift to copy labels)"),
            enabled=False,
        )
        save = create_action(
            self.tr("&Save"),
            self.save_file,
            shortcuts["save"],
            "save",
            self.tr("Save labels to file"),
            enabled=False,
        )
        save_as = create_action(
            self.tr("&Save As"),
            self.save_file_as,
            shortcuts["save_as"],
            "save",
            self.tr("Save labels to a different file"),
            enabled=False,
        )

        delete_file = create_action(
            self.tr("&Delete File"),
            self.delete_file,
            shortcuts["delete_file"],
            "delete",
            self.tr("Delete current label file"),
            enabled=False,
        )

        change_output_dir = create_action(
            self.tr("&Change Output Dir"),
            slot=self.change_output_dir_dialog,
            shortcut=shortcuts["save_to"],
            icon="open",
            tip=self.tr("Change where annotations are loaded/saved"),
        )

        save_auto = create_action(
            text=self.tr("Save &Automatically"),
            slot=lambda x: self.actions.save_auto.setChecked(x),
            icon="save",
            tip=self.tr("Save automatically"),
            checkable=True,
            enabled=True,
        )
        save_auto.setChecked(self._config["auto_save"])

        save_with_image_data = create_action(
            text=self.tr("Save With Image Data"),
            slot=self.enable_save_image_with_data,
            icon="save",
            tip=self.tr("Save image data in label file"),
            checkable=True,
            checked=self._config["store_data"],
        )

        close = create_action(
            self.tr("&Close"),
            self.close_file,
            shortcuts["close"],
            "cancel",
            self.tr("Close current file"),
        )

        toggle_keep_prev_mode = create_action(
            self.tr("Keep Previous Annotation"),
            self.toggle_keep_prev_mode,
            shortcuts["toggle_keep_prev_mode"],
            None,
            self.tr('Toggle "Keep Previous Annotation" mode'),
            checkable=True,
        )
        toggle_keep_prev_mode.setChecked(self._config["keep_prev"])

        toggle_auto_use_last_label_mode = create_action(
            self.tr("Auto Use Last Label"),
            self.toggle_auto_use_last_label,
            shortcuts["toggle_auto_use_last_label"],
            None,
            self.tr('Toggle "Auto Use Last Label" mode'),
            checkable=True,
        )
        toggle_auto_use_last_label_mode.setChecked(self._config["auto_use_last_label"])

        toggle_pplxpl_sync_mode = create_action(
            self.tr("PPL-XPL Sync"),
            self.toggle_pplxpl_sync,
            None,
            "group",
            self.tr("Apply labels to all images in folder"),
            checkable=True,
        )
        toggle_pplxpl_sync_mode.setChecked(self.sync_pplxpl)

        create_mode = create_action(
            self.tr("Create Polygons"),
            lambda: self.toggle_draw_mode(False, create_mode="polygon"),
            shortcuts["create_polygon"],
            "polygon",
            self.tr("Start drawing polygons"),
            enabled=False,
        )
        create_rectangle_mode = create_action(
            self.tr("Create Rectangle"),
            lambda: self.toggle_draw_mode(False, create_mode="rectangle"),
            shortcuts["create_rectangle"],
            "rectangle",
            self.tr("Start drawing rectangles"),
            enabled=False,
        )
        create_cirle_mode = create_action(
            self.tr("Create Circle"),
            lambda: self.toggle_draw_mode(False, create_mode="circle"),
            shortcuts["create_circle"],
            "circle",
            self.tr("Start drawing circles"),
            enabled=False,
        )
        create_line_mode = create_action(
            self.tr("Create Line"),
            lambda: self.toggle_draw_mode(False, create_mode="line"),
            shortcuts["create_line"],
            "line",
            self.tr("Start drawing lines"),
            enabled=False,
        )
        create_point_mode = create_action(
            self.tr("Create Point"),
            lambda: self.toggle_draw_mode(False, create_mode="point"),
            shortcuts["create_point"],
            "point",
            self.tr("Start drawing points"),
            enabled=False,
        )
        create_line_strip_mode = create_action(
            self.tr("Create LineStrip"),
            lambda: self.toggle_draw_mode(False, create_mode="linestrip"),
            shortcuts["create_linestrip"],
            "line-strip",
            self.tr("Start drawing linestrip. Ctrl+LeftClick ends creation."),
            enabled=False,
        )
        edit_mode = create_action(
            self.tr("Edit Object"),
            self.set_edit_mode,
            shortcuts["edit_polygon"],
            "edit",
            self.tr("Move and edit the selected polygons"),
            enabled=False,
        )
        group_selected_shapes = create_action(
            self.tr("Group Selected Shapes"),
            self.canvas.group_selected_shapes,
            shortcuts["group_selected_shapes"],
            "group",
            self.tr("Group shapes by assigning a same group_id"),
            enabled=True,
        )
        ungroup_selected_shapes = create_action(
            self.tr("Ungroup Selected Shapes"),
            self.canvas.ungroup_selected_shapes,
            shortcuts["ungroup_selected_shapes"],
            "group",
            self.tr("Ungroup shapes"),
            enabled=True,
        )

        delete = create_action(
            self.tr("Delete"),
            self.delete_selected_shape,
            shortcuts["delete_polygon"],
            "cancel",
            self.tr("Delete the selected polygons"),
            enabled=False,
        )
        duplicate = create_action(
            self.tr("Duplicate Polygons"),
            self.duplicate_selected_shape,
            shortcuts["duplicate_polygon"],
            "copy",
            self.tr("Create a duplicate of the selected polygons"),
            enabled=False,
        )
        copy = create_action(
            self.tr("Copy Object"),
            self.copy_selected_shape,
            shortcuts["copy_polygon"],
            "copy",
            self.tr("Copy selected polygons to clipboard"),
            enabled=False,
        )
        paste = create_action(
            self.tr("Paste Object"),
            self.paste_selected_shape,
            shortcuts["paste_polygon"],
            "paste",
            self.tr("Paste copied polygons"),
            enabled=False,
        )
        undo_last_point = create_action(
            self.tr("Undo last point"),
            self.canvas.undo_last_point,
            shortcuts["undo_last_point"],
            "undo",
            self.tr("Undo last drawn point"),
            enabled=False,
        )
        remove_point = create_action(
            text=self.tr("Remove Selected Point"),
            slot=self.remove_selected_point,
            shortcut=shortcuts["remove_selected_point"],
            icon="edit",
            tip=self.tr("Remove selected point from polygon"),
            enabled=False,
        )

        undo = create_action(
            self.tr("Undo"),
            self.undo_shape_edit,
            shortcuts["undo"],
            "undo",
            self.tr("Undo last add and edit of shape"),
            enabled=False,
        )

        hide_all = create_action(
            self.tr("&Hide\nPolygons"),
            functools.partial(self.toggle_polygons, False),
            icon="eye",
            tip=self.tr("Hide all polygons"),
            enabled=False,
        )
        show_all = create_action(
            self.tr("&Show\nPolygons"),
            functools.partial(self.toggle_polygons, True),
            icon="eye",
            tip=self.tr("Show all polygons"),
            enabled=False,
        )

        documentation = create_action(
            self.tr("&Documentation"),
            self.documentation,
            icon="help",
            tip=self.tr("Show documentation"),
        )

        contact = create_action(
            self.tr("&Contact me"),
            self.contact,
            icon="help",
            tip=self.tr("Show contact page"),
        )

        zoom = QtWidgets.QWidgetAction(self)
        zoom.setDefaultWidget(self.zoom_widget)
        self.zoom_widget.setWhatsThis(
            str(
                self.tr(
                    "Zoom in or out of the image. Also accessible with "
                    "{} and {} from the canvas."
                )
            ).format(
                utils.fmt_shortcut(f"{shortcuts['zoom_in']},{shortcuts['zoom_out']}"),
                utils.fmt_shortcut(self.tr("Ctrl+Wheel")),
            )
        )
        self.zoom_widget.setEnabled(False)

        zoom_in = create_action(
            self.tr("Zoom &In"),
            functools.partial(self.add_zoom, 1.1),
            shortcuts["zoom_in"],
            "zoom-in",
            self.tr("Increase zoom level"),
            enabled=False,
        )
        zoom_out = create_action(
            self.tr("&Zoom Out"),
            functools.partial(self.add_zoom, 0.9),
            shortcuts["zoom_out"],
            "zoom-out",
            self.tr("Decrease zoom level"),
            enabled=False,
        )
        zoom_org = create_action(
            self.tr("&Original size"),
            functools.partial(self.set_zoom, 100),
            shortcuts["zoom_to_original"],
            "zoom",
            self.tr("Zoom to original size"),
            enabled=False,
        )
        keep_prev_scale = create_action(
            self.tr("&Keep Previous Scale"),
            self.enable_keep_prev_scale,
            tip=self.tr("Keep previous zoom scale"),
            checkable=True,
            checked=self._config["keep_prev_scale"],
            enabled=True,
        )
        keep_prev_loc = create_action(
            self.tr("&Keep Previous Location"),
            self.enable_keep_prev_loc,
            tip=self.tr("Keep previous canvas location"),
            checkable=True,
            checked=self._config.get("keep_prev_loc", True),
            enabled=True,
        )
        fit_window = create_action(
            self.tr("&Fit Window"),
            self.set_fit_window,
            shortcuts["fit_window"],
            "fit-window",
            self.tr("Zoom follows window size"),
            checkable=True,
            enabled=False,
        )
        fit_width = create_action(
            self.tr("Fit &Width"),
            self.set_fit_width,
            shortcuts["fit_width"],
            "fit-width",
            self.tr("Zoom follows window width"),
            checkable=True,
            enabled=False,
        )
        brightness_contrast = create_action(
            self.tr("&Brightness Contrast"),
            self.brightness_contrast,
            None,
            "color",
            "Adjust brightness and contrast",
            enabled=False,
        )
        line_width_act = QtWidgets.QWidgetAction(self)
        line_width_act.setDefaultWidget(self.line_width_spinbox)
        self.line_width_spinbox.setEnabled(True)

        fill_opacity_act = QtWidgets.QWidgetAction(self)
        fill_opacity_act.setDefaultWidget(self.fill_opacity_slider)
        self.fill_opacity_slider.setEnabled(True)
        show_cross_line = create_action(
            self.tr("&Show Cross Line"),
            self.enable_show_cross_line,
            tip=self.tr("Show cross line for mouse position"),
            icon="cartesian",
            checkable=True,
            checked=self._config["show_cross_line"],
            enabled=True,
        )
        show_groups = create_action(
            self.tr("&Show Groups"),
            self.enable_show_groups,
            tip=self.tr("Show shape groups"),
            icon=None,
            checkable=True,
            checked=self._config["show_groups"],
            enabled=True,
        )
        show_texts = create_action(
            self.tr("&Show Texts"),
            self.enable_show_texts,
            tip=self.tr("Show text above shapes"),
            icon=None,
            checkable=True,
            checked=self._config["show_texts"],
            enabled=True,
        )

        reset_views = create_action(
            self.tr("&Reset Views"),
            self.reset_dock_layout,
            shortcuts.get("reset_views", "Ctrl+Shift+V"),
            "refresh",
            self.tr("Reset dock widgets layout to default"),
            enabled=True,
        )

        # Languages
        select_lang_en = create_action(
            "English",
            functools.partial(self.set_language, "en_US"),
            icon="us",
            checkable=True,
            checked=self._config["language"] == "en_US",
            enabled=True,  # Always enable all language options
        )
        select_lang_vi = create_action(
            "Tiếng Việt",
            functools.partial(self.set_language, "vi_VN"),
            icon="vn",
            checkable=True,
            checked=self._config["language"] == "vi_VN",
            enabled=True,  # Always enable all language options
        )
        select_lang_zh = create_action(
            "中文",
            functools.partial(self.set_language, "zh_CN"),
            icon="cn",
            checkable=True,
            checked=self._config["language"] == "zh_CN",
            enabled=True,  # Always enable all language options
        )

        # Create action group for language actions to make them mutually exclusive
        lang_action_group = QtWidgets.QActionGroup(self)
        lang_action_group.setExclusive(True)
        lang_action_group.addAction(select_lang_en)
        lang_action_group.addAction(select_lang_vi)
        lang_action_group.addAction(select_lang_zh)

        # Store language actions for later use
        lang_actions = (select_lang_en, select_lang_vi, select_lang_zh)

        # Theme selector
        current_theme = self._config.get("theme", "system")
        select_theme_system = create_action(
            self.tr("System"),
            functools.partial(self.set_theme, "system"),
            icon="computer",
            checkable=True,
            checked=current_theme == "system",
            enabled=True,
        )
        select_theme_light = create_action(
            self.tr("Light"),
            functools.partial(self.set_theme, "light"),
            icon="sun",
            checkable=True,
            checked=current_theme == "light",
            enabled=True,
        )
        select_theme_dark = create_action(
            self.tr("Dark"),
            functools.partial(self.set_theme, "dark"),
            icon="moon",
            checkable=True,
            checked=current_theme == "dark",
            enabled=True,
        )

        # Create action group for theme actions to make them mutually exclusive
        theme_action_group = QtWidgets.QActionGroup(self)
        theme_action_group.setExclusive(True)
        theme_action_group.addAction(select_theme_system)
        theme_action_group.addAction(select_theme_light)
        theme_action_group.addAction(select_theme_dark)

        # Store theme actions for later use
        theme_actions = (select_theme_system, select_theme_light, select_theme_dark)

        # Group zoom controls into a list for easier toggling.
        zoom_actions = (
            self.zoom_widget,
            zoom_in,
            zoom_out,
            zoom_org,
            fit_window,
            fit_width,
        )
        self.zoom_mode = self.FIT_WINDOW
        fit_window.setChecked(Qt.Checked)
        self.scalers = {
            self.FIT_WINDOW: self.scale_fit_window,
            self.FIT_WIDTH: self.scale_fit_width,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = create_action(
            self.tr("&Edit Label"),
            self.edit_label,
            shortcuts["edit_label"],
            "edit",
            self.tr("Modify the label of the selected polygon"),
            enabled=False,
        )
        set_image_label = create_action(
            self.tr("Set Image Label"),
            self.edit_image_label,
            None,
            "tag",
            self.tr("Set label for the entire image"),
        )

        fill_drawing = create_action(
            self.tr("Fill Drawing Polygon"),
            self.canvas.set_fill_drawing,
            None,
            "color",
            self.tr("Fill polygon while drawing"),
            checkable=True,
            enabled=True,
        )
        fill_drawing.trigger()

        # AI Actions
        toggle_auto_labeling_widget = create_action(
            self.tr("&Auto Labeling"),
            self.toggle_auto_labeling_widget,
            shortcuts["auto_label"],
            "brain",
            self.tr("Auto Labeling"),
        )

        # Label list context menu.
        label_menu = QtWidgets.QMenu()
        utils.add_actions(label_menu, (edit, delete))
        self.label_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label_list.customContextMenuRequested.connect(self.pop_label_list_menu)

        # Store actions for further handling.
        self.actions = utils.Struct(
            save_auto=save_auto,
            save_with_image_data=save_with_image_data,
            change_output_dir=change_output_dir,
            save=save,
            save_as=save_as,
            open=open_,
            close=close,
            delete_file=delete_file,
            toggle_keep_prev_mode=toggle_keep_prev_mode,
            toggle_auto_use_last_label_mode=toggle_auto_use_last_label_mode,
            toggle_pplxpl_sync_mode=toggle_pplxpl_sync_mode,
            delete=delete,
            edit=edit,
            duplicate=duplicate,
            copy=copy,
            paste=paste,
            undo_last_point=undo_last_point,
            undo=undo,
            remove_point=remove_point,
            set_image_label=set_image_label,
            create_mode=create_mode,
            edit_mode=edit_mode,
            create_rectangle_mode=create_rectangle_mode,
            create_cirle_mode=create_cirle_mode,
            create_line_mode=create_line_mode,
            create_point_mode=create_point_mode,
            create_line_strip_mode=create_line_strip_mode,
            zoom=zoom,
            zoom_in=zoom_in,
            zoom_out=zoom_out,
            zoom_org=zoom_org,
            keep_prev_scale=keep_prev_scale,
            keep_prev_loc=keep_prev_loc,
            fit_window=fit_window,
            fit_width=fit_width,
            line_width=line_width_act,
            fill_opacity=fill_opacity_act,
            brightness_contrast=brightness_contrast,
            show_cross_line=show_cross_line,
            show_groups=show_groups,
            show_texts=show_texts,
            zoom_actions=zoom_actions,
            open_next_image=open_next_image,
            open_prev_image=open_prev_image,
            file_menu_actions=(open_, opendir, save, save_as, close),
            tool=(),
            # XXX: need to add some actions here to activate the shortcut
            editMenu=(
                edit,
                duplicate,
                delete,
                None,
                undo,
                undo_last_point,
                None,
                remove_point,
                None,
                toggle_keep_prev_mode,
                toggle_auto_use_last_label_mode,
                toggle_pplxpl_sync_mode,
                None,
                set_image_label,
            ),
            # menu shown at right click
            menu=(
                create_mode,
                create_rectangle_mode,
                create_cirle_mode,
                create_line_mode,
                create_point_mode,
                create_line_strip_mode,
                edit_mode,
                edit,
                duplicate,
                copy,
                paste,
                delete,
                undo,
                undo_last_point,
                remove_point,
            ),
            on_load_active=(
                close,
                create_mode,
                create_rectangle_mode,
                create_cirle_mode,
                create_line_mode,
                create_point_mode,
                create_line_strip_mode,
                edit_mode,
                brightness_contrast,
            ),
            on_shapes_present=(save_as, hide_all, show_all),
            group_selected_shapes=group_selected_shapes,
            ungroup_selected_shapes=ungroup_selected_shapes,
        )

        self.canvas.vertex_selected.connect(self.actions.remove_point.setEnabled)

        # Tools
        create_action(
            self.tr("Tools"),
            self.toggle_tools,
            "tools",
            "tools",
            self.tr("Tools"),
            enabled=False,
        )

        export_annotations = create_action(
            self.tr("Export Annotations"),
            self.export_annotations,
            None,
            "box",
            self.tr("Export annotations to other formats"),
        )

        # Store theme actions for later use
        theme_actions = (select_theme_system, select_theme_light, select_theme_dark)

        self.menus = utils.Struct(
            file=self.menu(self.tr("&File")),
            edit=self.menu(self.tr("&Edit")),
            view=self.menu(self.tr("&View")),
            language=self.menu(self.tr("&Language")),
            theme=self.menu(self.tr("&Theme")),
            label_sets=self.menu(self.tr("&Label Sets")),
            tools=self.menu(self.tr("&Tools")),
            help=self.menu(self.tr("&Help")),
            recent_files=QtWidgets.QMenu(self.tr("Open &Recent")),
            label_list=label_menu,
        )

        # Add theme actions
        utils.add_actions(
            self.menus.theme,
            theme_actions,
        )

        # Add theme actions
        utils.add_actions(
            self.menus.theme,
            theme_actions,
        )

        utils.add_actions(
            self.menus.file,
            (
                open_,
                open_next_image,
                open_prev_image,
                opendir,
                self.menus.recent_files,
                save,
                save_as,
                save_auto,
                change_output_dir,
                save_with_image_data,
                close,
                delete_file,
                None,
            ),
        )
        utils.add_actions(
            self.menus.help,
            (
                documentation,
                contact,
            ),
        )
        utils.add_actions(
            self.menus.tools,
            (export_annotations,),
        )
        utils.add_actions(
            self.menus.language,
            lang_actions,
        )
        utils.add_actions(
            self.menus.theme,
            (
                select_theme_system,
                select_theme_light,
                select_theme_dark,
            ),
        )

        if self._config.get("label_sets"):
            actions = []
            for name in self._config["label_sets"]:
                act = create_action(
                    name,
                    functools.partial(self.switch_label_set, name),
                    enabled=True,
                )
                actions.append(act)
            utils.add_actions(self.menus.label_sets, actions)

        utils.add_actions(
            self.menus.view,
            (
                self.shape_text_dock.toggleViewAction(),
                self.flag_dock.toggleViewAction(),
                self.label_dock.toggleViewAction(),
                self.shape_dock.toggleViewAction(),
                self.file_dock.toggleViewAction(),
                reset_views,
                None,
                fill_drawing,
                None,
                hide_all,
                show_all,
                None,
                zoom_in,
                zoom_out,
                zoom_org,
                keep_prev_scale,
                keep_prev_loc,
                None,
                fit_window,
                fit_width,
                None,
                brightness_contrast,
                show_cross_line,
                show_texts,
                show_groups,
                group_selected_shapes,
                ungroup_selected_shapes,
            ),
        )

        self.menus.file.aboutToShow.connect(self.update_file_menu)

        # Custom context menu for the canvas widget:
        utils.add_actions(self.canvas.menus[0], self.actions.menu)
        utils.add_actions(
            self.canvas.menus[1],
            (
                utils.new_action(self, "&Copy here", self.copy_shape),
                utils.new_action(self, "&Move here", self.move_shape),
            ),
        )

        # Tool actions definition
        self.actions.tool = (
            # open_,
            opendir,
            open_next_image,
            open_prev_image,
            save,
            delete_file,
            None,
            create_mode,
            self.actions.create_rectangle_mode,
            self.actions.create_cirle_mode,
            self.actions.create_line_mode,
            self.actions.create_point_mode,
            self.actions.create_line_strip_mode,
            edit_mode,
            delete,
            undo,
            None,
            zoom,
            line_width_act,
            fill_opacity_act,
            fit_width,
            toggle_pplxpl_sync_mode,
            toggle_auto_labeling_widget,
        )

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

        # Scale icon and dock size based on screen dpi
        screen = QtWidgets.QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch() if screen else 96
        scale = dpi / 96.0
        base_icon_size = int(24 * scale)
        self._base_icon_size = base_icon_size
        self._icon_size = base_icon_size
        self._dock_width = int(base_icon_size + 16)

        self.tools.setIconSize(QtCore.QSize(self._icon_size, self._icon_size))

        # Set initial size constraints for vertical layout
        self.tools_dock.setMinimumWidth(self._dock_width)
        self.tools_dock.setMaximumWidth(self._dock_width)

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

        # Add dock to main window
        self.main_window.addDockWidget(Qt.LeftDockWidgetArea, self.tools_dock)

        # Connect signal for location changes to update toolbar orientation
        self.tools_dock.dockLocationChanged.connect(self.on_tools_dock_location_changed)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.main_window)

        # Setup central area
        central_layout = QVBoxLayout()
        central_layout.setContentsMargins(0, 0, 0, 0)
        self.label_instruction = QLabel(self.get_labeling_instruction())
        self.label_instruction.setContentsMargins(0, 0, 0, 0)
        self.auto_labeling_widget = AutoLabelingWidget(self)
        self.auto_labeling_widget.auto_segmentation_requested.connect(
            self.on_auto_segmentation_requested
        )
        self.auto_labeling_widget.auto_segmentation_disabled.connect(
            self.on_auto_segmentation_disabled
        )
        self.canvas.auto_labeling_marks_updated.connect(
            self.auto_labeling_widget.on_new_marks
        )
        self.auto_labeling_widget.auto_labeling_mode_changed.connect(
            self.canvas.set_auto_labeling_mode
        )
        self.auto_labeling_widget.clear_auto_labeling_action_requested.connect(
            self.clear_auto_labeling_marks
        )
        self.auto_labeling_widget.finish_auto_labeling_object_action_requested.connect(
            self.finish_auto_labeling_object
        )
        self.auto_labeling_widget.model_manager.prediction_started.connect(
            lambda: self.canvas.set_loading(True, self.tr("Please wait..."))
        )
        self.auto_labeling_widget.model_manager.prediction_finished.connect(
            lambda: self.canvas.set_loading(False)
        )
        self.next_files_changed.connect(
            self.auto_labeling_widget.model_manager.on_next_files_changed
        )
        self.auto_labeling_widget.model_manager.request_next_files_requested.connect(
            lambda: self.inform_next_files(self.filename)
        )
        self.auto_labeling_widget.hide()  # Hide by default

        central_layout.addWidget(self.label_instruction)
        central_layout.addWidget(self.auto_labeling_widget)
        central_layout.addWidget(scroll_area)

        # Set the central widget content
        center_widget = QtWidgets.QWidget()
        center_widget.setLayout(central_layout)
        self.main_window.centralWidget().layout().addWidget(center_widget)

        # Stretch central area (image view)
        layout.setStretch(0, 1)

        # Arrange dock widgets separately rather than tabbing them
        # All docks are initially added to RightDockWidgetArea but can be moved by the user
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_text_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)

        self.shape_text_edit.textChanged.connect(self.shape_text_changed)

        self.setLayout(layout)

        if output_file is not None and self._config["auto_save"]:
            logger.warning(
                "If `auto_save` argument is True, `output_file` argument "
                "is ignored and output filename is automatically "
                "set as IMAGE_BASENAME.json."
            )
        self.output_file = output_file
        self.output_dir = output_dir

        # Application state.
        self.image = QtGui.QImage()
        self.image_path = None
        self.recent_files = []
        self.max_recent = 7
        self.other_data = {}
        self.zoom_level = 100
        self.fit_window = False
        self.zoom_values = {}  # key=filename, value=(zoom_mode, zoom_value)
        self.brightness_contrast_values = {}
        self.scroll_values = {
            Qt.Horizontal: {},
            Qt.Vertical: {},
        }  # key=filename, value=scroll_value
        self.prev_image_size = None

        if filename is not None and osp.isdir(filename):
            self.import_image_folder(filename, load=False)
        else:
            self.filename = filename

        if config["file_search"]:
            self.file_search.setText(config["file_search"])
            self.file_search_changed()

        # XXX: Could be completely declarative.
        # Restore application settings.
        self.recent_files = self.settings.value("recent_files", []) or []
        size = self.settings.value("window/size", QtCore.QSize(600, 500))
        position = self.settings.value("window/position", QtCore.QPoint(0, 0))
        # state = self.settings.value("window/state", QtCore.QByteArray())
        self.resize(size)
        self.move(position)
        self.update_toolbar_scale()
        # or simply:
        # self.restoreGeometry(settings['window/geometry']

        # Populate the File menu dynamically.
        self.update_file_menu()

        # Since loading the file may take some time,
        # make sure it runs in the background.
        if self.filename is not None:
            self.queue_event(functools.partial(self.load_file, self.filename))

        # Callbacks:
        self.zoom_widget.valueChanged.connect(self.paint_canvas)

        self.populate_mode_actions()

        self.first_start = False
        if self.first_start:
            QWhatsThis.enterWhatsThisMode()

        self.set_text_editing(False)

        # We'll load dock state with a longer delay to ensure UI is fully ready
        QtCore.QTimer.singleShot(100, self.load_dock_state)

        # Setup periodic dock state saving
        self._dock_save_timer = QtCore.QTimer(self)
        self._dock_save_timer.setInterval(60000)  # Save state every minute
        self._dock_save_timer.timeout.connect(lambda: self.save_dock_state(force=True))
        self._dock_save_timer.start()

    def set_language(self, language):
        if self._config["language"] == language:
            return
        self._config["language"] = language
        save_config(self._config)

        # Show dialog to restart application
        msg_box = QMessageBox()
        msg_box.setText(self.tr("Please restart the application to apply changes."))
        msg_box.exec_()
        self.parent.parent.close()

    def get_labeling_instruction(self):
        text_mode = self.tr("Mode:")
        text_shortcuts = self.tr("Shortcuts:")
        text_previous = self.tr("Previous:")
        text_next = self.tr("Next:")
        text_rectangle = self.tr("Rectangle:")
        text_polygon = self.tr("Polygon:")
        return (
            f"<b>{text_mode}</b> {self.canvas.get_mode()} - <b>{text_shortcuts}</b>"
            f" {text_previous} <b>A</b>, {text_next} <b>D</b>,"
            f" {text_rectangle} <b>R</b>,"
            f" {text_polygon} <b>P</b>"
        )

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
        toolbar.setIconSize(QtCore.QSize(self._icon_size, self._icon_size))
        toolbar.setMaximumWidth(self._dock_width)
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
            self.actions.create_line_strip_mode,
            self.actions.edit_mode,
        )
        utils.add_actions(self.menus.edit, actions + self.actions.editMenu)

    def set_dirty(self):
        # Even if we autosave the file, we keep the ability to undo
        self.actions.undo.setEnabled(self.canvas.is_shape_restorable)

        if self._config["auto_save"] or self.actions.save_auto.isChecked():
            label_file = osp.splitext(self.image_path)[0] + ".json"
            if self.output_dir:
                label_file_without_path = osp.basename(label_file)
                label_file = osp.join(self.output_dir, label_file_without_path)
            self.save_labels(label_file)
            return
        self.dirty = True
        self.actions.save.setEnabled(True)
        title = __appname__
        if self.filename is not None:
            title = f"{title} - {self.filename}*"
        self.setWindowTitle(title)
        if self.sync_pplxpl:
            self.sync_annotations_to_folder()

    def set_clean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create_mode.setEnabled(True)
        self.actions.create_rectangle_mode.setEnabled(True)
        self.actions.create_cirle_mode.setEnabled(True)
        self.actions.create_line_mode.setEnabled(True)
        self.actions.create_point_mode.setEnabled(True)
        self.actions.create_line_strip_mode.setEnabled(True)
        title = __appname__
        if self.filename is not None:
            title = f"{title} - {self.filename}"
        self.setWindowTitle(title)

        if self.has_label_file():
            self.actions.delete_file.setEnabled(True)
        else:
            self.actions.delete_file.setEnabled(False)

    def toggle_actions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for act in self.actions.zoom_actions:
            act.setEnabled(value)
        for act in self.actions.on_load_active:
            act.setEnabled(value)

    def queue_event(self, function):
        QtCore.QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def reset_state(self):
        self.label_list.clear()
        self.filename = None
        self.image_path = None
        self.image_data = None
        self.label_file = None
        self.other_data = {}
        self.canvas.reset_state()

    def current_item(self):
        items = self.label_list.selected_items()
        if items:
            return items[0]
        return None

    def add_recent_file(self, filename):
        if filename in self.recent_files:
            self.recent_files.remove(filename)
        elif len(self.recent_files) >= self.max_recent:
            self.recent_files.pop()
        self.recent_files.insert(0, filename)

    # Callbacks

    def undo_shape_edit(self):
        self.canvas.restore_shape()
        self.label_list.clear()
        self.load_shapes(self.canvas.shapes)
        self.actions.undo.setEnabled(self.canvas.is_shape_restorable)

    def documentation(self):
        url = "https://anylabeling.nrl.ai/"  # NOQA
        webbrowser.open(url)

    def contact(self):
        url = "https://www.nrl.ai/contact"  # NOQA
        webbrowser.open(url)

    def toggle_drawing_sensitive(self, drawing=True):
        """Toggle drawing sensitive.

        In the middle of drawing, toggling between modes should be disabled.
        """
        self.actions.edit_mode.setEnabled(not drawing)
        self.actions.undo_last_point.setEnabled(drawing)
        self.actions.undo.setEnabled(not drawing)
        self.actions.delete.setEnabled(not drawing)

    def toggle_draw_mode(
        self, edit=True, create_mode="rectangle", disable_auto_labeling=True
    ):
        # Disable auto labeling if needed
        if (
            disable_auto_labeling
            and self.auto_labeling_widget.auto_labeling_mode != AutoLabelingMode.NONE
        ):
            self.clear_auto_labeling_marks()
            self.auto_labeling_widget.set_auto_labeling_mode(None)

        self.set_text_editing(False)

        self.canvas.set_editing(edit)
        self.canvas.create_mode = create_mode
        if edit:
            self.actions.create_mode.setEnabled(True)
            self.actions.create_rectangle_mode.setEnabled(True)
            self.actions.create_cirle_mode.setEnabled(True)
            self.actions.create_line_mode.setEnabled(True)
            self.actions.create_point_mode.setEnabled(True)
            self.actions.create_line_strip_mode.setEnabled(True)
        else:
            if create_mode == "polygon":
                self.actions.create_mode.setEnabled(False)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
            elif create_mode == "rectangle":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(False)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
            elif create_mode == "line":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(False)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
            elif create_mode == "point":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(False)
                self.actions.create_line_strip_mode.setEnabled(True)
            elif create_mode == "circle":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(False)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
            elif create_mode == "linestrip":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(False)
            else:
                raise ValueError(f"Unsupported create_mode: {create_mode}")
        self.actions.edit_mode.setEnabled(not edit)
        self.label_instruction.setText(self.get_labeling_instruction())

    def set_edit_mode(self):
        # Disable auto labeling
        self.clear_auto_labeling_marks()
        self.auto_labeling_widget.set_auto_labeling_mode(None)

        self.toggle_draw_mode(True)
        self.set_text_editing(True)
        self.label_instruction.setText(self.get_labeling_instruction())

    def update_file_menu(self):
        current = self.filename

        def exists(filename):
            return osp.exists(str(filename))

        menu = self.menus.recent_files
        menu.clear()
        files = [f for f in self.recent_files if f != current and exists(f)]
        for i, f in enumerate(files):
            icon = utils.new_icon("labels")
            menu_action = QtWidgets.QAction(
                icon, "&%d %s" % (i + 1, QtCore.QFileInfo(f).fileName()), self
            )
            menu_action.triggered.connect(functools.partial(self.load_recent, f))
            menu.addAction(menu_action)

    def pop_label_list_menu(self, point):
        self.menus.label_list.exec_(self.label_list.mapToGlobal(point))

    def validate_label(self, label):
        # no validation
        if self._config["validate_label"] is None:
            return True

        for i in range(self.unique_label_list.count()):
            label_i = self.unique_label_list.item(i).data(Qt.UserRole)
            if self._config["validate_label"] in ["exact"]:
                if label_i == label:
                    return True
        return False

    def edit_label(self, item=None):
        if item and not isinstance(item, LabelListWidgetItem):
            raise TypeError("item must be LabelListWidgetItem type")

        if not self.canvas.editing():
            return
        if not item:
            item = self.current_item()
        if item is None:
            return
        shape = item.shape()
        if shape is None:
            return
        text, flags, group_id = self.label_dialog.pop_up(
            text=shape.label,
            flags=shape.flags,
            group_id=shape.group_id,
        )
        if text is None:
            return
        labels = [t.strip() for t in text.split(",") if t.strip()]
        for lb in labels:
            if not self.validate_label(lb):
                self.error_message(
                    self.tr("Invalid label"),
                    self.tr("Invalid label '{}' with validation type '{}'").format(
                        lb, self._config["validate_label"]
                    ),
                )
                return
        shape.label = text
        shape.flags = flags
        shape.group_id = group_id

        # Add to label history
        for lb in shape.labels:
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
                self.label_file.image_labels
                if self.label_file
                else self.other_data.get("image_labels", [])
            ),
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
                    self.tr("Invalid label '{}' with validation type '{}'").format(
                        lb, self._config["validate_label"]
                    ),
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

        if not self.may_continue():
            return

        current_index = self.image_list.index(str(item.text()))
        if current_index < len(self.image_list):
            filename = self.image_list[current_index]
            if filename:
                self.load_file(filename)

    # React to canvas signals.
    def shape_selection_changed(self, selected_shapes):
        self._no_selection_slot = True
        for shape in self.canvas.selected_shapes:
            shape.selected = False
        self.label_list.clearSelection()
        self.canvas.selected_shapes = selected_shapes
        for shape in self.canvas.selected_shapes:
            shape.selected = True
            item = self.label_list.find_item_by_shape(shape)
            self.label_list.select_item(item)
            self.label_list.scroll_to_item(item)
        self._no_selection_slot = False
        n_selected = len(selected_shapes)
        self.actions.delete.setEnabled(n_selected)
        self.actions.duplicate.setEnabled(n_selected)
        self.actions.copy.setEnabled(n_selected)
        self.actions.edit.setEnabled(n_selected == 1)
        self.set_text_editing(True)

    def update_unique_label_list(self):
        """Refresh unique label list from current config."""
        self.unique_label_list.clear()
        if self._config.get("labels"):
            for label in self._config["labels"]:
                item = self.unique_label_list.create_item_from_label(label)
                self.unique_label_list.addItem(item)
                rgb = self._get_rgb_by_label(label)
                self.unique_label_list.set_item_label(item, label, rgb)

    def update_label_dialog_labels(self):
        """Refresh label dialog list from current config."""
        self.label_dialog.label_list.clear()
        if self._config.get("labels"):
            self.label_dialog.label_list.addItems(self._config["labels"])
        if self.label_dialog._sort_labels:
            self.label_dialog.label_list.sortItems()

    def add_label(self, shape):
        if shape.group_id is None:
            text = shape.label
        else:
            text = f"{shape.label} ({shape.group_id})"
        label_list_item = LabelListWidgetItem(text, shape)
        self.label_list.add_iem(label_list_item)
        # Don't add special autolabeling labels to the unique_label_list
        for lb in shape.labels:
            if lb not in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ] and not self.unique_label_list.find_items_by_label(lb):
                item = self.unique_label_list.create_item_from_label(lb)
                self.unique_label_list.addItem(item)
                rgb = self._get_rgb_by_label(lb)
                self.unique_label_list.set_item_label(item, lb, rgb)

        # Add label to history if it is not a special label
        for lb in shape.labels:
            if lb not in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ]:
                self.label_dialog.add_label_history(lb)

        for action in self.actions.on_shapes_present:
            action.setEnabled(True)

        self._update_shape_color(shape)
        label_list_item.setText(
            '{} <font color="#{:02x}{:02x}{:02x}">●</font>'.format(
                html.escape(text), *shape.fill_color.getRgb()[:3]
            )
        )

    def shape_text_changed(self):
        text = self.shape_text_edit.toPlainText()
        if self.canvas.current is not None:
            self.canvas.current.text = text
        elif self.canvas.editing() and len(self.canvas.selected_shapes) == 1:
            self.canvas.selected_shapes[0].text = text
        else:
            self.other_data["image_text"] = text
        self.set_dirty()

    def _update_shape_color(self, shape):
        r, g, b = self._get_rgb_by_label(shape.primary_label)
        shape.line_color = QtGui.QColor(r, g, b)
        shape.vertex_fill_color = QtGui.QColor(r, g, b)
        shape.hvertex_fill_color = QtGui.QColor(255, 255, 255)
        shape.fill_color = QtGui.QColor(r, g, b, Shape.fill_opacity)
        shape.select_line_color = QtGui.QColor(255, 255, 255)
        shape.select_fill_color = QtGui.QColor(r, g, b, Shape.fill_opacity)

    def _get_rgb_by_label(self, label):
        if self._config["shape_color"] == "auto":
            # For special autolabeling labels, use fixed colors
            if label in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ]:
                if label == AutoLabelingMode.OBJECT:
                    return (0, 255, 255)  # Cyan color for object
                elif label == AutoLabelingMode.ADD:
                    return (0, 255, 0)  # Green color for add
                elif label == AutoLabelingMode.REMOVE:
                    return (255, 0, 0)  # Red color for remove

            if not self.unique_label_list.find_items_by_label(label):
                item = self.unique_label_list.create_item_from_label(label)
                self.unique_label_list.addItem(item)
            item = self.unique_label_list.find_items_by_label(label)[0]
            label_id = self.unique_label_list.indexFromItem(item).row() + 1
            label_id += self._config["shift_auto_shape_color"]
            return LABEL_COLORMAP[label_id % len(LABEL_COLORMAP)]
        if (
            self._config["shape_color"] == "manual"
            and self._config["label_colors"]
            and label in self._config["label_colors"]
        ):
            return self._config["label_colors"][label]
        if self._config["default_shape_color"]:
            return self._config["default_shape_color"]
        return (0, 255, 0)

    def remove_labels(self, shapes):
        for shape in shapes:
            item = self.label_list.find_item_by_shape(shape)
            self.label_list.remove_item(item)

    def load_shapes(self, shapes, replace=True):
        self._no_selection_slot = True
        for shape in shapes:
            self.add_label(shape)
        self.label_list.clearSelection()
        self._no_selection_slot = False
        self.canvas.load_shapes(shapes, replace=replace)

    def load_labels(self, shapes):
        s = []
        for shape in shapes:
            labels = shape.get("labels", [])
            if not labels:
                label = shape.get("label", "")
                labels = [label] if label else []
            text = shape.get("text", "")
            points = shape["points"]
            shape_type = shape["shape_type"]
            flags = shape["flags"]
            group_id = shape["group_id"]
            other_data = shape["other_data"]

            if not points:
                # skip point-empty shape
                continue

            shape = Shape(
                labels=labels,
                text=text,
                shape_type=shape_type,
                group_id=group_id,
            )
            for x, y in points:
                shape.add_point(QtCore.QPointF(x, y))
            shape.close()

            default_flags = {}
            if self._config["label_flags"]:
                for pattern, keys in self._config["label_flags"].items():
                    for lb in labels:
                        if re.match(pattern, lb):
                            for key in keys:
                                default_flags[key] = False
            shape.flags = default_flags
            if flags:
                shape.flags.update(flags)
            shape.other_data = other_data

            s.append(shape)
        self.load_shapes(s)

    def load_flags(self, flags):
        self.flag_widget.clear()
        for key, flag in flags.items():
            item = QtWidgets.QListWidgetItem(key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if flag else Qt.Unchecked)
            self.flag_widget.addItem(item)

    def save_labels(self, filename):
        label_file = LabelFile()

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

        # Get current shapes
        # Excluding auto labeling special shapes
        shapes = [
            format_shape(item.shape())
            for item in self.label_list
            if item.shape().primary_label
            not in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ]
        ]
        flags = {}
        for i in range(self.flag_widget.count()):
            item = self.flag_widget.item(i)
            key = item.text()
            flag = item.checkState() == Qt.Checked
            flags[key] = flag
        try:
            image_path = osp.relpath(self.image_path, osp.dirname(filename))
            image_data = self.image_data if self._config["store_data"] else None
            if osp.dirname(filename) and not osp.exists(osp.dirname(filename)):
                os.makedirs(osp.dirname(filename))
            label_file.image_labels = self.other_data.get("image_labels", [])
            label_file.save(
                filename=filename,
                shapes=shapes,
                image_path=image_path,
                image_data=image_data,
                image_height=self.image.height(),
                image_width=self.image.width(),
                other_data=self.other_data,
                flags=flags,
            )
            self.label_file = label_file
            items = self.file_list_widget.findItems(self.image_path, Qt.MatchExactly)
            if len(items) > 0:
                if len(items) != 1:
                    raise RuntimeError("There are duplicate files.")
                items[0].setCheckState(Qt.Checked)
            if self.sync_pplxpl:
                self.sync_annotations_to_folder()
            # disable allows next and previous image to proceed
            # self.filename = filename
            return True
        except LabelFileError as e:
            self.error_message(
                self.tr("Error saving label data"), self.tr("<b>%s</b>") % e
            )
            return False

    def duplicate_selected_shape(self):
        added_shapes = self.canvas.duplicate_selected_shapes()
        self.label_list.clearSelection()
        for shape in added_shapes:
            self.add_label(shape)
        self.set_dirty()

    def paste_selected_shape(self):
        self.load_shapes(self._copied_shapes, replace=False)
        self.set_dirty()

    def copy_selected_shape(self):
        self._copied_shapes = [s.copy() for s in self.canvas.selected_shapes]
        self.actions.paste.setEnabled(len(self._copied_shapes) > 0)

    def label_selection_changed(self):
        if self._no_selection_slot:
            return
        if self.canvas.editing():
            selected_shapes = []
            for item in self.label_list.selected_items():
                selected_shapes.append(item.shape())
            if selected_shapes:
                self.canvas.select_shapes(selected_shapes)
            else:
                self.canvas.deselect_shape()

    def label_item_changed(self, item):
        shape = item.shape()
        self.canvas.set_shape_visible(shape, item.checkState() == Qt.Checked)

    def label_order_changed(self):
        self.set_dirty()
        self.canvas.load_shapes([item.shape() for item in self.label_list])

    # Callback functions:

    def new_shape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        items = self.unique_label_list.selectedItems()
        text = None
        if items:
            text = ",".join(item.data(Qt.UserRole) for item in items)
        flags = {}
        group_id = None

        if self.canvas.shapes[-1].primary_label in [
            AutoLabelingMode.ADD,
            AutoLabelingMode.REMOVE,
        ]:
            text = self.canvas.shapes[-1].primary_label
        elif (
            self._config["display_label_popup"]
            or not text
            or self.canvas.shapes[-1].primary_label == AutoLabelingMode.OBJECT
        ):
            last_label = self.find_last_label()
            if self._config["auto_use_last_label"] and last_label:
                text = last_label
            else:
                previous_text = self.label_dialog.edit.text()
                text, flags, group_id = self.label_dialog.pop_up(text)
                if not text:
                    self.label_dialog.edit.setText(previous_text)

        if text:
            for lb in [t.strip() for t in text.split(",") if t.strip()]:
                if not self.validate_label(lb):
                    self.error_message(
                        self.tr("Invalid label"),
                        self.tr("Invalid label '{}' with validation type '{}'").format(
                            lb, self._config["validate_label"]
                        ),
                    )
                    text = ""
                    return

        if text:
            self.label_list.clearSelection()
            shape = self.canvas.set_last_label(text, flags)
            shape.group_id = group_id
            shape.label = text
            self.add_label(shape)
            self.actions.edit_mode.setEnabled(True)
            self.actions.undo_last_point.setEnabled(False)
            self.actions.undo.setEnabled(True)
            self.set_dirty()
        else:
            self.canvas.undo_last_line()
            self.canvas.shapes_backups.pop()

    def scroll_request(self, delta, orientation):
        units = -delta * 0.1  # natural scroll
        scroll_bar = self.scroll_bars[orientation]
        value = scroll_bar.value() + scroll_bar.singleStep() * units
        self.set_scroll(orientation, value)

    def set_scroll(self, orientation, value):
        self.scroll_bars[orientation].setValue(round(value))
        self.scroll_values[orientation][self.filename] = value

    def set_zoom(self, value):
        self.actions.fit_width.setChecked(False)
        self.actions.fit_window.setChecked(False)
        self.zoom_mode = self.MANUAL_ZOOM
        self.zoom_widget.setValue(value)
        self.zoom_values[self.filename] = (self.zoom_mode, value)

    def add_zoom(self, increment=1.1):
        zoom_value = self.zoom_widget.value() * increment
        if increment > 1:
            zoom_value = math.ceil(zoom_value)
        else:
            zoom_value = math.floor(zoom_value)
        self.set_zoom(zoom_value)

    def zoom_request(self, delta, pos):
        canvas_width_old = self.canvas.width()
        units = 1.1
        if delta < 0:
            units = 0.9
        self.add_zoom(units)

        canvas_width_new = self.canvas.width()
        if canvas_width_old != canvas_width_new:
            canvas_scale_factor = canvas_width_new / canvas_width_old

            x_shift = round(pos.x() * canvas_scale_factor - pos.x())
            y_shift = round(pos.y() * canvas_scale_factor - pos.y())

            self.set_scroll(
                Qt.Horizontal,
                self.scroll_bars[Qt.Horizontal].value() + x_shift,
            )
            self.set_scroll(
                Qt.Vertical,
                self.scroll_bars[Qt.Vertical].value() + y_shift,
            )

    def set_fit_window(self, value=True):
        if value:
            self.actions.fit_width.setChecked(False)
        self.zoom_mode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def set_fit_width(self, value=True):
        if value:
            self.actions.fit_window.setChecked(False)
        self.zoom_mode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def enable_keep_prev_scale(self, enabled):
        self._config["keep_prev_scale"] = enabled
        self.actions.keep_prev_scale.setChecked(enabled)
        save_config(self._config)

    def enable_keep_prev_loc(self, enabled):
        self._config["keep_prev_loc"] = enabled
        self.actions.keep_prev_loc.setChecked(enabled)
        save_config(self._config)

    def enable_show_cross_line(self, enabled):
        self._config["show_cross_line"] = enabled
        self.actions.show_cross_line.setChecked(enabled)
        self.canvas.set_show_cross_line(enabled)
        save_config(self._config)

    def enable_show_groups(self, enabled):
        self._config["show_groups"] = enabled
        self.actions.show_groups.setChecked(enabled)
        self.canvas.set_show_groups(enabled)
        save_config(self._config)

    def enable_show_texts(self, enabled):
        self._config["show_texts"] = enabled
        self.actions.show_texts.setChecked(enabled)
        self.canvas.set_show_texts(enabled)
        save_config(self._config)

    def line_width_changed(self, value):
        self._apply_line_width_to_all(value)

    @classmethod
    def _apply_line_width_to_all(cls, value):
        """Apply line width to every open labeling widget (update all shapes)."""
        Shape.line_width = value
        for widget in list(cls._instances):
            widget._config["shape"]["line_width"] = value
            if widget.line_width_spinbox.value() != value:
                widget.line_width_spinbox.blockSignals(True)
                widget.line_width_spinbox.setValue(value)
                widget.line_width_spinbox.blockSignals(False)
            for shape in widget.canvas.shapes:
                shape.line_width = value
            widget.canvas.update()
            save_config(widget._config)

    @classmethod
    def _apply_fill_opacity_to_all(cls, value):
        """Apply mask opacity to every open labeling widget (update all shapes)."""
        # 更新全局 Shape 默认透明度和填充颜色的 alpha 值
        Shape.fill_opacity = value
        Shape.fill_color.setAlpha(value)
        Shape.select_fill_color.setAlpha(value)
        # 遍历所有活动的 LabelingWidget 实例，同步设置遮罩透明度
        for widget in list(cls._instances):
            # 更新配置中的透明度值
            widget._config["shape"]["fill_opacity"] = value
            # 同步更新各实例的滑块数值（避免递归信号触发）
            if widget.fill_opacity_slider.value() != value:
                widget.fill_opacity_slider.blockSignals(True)
                widget.fill_opacity_slider.setValue(value)
                widget.fill_opacity_slider.blockSignals(False)
            # 更新该实例所有 Shape 对象的填充颜色透明度
            for shape in widget.canvas.shapes:
                shape.fill_color.setAlpha(value)
                shape.select_fill_color.setAlpha(value)
            # 重绘画布，立即应用透明度更改
            widget.canvas.update()
            save_config(widget._config)

    def fill_opacity_changed(self, value):
        """滑块值改变时的回调函数，应用新的遮罩透明度。"""
        self._apply_fill_opacity_to_all(value)

    def on_new_brightness_contrast(self, qimage):
        self.canvas.load_pixmap(QtGui.QPixmap.fromImage(qimage), clear_shapes=False)

    def brightness_contrast(self, _):
        dialog = BrightnessContrastDialog(
            utils.img_data_to_pil(self.image_data),
            self.on_new_brightness_contrast,
            parent=self,
        )
        brightness, contrast = self.brightness_contrast_values.get(
            self.filename, (None, None)
        )
        if brightness is not None:
            dialog.slider_brightness.setValue(brightness)
        if contrast is not None:
            dialog.slider_contrast.setValue(contrast)
        dialog.exec_()

        brightness = dialog.slider_brightness.value()
        contrast = dialog.slider_contrast.value()
        self.brightness_contrast_values[self.filename] = (brightness, contrast)

    def toggle_polygons(self, value):
        for item in self.label_list:
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def get_next_files(self, filename, num_files):
        """Get the next files in the list."""
        if not self.image_list:
            return []
        filenames = []
        current_index = 0
        if filename is not None:
            try:
                current_index = self.image_list.index(filename)
            except ValueError:
                return []
            filenames.append(filename)
        for _ in range(num_files):
            if current_index + 1 < len(self.image_list):
                filenames.append(self.image_list[current_index + 1])
                current_index += 1
            else:
                filenames.append(self.image_list[-1])
                break
        return filenames

    def inform_next_files(self, filename):
        """Inform the next files to be annotated.
        This list can be used by the user to preload the next files
        or running a background process to process them
        """
        next_files = self.get_next_files(filename, 5)
        if next_files:
            self.next_files_changed.emit(next_files)

    def load_file(self, filename=None):  # noqa: C901
        """Load the specified file, or the last opened file if None."""

        prev_size = self.prev_image_size

        # For auto labeling, clear the previous marks
        # and inform the next files to be annotated
        self.clear_auto_labeling_marks()
        self.inform_next_files(filename)

        # Changing file_list_widget loads file
        if filename in self.image_list and (
            self.file_list_widget.currentRow() != self.image_list.index(filename)
        ):
            self.file_list_widget.setCurrentRow(self.image_list.index(filename))
            self.file_list_widget.repaint()
            return False

        self.reset_state()
        self.canvas.setEnabled(False)
        if filename is None:
            filename = self.settings.value("filename", "")
        filename = str(filename)
        if not QtCore.QFile.exists(filename):
            self.error_message(
                self.tr("Error opening file"),
                self.tr("No such file: <b>%s</b>") % filename,
            )
            return False

        # assumes same name, but json extension
        self.status(str(self.tr("Loading %s...")) % osp.basename(str(filename)))
        label_file = osp.splitext(filename)[0] + ".json"
        if self.output_dir:
            label_file_without_path = osp.basename(label_file)
            label_file = osp.join(self.output_dir, label_file_without_path)
        if QtCore.QFile.exists(label_file) and LabelFile.is_label_file(label_file):
            try:
                self.label_file = LabelFile(label_file)
            except LabelFileError as e:
                self.error_message(
                    self.tr("Error opening file"),
                    self.tr(
                        "<p><b>%s</b></p><p>Make sure <i>%s</i> is a valid label file."
                    )
                    % (e, label_file),
                )
                self.status(self.tr("Error reading %s") % label_file)
                return False
            self.image_data = self.label_file.image_data
            self.image_path = osp.join(
                osp.dirname(label_file),
                self.label_file.image_path,
            )
            self.other_data["image_labels"] = self.label_file.image_labels
            self.shape_text_edit.textChanged.disconnect()
            self.shape_text_edit.setPlainText(self.other_data.get("image_text", ""))
            self.shape_text_edit.textChanged.connect(self.shape_text_changed)
        else:
            self.image_data = LabelFile.load_image_file(filename)
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
        if not same_size and self._config.get("keep_prev_loc", True):
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Different Image Size"),
                self.tr("Cannot keep previous location because image sizes differ."),
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
        is_initial_load = not self.zoom_values
        prev_filename = self.recent_files[0] if self.recent_files else None
        if self.filename in self.zoom_values:
            self.zoom_mode = self.zoom_values[self.filename][0]
            self.set_zoom(self.zoom_values[self.filename][1])
        elif (
            is_initial_load
            or not self._config["keep_prev_scale"]
            or not same_size
            or not prev_filename
            or prev_filename not in self.zoom_values
        ):
            self.adjust_scale(initial=True)
        else:
            self.zoom_mode = self.zoom_values[prev_filename][0]
            self.set_zoom(self.zoom_values[prev_filename][1])
        # set scroll values
        for orientation in self.scroll_values:
            if self.filename in self.scroll_values[orientation]:
                self.set_scroll(
                    orientation, self.scroll_values[orientation][self.filename]
                )
            elif (
                self._config.get("keep_prev_loc", True)
                and same_size
                and prev_filename in self.scroll_values[orientation]
            ):
                self.set_scroll(
                    orientation, self.scroll_values[orientation][prev_filename]
                )
        # set brightness contrast values
        dialog = BrightnessContrastDialog(
            utils.img_data_to_pil(self.image_data),
            self.on_new_brightness_contrast,
            parent=self,
        )
        brightness, contrast = self.brightness_contrast_values.get(
            self.filename, (None, None)
        )
        if self._config["keep_prev_brightness"] and self.recent_files:
            brightness, _ = self.brightness_contrast_values.get(
                self.recent_files[0], (None, None)
            )
        if self._config["keep_prev_contrast"] and self.recent_files:
            _, contrast = self.brightness_contrast_values.get(
                self.recent_files[0], (None, None)
            )
        if brightness is not None:
            dialog.slider_brightness.setValue(brightness)
        if contrast is not None:
            dialog.slider_contrast.setValue(contrast)
        self.brightness_contrast_values[self.filename] = (brightness, contrast)
        if brightness is not None or contrast is not None:
            dialog.on_new_value(None)
        self.paint_canvas()
        self.add_recent_file(self.filename)
        self.toggle_actions(True)
        self.canvas.setFocus()
        self.status(str(self.tr("Loaded %s")) % osp.basename(str(filename)))

        # Save dock state after loading file (to capture any UI adjustments)
        QtCore.QTimer.singleShot(100, self.save_dock_state)

        self.prev_image_size = new_size

        return True

    # QT Overload
    def resizeEvent(self, _):
        if (
            self.canvas
            and not self.image.isNull()
            and self.zoom_mode != self.MANUAL_ZOOM
        ):
            self.adjust_scale()

        # Save dock state after resize (after a short delay to let layout settle)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.stop()
        else:
            self._resize_timer = QtCore.QTimer()
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self.save_dock_state)

        self._resize_timer.start(100)

        # Update toolbar scaling to match new window size
        self.update_toolbar_scale()

    def paint_canvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoom_widget.value()
        self.canvas.adjustSize()
        self.canvas.update()

    def adjust_scale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoom_mode]()
        value = int(100 * value)
        self.zoom_widget.setValue(value)
        self.zoom_values[self.filename] = (self.zoom_mode, value)

    def scale_fit_window(self):
        """Figure out the size of the pixmap to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.central_widget().width() - e
        h1 = self.central_widget().height() - e
        wh_ratio1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        wh_ratio2 = w2 / h2
        return w1 / w2 if wh_ratio2 >= wh_ratio1 else h1 / h2

    def scale_fit_width(self):
        # The epsilon does not seem to work too well here.
        w = self.central_widget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def enable_save_image_with_data(self, enabled):
        self._config["store_data"] = enabled
        self.actions.save_with_image_data.setChecked(enabled)

    # QT Overload
    def closeEvent(self, event):
        if not self.may_continue():
            event.ignore()
        self.settings.setValue("filename", self.filename if self.filename else "")
        self.settings.setValue("window/size", self.size())
        self.settings.setValue("window/position", self.pos())
        self.settings.setValue("window/state", self.parent.parent.saveState())

        # Save dock layout to config (final save on exit)
        self.save_dock_state(force=True)

        self.settings.setValue("recent_files", self.recent_files)
        # ask the use for where to save the labels
        # self.settings.setValue('window/geometry', self.saveGeometry())

    # QT Overload
    def dragEnterEvent(self, event):
        extensions = [
            f".{fmt.data().decode().lower()}"
            for fmt in QtGui.QImageReader.supportedImageFormats()
        ]
        if event.mimeData().hasUrls():
            items = [i.toLocalFile() for i in event.mimeData().urls()]
            if any(i.lower().endswith(tuple(extensions)) for i in items):
                event.accept()
        else:
            event.ignore()

    # QT Overload
    def dropEvent(self, event):
        if not self.may_continue():
            event.ignore()
            return
        items = [i.toLocalFile() for i in event.mimeData().urls()]
        self.import_dropped_image_files(items)

    def load_recent(self, filename):
        if self.may_continue():
            self.load_file(filename)

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

    def open_prev_image(self, _value=False):
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
                    self.filename = filename
                    self._update_canvas_image(filename)
                else:
                    self.load_file(filename)

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
                self._update_canvas_image(self.filename)
            else:
                self.load_file(self.filename)

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
            if filename:
                self.load_file(filename)

    def change_output_dir_dialog(self, _value=False):
        default_output_dir = self.output_dir
        if default_output_dir is None and self.filename:
            default_output_dir = osp.dirname(self.filename)
        if default_output_dir is None:
            default_output_dir = self.current_path()

        output_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            self.tr("%s - Save/Load Annotations in Directory") % __appname__,
            default_output_dir,
            QtWidgets.QFileDialog.ShowDirsOnly
            | QtWidgets.QFileDialog.DontResolveSymlinks,
        )
        output_dir = str(output_dir)

        if not output_dir:
            return

        self.output_dir = output_dir

        self.statusBar().showMessage(
            self.tr("%s . Annotations will be saved/loaded in %s")
            % ("Change Annotations Dir", self.output_dir)
        )
        self.statusBar().show()

        current_filename = self.filename
        self.import_image_folder(self.last_open_dir, load=False)

        if current_filename in self.image_list:
            # retain currently selected file
            self.file_list_widget.setCurrentRow(self.image_list.index(current_filename))
            self.file_list_widget.repaint()

    def save_file(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        if self.label_file:
            # DL20180323 - overwrite when in directory
            self._save_file(self.label_file.filename)
        elif self.output_file:
            self._save_file(self.output_file)
            self.close()
        else:
            self._save_file(self.save_file_dialog())

    def save_file_as(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._save_file(self.save_file_dialog())

    def save_file_dialog(self):
        caption = self.tr("%s - Choose File") % __appname__
        filters = self.tr("Label files (*%s)") % LabelFile.suffix
        if self.output_dir:
            file_dialog = QtWidgets.QFileDialog(self, caption, self.output_dir, filters)
        else:
            file_dialog = QtWidgets.QFileDialog(
                self, caption, self.current_path(), filters
            )
        file_dialog.setDefaultSuffix(LabelFile.suffix[1:])
        file_dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        file_dialog.setOption(QtWidgets.QFileDialog.DontConfirmOverwrite, False)
        file_dialog.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, False)
        basename = osp.basename(osp.splitext(self.filename)[0])
        if self.output_dir:
            default_labelfile_name = osp.join(
                self.output_dir, basename + LabelFile.suffix
            )
        else:
            default_labelfile_name = osp.join(
                self.current_path(), basename + LabelFile.suffix
            )
        filename = file_dialog.getSaveFileName(
            self,
            self.tr("Choose File"),
            default_labelfile_name,
            self.tr("Label files (*%s)") % LabelFile.suffix,
        )
        if isinstance(filename, tuple):
            filename, _ = filename
        return filename

    def _save_file(self, filename):
        if filename and self.save_labels(filename):
            self.add_recent_file(filename)
            self.set_clean()

    def close_file(self, _value=False):
        if not self.may_continue():
            return
        self.reset_state()
        self.set_clean()
        self.toggle_actions(False)
        self.canvas.setEnabled(False)
        self.actions.save_as.setEnabled(False)

    def get_label_file(self):
        if self.filename.lower().endswith(".json"):
            label_file = self.filename
        else:
            label_file = osp.splitext(self.filename)[0] + ".json"

        return label_file

    def delete_file(self):
        mb = QtWidgets.QMessageBox
        msg = self.tr(
            "You are about to permanently delete this label file, proceed anyway?"
        )
        answer = mb.warning(self, self.tr("Attention"), msg, mb.Yes | mb.No)
        if answer != mb.Yes:
            return

        label_file = self.get_label_file()
        if osp.exists(label_file):
            os.remove(label_file)
            logger.info("Label file is removed: %s", label_file)

            item = self.file_list_widget.currentItem()
            item.setCheckState(Qt.Unchecked)

            self.reset_state()

    # Message Dialogs. #
    def has_labels(self):
        if self.no_shape():
            self.error_message(
                "No objects labeled",
                "You must label at least one object to save the file.",
            )
            return False
        return True

    def has_label_file(self):
        if self.filename is None:
            return False

        label_file = self.get_label_file()
        return osp.exists(label_file)

    def may_continue(self):
        if not self.dirty:
            return True
        mb = QtWidgets.QMessageBox
        msg = self.tr(f'Save annotations to "{self.filename!r}" before closing?')
        answer = mb.question(
            self,
            self.tr("Save annotations?"),
            msg,
            mb.Save | mb.Discard | mb.Cancel,
            mb.Save,
        )
        if answer == mb.Discard:
            return True
        if answer == mb.Save:
            self.save_file()
            return True
        # answer == mb.Cancel
        return False

    def error_message(self, title, message):
        return QtWidgets.QMessageBox.critical(
            self, title, f"<p><b>{title}</b></p>{message}"
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
                self.scroll_values[orientation][dst] = self.scroll_values[orientation][
                    src
                ]

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

    def _load_pplxpl_overlay(self, folder):
        """Return a QPixmap stacking all images in a folder."""
        exts = [
            f".{fmt.data().decode().lower()}"
            for fmt in QtGui.QImageReader.supportedImageFormats()
        ]
        files = [
            osp.join(folder, f)
            for f in os.listdir(folder)
            if osp.isfile(osp.join(folder, f)) and f.lower().endswith(tuple(exts))
        ]
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
                    self.tr(
                        "Images in folder have different sizes. Using the first image only."
                    ),
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
               not in [
                   AutoLabelingMode.OBJECT,
                   AutoLabelingMode.ADD,
                   AutoLabelingMode.REMOVE,
               ]
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
            else:
                img_data = None
                reader = QtGui.QImageReader(img)
                image = QtGui.QImage()
                if reader.canRead():
                    image = QtGui.QImage(img)

            image_height = image.height() if not image.isNull() else None
            image_width = image.width() if not image.isNull() else None

            label_file = LabelFile()
            label_file.image_labels = self.other_data.get("image_labels", [])
            label_file.save(
                filename=label_path,
                shapes=shapes,
                image_path=osp.relpath(img, osp.dirname(label_path)),
                image_data=img_data,
                image_height=image_height,
                image_width=image_width,
                other_data=self.other_data,
                flags=flags,
            )

    def _load_pplxpl_overlay(self, files):
        """Return stacked overlay image from given files.

        Parameters
        ----------
        files : list[str]
            Image paths to load and stack.

        Returns
        -------
        QtGui.QImage | None
            The overlay image or ``None`` if no valid image could be built.
        """

        images = []
        for f in files:
            img = QtGui.QImage(f)
            if not img.isNull():
                images.append(img)

        if not images:
            return None

        w = images[0].width()
        h = images[0].height()
        if not all(img.width() == w and img.height() == h for img in images):
            return None

        arrs = [qt_img_to_rgb_cv_img(img) for img in images]
        stacked = np.stack(arrs, axis=0)
        overlay_arr = stacked.mean(axis=0).astype(np.uint8)
        return cv_img_to_qt_img(overlay_arr)

    def remove_selected_point(self):
        self.canvas.remove_selected_point()
        self.canvas.update()
        if self.canvas.h_hape is not None and not self.canvas.h_hape.points:
            self.canvas.delete_shape(self.canvas.h_hape)
            self.remove_labels([self.canvas.h_hape])
            self.set_dirty()
            if self.no_shape():
                for act in self.actions.on_shapes_present:
                    act.setEnabled(False)

    def delete_selected_shape(self):
        yes, no = QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No
        msg = self.tr(
            "You are about to permanently delete {} polygons, proceed anyway?"
        ).format(len(self.canvas.selected_shapes))
        if yes == QtWidgets.QMessageBox.warning(
            self, self.tr("Attention"), msg, yes | no, yes
        ):
            self.remove_labels(self.canvas.delete_selected())
            self.set_dirty()
            if self.no_shape():
                for act in self.actions.on_shapes_present:
                    act.setEnabled(False)

    def copy_shape(self):
        self.canvas.end_move(copy=True)
        for shape in self.canvas.selected_shapes:
            self.add_label(shape)
        self.label_list.clearSelection()
        self.set_dirty()

    def move_shape(self):
        self.canvas.end_move(copy=False)
        self.set_dirty()

    def open_folder_dialog(self, _value=False, dirpath=None):
        if not self.may_continue():
            return

        default_open_dir_path = dirpath if dirpath else "."
        if self.last_open_dir and osp.exists(self.last_open_dir):
            default_open_dir_path = self.last_open_dir
        else:
            default_open_dir_path = osp.dirname(self.filename) if self.filename else "."

        target_dir_path = str(
            QtWidgets.QFileDialog.getExistingDirectory(
                self,
                self.tr("%s - Open Directory") % __appname__,
                default_open_dir_path,
                QtWidgets.QFileDialog.ShowDirsOnly
                | QtWidgets.QFileDialog.DontResolveSymlinks,
            )
        )
        self.import_image_folder(target_dir_path)

    @property
    def image_list(self):
        lst = []
        for i in range(self.file_list_widget.count()):
            item = self.file_list_widget.item(i)
            lst.append(item.text())
        return lst

    def import_dropped_image_files(self, image_files):
        extensions = [
            f".{fmt.data().decode().lower()}"
            for fmt in QtGui.QImageReader.supportedImageFormats()
        ]

        self.filename = None
        for file in image_files:
            if file in self.image_list or not file.lower().endswith(tuple(extensions)):
                continue
            label_file = osp.splitext(file)[0] + ".json"
            if self.output_dir:
                label_file_without_path = osp.basename(label_file)
                label_file = osp.join(self.output_dir, label_file_without_path)
            item = QtWidgets.QListWidgetItem(file)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if QtCore.QFile.exists(label_file) and LabelFile.is_label_file(label_file):
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self.file_list_widget.addItem(item)

        if len(self.image_list) > 1:
            self.actions.open_next_image.setEnabled(True)
            self.actions.open_prev_image.setEnabled(True)

        self.open_next_image()

    def import_image_folder(self, dirpath, pattern=None, load=True):
        self.actions.open_next_image.setEnabled(True)
        self.actions.open_prev_image.setEnabled(True)

        if not self.may_continue() or not dirpath:
            return

        self.last_open_dir = dirpath
        self.filename = None
        self.file_list_widget.clear()
        for filename in self.scan_all_images(dirpath):
            if pattern and pattern not in filename:
                continue
            label_file = osp.splitext(filename)[0] + ".json"
            if self.output_dir:
                label_file_without_path = osp.basename(label_file)
                label_file = osp.join(self.output_dir, label_file_without_path)
            item = QtWidgets.QListWidgetItem(filename)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if QtCore.QFile.exists(label_file) and LabelFile.is_label_file(label_file):
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self.file_list_widget.addItem(item)
        self.open_next_image(load=load)

    def scan_all_images(self, folder_path):
        extensions = [
            f".{fmt.data().decode().lower()}"
            for fmt in QtGui.QImageReader.supportedImageFormats()
            if fmt.data().decode().lower() != "svg"
        ]

        images = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(tuple(extensions)):
                    relative_path = osp.join(root, file)
                    images.append(relative_path)
        images = natsort.os_sorted(images)
        return images

    def toggle_auto_labeling_widget(self):
        """Toggle auto labeling widget visibility."""
        if self.auto_labeling_widget.isVisible():
            self.auto_labeling_widget.hide()
        else:
            self.auto_labeling_widget.show()

    @pyqtSlot()
    def new_shapes_from_auto_labeling(self, auto_labeling_result):
        """Apply auto labeling results to the current image."""
        if not self.image or not self.image_path:
            return
        # Clear existing shapes
        if auto_labeling_result.replace:
            self.load_shapes([], replace=True)
            self.label_list.clear()
            self.load_shapes(auto_labeling_result.shapes, replace=True)
        else:  # Just update existing shapes
            # Remove shapes with label AutoLabelingMode.OBJECT
            for shape in self.canvas.shapes:
                if shape.primary_label == AutoLabelingMode.OBJECT:
                    item = self.label_list.find_item_by_shape(shape)
                    self.label_list.remove_item(item)
            self.load_shapes(auto_labeling_result.shapes, replace=False)

        self.set_dirty()

    def clear_auto_labeling_marks(self):
        """Clear auto labeling marks from the current image."""
        # Clean up label list
        for shape in self.canvas.shapes:
            if shape.primary_label in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ]:
                try:
                    item = self.label_list.find_item_by_shape(shape)
                    self.label_list.remove_item(item)
                except ValueError:
                    pass

        # Clean up unique label list
        for shape_label in [
            AutoLabelingMode.OBJECT,
            AutoLabelingMode.ADD,
            AutoLabelingMode.REMOVE,
        ]:
            for item in self.unique_label_list.find_items_by_label(shape_label):
                self.unique_label_list.takeItem(self.unique_label_list.row(item))

        # Remove shapes from the canvas
        self.canvas.shapes = [
            shape
            for shape in self.canvas.shapes
            if shape.primary_label
            not in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ]
        ]
        self.canvas.update()

    def find_last_label(self):
        """
        Find the last label in the label list.
        Exclude labels for auto labeling.
        """

        # Get from dialog history
        last_label = self.label_dialog.get_last_label()
        if last_label:
            return last_label

        # Get selected label from the label list
        items = self.label_list.selected_items()
        if items:
            shape = items[0].data(Qt.UserRole)
            return shape.primary_label

        # Get the last label from the label list
        for item in reversed(self.label_list):
            shape = item.data(Qt.UserRole)
            if shape.primary_label not in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ]:
                return shape.primary_label

        # No label is found
        return ""

    def finish_auto_labeling_object(self):
        """Finish auto labeling object."""
        has_object = False
        for shape in self.canvas.shapes:
            if shape.primary_label == AutoLabelingMode.OBJECT:
                has_object = True
                break

        # If there is no object, do nothing
        if not has_object:
            return

        # Ask a label for the object
        text, flags, group_id = "", {}, None
        last_label = self.find_last_label()
        if self._config["auto_use_last_label"] and last_label:
            text = last_label
        else:
            previous_text = self.label_dialog.edit.text()
            text, flags, group_id = self.label_dialog.pop_up(
                text=self.find_last_label(),
                flags={},
                group_id=None,
            )
            if not text:
                self.label_dialog.edit.setText(previous_text)
                return

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
                        unique_label_item = (
                            self.unique_label_list.create_item_from_label(lb)
                        )
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

        if updated_shapes:
            self.set_dirty()

    def set_text_editing(self, enable):
        """Set text editing."""
        if enable:
            # Enable text editing and set shape text from selected shape
            if len(self.canvas.selected_shapes) == 1:
                self.shape_text_label.setText(self.tr("Object Text"))
                self.shape_text_edit.textChanged.disconnect()
                self.shape_text_edit.setPlainText(self.canvas.selected_shapes[0].text)
                self.shape_text_edit.textChanged.connect(self.shape_text_changed)
            else:
                self.shape_text_label.setText(self.tr("Image Text"))
                self.shape_text_edit.textChanged.disconnect()
                self.shape_text_edit.setPlainText(self.other_data.get("image_text", ""))
                self.shape_text_edit.textChanged.connect(self.shape_text_changed)
            self.shape_text_edit.setDisabled(False)
        else:
            self.shape_text_edit.setDisabled(True)
            self.shape_text_label.setText(
                self.tr("Switch to Edit mode for text editing")
            )
            self.shape_text_edit.textChanged.disconnect()
            self.shape_text_edit.setPlainText("")
            self.shape_text_edit.textChanged.connect(self.shape_text_changed)

    def export_annotations(self):
        """Open export dialog to export annotations to different formats."""
        # Get the current directory
        current_dir = None
        if self.filename:
            current_dir = osp.dirname(self.filename)
        elif self.output_dir:
            current_dir = self.output_dir

        # Create and show export dialog
        dialog = ExportDialog(self, current_dir)
        dialog.exec_()

    def toggle_tools(self):
        """Toggle the tools panel visibility."""
        if hasattr(self.parent, "toggle_tools_panel"):
            self.parent.toggle_tools_panel()

    def reset_dock_layout(self):
        """Reset dock widget layout to default positions."""
        # Close all docks first
        self.shape_text_dock.close()
        self.flag_dock.close()
        self.label_dock.close()
        self.shape_dock.close()
        self.file_dock.close()
        self.tools_dock.close()

        # Re-add them in the desired order/position
        self.main_window.addDockWidget(Qt.LeftDockWidgetArea, self.tools_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_text_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)

        # Show all docks
        self.tools_dock.show()
        self.file_dock.show()
        self.shape_dock.show()
        self.label_dock.show()
        self.flag_dock.hide()
        self.shape_text_dock.show()

        # Make sure tools dock is visible
        self.tools_dock.raise_()

        # Connect dock signals to save state when changed and update orientation
        self.tools_dock.dockLocationChanged.connect(self.on_tools_dock_location_changed)
        self.shape_text_dock.dockLocationChanged.connect(self.save_dock_state)
        self.flag_dock.dockLocationChanged.connect(self.save_dock_state)
        self.label_dock.dockLocationChanged.connect(self.save_dock_state)
        self.shape_dock.dockLocationChanged.connect(self.save_dock_state)
        self.file_dock.dockLocationChanged.connect(self.save_dock_state)

        # Also connect visibility changes
        self.tools_dock.visibilityChanged.connect(self.save_dock_state)
        self.shape_text_dock.visibilityChanged.connect(self.save_dock_state)
        self.flag_dock.visibilityChanged.connect(self.save_dock_state)
        self.label_dock.visibilityChanged.connect(self.save_dock_state)
        self.shape_dock.visibilityChanged.connect(self.save_dock_state)
        self.file_dock.visibilityChanged.connect(self.save_dock_state)

        # Apply a workaround to ensure proper sizes
        self.main_window.resizeDocks(
            [
                self.tools_dock,
                self.shape_text_dock,
                self.flag_dock,
                self.label_dock,
                self.shape_dock,
                self.file_dock,
            ],
            [40, 300, 300, 300, 300, 300],
            Qt.Horizontal,
        )

        # Reset any saved dock state in config
        try:
            config = get_config()
            if (
                "ui" in config
                and isinstance(config["ui"], dict)
                and "dock_state" in config["ui"]
            ):
                del config["ui"]["dock_state"]
                save_config(config)
                logger.info("Previous dock state cleared from config")
        except Exception as e:
            logger.error(f"Error clearing dock state from config: {e}")

        # Wait a short time for layout to stabilize, then save new layout
        QtCore.QTimer.singleShot(100, self.save_dock_state)

        # Show a status message
        self.statusBar().showMessage(self.tr("Dock layout reset to default"), 5000)

    def set_theme(self, theme):
        """Set application theme"""
        # Update environment variable to override system theme detection
        if theme == "light":
            os.environ["DARK_MODE"] = "0"
        elif theme == "dark":
            os.environ["DARK_MODE"] = "1"
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

            base64_state = byte_state.toBase64().data().decode()
            if not base64_state:
                logger.warning("Failed to encode dock state to Base64")
                return

            # Store in config and save
            config["ui"]["dock_state"] = base64_state
            save_config(config)
            self._last_dock_save_time = current_time
            logger.debug("Dock state saved successfully")

        except Exception as e:
            logger.error(f"Error saving dock state: {e}")

    def load_dock_state(self):
        """Load dock state from config with better error handling."""
        config = get_config()

        # Check if we have a valid dock state in config
        has_dock_state = (
            "ui" in config
            and isinstance(config["ui"], dict)
            and "dock_state" in config["ui"]
            and config["ui"]["dock_state"]
        )

        if not has_dock_state:
            logger.info("No saved dock state found, using default layout")
            return

        logger.info("Attempting to load dock state...")

        try:
            # Convert stored Base64 string back to QByteArray
            base64_str = config["ui"]["dock_state"]
            logger.debug(f"Encoded dock state: {base64_str[:30]}...")

            try:
                dock_state = QtCore.QByteArray.fromBase64(base64_str.encode())
                logger.debug(f"Decoded QByteArray size: {len(dock_state)}")
            except Exception as decode_error:
                logger.error(f"Failed to decode Base64 string: {decode_error}")
                raise decode_error

            # Make sure all dock widgets exist before restoring state
            all_docks_exist = all(
                [
                    hasattr(self, "tools_dock"),
                    hasattr(self, "shape_text_dock"),
                    hasattr(self, "flag_dock"),
                    hasattr(self, "label_dock"),
                    hasattr(self, "shape_dock"),
                    hasattr(self, "file_dock"),
                ]
            )

            if not all_docks_exist:
                logger.error(
                    "Cannot restore dock state - not all dock widgets are initialized"
                )
                return

            # Force all docks to be visible first
            self.tools_dock.setVisible(True)
            self.shape_text_dock.setVisible(True)
            self.flag_dock.setVisible(True)
            self.label_dock.setVisible(True)
            self.shape_dock.setVisible(True)
            self.file_dock.setVisible(True)

            # Try to restore state
            if self.main_window.restoreState(dock_state):
                logger.info("✓ Dock state loaded successfully")
                # Apply a workaround for proper dock resizing
                self.main_window.resizeDocks(
                    [
                        self.tools_dock,
                        self.shape_text_dock,
                        self.flag_dock,
                        self.label_dock,
                        self.shape_dock,
                        self.file_dock,
                    ],
                    [40, 300, 300, 300, 300, 300],
                    Qt.Horizontal,
                )
            else:
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
            height = int(self._icon_size + 30)
            self.tools_dock.setMinimumHeight(height)
            self.tools_dock.setMaximumHeight(height)
            # Reset width constraints
            self.tools_dock.setMinimumWidth(0)
            self.tools_dock.setMaximumWidth(16777215)  # Qt's QWIDGETSIZE_MAX
        else:  # Otherwise (left, right, or floating), use vertical layout
            self.tools.setOrientation(Qt.Vertical)
            self.tools_dock.setMinimumWidth(self._dock_width)
            self.tools_dock.setMaximumWidth(self._dock_width)
            # Reset height constraints
            self.tools_dock.setMinimumHeight(0)
            self.tools_dock.setMaximumHeight(16777215)  # Qt's QWIDGETSIZE_MAX

            # If floating, provide more reasonable dimensions
            if not area:  # Qt returns 0 for floating docks
                self.tools_dock.setMinimumWidth(0)
                self.tools_dock.setMaximumWidth(16777215)
                self.tools_dock.resize(self._dock_width, 300)
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
        self.on_tools_dock_location_changed()