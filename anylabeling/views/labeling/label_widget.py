import copy
import functools
import html
import math
import os
import os.path as osp
import re
import webbrowser
import time
import weakref
import yaml
import sys
from importlib import import_module
from pathlib import Path

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
    QScrollArea,
    QCheckBox,
)

from Extract_Kong.extract_kong import PoreMaskExtractorDialog
from anylabeling.services.auto_labeling.types import AutoLabelingMode
from anylabeling.utils.resource_manager import ResourceManager, SafeResource, safe_operation


from anylabeling.app_info import __appname__
from anylabeling.config import get_config, save_config
from anylabeling.views.labeling import utils
from anylabeling.views.labeling.utils.opencv import (
    cv_img_to_qt_img,
    qt_img_to_rgb_cv_img,
)

import numpy as np
from anylabeling.app_info import __appname__
from anylabeling.config import get_config, save_config
from anylabeling.views.labeling import utils
from anylabeling.views.labeling.label_file import (
    LabelFile,
    LabelFileError,
    FOLDER_SYNC_SENTINEL,
)
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
from .widgets.crop_dialog import CropDialog
from .widgets.export_dialog import ExportDialog
from anylabeling.styles import AppTheme

LABEL_COLORMAP = imgviz.label_colormap()

# Green for the first label
LABEL_COLORMAP[2] = LABEL_COLORMAP[1]
LABEL_COLORMAP[1] = [0, 180, 33]


def _candidate_sam2_base_dirs():
    here = Path(__file__).resolve()
    candidates = {Path.cwd()}
    candidates.update(here.parents)
    try:
        candidates.add(Path.cwd().parent)
    except Exception:
        pass
    # Include potential nested "sam2" directories alongside existing bases
    for base in list(candidates):
        if not isinstance(base, Path):
            continue
        candidates.add(base / "sam2")
    unique = []
    for base in candidates:
        if not isinstance(base, Path):
            continue
        try:
            resolved = base.resolve()
        except Exception:
            continue
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _install_sam2_dependency_stubs():
    import types

    # Provide minimal hydra stub so importing sam2 doesn't fail when hydra-core
    # is absent. Training utilities won't work, but inference paths only need
    # initialize_config_module.
    hydra_mod = sys.modules.setdefault("hydra", types.ModuleType("hydra"))

    def _noop(*_args, **_kwargs):
        return None

    hydra_mod.initialize_config_module = _noop
    hydra_mod.compose = _noop
    hydra_mod.initialize = _noop

    hydra_core_mod = sys.modules.setdefault("hydra.core", types.ModuleType("hydra.core"))
    global_hydra_mod = types.ModuleType("hydra.core.global_hydra")

    class _DummyGlobalHydra:
        _instance = None

        @classmethod
        def instance(cls):
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        def is_initialized(self):
            return True

        def clear(self):
            type(self)._instance = None

    global_hydra_mod.GlobalHydra = _DummyGlobalHydra
    sys.modules["hydra.core.global_hydra"] = global_hydra_mod
    hydra_core_mod.global_hydra = global_hydra_mod
    hydra_mod.core = hydra_core_mod

    # Provide minimal iopath implementation if missing. SAM2 only needs
    # g_pathmgr.open / get_local_path for checkpoint loading.
    if "iopath.common.file_io" not in sys.modules:
        iopath_mod = sys.modules.setdefault("iopath", types.ModuleType("iopath"))
        common_mod = types.ModuleType("iopath.common")
        file_io_mod = types.ModuleType("iopath.common.file_io")

        class _SimplePathManager:
            def open(self, path, mode="r", *args, **kwargs):
                return open(path, mode, *args, **kwargs)

            def get_local_path(self, path, *args, **kwargs):
                return path

        file_io_mod.g_pathmgr = _SimplePathManager()

        sys.modules["iopath.common"] = common_mod
        sys.modules["iopath.common.file_io"] = file_io_mod
        common_mod.file_io = file_io_mod
        setattr(iopath_mod, "common", common_mod)


def _locate_sam2_package():
    for candidate in _candidate_sam2_base_dirs():
        if not isinstance(candidate, Path):
            continue
        try:
            candidate = candidate.resolve()
        except Exception:
            continue
        if candidate.name == "sam2" and (candidate / "__init__.py").is_file():
            return candidate
        package_dir = candidate / "sam2"
        if package_dir.is_dir() and (package_dir / "__init__.py").is_file():
            return package_dir.resolve()
    return None


def _ensure_sam2_package():
    try:
        import sam2  # type: ignore
        return sam2
    except ModuleNotFoundError:
        package_dir = _locate_sam2_package()
        if package_dir is None:
            return None
        parent_dir = package_dir.parent
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        _install_sam2_dependency_stubs()
        try:
            import sam2  # type: ignore
            return sam2
        except Exception as inner_exc:
            logger.debug("Failed to import sam2 package after installing stubs: %s", inner_exc)
            return None
    return None


def _coerce_config_value(value):
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        try:
            if text.startswith(("0x", "-0x", "+0x")):
                return int(text, 16)
            if text.startswith(("0b", "-0b", "+0b")):
                return int(text, 2)
            if text.startswith(("0o", "-0o", "+0o")):
                return int(text, 8)
            if text.isdigit() or (text.startswith(("+", "-")) and text[1:].isdigit()):
                return int(text)
            return float(text)
        except ValueError:
            return value
    return value


def _instantiate_from_config(cfg):
    if isinstance(cfg, dict):
        data = dict(cfg)
        target = data.pop("_target_", None)
        kwargs = {k: _instantiate_from_config(v) for k, v in data.items()}
        if target:
            module_name, _, attr = target.rpartition(".")
            if not module_name:
                raise ImportError(f"Invalid target specification: {target}")
            module = import_module(module_name)
            constructor = getattr(module, attr)
            coerced_kwargs = {k: _coerce_config_value(v) for k, v in kwargs.items()}
            return constructor(**coerced_kwargs)
        return {k: _coerce_config_value(v) for k, v in kwargs.items()}
    if isinstance(cfg, list):
        return [_instantiate_from_config(item) for item in cfg]
    return _coerce_config_value(cfg)


def _load_sam2_model(config_name, ckpt_path, device):
    import sam2  # type: ignore
    import torch

    config_root = Path(sam2.__path__[0]).joinpath("configs")
    config_path = config_root.joinpath(f"{config_name}.yaml")
    if not config_path.exists():
        raise FileNotFoundError(f"SAM-2 配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config_dict = yaml.safe_load(f) or {}

    if "model" not in config_dict:
        raise ValueError(f"无效的 SAM-2 配置文件: {config_path}")

    model_cfg = copy.deepcopy(config_dict["model"])
    extra_args = model_cfg.setdefault("sam_mask_decoder_extra_args", {})
    extra_args.setdefault("dynamic_multimask_via_stability", True)
    extra_args.setdefault("dynamic_multimask_stability_delta", 0.05)
    extra_args.setdefault("dynamic_multimask_stability_thresh", 0.98)

    model = _instantiate_from_config(model_cfg)

    checkpoint = torch.load(str(ckpt_path), map_location="cpu")
    if isinstance(checkpoint, dict) and "model" in checkpoint:
        checkpoint = checkpoint["model"]

    load_result = model.load_state_dict(checkpoint, strict=False)
    missing_keys = getattr(load_result, "missing_keys", [])
    unexpected_keys = getattr(load_result, "unexpected_keys", [])
    if missing_keys:
        logger.debug("[SAM2] Missing keys when loading weights: %s", missing_keys[:5])
    if unexpected_keys:
        logger.debug("[SAM2] Unexpected keys when loading weights: %s", unexpected_keys[:5])

    model = model.to(device)
    model.eval()
    return model

def load_label_set_from_yaml(label_set_name):
    """从配置文件中加载指定的标签集合"""
    try:
        # 获取配置文件路径
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'configs', 'anylabeling_config.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        label_sets = config.get('label_sets', {})
        return label_sets.get(label_set_name, [])
    except Exception as e:
        # print(f"Failed to load label set '{label_set_name}': {e}")
        return []

class LabelingWidget(LabelDialog):
    """The main widget for labeling images"""

    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = 0, 1, 2
    next_files_changed = QtCore.pyqtSignal(list)

    # Keep weak references to all active labeling widgets so that
    # global settings such as mask opacity can be propagated.
    _instances = weakref.WeakSet()
    
    # 资源管理器
    _resource_manager = None

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
        
        # 初始化资源管理器
        if LabelingWidget._resource_manager is None:
            LabelingWidget._resource_manager = ResourceManager()
            LabelingWidget._resource_manager.start_monitoring(
                check_interval=30.0,
                memory_threshold_mb=1500.0
            )
        if output is not None:
            logger.warning("argument output is deprecated, use output_file instead")
            if output_file is None:
                output_file = output

        self.filename = None
        self.image_path = None
        self.image_data = None
        self.label_file = None
        self.other_data = {}
        self._legacy_migrated_folders = set()

        # SAM-2 缓存
        self._sam2_predictor = None
        self._sam2_mask_gen = None
        self._sam2_variant = None
        self._sam2_mask_gen_relaxed = {}

        # see configs/anylabeling_config.yaml for valid configuration
        if config is None:
            config = get_config()
        self._config = config
        shape_cfg = self._config.setdefault("shape", {})
        if shape_cfg.get("line_width") in (None, 2):
            shape_cfg["line_width"] = 3
        if shape_cfg.get("fill_opacity") in (None, 150):
            shape_cfg["fill_opacity"] = 0
        self.sync_pplxpl = self._config.get("pplxpl_sync", False)

        # set default shape colors
        Shape.line_color = QtGui.QColor(*self._config["shape"]["line_color"])
        Shape.fill_color = QtGui.QColor(*self._config["shape"]["fill_color"])
        Shape.select_line_color = QtGui.QColor(
            *self._config["shape"]["select_line_color"]
        )
        # 抑制模式提示（用于圈选期间防止遮挡对话框）
        self._suppress_mode_hint = False
        Shape.select_fill_color = QtGui.QColor(
            *self._config["shape"]["select_fill_color"]
        )
        Shape.vertex_fill_color = QtGui.QColor(
            *self._config["shape"]["vertex_fill_color"]
        )
        Shape.hvertex_fill_color = QtGui.QColor(
            *self._config["shape"]["hvertex_fill_color"]
        )
        Shape.line_width = self._config["shape"].get("line_width", 3)
        Shape.fill_opacity = self._config["shape"].get("fill_opacity", Shape.fill_color.alpha())
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

        # Track current label set name
        # ✅ 先使用默认值，稍后在初始化完成后进行智能检测
        self.current_label_set_name = "默认标签"

        # Main widgets and related state.
        self.label_dialog = LabelDialog(
            parent=self,
            labels=self._config["labels"],
            sort_labels=self._config["sort_labels"],
            show_text_field=self._config["show_label_text_field"],
            completion=self._config["label_completion"],
            fit_to_content=self._config["fit_to_content"],
            flags=self._config["label_flags"],
            label_set_name=self.current_label_set_name,
        )

        self.label_list = LabelListWidget()
        self.last_open_dir = None
        # 加载/同步状态标志
        self._suppress_sync = False
        self._is_loading = False

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

        # 文本编辑器模式：记录上一次模式，用于在切换时提示
        self._last_text_edit_mode = None  # 'object' 或 'image'

        # Create right sidebar with shape text editor
        shape_text_widget = QtWidgets.QWidget()
        
        # 创建垂直分割器来分隔文本编辑区域和标签区域
        self.text_splitter = QtWidgets.QSplitter(Qt.Vertical)
        
        # 创建上方的文本编辑区域
        text_area_widget = QtWidgets.QWidget()
        text_area_layout = QVBoxLayout()
        text_area_layout.setContentsMargins(0, 0, 0, 0)
        text_area_layout.setSpacing(2)
        
        self.shape_text_label = QLabel("Object Text")
        self.shape_text_label.setStyleSheet(
            "QLabel {"
            "text-align: center;"
            "padding: 0px;"
            "font-size: 10px;"
            "margin-bottom: 2px;"
            "}"
        )
        self.shape_text_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.shape_text_label.setMaximumHeight(20)
        self.shape_text_label.setMinimumHeight(15)
        
        self.shape_text_edit = QPlainTextEdit()
        # 自定义中文右键菜单
        self.shape_text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.shape_text_edit.customContextMenuRequested.connect(self.show_text_edit_context_menu)
        self.shape_text_edit.setMinimumHeight(60)
        
        text_area_layout.addWidget(self.shape_text_label, 0, Qt.AlignCenter)
        text_area_layout.addWidget(self.shape_text_edit)
        text_area_widget.setLayout(text_area_layout)

        # ✅ 新增：标签勾选框区域（当选择岩浆岩或火山碎屑岩相关标签集合时）
        # 默认加载岩浆岩结构-构造，稍后会根据实际选择的标签集更新
        self.available_tags = load_label_set_from_yaml("岩浆岩结构-构造")
        self.tag_checkboxes = []
        self.label_options_widget = QtWidgets.QWidget()
        self.label_options_layout = QVBoxLayout()
        self.label_options_layout.setContentsMargins(2, 2, 2, 2)  # 减少内边距
        self.label_options_layout.setSpacing(1)  # 减少复选框间距
        
        # 创建标签勾选框
        for tag in self.available_tags:
            cb = QCheckBox(tag)
            cb.setStyleSheet("""
                QCheckBox {
                    font-size: 9px;
                    padding: 1px;
                    margin: 0px;
                }
                QCheckBox::indicator {
                    width: 12px;
                    height: 12px;
                }
            """)
            cb.stateChanged.connect(self.on_text_editor_tag_checkbox_changed)
            self.label_options_layout.addWidget(cb)
            self.tag_checkboxes.append(cb)
        
        self.label_options_widget.setLayout(self.label_options_layout)
        
        # 添加可拖拽调整大小的滚动区域
        self.tag_scroll_area = QScrollArea()
        self.tag_scroll_area.setWidgetResizable(True)
        self.tag_scroll_area.setWidget(self.label_options_widget)
        self.tag_scroll_area.setMinimumHeight(120)   # 增加最小高度，让标签区域更大
        # 移除最大高度限制，让标签区域可以完全填充剩余空间
        self.tag_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tag_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tag_scroll_area.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)  # 垂直方向可扩展
        
        # 设置滚动区域的样式，使其看起来可以拖动
        self.tag_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ccc;
                border-radius: 3px;
                background-color: #f9f9f9;
            }
            QScrollArea::handle {
                background-color: #ddd;
                border-radius: 2px;
            }
        """)
        
        # 将文本编辑区域和标签区域添加到分割器
        self.text_splitter.addWidget(text_area_widget)
        self.text_splitter.addWidget(self.tag_scroll_area)
        
        # 设置分割器的初始大小比例
        self.text_splitter.setSizes([120, 200])  # 文本区域120px，标签区域200px
        
        # 设置分割器的拉伸因子
        self.text_splitter.setStretchFactor(0, 0)  # 文本区域不随窗口拉伸
        self.text_splitter.setStretchFactor(1, 1)  # 标签区域随窗口拉伸
        
        # 设置分割器手柄的样式，让拖拽更明显（使用与背景一致的颜色，无红色）
        self.text_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #f9f9f9;
                border: 1px solid #e0e0e0;
                height: 1px;
            }
            QSplitter::handle:hover {
                background-color: #eaeaea;
            }
            QSplitter::handle:pressed {
                background-color: #e0e0e0;
            }
        """)
        
        # 创建主布局并添加分割器
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.text_splitter)
        
        # 根据当前标签集合决定是否显示标签区域
        should_show_tags = self.current_label_set_name in ["岩浆岩", "岩浆岩结构-构造", "火山碎屑岩", "砂岩铸体孔隙", "碳酸盐岩", "碳酸盐岩-构造"]
        self.tag_scroll_area.setVisible(should_show_tags)
        
        shape_text_widget.setLayout(main_layout)

        # Add shape text widget to dock
        self.shape_text_dock = QtWidgets.QDockWidget(
            self.tr("文本编辑器"), self.main_window
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
        # 完全禁用Flags dock widget
        # self.flag_dock = QtWidgets.QDockWidget(self.tr("Flags"), self.main_window)
        # self.flag_dock.setObjectName("Flags")
        # self.flag_dock.setFeatures(features)
        # self.flag_widget = QtWidgets.QListWidget()
        # if config["flags"]:
        #     self.load_flags(dict.fromkeys(config["flags"], False))
        # else:
        #     self.flag_dock.hide()
        # self.flag_dock.setWidget(self.flag_widget)
        # self.flag_widget.itemChanged.connect(self.set_dirty)
        # self.flag_dock.setStyleSheet(dock_title_style)
        # self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)

        self.label_list.item_selection_changed.connect(self.label_selection_changed)
        self.label_list.item_double_clicked.connect(self.edit_label)
        self.label_list.item_changed.connect(self.label_item_changed)
        self.label_list.item_dropped.connect(self.label_order_changed)
        self.shape_dock = QtWidgets.QDockWidget(self.tr("对象"), self.main_window)
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
        self.label_dock = QtWidgets.QDockWidget(self.tr("标签"), self.main_window)
        self.label_dock.setObjectName("Labels")
        self.label_dock.setFeatures(features)
        # 为“标签”面板添加搜索框
        self.label_search = QtWidgets.QLineEdit()
        self.label_search.setPlaceholderText(self.tr("搜索标签"))
        try:
            self.label_search.setClearButtonEnabled(True)
        except Exception:
            pass
        self.label_search.textChanged.connect(self.on_label_search_changed)
        label_container = QtWidgets.QWidget()
        label_container_layout = QtWidgets.QVBoxLayout()
        label_container_layout.setContentsMargins(0, 0, 0, 0)
        label_container_layout.setSpacing(2)
        label_container_layout.addWidget(self.label_search)
        label_container_layout.addWidget(self.unique_label_list)
        label_container.setLayout(label_container_layout)
        self.label_dock.setWidget(label_container)
        self.label_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)

        self.file_search = QtWidgets.QLineEdit()
        self.file_search.setPlaceholderText(self.tr("搜索文件名"))
        self.file_search.textChanged.connect(self.file_search_changed)
        self.file_list_widget = QtWidgets.QListWidget()
        self.file_list_widget.itemSelectionChanged.connect(self.file_selection_changed)
        file_list_layout = QtWidgets.QVBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.setSpacing(0)
        file_list_layout.addWidget(self.file_search)
        file_list_layout.addWidget(self.file_list_widget)
        self.file_dock = QtWidgets.QDockWidget(self.tr("文件"), self.main_window)
        self.file_dock.setObjectName("Files")
        self.file_dock.setFeatures(features)
        file_list_widget = QtWidgets.QWidget()
        file_list_widget.setLayout(file_list_layout)
        self.file_dock.setWidget(file_list_widget)
        self.file_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)

        # ✅ 创建统计信息dock widget
        self.statistics_widget = QtWidgets.QWidget()
        statistics_layout = QtWidgets.QVBoxLayout()
        statistics_layout.setContentsMargins(3, 3, 3, 3)  # 减少边距
        statistics_layout.setSpacing(2)  # 减少间距
        
        # 标题
        title_label = QtWidgets.QLabel(self.tr("标注统计"))
        title_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        statistics_layout.addWidget(title_label)
        
        # 统计信息在同一行显示
        stats_row_layout = QtWidgets.QHBoxLayout()
        stats_row_layout.setContentsMargins(0, 0, 0, 0)
        stats_row_layout.setSpacing(10)  # 设置两个标签之间的间距
        
        # 总数统计
        self.stats_total_label = QtWidgets.QLabel(self.tr("总标注数: 0"))
        stats_row_layout.addWidget(self.stats_total_label)
        
        # 标签种类统计
        self.stats_types_label = QtWidgets.QLabel(self.tr("标签种类: 0"))
        stats_row_layout.addWidget(self.stats_types_label)
        
        # 添加弹性空间，让标签左对齐
        stats_row_layout.addStretch()
        
        # 将水平布局添加到主布局
        statistics_layout.addLayout(stats_row_layout)
        
        # 详细标签列表
        detail_label = QtWidgets.QLabel(self.tr("标签详情:"))
        detail_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        statistics_layout.addWidget(detail_label)
        
        # # ✅ 添加使用提示
        # hint_label = QtWidgets.QLabel(self.tr("💡 点击选择标签，然后使用下方按钮进行操作"))
        # hint_label.setStyleSheet("color: #666; font-size: 10px; margin-bottom: 2px;")
        # hint_label.setWordWrap(True)
        # statistics_layout.addWidget(hint_label)
        
        self.stats_detail_list = QtWidgets.QListWidget()
        self.stats_detail_list.setMaximumHeight(120)  # 减少高度
        # ✅ 启用多选模式，支持Ctrl键选择多个标签
        self.stats_detail_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        # ✅ 启用右键菜单和双击事件
        self.stats_detail_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.stats_detail_list.customContextMenuRequested.connect(self.show_statistics_context_menu)
        self.stats_detail_list.itemDoubleClicked.connect(self.on_statistics_label_double_clicked)
        self.stats_detail_list.itemSelectionChanged.connect(self.on_statistics_selection_changed)
        self.stats_detail_list.setToolTip(
            self.tr("双击标签可选择所有该标签的对象\nCtrl+点击可选择多个标签进行批量操作\n右键可进行批量操作")
        )
        statistics_layout.addWidget(self.stats_detail_list)
        
        # 持久化记录"标签详情"中的用户选择，用于跨图片切换时恢复
        self._stats_persist_selected_labels = set()
        # 持久化记录"画布手动选择"的几何签名（跨图像几何匹配）
        self._canvas_persist_selected_sigs = []
        # 标记当前一次选择是否来源于"标签详情"操作（用于避免覆盖手动签名）
        self._selection_from_stats = False
        # 抑制统计面板自动选择（一次性）- 用于删除后避免重新选中同标签对象
        self._suppress_stats_autoselect_once = False
        
        # ✅ 添加批量操作按钮
        buttons_layout = QtWidgets.QGridLayout()
        buttons_layout.setContentsMargins(0, 2, 0, 2)  # 减少按钮区域的上下边距
        buttons_layout.setHorizontalSpacing(4)
        buttons_layout.setVerticalSpacing(4)

        self.stats_select_all_btn = QtWidgets.QPushButton(self.tr("选择全部"))
        self.stats_select_all_btn.setToolTip(self.tr("先在上面列表中点击选择标签（Ctrl+点击可多选），然后点此按钮选择该标签的所有对象"))
        self.stats_select_all_btn.clicked.connect(self.on_stats_select_all_clicked)
        buttons_layout.addWidget(self.stats_select_all_btn, 0, 0)

        self.stats_delete_btn = QtWidgets.QPushButton(self.tr("删除标签"))
        self.stats_delete_btn.setToolTip(self.tr("先在上面列表中点击选择标签（Ctrl+点击可多选），然后点此按钮删除该标签的所有对象"))
        self.stats_delete_btn.clicked.connect(self.on_stats_delete_clicked)
        buttons_layout.addWidget(self.stats_delete_btn, 0, 1)

        self.stats_relabel_btn = QtWidgets.QPushButton(self.tr("更改标签"))
        self.stats_relabel_btn.setToolTip(self.tr("先在上面列表中点击选择标签（Ctrl+点击可多选），然后点此按钮更改该标签的名称"))
        self.stats_relabel_btn.clicked.connect(self.on_stats_relabel_clicked)
        buttons_layout.addWidget(self.stats_relabel_btn, 0, 2)

        # 仅显示选中标签
        self.stats_show_only_btn = QtWidgets.QPushButton(self.tr("仅显示"))
        self.stats_show_only_btn.setToolTip(self.tr("只在画布上显示选中的标签（可多选），其他标签将被隐藏"))
        self.stats_show_only_btn.clicked.connect(self.on_stats_show_only_clicked)
        buttons_layout.addWidget(self.stats_show_only_btn, 1, 0)

        # 隐藏选中标签
        self.stats_hide_btn = QtWidgets.QPushButton(self.tr("隐藏"))
        self.stats_hide_btn.setToolTip(self.tr("在画布上隐藏选中的标签（可多选），其他标签保持显示"))
        self.stats_hide_btn.clicked.connect(self.on_stats_hide_clicked)
        buttons_layout.addWidget(self.stats_hide_btn, 1, 1)

        # 清除过滤（恢复全部显示）
        self.stats_clear_filter_btn = QtWidgets.QPushButton(self.tr("清除过滤"))
        self.stats_clear_filter_btn.setToolTip(self.tr("恢复显示所有标签"))
        self.stats_clear_filter_btn.clicked.connect(self.on_stats_clear_filter_clicked)
        buttons_layout.addWidget(self.stats_clear_filter_btn, 1, 2)

        statistics_layout.addLayout(buttons_layout)
        
        # 形状类型统计
        shape_label = QtWidgets.QLabel(self.tr("形状统计:"))
        shape_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        statistics_layout.addWidget(shape_label)
        
        self.stats_shapes_list = QtWidgets.QListWidget()
        self.stats_shapes_list.setMaximumHeight(80)  # 减少高度
        statistics_layout.addWidget(self.stats_shapes_list)
        
        # 添加弹性空间
        statistics_layout.addStretch()
        
        self.statistics_widget.setLayout(statistics_layout)
        self.statistics_dock = QtWidgets.QDockWidget(self.tr("统计信息"), self.main_window)
        self.statistics_dock.setObjectName("Statistics")
        self.statistics_dock.setFeatures(features)
        self.statistics_dock.setWidget(self.statistics_widget)
        self.statistics_dock.setStyleSheet(dock_title_style)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.statistics_dock)

        # 初始10%
        self.zoom_widget = ZoomWidget(10)
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
        # Initialize shared edges and edge snapping from config
        shared_edges_config = self._config.get("shared_edges_enabled", True)
        edge_snapping_config = self._config.get("edge_snapping_enabled", False)
        print(f"[LABEL_WIDGET INIT] Loading config: shared_edges={shared_edges_config}, edge_snapping={edge_snapping_config}")
        logger.info(f"[LABEL_WIDGET INIT] Loading config: shared_edges={shared_edges_config}, edge_snapping={edge_snapping_config}")
        self.canvas.set_shared_edges_enabled(shared_edges_config)
        self.canvas.set_edge_snapping_enabled(edge_snapping_config)
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
        self.canvas.circle_selection_completed.connect(self.handle_circle_selection)
        # 手动共边（绘制区域）状态提示到状态栏
        try:
            self.canvas.manual_shared_edge_status.connect(self.status)
        except Exception:
            pass
        
        # ✅ 连接统计更新信号
        self.canvas.new_shape.connect(self.update_statistics)
        self.canvas.shape_moved.connect(self.update_statistics)

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
            self.tr("保持上一标注"),
            self.toggle_keep_prev_mode,
            shortcuts["toggle_keep_prev_mode"],
            None,
            self.tr("切换“保持上一标注”模式"),
            checkable=True,
        )
        toggle_keep_prev_mode.setChecked(self._config["keep_prev"])

        toggle_auto_use_last_label_mode = create_action(
            self.tr("自动使用最后标签"),
            self.toggle_auto_use_last_label,
            shortcuts["toggle_auto_use_last_label"],
            None,
            self.tr("切换“自动使用最后标签”模式"),
            checkable=True,
        )
        toggle_auto_use_last_label_mode.setChecked(self._config["auto_use_last_label"])

        toggle_pplxpl_sync_mode = create_action(
            self.tr("文件夹标签同步"),
            self.toggle_pplxpl_sync,
            None,
            "group",
            self.tr("将标签应用到文件夹中的所有图像"),
            checkable=True,
        )
        toggle_pplxpl_sync_mode.setChecked(self.sync_pplxpl)

        create_mode = create_action(
            self.tr("创建多边形"),
            lambda: self.toggle_draw_mode(False, create_mode="polygon"),
            shortcuts["create_polygon"],
            "polygon",
            self.tr("Start drawing polygons"),
            enabled=False,
        )
        create_rectangle_mode = create_action(
            self.tr("创建矩形"),
            lambda: self.toggle_draw_mode(False, create_mode="rectangle"),
            shortcuts["create_rectangle"],
            "rectangle",
            self.tr("Start drawing rectangles"),
            enabled=False,
        )
        create_cirle_mode = create_action(
            self.tr("创建圆形"),
            lambda: self.toggle_draw_mode(False, create_mode="circle"),
            shortcuts["create_circle"],
            "circle",
            self.tr("Start drawing circles"),
            enabled=False,
        )
        create_line_mode = create_action(
            self.tr("创建直线"),
            lambda: self.toggle_draw_mode(False, create_mode="line"),
            shortcuts["create_line"],
            "line",
            self.tr("Start drawing lines"),
            enabled=False,
        )
        create_point_mode = create_action(
            self.tr("创建点"),
            lambda: self.toggle_draw_mode(False, create_mode="point"),
            shortcuts["create_point"],
            "point",
            self.tr("Start drawing points"),
            enabled=False,
        )
        create_line_strip_mode = create_action(
            self.tr("创建折线"),
            lambda: self.toggle_draw_mode(False, create_mode="linestrip"),
            shortcuts["create_linestrip"],
            "line-strip",
            self.tr("Start drawing linestrip. Ctrl+LeftClick ends creation."),
            enabled=False,
        )
        circle_select_mode = create_action(
            self.tr("圈选模式"),
            self.set_circle_select_mode,
            None,  # 暂时不设快捷键
            "circle_select",
            self.tr("Select shapes inside a circle for deletion or labeling"),
            enabled=False,
        )
        edit_mode = create_action(
            self.tr("编辑对象"),
            self.set_edit_mode,
            shortcuts["edit_polygon"],
            "edit",
            self.tr("Move and edit the selected polygons"),
            enabled=False,
        )
        group_selected_shapes = create_action(
            self.tr("分组选中图形"),
            self.canvas.group_selected_shapes,
            shortcuts["group_selected_shapes"],
            "group",
            self.tr("Group shapes by assigning a same group_id"),
            enabled=True,
        )
        ungroup_selected_shapes = create_action(
            self.tr("取消图形分组"),
            self.canvas.ungroup_selected_shapes,
            shortcuts["ungroup_selected_shapes"],
            "group",
            self.tr("Ungroup shapes"),
            enabled=True,
        )

        delete = create_action(
            self.tr("删除"),
            self.delete_selected_shape,
            shortcuts["delete_polygon"],
            "cancel",
            self.tr("Delete the selected polygons"),
            enabled=False,
        )
        duplicate = create_action(
            self.tr("复制多边形"),
            self.duplicate_selected_shape,
            shortcuts["duplicate_polygon"],
            "copy",
            self.tr("Create a duplicate of the selected polygons"),
            enabled=False,
        )
        copy = create_action(
            self.tr("复制对象"),
            self.copy_selected_shape,
            shortcuts["copy_polygon"],
            "copy",
            self.tr("Copy selected polygons to clipboard"),
            enabled=False,
        )
        paste = create_action(
            self.tr("粘贴对象"),
            self.paste_selected_shape,
            shortcuts["paste_polygon"],
            "paste",
            self.tr("Paste copied polygons"),
            enabled=False,
        )
        
        # ✅ 批量操作actions
        batch_delete = create_action(
            self.tr("批量删除"),
            self.batch_delete_shapes,
            None,
            "delete",
            self.tr("删除多个选中的标注对象"),
            enabled=False,
        )
        batch_labeling = create_action(
            self.tr("批量标签"),
            self.batch_set_labels,
            None,
            "edit",
            self.tr("为多个选中的标注对象设置标签"),
            enabled=False,
        )
        
        # ✅ 反选功能（已禁用）
        invert_selection = None
        
        undo_last_point = create_action(
            self.tr("撤销上一个点"),
            self.canvas.undo_last_point,
            shortcuts["undo_last_point"],
            "undo",
            self.tr("Undo last drawn point"),
            enabled=False,
        )
        remove_point = create_action(
            text=self.tr("移除选中点"),
            slot=self.remove_selected_point,
            shortcut=shortcuts["remove_selected_point"],
            icon="edit",
            tip=self.tr("Remove selected point from polygon"),
            enabled=False,
        )

        undo = create_action(
            self.tr("撤销"),
            self.undo_shape_edit,
            shortcuts["undo"],
            "undo",
            self.tr("Undo last add and edit of shape"),
            enabled=False,
        )

        hide_all = create_action(
            self.tr("&隐藏\n多边形"),
            functools.partial(self.toggle_polygons, False),
            icon="eye",
            tip=self.tr("Hide all polygons"),
            enabled=False,
        )
        show_all = create_action(
            self.tr("&显示\n多边形"),
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
            self.tr("&放大"),
            functools.partial(self.add_zoom, 1.1),
            shortcuts["zoom_in"],
            "zoom-in",
            self.tr("Increase zoom level"),
            enabled=False,
        )
        zoom_out = create_action(
            self.tr("&缩小"),
            functools.partial(self.add_zoom, 0.9),
            shortcuts["zoom_out"],
            "zoom-out",
            self.tr("Decrease zoom level"),
            enabled=False,
        )
        zoom_org = create_action(
            self.tr("&原始大小"),
            functools.partial(self.set_zoom, 100),
            shortcuts["zoom_to_original"],
            "zoom",
            self.tr("Zoom to original size"),
            enabled=False,
        )
        keep_prev_scale = create_action(
            self.tr("&保持上一缩放比例"),
            self.enable_keep_prev_scale,
            tip=self.tr("Keep previous zoom scale"),
            checkable=True,
            checked=self._config["keep_prev_scale"],
            enabled=True,
        )
        fit_window = create_action(
            self.tr("&适应窗口"),
            self.set_fit_window,
            shortcuts["fit_window"],
            "fit-window",
            self.tr("Zoom follows window size"),
            checkable=True,
            enabled=False,
        )
        fit_width = create_action(
            self.tr("适应&宽度"),
            self.set_fit_width,
            shortcuts["fit_width"],
            "fit-width",
            self.tr("Zoom follows window width"),
            checkable=True,
            enabled=False,
        )
        brightness_contrast = create_action(
            self.tr("&亮度对比度"),
            self.brightness_contrast,
            None,
            "color",
            "Adjust brightness and contrast",
            enabled=False,
        )
        
        # Crop Actions
        crop_image = create_action(
            self.tr("&Crop Image"),
            self.crop_image,
            None,
            "scissors",
            self.tr("Crop image (follows folder sync mode)"),
            enabled=False,
        )
        line_width_act = QtWidgets.QWidgetAction(self)
        line_width_act.setDefaultWidget(self.line_width_spinbox)
        self.line_width_spinbox.setEnabled(True)

        fill_opacity_act = QtWidgets.QWidgetAction(self)
        fill_opacity_act.setDefaultWidget(self.fill_opacity_slider)
        self.fill_opacity_slider.setEnabled(True)
        show_cross_line = create_action(
            self.tr("&显示十字线"),
            self.enable_show_cross_line,
            tip=self.tr("Show cross line for mouse position"),
            icon="cartesian",
            checkable=True,
            checked=self._config["show_cross_line"],
            enabled=True,
        )
        show_groups = create_action(
            self.tr("&显示分组"),
            self.enable_show_groups,
            tip=self.tr("Show shape groups"),
            icon=None,
            checkable=True,
            checked=self._config["show_groups"],
            enabled=True,
        )
        show_texts = create_action(
            self.tr("&显示文字"),
            self.enable_show_texts,
            tip=self.tr("Show text above shapes"),
            icon=None,
            checkable=True,
            checked=self._config["show_texts"],
            enabled=True,
        )
        
        show_shared_edges = create_action(
            self.tr("&共享边"),
            self.enable_shared_edges,
            shortcuts.get("toggle_shared_edges", "Ctrl+Shift+E"),
            None,
            self.tr("Enable shared edge functionality (clipping and vertex reuse)"),
            checkable=True,
            checked=self._config.get("shared_edges_enabled", True),
            enabled=True,
        )
        
        enable_edge_snapping = create_action(
            self.tr("&边缘吸附"),
            self.toggle_edge_snapping,
            tip=self.tr("Enable snapping to edges when drawing polygons"),
            icon=None,
            checkable=True,
            checked=self._config.get("edge_snapping_enabled", False),
            enabled=True,
        )

        # Manual shared edge (lasso) action
        manual_shared_edge = create_action(
            self.tr("手动共享边（绘制区域）"),
            self.start_manual_shared_edge_by_drawing,
            None,
            None,
            self.tr("Select two polygons, then draw a lasso to replace O1's segment with O2's inside the lasso"),
            enabled=True,
        )
        
        reset_views = create_action(
            self.tr("&重置视图"),
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
        # 初始使用 MANUAL_ZOOM，避免窗口自适应覆盖初始10%
        self.zoom_mode = self.MANUAL_ZOOM
        try:
            fit_window.setChecked(False)
            fit_width.setChecked(False)
        except Exception:
            pass
        self.scalers = {
            self.FIT_WINDOW: self.scale_fit_window,
            self.FIT_WIDTH: self.scale_fit_width,
            # 保持当前缩放（此处返回1不会生效于 MANUAL_ZOOM，我们在 paint_canvas 使用 zoom_widget 的值）
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = create_action(
            self.tr("&编辑标签"),
            self.edit_label,
            shortcuts["edit_label"],
            "edit",
            self.tr("Modify the label of the selected polygon"),
            enabled=False,
        )
        set_image_label = create_action(
            self.tr("设置图像标签"),
            self.edit_image_label,
            None,
            "tag",
            self.tr("Set label for the entire image"),
        )

        fill_drawing = create_action(
            self.tr("绘制时填充多边形"),
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

        # Segment All Action
        segment_all = create_action(
            self.tr("整张图分割SAM2"),
            self.segment_all_instances,
            None,
            "sam2_particle",
            self.tr("使用 SAM-2 自动分割整张图像"),
            enabled=False,
        )
        
        # HSV Actions
        toggle_hsv_widget = create_action(
            self.tr("&HSV Color Extraction"),
            self.toggle_hsv_widget,
            shortcuts["hsv_color_extraction"],
            "hsv",
            self.tr("HSV Color Extraction"),
        )
        # Label list context menu.
        label_menu = QtWidgets.QMenu()
        copy_label_text = utils.new_action(
            self,
            self.tr("复制标签文字"),
            self.copy_selected_label_texts,
        )
        utils.add_actions(label_menu, (edit, delete, copy_label_text))
        self.label_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label_list.customContextMenuRequested.connect(self.pop_label_list_menu)

        # Unique label list context menu.
        unique_label_menu = QtWidgets.QMenu()
        copy_unique_label_text = utils.new_action(
            self,
            self.tr("复制标签文字"),
            self.copy_selected_unique_label_texts,
        )
        utils.add_actions(unique_label_menu, (copy_unique_label_text,))
        self.unique_label_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.unique_label_list.customContextMenuRequested.connect(self.pop_unique_label_menu)

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
                copy_label_text=copy_label_text,
                copy_unique_label_text=copy_unique_label_text,
            batch_delete=batch_delete,
            batch_labeling=batch_labeling,
            invert_selection=None,
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
            circle_select_mode=circle_select_mode,
            zoom=zoom,
            zoom_in=zoom_in,
            zoom_out=zoom_out,
            zoom_org=zoom_org,
            keep_prev_scale=keep_prev_scale,
            fit_window=fit_window,
            fit_width=fit_width,
            line_width=line_width_act,
            fill_opacity=fill_opacity_act,
            brightness_contrast=brightness_contrast,
            crop_image=crop_image,
            show_cross_line=show_cross_line,
            show_groups=show_groups,
            show_texts=show_texts,
            show_shared_edges=show_shared_edges,
            manual_shared_edge=manual_shared_edge,
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
                batch_delete,
                batch_labeling,
                None,
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
                enable_edge_snapping,
                manual_shared_edge,
                None,
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
                None,  # 分隔线
                batch_delete,
                batch_labeling,
                None,
                None,  # 分隔线
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
                circle_select_mode,
                edit_mode,
                brightness_contrast,
                crop_image,
                segment_all,
            ),
            on_shapes_present=(save_as, hide_all, show_all),
            on_multiple_shapes_selected=(batch_delete, batch_labeling),
            group_selected_shapes=group_selected_shapes,
            ungroup_selected_shapes=ungroup_selected_shapes,
            segment_all=segment_all,
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
            edit=self.menu(self.tr("&编辑")),
            view=self.menu(self.tr("&视图")),
            language=self.menu(self.tr("&Language")),
            theme=self.menu(self.tr("&Theme")),
            label_sets=self.menu(self.tr("&Label Sets")),
            tools=self.menu(self.tr("&Tools")),
            recent_files=QtWidgets.QMenu(self.tr("Open &Recent")),
            label_list=label_menu,
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
            # ✅ 存储标签集actions的引用，用于更新勾选状态
            self.label_set_actions = {}
            for name in self._config["label_sets"]:
                # ✅ 跳过"岩浆岩结构-构造"、"火山碎屑岩-构造"、"砂岩铸体孔隙-组分"和"碳酸盐岩-构造"，不在下拉栏中显示
                if name in ["岩浆岩结构-构造", "火山碎屑岩-构造", "砂岩铸体孔隙-组分", "碳酸盐岩-构造"]:
                    continue
                act = create_action(
                    name,
                    functools.partial(self.switch_label_set, name),
                    enabled=True,
                    checkable=True,  # ✅ 使菜单项可勾选
                )
                # ✅ 设置当前选中标签集的勾选状态
                if name == self.current_label_set_name:
                    act.setChecked(True)
                actions.append(act)
                # ✅ 存储action引用
                self.label_set_actions[name] = act
            utils.add_actions(self.menus.label_sets, actions)
            
            # ✅ 在菜单创建完成后进行智能检测并更新勾选状态
            detected_label_set = self._detect_current_label_set()
            if detected_label_set != self.current_label_set_name:
                self.current_label_set_name = detected_label_set
                self.update_label_set_menu_checks(detected_label_set)

        utils.add_actions(
            self.menus.view,
            (
                self.shape_text_dock.toggleViewAction(),
                # self.flag_dock.toggleViewAction(),  # 注释掉，因为flag_dock已被禁用
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
                None,
                fit_window,
                fit_width,
                None,
                brightness_contrast,
                show_cross_line,
                show_texts,
                show_groups,
                show_shared_edges,
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
            self.actions.circle_select_mode,
            edit_mode,
            delete,
            undo,
            invert_selection,
            None,
            zoom,
            line_width_act,
            fill_opacity_act,
            fit_width,
            toggle_pplxpl_sync_mode,
            toggle_auto_labeling_widget,
            toggle_hsv_widget,
            None,
            crop_image,
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
        tools_widget.setContentsMargins(0, 0, 0, 0)  # 移除widget内边距
        tools_layout = QtWidgets.QVBoxLayout()
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(0)

        # Create toolbar for tools
        self.tools = ToolBar("Tools")
        self.tools.setObjectName("ToolsToolBar")
        self.tools.setOrientation(Qt.Vertical)
        self.tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        ##self.tools.setIconSize(QtCore.QSize(24, 24))

        # Set initial size constraints for vertical layout
        ##self.tools_dock.setMinimumWidth(40)
        ##self.tools_dock.setMaximumWidth(40)


        # Scale icon and dock size based on screen dpi
        screen = QtWidgets.QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch() if screen else 96
        scale = dpi / 96.0
        base_icon_size = int(24 * scale)
        self._base_icon_size = base_icon_size
        self._icon_size = base_icon_size
        self._dock_width = int(base_icon_size + 4)  # 减少边距：从16改为4，让工具栏更紧凑

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
            "padding: 0px;"
            "margin: 0px;"
            "}"
            "QDockWidget::title {"
            "text-align: center;"
            "background-color: " + AppTheme.get_color("dock_title_bg") + ";"
            "color: " + AppTheme.get_color("dock_title_text") + ";"
            "border-radius: 4px;"
            "margin-bottom: 2px;"
            "}"
            "QWidget {"
            "padding: 0px;"
            "margin: 0px;"
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
        # self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)  # 注释掉，因为flag_dock已被禁用
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.statistics_dock)

        self.shape_text_edit.textChanged.connect(self.shape_text_changed)
        
        # ✅ 初始化统计信息
        self.update_statistics()

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

        # 首次加载时自适应（Fit Window）一次，然后固定为手动缩放
        self._initial_fit_applied = False

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
        # ✅ 跳过窗口尺寸设置，直接使用最大化显示避免视觉跳动
        # size = self.settings.value("window/size", QtCore.QSize(600, 500))
        # position = self.settings.value("window/position", QtCore.QPoint(0, 0))
        # self.resize(size)
        # self.move(position)
        logger.info("Skipping window size restore to prevent visual jumping, will maximize instead")

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

        # ✅ 移除内部main_window的显示，避免与外层MainWindow冲突
        # QtCore.QTimer.singleShot(50, lambda: self.main_window.showMaximized())
        
        # ✅ 延迟加载dock状态，确保包括statistics_dock在内的所有组件都完全初始化
        QtCore.QTimer.singleShot(200, self.load_dock_state)  # 增加延迟时间防止布局冲突

        # Setup periodic dock state saving
        self._dock_save_timer = QtCore.QTimer(self)
        self._dock_save_timer.setInterval(60000)  # Save state every minute
        self._dock_save_timer.timeout.connect(lambda: self.save_dock_state(force=True))
        self._dock_save_timer.start()

        # # PPL-XPL 图像缓存
        # self._pplxpl_cache = {}  # 缓存叠加图像
        # self._pplxpl_cache_max_size = 10  # 增加到10个文件夹

        # 记录用户绘制的 shape 签名，防止在多次切换过程中重复累积
        # self._pplxpl_user_shape_signatures: set = set()

        # # ---- 异步同步标注 ----
        # import threading
        # self._sync_thread = None  # 后台同步线程
        # self._sync_lock = threading.Lock()
        # self._pending_sync = False

        # 全局仅缓存一段未完成绘制数据
        self._unfinished_drawing = None  # {shape, line_points, create_mode}
        # 一旦用户真正完成多边形（Canvas.new_shape 发射）就清除缓存
        self.canvas.new_shape.connect(lambda: setattr(self, "_unfinished_drawing", None))

        # 为 CPU 推理优化 PyTorch 线程数（仅设置一次）
        try:
            import torch, multiprocessing, os
            if not torch.cuda.is_available():
                num_core = multiprocessing.cpu_count()
                # 强制设置环境变量（对打包后的应用特别重要）
                os.environ["OMP_NUM_THREADS"] = str(num_core)
                os.environ["MKL_NUM_THREADS"] = str(num_core)
                os.environ["NUMEXPR_NUM_THREADS"] = str(num_core)
                os.environ["MKL_DYNAMIC"] = "FALSE"
                os.environ["OMP_DYNAMIC"] = "FALSE"
                os.environ["OMP_WAIT_POLICY"] = "ACTIVE"
                os.environ["OMP_PROC_BIND"] = "TRUE"
                # 设置 PyTorch 线程数（强制使用全部核心）
                torch.set_num_threads(num_core)
                torch.set_num_interop_threads(num_core)  # 使用更多交互线程
        except (ImportError, RuntimeError):
            pass  # 忽略设置失败或重复设置

        # ✅ 文本编辑器复选框去抖定时器：合并多次勾选操作
        self._tag_checkbox_timer = QtCore.QTimer(self)
        self._tag_checkbox_timer.setSingleShot(True)
        self._tag_checkbox_timer.setInterval(250)  # 250ms 去抖
        self._tag_checkbox_timer.timeout.connect(self._apply_text_editor_changes_from_checkbox)

    def _preserve_unfinished_drawing(self):
        """将当前未完成的多边形保存到缓存，以便稍后恢复。"""
        if self.canvas.drawing() and self.canvas.current is not None and self.filename:
            shape_copy = self.canvas.current.copy()
            line_pts = [p for p in self.canvas.line.points]
            self._unfinished_drawing = {
                "shape": shape_copy,
                "line_points": line_pts,
                "create_mode": self.canvas.create_mode,
                "free_drawing": self.canvas.free_drawing_polygon,
                "pause_drawing": self.canvas.pause_drawing_polygon,
                "mode": self.canvas.mode,
            }
        return True

    def _restore_unfinished_drawing(self, filename):
        """如有缓存的未完成多边形，则恢复继续绘制。"""
        data = self._unfinished_drawing
        if data:
            self.canvas.current = data["shape"]
            self.canvas.create_mode = data["create_mode"]
            self.canvas.line.points = data["line_points"]
            # 恢复 freehand / pause 状态
            self.canvas.free_drawing_polygon = data.get("free_drawing", False)
            self.canvas.pause_drawing_polygon = data.get("pause_drawing", False)
            self.canvas.mode = data.get("mode", self.canvas.CREATE)
            self.canvas.set_hiding()
            self.canvas.drawing_polygon.emit(True)
            self.canvas.update()

    def _finalise_ongoing_drawing(self):
        # 现在仅做保存，不再 finalize
        return self._preserve_unfinished_drawing()
    
    def set_language(self, language):
        if self._config["language"] == language:
            return
        self._config["language"] = language
        save_config(self._config)

        # Show dialog to restart application
        msg_box = QMessageBox()
        msg_box.setText(self.tr("Please restart the application to apply changes."))
        msg_box.exec_()
        self.window().close()

    def on_text_editor_tag_checkbox_changed(self):
        """文本编辑器标签勾选框变化处理"""
        if hasattr(self, 'tag_checkboxes') and self.tag_checkboxes:
            # 避免频繁 textChanged 触发：仅更新文本，不立刻保存；使用去抖定时器合并
            try:
                self.shape_text_edit.textChanged.disconnect(self.shape_text_changed)
            except Exception:
                pass
            # 获取所有可用的标签
            all_available_tags = [cb.text() for cb in self.tag_checkboxes]
            # 获取当前选中的标签
            selected_tags = [cb.text() for cb in self.tag_checkboxes if cb.isChecked()]
            
            # 获取当前文本内容
            current_text = self.shape_text_edit.toPlainText()
            current_labels = [t.strip() for t in current_text.split(",") if t.strip()]
            
            # 处理标签，避免重复
            final_labels = []
            processed_base_tags = set()  # 记录已处理的基础标签
            
            for label in current_labels:
                # 检查是否是标准标签（精确匹配）
                if label in all_available_tags:
                    if label in selected_tags:
                        final_labels.append(label)
                        processed_base_tags.add(label)
                else:
                    # 检查是否是标签扩展（以标准标签开头）
                    is_extended = False
                    for tag in all_available_tags:
                        if label.startswith(tag):
                            # 如果这个标签当前被选中，保留扩展信息
                            if tag in selected_tags:
                                final_labels.append(label)
                                processed_base_tags.add(tag)
                            is_extended = True
                            break
                    
                    # 如果不是标签扩展，作为独立用户内容保留
                    if not is_extended:
                        final_labels.append(label)
            
            # 添加其他被选中但未处理的基础标签
            for tag in selected_tags:
                if tag not in processed_base_tags:
                    final_labels.append(tag)
            
            # 保存当前滚动条位置
            scrollbar = self.shape_text_edit.verticalScrollBar()
            scroll_position = scrollbar.value() if scrollbar else 0
            
            # 更新文本内容
            new_text = ",".join(final_labels)
            self.shape_text_edit.setPlainText(new_text)
            
            # 恢复滚动条位置到底部
            if scrollbar:
                scrollbar.setValue(scrollbar.maximum())
            
            # 重新连接；通过去抖定时器统一触发一次保存
            self.shape_text_edit.textChanged.connect(self.shape_text_changed)
            self._tag_checkbox_timer.start()

    def on_label_search_changed(self, text):
        """当标签搜索框变化时，实时过滤右侧标签列表"""
        try:
            # 仅刷新 unique_label_list，避免其他状态被重置
            self.update_unique_label_list()
        except Exception:
            pass

    def _apply_text_editor_changes_from_checkbox(self):
        """在去抖时间窗结束后统一应用一次保存，减少卡顿。"""
        self.shape_text_changed()

    def show_text_edit_context_menu(self, pos):
        """显示文本编辑器中文右键菜单（完全自定义，避免英文残留）"""
        try:
            edit = self.shape_text_edit
            menu = QtWidgets.QMenu(self)
            # 撤销 / 重做
            act_undo = menu.addAction(self.tr("撤销"))
            act_undo.triggered.connect(edit.undo)
            try:
                act_undo.setEnabled(edit.document().isUndoAvailable())
            except Exception:
                pass
            act_redo = menu.addAction(self.tr("重做"))
            act_redo.triggered.connect(edit.redo)
            try:
                act_redo.setEnabled(edit.document().isRedoAvailable())
            except Exception:
                pass
            menu.addSeparator()
            # 剪切 / 复制 / 粘贴 / 删除
            act_cut = menu.addAction(self.tr("剪切"))
            act_cut.triggered.connect(edit.cut)
            act_copy = menu.addAction(self.tr("复制"))
            act_copy.triggered.connect(edit.copy)
            act_paste = menu.addAction(self.tr("粘贴"))
            act_paste.triggered.connect(edit.paste)
            act_delete = menu.addAction(self.tr("删除"))
            act_delete.triggered.connect(self._text_edit_delete_selection)
            try:
                has_sel = edit.textCursor().hasSelection()
                read_only = edit.isReadOnly()
                act_cut.setEnabled(has_sel and not read_only)
                act_copy.setEnabled(has_sel)
                act_delete.setEnabled(has_sel and not read_only)
                act_paste.setEnabled(edit.canPaste() and not read_only)
            except Exception:
                pass
            menu.addSeparator()
            # 全选
            act_select_all = menu.addAction(self.tr("全选"))
            act_select_all.triggered.connect(edit.selectAll)
            menu.exec_(edit.mapToGlobal(pos))
        except Exception:
            pass

    def _text_edit_delete_selection(self):
        try:
            cursor = self.shape_text_edit.textCursor()
            if cursor and cursor.hasSelection() and not self.shape_text_edit.isReadOnly():
                cursor.removeSelectedText()
        except Exception:
            pass

    def update_text_editor_tags_visibility(self):
        """更新文本编辑器中标签选择框的显示状态"""
        if hasattr(self, 'tag_scroll_area'):
            should_show_tags = self.current_label_set_name in ["岩浆岩", "岩浆岩结构-构造", "火山碎屑岩", "砂岩铸体孔隙", "碳酸盐岩", "碳酸盐岩-构造"]
            self.tag_scroll_area.setVisible(should_show_tags)

    def refresh_text_editor_tags(self):
        """刷新文本编辑器中的标签选择框"""
        if hasattr(self, 'tag_checkboxes'):
            # 清除现有的勾选框
            for cb in self.tag_checkboxes:
                self.label_options_layout.removeWidget(cb)
                cb.deleteLater()
            self.tag_checkboxes.clear()
            
            # 根据当前标签集合重新加载标签
            if self.current_label_set_name in ["岩浆岩", "岩浆岩结构-构造"]:
                self.available_tags = load_label_set_from_yaml("岩浆岩结构-构造")
            elif self.current_label_set_name == "火山碎屑岩":
                self.available_tags = load_label_set_from_yaml("火山碎屑岩-构造")
            elif self.current_label_set_name == "砂岩铸体孔隙":
                self.available_tags = load_label_set_from_yaml("砂岩铸体孔隙-组分")
            elif self.current_label_set_name in ["碳酸盐岩", "碳酸盐岩-构造"]:
                self.available_tags = load_label_set_from_yaml("碳酸盐岩-构造")
            
            # 为所有支持的标签集创建勾选框
            if self.current_label_set_name in ["岩浆岩", "岩浆岩结构-构造", "火山碎屑岩", "砂岩铸体孔隙", "碳酸盐岩", "碳酸盐岩-构造"]:
                for tag in self.available_tags:
                    cb = QCheckBox(tag)
                    cb.stateChanged.connect(self.on_text_editor_tag_checkbox_changed)
                    self.label_options_layout.addWidget(cb)
                    self.tag_checkboxes.append(cb)
                
                # ✅ 根据当前文本内容同步勾选框状态
                current_text = self.shape_text_edit.toPlainText()
                if current_text.strip():
                    self.sync_checkboxes_with_text(current_text)

    def clear_text_editor_checkboxes(self):
        """清除文本编辑器中的标签勾选框状态"""
        if hasattr(self, 'tag_checkboxes') and self.tag_checkboxes:
            for cb in self.tag_checkboxes:
                cb.blockSignals(True)  # 防止触发change事件
                cb.setChecked(False)
                cb.blockSignals(False)
    
    def sync_checkboxes_with_text(self, text):
        """根据文本内容同步复选框状态"""
        if hasattr(self, 'tag_checkboxes') and self.tag_checkboxes:
            # 解析文本中的标签
            text_labels = [t.strip() for t in text.split(",") if t.strip()]
            
            # 更新复选框状态（阻塞信号避免循环触发）
            for cb in self.tag_checkboxes:
                cb.blockSignals(True)
                # 检查精确匹配或标签扩展
                cb.setChecked(cb.text() in text_labels or any(label.startswith(cb.text()) for label in text_labels))
                cb.blockSignals(False)

    def get_labeling_instruction(self):
        text_mode = self.tr("Mode:")
        text_shortcuts = self.tr("Shortcuts:")
        text_previous = self.tr("Previous:")
        text_next = self.tr("Next:")
        text_rectangle = self.tr("Rectangle:")
        text_polygon = self.tr("Polygon:")
        
        # ✅ 安全获取canvas模式，如果canvas未初始化则使用默认值
        canvas_mode = self.canvas.get_mode() if hasattr(self, 'canvas') and self.canvas else "Drawing"
        
        return (
            f"<b>{text_mode}</b> {canvas_mode} - <b>{text_shortcuts}</b>"
            f" {text_previous} <b>A</b>, {text_next} <b>D</b>,"
            f" {text_rectangle} <b>R</b>,"
            f" {text_polygon} <b>P</b>"
        )

    def _force_full_refresh_after_edit(self):
        """强制将当前图像的标注保存再重新加载，彻底同步所有视图状态。

        适用于极端情况下局部刷新仍然存在显示异常的场景。
        """
        try:
            # 先保存当前标签文件
            if self.filename:
                #label_file = osp.splitext(self.image_path)[0] + ".json"
                label_file = self._label_path_for_image(self.image_path)
                # if self.output_dir:
                #     label_file_without_path = osp.basename(label_file)
                #     label_file = osp.join(self.output_dir, label_file_without_path)
                self.save_labels(label_file)
        except Exception:
            pass

        try:
            # 再用当前 shapes 重载所有视图组件
            self.label_list.clear()
            for shp in self.canvas.shapes:
                self.add_label(shp)
            self.canvas.visible.clear()
            self.canvas.load_shapes(self.canvas.shapes, replace=True)
            self.canvas.set_hiding(False)
            for s in self.canvas.shapes:
                s.selected = False
            self.canvas.selected_shapes = []
            self.canvas.selection_changed.emit([])
            self.canvas.update()
            self.update_statistics()
        except Exception:
            # 兜底：触发一次简单重绘
            self.canvas.update()

    @pyqtSlot()
    def on_auto_segmentation_requested(self):
        if hasattr(self, 'canvas') and self.canvas:
            self.canvas.set_auto_labeling(True)
        if hasattr(self, 'label_instruction'):
            self.label_instruction.setText(self.get_labeling_instruction())

    @pyqtSlot()
    def on_auto_segmentation_disabled(self):
        if hasattr(self, 'canvas') and self.canvas:
            self.canvas.set_auto_labeling(False)
        if hasattr(self, 'label_instruction'):
            self.label_instruction.setText(self.get_labeling_instruction())

    def menu(self, title, actions=None):
        menu = self.window().menuBar().addMenu(title)
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
        toolbar.setIconSize(QtCore.QSize(24, 24))
        toolbar.setMaximumWidth(40)
        if actions:
            utils.add_actions(toolbar, actions)
        return toolbar

    def statusBar(self):
        return self.window().statusBar()

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

        if ((self._config["auto_save"] or self.actions.save_auto.isChecked())
                and getattr(self, 'image_path', None)
                and not getattr(self, '_is_loading', False)):
            label_file = self._label_path_for_image(self.image_path)
            self.save_labels(label_file)
            return

        self.dirty = True
        self.actions.save.setEnabled(True)
        title = __appname__
        if self.filename is not None:
            title = f"{title} - {self.filename}*"
        self.setWindowTitle(title)
        if (hasattr(self, 'sync_pplxpl') and self.sync_pplxpl
                and not getattr(self, '_suppress_sync', False)
                and not getattr(self, '_is_loading', False)
                and getattr(self, 'image_path', None)):
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
        # 清除未完成绘制缓存，避免跨文件夹恢复
        try:
            self._unfinished_drawing = None
        except Exception:
            pass
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
        if not self.canvas.is_shape_restorable:
            self.status(self.tr("当前没有可撤销的形状操作"), delay=3000)
            self.actions.undo.setEnabled(False)
            return
        self.canvas.restore_shape()
        self.label_list.clear()
        self.load_shapes(self.canvas.shapes)
        self.actions.undo.setEnabled(self.canvas.is_shape_restorable)
        self.status(self.tr("已撤销最近一次形状编辑"), delay=2000)

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
            self.clear_auto_labeling_marks(preserve_objects=True)
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
            self.actions.circle_select_mode.setEnabled(True)
        else:
            if create_mode == "polygon":
                self.actions.create_mode.setEnabled(False)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
                self.actions.circle_select_mode.setEnabled(True)
            elif create_mode == "rectangle":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(False)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
                self.actions.circle_select_mode.setEnabled(True)
            elif create_mode == "line":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(False)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
                self.actions.circle_select_mode.setEnabled(True)
            elif create_mode == "point":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(False)
                self.actions.create_line_strip_mode.setEnabled(True)
                self.actions.circle_select_mode.setEnabled(True)
            elif create_mode == "circle":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(False)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
                self.actions.circle_select_mode.setEnabled(True)
            elif create_mode == "linestrip":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(False)
                self.actions.circle_select_mode.setEnabled(True)
            elif create_mode == "circle_select":
                self.actions.create_mode.setEnabled(True)
                self.actions.create_rectangle_mode.setEnabled(True)
                self.actions.create_cirle_mode.setEnabled(True)
                self.actions.create_line_mode.setEnabled(True)
                self.actions.create_point_mode.setEnabled(True)
                self.actions.create_line_strip_mode.setEnabled(True)
                self.actions.circle_select_mode.setEnabled(False)
            else:
                raise ValueError(f"Unsupported create_mode: {create_mode}")
        self.actions.edit_mode.setEnabled(not edit)
        self.label_instruction.setText(self.get_labeling_instruction())
        # 当切回编辑模式时，恢复编辑模式提示（圈选模式下会被抑制）
        try:
            if edit and not getattr(self, '_suppress_mode_hint', False):
                self._show_mode_hint(self.tr("已切换到编辑模式：可选择/编辑对象"))
        except Exception:
            pass

    def set_circle_select_mode(self):
        """启用圈选模式"""
        # Disable auto labeling
        self.clear_auto_labeling_marks(preserve_objects=True)
        self.auto_labeling_widget.set_auto_labeling_mode(None)

        self.toggle_draw_mode(False, create_mode="circle_select")
        self.canvas.set_circle_selection_mode(True)
        # 圈选模式下禁用模式提示，避免遮挡
        self._suppress_mode_hint = True
        self.set_text_editing(False)
        self.label_instruction.setText("圈选模式：拖拽鼠标画圆圈选中区域内的所有形状")

    def handle_circle_selection(self, selected_shapes):
        """处理圈选完成事件"""
        if not selected_shapes:
            # 没有选中任何形状
            self.status(self.tr("圈选完成，但没有形状在圈内"))
            # 直接退出圈选模式，回到编辑
            try:
                self.canvas.set_circle_selection_mode(False)
                self.toggle_draw_mode(True)
            except Exception:
                pass
            return

        # 先高亮所选形状让用户确认
        try:
            # 仅高亮这些形状，隐藏其他形状（但不改变持久可见性）
            self.canvas.selection_changed.emit(selected_shapes)
            self.canvas.update()
        except Exception:
            pass

        # 显示选择对话框（包含确认提示）
        from PyQt5.QtWidgets import QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("圈选操作 - 确认选中对象"))
        dialog.setModal(True)
        
        layout = QVBoxLayout()
        
        # 显示选中的形状数量
        count_label = QLabel(self.tr(f"选中了 {len(selected_shapes)} 个形状。请确认是否对这些对象执行操作。"))
        layout.addWidget(count_label)
        
        # 按钮布局
        button_layout = QHBoxLayout()
        
        delete_button = QPushButton(self.tr("确认删除"))
        delete_button.clicked.connect(lambda: self.circle_delete_shapes(selected_shapes, dialog))
        
        edit_label_button = QPushButton(self.tr("确认修改标签"))
        edit_label_button.clicked.connect(lambda: self.circle_edit_labels(selected_shapes, dialog))
        
        cancel_button = QPushButton(self.tr("取消(返回编辑)"))
        cancel_button.clicked.connect(dialog.reject)
        
        button_layout.addWidget(delete_button)
        button_layout.addWidget(edit_label_button)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        
        res = dialog.exec_()
        # 用户关闭对话框（点击 X 或取消）时也退出圈选模式
        if res != QtWidgets.QDialog.Accepted:
            try:
                self.canvas.set_circle_selection_mode(False)
                self.toggle_draw_mode(True)
                self._suppress_mode_hint = False
            except Exception:
                pass

    def circle_delete_shapes(self, shapes, dialog):
        """删除圈选的形状"""
        for shape in shapes:
            if shape in self.canvas.shapes:
                self.canvas.shapes.remove(shape)
        # 同步对象列表，确保保存时不包含已删除的形状
        try:
            self.remove_labels(shapes)
        except Exception:
            pass
        
        self.canvas.store_shapes()
        self.canvas.selection_changed.emit([])
        self.canvas.update()
        self.set_dirty()
        self.update_statistics()
        # 触发保存（强制保存）
        try:
            self.save_file()
        except Exception:
            pass
        
        self.status(self.tr(f"已删除 {len(shapes)} 个形状"))
        # 完成后退出圈选模式，返回编辑模式
        try:
            self.canvas.set_circle_selection_mode(False)
            self.toggle_draw_mode(True)
            self._suppress_mode_hint = False
        except Exception:
            pass
        dialog.accept()

    def circle_edit_labels(self, shapes, dialog):
        """使用标准标签对话框修改圈选形状的标签（与单个标注一致）。"""
        # 计算初始文本：若所有被选形状标签一致，则展示该标签，否则置空
        existing_labels = {getattr(s, 'label', '') for s in shapes}
        init_text = next(iter(existing_labels)) if len(existing_labels) == 1 else ""

        # 取第一个形状的 flags / group_id 作为初始值（批量时可被覆盖）
        init_flags = getattr(shapes[0], 'flags', {}) if shapes else {}
        init_group = getattr(shapes[0], 'group_id', None) if shapes else None

        # 弹出与单个标注一致的标签对话框
        text, flags, group_id = self.label_dialog.pop_up(
            text=init_text,
            flags=init_flags,
            group_id=init_group,
        )

        if text is None and flags is None and group_id is None:
            dialog.reject()
            return

        # 更新所有形状
        updated_count = 0
        if text is not None:
            # 记录到历史
            for lb in [t.strip() for t in text.split(',') if t.strip()]:
                self.label_dialog.add_label_history(lb)

        for shape in shapes:
            if text is not None:
                shape.label = text
                # 同步颜色
                self._update_shape_color(shape)
            if flags is not None:
                shape.flags = flags
            if group_id is not None:
                shape.group_id = group_id
            updated_count += 1

        # 刷新界面/统计
        self.canvas.store_shapes()
        self.canvas.update()
        # 触发保存（强制保存）
        try:
            self.save_file()
        except Exception:
            pass
        self.update_unique_label_list()
        self.set_dirty()
        self.update_statistics()

        self.status(self.tr(f"已修改 {updated_count} 个形状的标签"))
        # 完成后退出圈选模式，返回编辑模式
        try:
            self.canvas.set_circle_selection_mode(False)
            self.toggle_draw_mode(True)
            self._suppress_mode_hint = False
        except Exception:
            pass
        dialog.accept()

    def set_edit_mode(self):
        # Disable auto labeling
        self.clear_auto_labeling_marks(preserve_objects=True)
        self.auto_labeling_widget.set_auto_labeling_mode(None)

        self.toggle_draw_mode(True)
        self.set_text_editing(True)
        self.label_instruction.setText(self.get_labeling_instruction())

    def start_manual_shared_edge_by_drawing(self):
        """启动手动共边（绘制区域）模式。

        要求：当前选中两个多边形。其中第一个作为目标（将被修改），第二个作为来源（提供共边）。
        交互：进入绘制多边形模式，闭合后根据示意规则替换目标多边形在圈内的边段。
        """
        try:
            sel = list(self.canvas.selected_shapes)
        except Exception:
            sel = []

        if len(sel) != 2:
            self.status(self.tr("❌ 请先选中两个多边形（第一个为目标，第二个为来源）"))
            return

        for s in sel:
            if not hasattr(s, 'shape_type') or s.shape_type != 'polygon' or not s.is_closed():
                self.status(self.tr("❌ 手动共边仅支持封闭多边形"))
                return

        # 通知 Canvas 进入手动共边（套索）模式
        try:
            ok = self.canvas.start_manual_shared_edge_polygon_mode(sel[0], sel[1])
            if not ok:
                return
        except Exception as e:
            # 如果 Canvas 版本较旧没有该方法，给出提示
            self.status(self.tr(f"❌ 无法进入手动共边模式: {e}"))
            return

        # 进入绘制多边形模式
        self.toggle_draw_mode(False, create_mode="polygon")
        self.set_text_editing(False)
        self.status(self.tr("手动共边：请绘制闭合区域，完成后自动应用"))

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

    def pop_unique_label_menu(self, point):
        try:
            # 右键命中项设为当前项，便于直接复制
            hit_item = self.unique_label_list.itemAt(point)
            if hit_item is not None:
                self.unique_label_list.setCurrentItem(hit_item)
                if hit_item.isSelected() is False:
                    self.unique_label_list.clearSelection()
                    hit_item.setSelected(True)
            menu = QtWidgets.QMenu()
            act = utils.new_action(
                self,
                self.tr("复制标签文字"),
                self.copy_selected_unique_label_texts,
            )
            utils.add_actions(menu, (act,))
            menu.exec_(self.unique_label_list.mapToGlobal(point))
        except Exception:
            pass

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

    def copy_selected_label_texts(self):
        try:
            items = self.label_list.selected_items()
        except Exception:
            items = []
        if not items:
            return
        texts = []
        for it in items:
            try:
                t = it.text()
            except Exception:
                t = ""
            if t:
                # 取纯文本（去掉可能的富文本）
                try:
                    doc = QtGui.QTextDocument()
                    doc.setHtml(t)
                    t = doc.toPlainText()
                except Exception:
                    pass
                texts.append(t)
        if not texts:
            return
        try:
            cb = QtWidgets.QApplication.clipboard()
            cb.setText("\n".join(texts))
        except Exception:
            pass

    def copy_selected_unique_label_texts(self):
        try:
            items = self.unique_label_list.selectedItems()
        except Exception:
            items = []
        if not items:
            # 若无选中，则尝试使用当前项
            try:
                current = self.unique_label_list.currentItem()
                if current:
                    items = [current]
            except Exception:
                items = []
        if not items:
            return
        labels = []
        for it in items:
            try:
                label = it.data(Qt.UserRole)
            except Exception:
                label = None
            if label:
                labels.append(str(label))
        if not labels:
            return
        try:
            cb = QtWidgets.QApplication.clipboard()
            cb.setText("\n".join(labels))
        except Exception:
            pass

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
        # 允许仅修改群组编号：当text为None或空时，保持原标签不变
        if text is None and group_id is None:
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
        if text:
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
            # 列表中不再追加群组编号，仅显示标签文本
            item.setText(f"{shape.label}")
        self.set_dirty()
        # ✅ 更新统计信息
        self.update_statistics()

    def edit_image_label(self):
        text, flags, _ = self.label_dialog.pop_up(
            text=", ".join(
                self.label_file.image_labels if self.label_file else self.other_data.get("image_labels", [])),
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
                    self.tr("Invalid label '{}' with validation type '{}'").format(lb, self._config["validate_label"]),
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
        # 切换前先缓存可能存在的未完成多边形
        self._finalise_ongoing_drawing()

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
                # ✅ 切换文件时清除文本编辑器中的标签勾选框状态
                self.clear_text_editor_checkboxes()
                
                # 始终同步缩放状态，不依赖特定配置
                if self.filename:
                    # 保存当前文件的缩放状态
                    current_zoom_value = self.zoom_widget.value() if hasattr(self, 'zoom_widget') else 100
                    self.zoom_values[self.filename] = (self.zoom_mode, current_zoom_value)
                    
                    # 复制缩放和滚动状态到新文件
                    self._copy_view_state(self.filename, filename)
                
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
            try:
                item = self.label_list.find_item_by_shape(shape)
                self.label_list.select_item(item)
                self.label_list.scroll_to_item(item)
            except ValueError:
                # Shape not found in label list, skip silently
                pass
        self._no_selection_slot = False
        n_selected = len(selected_shapes)
        self.actions.delete.setEnabled(n_selected)
        self.actions.duplicate.setEnabled(n_selected)
        self.actions.copy.setEnabled(n_selected)
        self.actions.edit.setEnabled(n_selected == 1)
        # 手动共边功能只在选中一个多边形时启用
        # ✅ 控制批量操作actions的启用状态
        self.actions.batch_delete.setEnabled(n_selected > 1)
        self.actions.batch_labeling.setEnabled(n_selected > 1)
        # 反选功能已取消，不再启用控件
        try:
            # 拖拽（moving_shape）或正在操作顶点时，不切换文本编辑模式，避免误判到“图像文本”
            is_moving = getattr(self.canvas, 'moving_shape', False)
            is_vertex_selected = False
            try:
                if hasattr(self.canvas, 'selected_vertex'):
                    is_vertex_selected = bool(self.canvas.selected_vertex())
            except Exception:
                is_vertex_selected = False
            if not (is_moving or is_vertex_selected):
                self.set_text_editing(True)
        except Exception:
            self.set_text_editing(True)

        # ✅ 仅更新“持久选择标签集合”（不改动右下角标签详情的UI选中）
        try:
            selected_label_names = set()
            # 若不是源自统计面板，则记录本次手动选择的几何签名
            canvas_sigs = [] if getattr(self, '_selection_from_stats', False) else []
            for s in selected_shapes or []:
                eff_labels = []
                if hasattr(s, 'labels') and s.labels:
                    eff_labels = [str(x).strip() for x in s.labels if str(x).strip()]
                elif hasattr(s, 'label') and s.label:
                    raw = str(s.label).strip()
                    eff_labels = [t.strip() for t in raw.split(',') if t.strip()]
                elif hasattr(s, 'primary_label') and s.primary_label:
                    eff_labels = [str(s.primary_label).strip()]
                for lb in eff_labels:
                    if lb:
                        selected_label_names.add(lb)
                # 生成几何签名：shape_type + 边界框(整数)
                try:
                    br = s.bounding_rect()
                    bbox = (int(br.x()), int(br.y()), int(br.width()), int(br.height()))
                    sig = (getattr(s, 'shape_type', 'polygon'), bbox)
                    if canvas_sigs is not None:
                        canvas_sigs.append(sig)
                except Exception:
                    pass
            self._stats_persist_selected_labels = set(selected_label_names)
            # 仅当此次选择不是由统计面板触发时，更新手动选择签名
            if not getattr(self, '_selection_from_stats', False):
                self._canvas_persist_selected_sigs = canvas_sigs
            # 重置来源标记
            self._selection_from_stats = False
        except Exception:
            pass


    def update_unique_label_list(self):
        """Refresh unique label list from current config."""
        self.unique_label_list.clear()
        labels = self._config.get("labels", [])
        if labels:
            # 若存在搜索关键字，则先过滤
            keyword = ""
            try:
                keyword = self.label_search.text().strip()
            except Exception:
                keyword = ""
            for label in labels:
                if keyword and keyword.lower() not in str(label).lower():
                    continue
                item = self.unique_label_list.create_item_from_label(label)
                self.unique_label_list.addItem(item)
                try:
                    rgb = self._get_rgb_by_label(label)
                except Exception:
                    rgb = None
                self.unique_label_list.set_item_label(item, label, rgb)

    def update_label_dialog_labels(self):
        """Refresh label dialog list from current config."""
        # 重新创建LabelDialog以支持不同的界面模式
        labels = self._config.get("labels", [])
        self.label_dialog = LabelDialog(
            parent=self,
            labels=labels,
            sort_labels=self._config["sort_labels"],
            show_text_field=self._config["show_label_text_field"],
            completion=self._config["label_completion"],
            fit_to_content=self._config["fit_to_content"],
            flags=self._config["label_flags"],
            label_set_name=self.current_label_set_name,
        )

    def add_label(self, shape):
        if shape.group_id is None:
            text = shape.label
        else:
            text = f"{shape.label}"
        label_list_item = LabelListWidgetItem(text, shape)
        self.label_list.add_iem(label_list_item)
        
        # # 在ppl-xpl模式下标记新创建的shapes
        # if hasattr(self, 'sync_pplxpl') and self.sync_pplxpl:
        #     if not hasattr(shape, 'is_from_json'):
        #         shape.is_from_json = False  # 标记为新创建的shape
        
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
        # 当前绘制中的对象优先
        if self.canvas.current is not None:
            self.canvas.current.text = text
        # 对象文本（包含多选）：当有选中对象时，写回所有选中对象
        elif self.canvas.editing() and self.canvas.selected_shapes:
            for s in self.canvas.selected_shapes:
                try:
                    s.text = text
                except Exception:
                    pass
        # 否则为图像文本
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
        """优化的标签删除方法"""
        if not shapes:
            return
        
        # ✅ 批量删除优化：收集所有对应的item，一次性批量删除
        items_to_remove = []
        for shape in shapes:
            try:
                item = self.label_list.find_item_by_shape(shape)
                if item is not None:
                    items_to_remove.append(item)
            except (ValueError, RuntimeError):
                continue

        # 使用新批量删除接口，最小化UI刷新和信号风暴；随后强制刷新一次对象面板
        try:
            self.label_list.remove_items(items_to_remove)
        except Exception:
            # 兼容旧路径
            for it in items_to_remove:
                try:
                    self.label_list.remove_item(it)
                except Exception:
                    pass
        # 删除完成后强制刷新对象面板，避免拖拽时黑屏
        try:
            self.label_list.viewport().update()
            self.label_list.viewport().repaint()
        except Exception:
            pass

        # 兜底方案：短暂隐藏/再显示触发一次完整布局与绘制
        try:
            self.label_list.setVisible(False)
            QtWidgets.QApplication.processEvents()
            self.label_list.setVisible(True)
        except Exception:
            pass

    def load_shapes(self, shapes, replace=True):
        self._no_selection_slot = True
        # 批量刷新对象列表，减少信号与重绘抖动
        try:
            if hasattr(self, 'label_list') and self.label_list:
                self.label_list.begin_bulk_update()
        except Exception:
            pass
        try:
            for shape in shapes:
                self.add_label(shape)
            self.label_list.clearSelection()
        finally:
            try:
                if hasattr(self, 'label_list') and self.label_list:
                    self.label_list.end_bulk_update()
            except Exception:
                pass
        self._no_selection_slot = False
        self.canvas.load_shapes(shapes, replace=replace)
        # ✅ 更新统计信息
        self.update_statistics()

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
            # 不从JSON恢复 group_id（仅运行时使用）
            group_id = None
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
        # 完全禁用标志框加载功能
        pass
        # self.flag_widget.clear()
        # for key, flag in flags.items():
        #     item = QtWidgets.QListWidgetItem(key)
        #     item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        #     item.setCheckState(Qt.Checked if flag else Qt.Unchecked)
        #     self.flag_widget.addItem(item)

    def save_labels(self, filename):
        label_file = LabelFile()
        using_folder_label = False
        if getattr(self, "image_path", None):
            try:
                target_folder_label = self._folder_label_path_for_image(self.image_path)
                using_folder_label = (
                    target_folder_label
                    and filename
                    and osp.normpath(filename) == osp.normpath(target_folder_label)
                )
            except Exception:
                using_folder_label = False
        if using_folder_label:
            self.other_data["folderSync"] = True
        else:
            self.other_data.pop("folderSync", None)

        def format_shape(s):
            data = s.other_data.copy()
            # 针对特定标签集使用单字段 "label"，其余保持使用 "labels"
            primary = s.primary_label
            use_single_label = getattr(self, 'current_label_set_name', None) in [
                "砂岩",
                "砂岩铸体孔隙",
                "碳酸盐岩",
            ]
            payload = {
                "text": s.text,
                "points": [(p.x(), p.y()) for p in s.points],
                # 群组编号仅用于检索/显示，不写入JSON
                "shape_type": s.shape_type,
                "flags": s.flags,
            }
            if use_single_label:
                payload["label"] = primary
            else:
                payload["labels"] = s.labels
            data.update(payload)
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
        # 完全禁用标志框保存功能
        flags = {}
        # for i in range(self.flag_widget.count()):
        #     item = self.flag_widget.item(i)
        #     key = item.text()
        #     flag = item.checkState() == Qt.Checked
        #     flags[key] = flag
        try:
            if using_folder_label:
                image_path = FOLDER_SYNC_SENTINEL
                image_data = None
            else:
                image_path = osp.relpath(self.image_path, osp.dirname(filename))
                image_data = self.image_data if self._config["store_data"] else None
            if osp.dirname(filename) and not osp.exists(osp.dirname(filename)):
                os.makedirs(osp.dirname(filename))
            label_file.image_labels = self.other_data.get("image_labels", [])
            other_data = dict(self.other_data)
            label_file.save(
                filename=filename,
                shapes=shapes,
                image_path=image_path,
                image_data=image_data,
                image_height=self.image.height(),
                image_width=self.image.width(),
                other_data=other_data,
                flags=flags,
            )
            self.label_file = label_file
            items = self.file_list_widget.findItems(self.image_path, Qt.MatchExactly)
            if len(items) > 0:
                if len(items) != 1:
                    raise RuntimeError("There are duplicate files.")
                items[0].setCheckState(Qt.Checked)
            if (hasattr(self, 'sync_pplxpl') and self.sync_pplxpl
                    and not getattr(self, '_suppress_sync', False)
                    and not getattr(self, '_is_loading', False)
                    and getattr(self, 'image_path', None)):
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
            # 复制/重复在“开启同步”时视为新建：打上同步标记
            try:
                if isinstance(getattr(shape, 'other_data', None), dict):
                    shape.other_data["pplxpl_sync"] = bool(getattr(self, 'sync_pplxpl', False))
            except Exception:
                pass
            self.add_label(shape)
        self.set_dirty()

    def paste_selected_shape(self):
        # 粘贴在“开启同步”时视为新建：打上同步标记
        try:
            for s in getattr(self, '_copied_shapes', []) or []:
                if isinstance(getattr(s, 'other_data', None), dict):
                    s.other_data["pplxpl_sync"] = bool(getattr(self, 'sync_pplxpl', False))
        except Exception:
            pass
        self.load_shapes(self._copied_shapes, replace=False)
        self.set_dirty()

    def copy_selected_shape(self):
        self._copied_shapes = [s.copy() for s in self.canvas.selected_shapes]
        self.actions.paste.setEnabled(len(self._copied_shapes) > 0)

    def label_selection_changed(self):
        if self._no_selection_slot:
            return
        # 移除对 self.canvas.editing() 的检查，确保在任何模式下都能正确同步选中状态
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
        # If the canvas requests to suppress the label dialog once (e.g. manual
        # shared-edge lasso applied an edit but did not create a new shape),
        # skip the dialog and just refresh UI/state.
        try:
            if getattr(self.canvas, "_suppress_label_dialog_once", False):
                self.canvas._suppress_label_dialog_once = False
                # Return to edit mode for clarity after lasso-based shared edge
                try:
                    self.toggle_draw_mode(True)
                except Exception:
                    pass
                # Mark dirty and refresh statistics; no new shape to add
                try:
                    self.set_dirty()
                    self.update_statistics()
                except Exception:
                    pass
                return
        except Exception:
            pass

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
                # 兼容 QTextEdit 和 QLineEdit
                if hasattr(self.label_dialog.edit, 'toPlainText'):
                    previous_text = self.label_dialog.edit.toPlainText()  # QTextEdit
                else:
                    previous_text = self.label_dialog.edit.text()  # QLineEdit
                    
                # ✅ 对于自动标注对象，始终从空白状态开始
                dialog_text = "" if self.canvas.shapes[-1].primary_label == AutoLabelingMode.OBJECT else text
                text, flags, group_id = self.label_dialog.pop_up(dialog_text)
                if not text:
                    # 兼容 QTextEdit 和 QLineEdit
                    if hasattr(self.label_dialog.edit, 'setPlainText'):
                        self.label_dialog.edit.setPlainText(previous_text)  # QTextEdit
                    else:
                        self.label_dialog.edit.setText(previous_text)  # QLineEdit

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
            # 记录该形状创建时是否处于“文件夹标签同步”模式，仅用于后续同步过滤
            try:
                if isinstance(getattr(shape, 'other_data', None), dict):
                    shape.other_data["pplxpl_sync"] = bool(getattr(self, 'sync_pplxpl', False))
            except Exception:
                pass
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
        self._is_loading = True
        try:
            # For auto labeling, clear the previous marks
            # and inform the next files to be annotated
            try:
                self.clear_auto_labeling_marks()
                self.inform_next_files(filename)
            except Exception as e:
                logger.warning("清理自动标注标记失败: %s", str(e))

            # Changing file_list_widget loads file
            if filename in self.image_list and (
                self.file_list_widget.currentRow() != self.image_list.index(filename)
            ):
                try:
                    self.file_list_widget.setCurrentRow(self.image_list.index(filename))
                    self.file_list_widget.repaint()
                except Exception as e:
                    logger.warning("更新文件列表选择失败: %s", str(e))
                return False

            # 安全重置状态
            try:
                self.reset_state()
                self.canvas.setEnabled(False)
            except Exception as e:
                logger.warning("重置状态失败: %s", str(e))

            if filename is None:
                try:
                    filename = self.settings.value("filename", "")
                except Exception as e:
                    logger.warning("读取设置中的文件名失败: %s", str(e))
                    filename = ""
            
            filename = str(filename) if filename else ""
            
            # 增强文件存在性检查
            if not filename:
                self.error_message(
                    self.tr("Error opening file"),
                    self.tr("No filename provided"),
                )
                return False
                
            if not QtCore.QFile.exists(filename):
                self.error_message(
                    self.tr("Error opening file"),
                    self.tr("No such file: <b>%s</b>") % filename,
                )
                return False
            
            # 检查文件访问权限
            try:
                with open(filename, 'rb') as test_file:
                    test_file.read(1)  # 尝试读取1字节
            except (PermissionError, OSError) as e:
                self.error_message(
                    self.tr("Error opening file"),
                    self.tr("Cannot access file: <b>%s</b><br/>Error: %s") % (filename, str(e)),
                )
                return False

            # assumes same name, but json extension
            try:
                self.status(str(self.tr("Loading %s...")) % osp.basename(str(filename)))
            except Exception as e:
                logger.warning("更新状态栏失败: %s", str(e))
            
            #label_file = osp.splitext(filename)[0] + ".json"
            if getattr(self, "sync_pplxpl", False):
                try:
                    self._maybe_migrate_legacy_folder_annotations(filename)
                except Exception as e:
                    logger.warning("迁移旧标注时发生异常: %s", e)
            label_file = self._label_path_for_image(filename)
            # if self.output_dir:
            #     label_file_without_path = osp.basename(label_file)
            #     label_file = osp.join(self.output_dir, label_file_without_path)
            
            # 增强标签文件加载
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
                except Exception as e:
                    self.error_message(
                        self.tr("Error opening file"),
                        self.tr("Unexpected error reading label file <i>%s</i>: %s") % (label_file, str(e)),
                    )
                    self.status(self.tr("Error reading %s") % label_file)
                    return False
                
                # 安全提取图像数据
                try:
                    is_folder_label = getattr(self.label_file, "is_folder_sync", False)
                    if is_folder_label:
                        self.other_data["folderSync"] = True
                        self.image_path = filename
                        self.image_data = LabelFile.load_image_file(filename)
                        try:
                            rel_path = osp.relpath(filename, osp.dirname(label_file))
                            self.label_file.image_path = rel_path
                        except Exception:
                            self.label_file.image_path = filename
                    else:
                        self.image_data = self.label_file.image_data
                        self.image_path = osp.join(
                            osp.dirname(label_file),
                            self.label_file.image_path,
                        )
                        self.other_data.pop("folderSync", None)
                    # ✅ 恢复完整的other_data，包括image_text等所有数据
                    if hasattr(self.label_file, 'other_data') and self.label_file.other_data:
                        self.other_data.update(self.label_file.other_data)
                    self.other_data["image_labels"] = self.label_file.image_labels
                    
                    # ✅ 恢复图像文本并同步复选框状态
                    try:
                        self.shape_text_edit.textChanged.disconnect()
                        image_text = self.other_data.get("image_text", "")
                        self.shape_text_edit.setPlainText(image_text)
                        # ✅ 根据图像文本内容同步勾选框状态
                        self.sync_checkboxes_with_text(image_text)
                        self.shape_text_edit.textChanged.connect(self.shape_text_changed)
                    except Exception as e:
                        logger.warning("恢复图像文本失败: %s", str(e))
                except Exception as e:
                    logger.error("提取标签文件数据失败: %s", str(e))
                    self.image_data = None
            else:
                # 直接加载图像文件
                try:
                    self.image_data = LabelFile.load_image_file(filename)
                    if self.image_data:
                        self.image_path = filename
                    else:
                        self.error_message(
                            self.tr("Error opening file"),
                            self.tr("Failed to load image data from <i>%s</i>") % filename,
                        )
                        return False
                    self.label_file = None
                    self.other_data = {}
                    self.other_data["image_labels"] = []
                except Exception as e:
                    logger.error("加载图像文件失败: %s", str(e))
                    self.error_message(
                        self.tr("Error opening file"),
                        self.tr("Failed to load image <i>%s</i>: %s") % (filename, str(e)),
                    )
                    return False
            
            # 安全检查图像数据
            if not self.image_data:
                self.error_message(
                    self.tr("Error opening file"),
                    self.tr("No image data loaded from <i>%s</i>") % filename,
                )
                return False
            
            # 创建QImage对象
            try:
                image = QtGui.QImage.fromData(self.image_data)
            except Exception as e:
                logger.error("创建QImage对象失败: %s", str(e))
                self.error_message(
                    self.tr("Error opening file"),
                    self.tr("Failed to create image object from <i>%s</i>: %s") % (filename, str(e)),
                )
                return False

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

            # 冻结 UI，避免切换/加载过程中多次重绘导致的卡顿
            _freeze_ok = True
            try:
                self.setUpdatesEnabled(False)
                self.canvas.setUpdatesEnabled(False)
                if hasattr(self, 'label_list') and self.label_list:
                    self.label_list.begin_bulk_update()
            except Exception:
                _freeze_ok = False
            if self._config["keep_prev"]:
                prev_shapes = self.canvas.shapes
            self.canvas.load_pixmap(QtGui.QPixmap.fromImage(image))
            flags = dict.fromkeys(self._config["flags"] or [], False)
            if self.label_file:
                self.load_labels(self.label_file.shapes)
                if self.label_file.flags is not None:
                    flags.update(self.label_file.flags)
            self.load_flags(flags)
            if self._config["keep_prev"] and self.no_shape() and not getattr(self, '_suppress_sync', False):
                self.load_shapes(prev_shapes, replace=False)
                self.set_dirty()
            else:
                self.set_clean()
            # 确保撤销栈至少记录当前初始状态，避免首次编辑无法撤销
            backups = getattr(self.canvas, "shapes_backups", None)
            if backups is None:
                backups = []
                self.canvas.shapes_backups = backups
            if len(backups) == 0:
                self.canvas.store_shapes()
            self.canvas.setEnabled(True)
            # set zoom values
            is_initial_load = not self.zoom_values
            if self.filename in self.zoom_values:
                # 直接应用已保存的缩放值（跨图片同步缩放）
                self.zoom_mode = self.zoom_values[self.filename][0]
                self.set_zoom(self.zoom_values[self.filename][1])
            else:
                # 首次启动的第一张图：按屏幕自适应一次，然后锁定为手动比例
                if not self._initial_fit_applied:
                    try:
                        fit_value = int(100 * self.scale_fit_window())
                        # 约束在控件范围内
                        fit_value = max(1, min(1000, fit_value))
                        self.set_zoom(fit_value)
                    except Exception:
                        # 兜底：失败则使用10%
                        self.set_zoom(10)
                    self._initial_fit_applied = True
                else:
                    # 非首次时无历史记录，保持10%
                    self.set_zoom(10)
            # set scroll values
            for orientation in self.scroll_values:
                if self.filename in self.scroll_values[orientation]:
                    self.set_scroll(
                        orientation, self.scroll_values[orientation][self.filename]
                    )
            # set brightness/contrast values（仅在需要应用时才创建对话框，避免每次切图的额外开销）
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
            self.brightness_contrast_values[self.filename] = (brightness, contrast)
            if brightness is not None or contrast is not None:
                try:
                    dialog = BrightnessContrastDialog(
                        utils.img_data_to_pil(self.image_data),
                        self.on_new_brightness_contrast,
                        parent=self,
                    )
                    if brightness is not None:
                        dialog.slider_brightness.setValue(brightness)
                    if contrast is not None:
                        dialog.slider_contrast.setValue(contrast)
                    dialog.on_new_value(None)
                except Exception:
                    pass
            self.paint_canvas()
            self.add_recent_file(self.filename)
            self.toggle_actions(True)
            self.canvas.setFocus()
            self.status(str(self.tr("Loaded %s")) % osp.basename(str(filename)))

            # Save dock state after loading file (to capture any UI adjustments)
            QtCore.QTimer.singleShot(100, self.save_dock_state)

            # 首次启动：在布局稳定后再按窗口自适应一次，避免初始几何尺寸为0导致回退到10%
            try:
                if not self._initial_fit_applied:
                    def _apply_initial_fit_once():
                        try:
                            if not hasattr(self, 'canvas') or not self.canvas:
                                return
                            if not hasattr(self.canvas, 'pixmap') or self.canvas.pixmap is None:
                                return
                            fit_value2 = int(100 * self.scale_fit_window())
                            fit_value2 = max(1, min(1000, fit_value2))
                            self.set_zoom(fit_value2)
                            self._initial_fit_applied = True
                            self.paint_canvas()
                        except Exception:
                            pass
                    QtCore.QTimer.singleShot(120, _apply_initial_fit_once)
            except Exception:
                pass

            # 若有未完成多边形则恢复
            self._restore_unfinished_drawing(filename)
            self.paint_canvas()

            # 解冻 UI
            try:
                if _freeze_ok and hasattr(self, 'label_list') and self.label_list:
                    self.label_list.end_bulk_update()
                if _freeze_ok:
                    self.canvas.setUpdatesEnabled(True)
                    self.setUpdatesEnabled(True)
            except Exception:
                pass

        except MemoryError:
            logger.error("内存不足，无法加载文件: %s", filename)
            try:
                # 强制垃圾回收
                import gc
                gc.collect()
                self.error_message(
                    self.tr("Memory Error"),
                    self.tr("Not enough memory to load <i>%s</i>.<br/>Try closing other applications or loading a smaller image.") % filename,
                )
            except Exception:
                pass
            return False
        except Exception as e:
            logger.error("加载文件时发生未知错误 %s: %s", filename, str(e))
            try:
                self.error_message(
                    self.tr("Error opening file"),
                    self.tr("Unexpected error loading <i>%s</i>: %s") % (filename, str(e)),
                )
            except Exception:
                pass
            return False
        finally:
            # 确保界面状态恢复
            self._is_loading = False
            try:
                if hasattr(self, 'canvas') and self.canvas:
                    self.canvas.setEnabled(True)
            except Exception:
                pass
        
        return True

    # QT Overload
    def resizeEvent(self, _):
        if (
            hasattr(self, 'canvas') and self.canvas
            and hasattr(self, 'image') and not self.image.isNull()
            and hasattr(self, 'zoom_mode') and self.zoom_mode != self.MANUAL_ZOOM
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

    def paint_canvas(self):
        """安全地绘制画布，包含完善的错误处理"""
        try:
            # 检查图像是否有效
            if not hasattr(self, 'image') or self.image is None or self.image.isNull():
                logger.warning("尝试绘制空图像，跳过绘制")
                return
            
            # 检查画布是否有效
            if not hasattr(self, 'canvas') or self.canvas is None:
                logger.warning("画布不存在，跳过绘制")
                return
            
            # 检查缩放控件是否有效
            if not hasattr(self, 'zoom_widget') or self.zoom_widget is None:
                logger.warning("缩放控件不存在，使用默认缩放")
                self.canvas.scale = 1.0
            else:
                try:
                    zoom_value = self.zoom_widget.value()
                    if zoom_value <= 0:
                        logger.warning("无效的缩放值: %s，使用100%%", zoom_value)
                        zoom_value = 100
                    self.canvas.scale = 0.01 * zoom_value
                except Exception as e:
                    logger.warning("获取缩放值失败: %s，使用默认缩放", str(e))
                    self.canvas.scale = 1.0
            
            # 安全调整画布大小
            try:
                self.canvas.adjustSize()
            except Exception as e:
                logger.warning("调整画布大小失败: %s", str(e))
            
            # 安全更新画布
            try:
                self.canvas.update()
            except Exception as e:
                logger.warning("更新画布失败: %s", str(e))
                
        except Exception as e:
            logger.error("绘制画布时发生错误: %s", str(e))

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
        self.settings.setValue("window/state", self.window().saveState())

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
        if not self._finalise_ongoing_drawing():
              return
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
                # 始终同步缩放状态，不依赖特定配置
                current_filename = self.filename
                
                # 保存当前文件的缩放状态
                current_zoom_value = self.zoom_widget.value() if hasattr(self, 'zoom_widget') else 100
                self.zoom_values[current_filename] = (self.zoom_mode, current_zoom_value)
                
                # 复制缩放和滚动状态到新文件
                self._copy_view_state(current_filename, filename)
                
                # 更新文件列表的选择状态
                self.file_list_widget.setCurrentRow(current_index - 1)
                
                self.load_file(filename)

        self._config["keep_prev"] = keep_prev
        save_config(self._config)

    def open_next_image(self, _value=False, load=True):
        if not self._finalise_ongoing_drawing():
            return
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
            # 始终同步缩放状态，不依赖特定配置
            if prev_filename:
                # 保存前一个文件的缩放状态
                current_zoom_value = self.zoom_widget.value() if hasattr(self, 'zoom_widget') else 100
                self.zoom_values[prev_filename] = (self.zoom_mode, current_zoom_value)
                
                # 复制缩放和滚动状态到新文件
                self._copy_view_state(prev_filename, self.filename)
            
            # 更新文件列表的选择状态
            new_index = self.image_list.index(self.filename)
            self.file_list_widget.setCurrentRow(new_index)
            
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
                # 与前后切图保持一致：同步前一张的视图状态
                if self.filename:
                    try:
                        current_zoom_value = self.zoom_widget.value() if hasattr(self, 'zoom_widget') else 100
                        self.zoom_values[self.filename] = (self.zoom_mode, current_zoom_value)
                        self._copy_view_state(self.filename, filename)
                    except Exception:
                        pass
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
        # 使用 _label_path_for_image 获取默认建议路径
        default_labelfile_name = self._label_path_for_image(self.filename)
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
        #if self.filename.lower().endswith(".json"):
        #    label_file = self.filename
        #else:
        #    label_file = osp.splitext(self.filename)[0] + ".json"
            #label_file = self._label_path_for_image(self.filename)
        #else:
        #       label_file = osp.splitext(self.filename)[0] + ".json"
        if self.filename.lower().endswith(".json"):
            return self.filename
        return self._label_path_for_image(self.filename)     
        
    def _resolve_label_output_path(self, target_path: str) -> str:
        """Resolve a label file path against the configured output directory."""
        if not target_path:
            return target_path
        norm_target = osp.normpath(target_path)
        if not self.output_dir:
            return norm_target
        # 优先维持与 last_open_dir 的相对目录结构，避免重名覆盖
        if self.last_open_dir:
            try:
                rel = osp.relpath(norm_target, self.last_open_dir)
            except Exception:
                rel = osp.basename(norm_target)
        else:
            rel = osp.basename(norm_target)
        return osp.normpath(osp.join(self.output_dir, rel))

    def _standard_label_path_for_image(self, image_path: str) -> str:
        base_json = osp.splitext(image_path)[0] + LabelFile.suffix
        return self._resolve_label_output_path(base_json)

    def _folder_label_path_for_image(self, image_path: str) -> str:
        folder_path = osp.dirname(image_path)
        folder_name = osp.basename(folder_path.rstrip(os.sep)) or osp.basename(folder_path) or "root"
        base_json = osp.join(folder_path, f"{folder_name}{LabelFile.suffix}")
        return self._resolve_label_output_path(base_json)

    def _should_use_folder_label_file(self, image_path: str) -> bool:
        """Determine whether the current image should use a shared folder label file."""
        if not image_path:
            return False
        if bool(getattr(self, "sync_pplxpl", False)):
            return True
        folder_label = self._folder_label_path_for_image(image_path)
        default_label = self._standard_label_path_for_image(image_path)
        # 如果存在共享 JSON 且单图 JSON 不存在，推断为共享模式（兼容旧数据）
        try:
            if osp.exists(folder_label) and not osp.exists(default_label):
                return True
        except Exception:
            pass
        return False

    def _label_path_for_image(self, image_path: str) -> str:
        """
        返回图像对应的标注 JSON 路径：
        - 未设置 output_dir: 使用 <image_path>.json
        - 设置了 output_dir: 在 output_dir 下镜像原有的子目录结构，避免重名覆盖
        - 开启文件夹标签同步: 同一文件夹共用 <folder_name>.json
        """
        if not image_path:
            return ""
        if self._should_use_folder_label_file(image_path):
            return self._folder_label_path_for_image(image_path)
        return self._standard_label_path_for_image(image_path)

    def _find_legacy_folder_label_files(self, folder_path: str, target_label_path: str) -> list:
        """Locate legacy per-image JSON files within a folder for migration."""
        if not folder_path:
            return []
        folder_norm = osp.normpath(folder_path)
        folder_name = osp.basename(folder_norm.rstrip(os.sep)) or osp.basename(folder_norm) or "root"
        target_norm = osp.normpath(target_label_path) if target_label_path else None
        legacy_paths = set()

        search_dirs = {folder_norm}
        if self.output_dir:
            try:
                if self.last_open_dir:
                    rel = osp.relpath(folder_norm, self.last_open_dir)
                    search_dirs.add(osp.normpath(osp.join(self.output_dir, rel)))
                else:
                    search_dirs.add(osp.normpath(self.output_dir))
            except Exception:
                search_dirs.add(osp.normpath(self.output_dir))

        for dir_path in list(search_dirs):
            if not dir_path or not osp.isdir(dir_path):
                continue
            try:
                for entry in os.listdir(dir_path):
                    if not entry.lower().endswith(".json"):
                        continue
                    full_path = osp.normpath(osp.join(dir_path, entry))
                    if target_norm and full_path == target_norm:
                        continue
                    base = osp.splitext(entry)[0]
                    if base.startswith(f"{folder_name}_"):
                        legacy_paths.add(full_path)
            except Exception as e:
                logger.debug("扫描旧标注目录失败 %s: %s", dir_path, e)

        for img_path in getattr(self, "image_list", []) or []:
            try:
                if osp.dirname(img_path) != folder_norm:
                    continue
            except Exception:
                continue
            try:
                legacy_path = self._standard_label_path_for_image(img_path)
            except Exception:
                continue
            if not legacy_path:
                continue
            legacy_norm = osp.normpath(legacy_path)
            if target_norm and legacy_norm == target_norm:
                continue
            if osp.exists(legacy_norm):
                legacy_paths.add(legacy_norm)

        return sorted(legacy_paths)

    def _maybe_migrate_legacy_folder_annotations(self, image_path: str):
        """Prompt and migrate legacy per-image JSON files to the new folder-level JSON."""
        if not getattr(self, "sync_pplxpl", False):
            return
        if not image_path:
            return
        folder_path = osp.dirname(image_path)
        if not folder_path:
            return
        folder_norm = osp.normpath(folder_path)
        if folder_norm in self._legacy_migrated_folders:
            return

        try:
            target_label_path = self._folder_label_path_for_image(image_path)
        except Exception:
            target_label_path = ""

        if target_label_path and osp.exists(target_label_path):
            self._legacy_migrated_folders.add(folder_norm)
            return

        legacy_paths = self._find_legacy_folder_label_files(folder_path, target_label_path)
        legacy_paths = [p for p in legacy_paths if osp.exists(p)]
        if not legacy_paths:
            self._legacy_migrated_folders.add(folder_norm)
            return

        folder_name = osp.basename(folder_norm.rstrip(os.sep)) or osp.basename(folder_norm) or "root"
        preview = "\n".join(f"• {osp.basename(p)}" for p in legacy_paths[:5])
        if len(legacy_paths) > 5:
            preview += "\n..."
        msg = self.tr(
            "检测到旧格式的文件夹同步标注：\n{files}\n\n是否将它们合并为新的“{target}”文件？\n"
            "此操作会复制标注内容到新文件并删除旧的 JSON。"
        ).format(files=preview, target=f"{folder_name}.json")

        reply = QtWidgets.QMessageBox.question(
            self,
            self.tr("迁移文件夹标注"),
            msg,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            # 用户拒绝转换，自动关闭文件夹同步功能避免数据不一致
            self.sync_pplxpl = False
            self._config["pplxpl_sync"] = False
            save_config(self._config)
            try:
                self.actions.toggle_pplxpl_sync_mode.setChecked(False)
            except Exception:
                pass
            self.status(self.tr("已取消文件夹同步，保留原有标注文件。"))
            return

        legacy_source_path = legacy_paths[0]
        try:
            legacy_label = LabelFile(legacy_source_path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                self.tr("迁移失败"),
                self.tr("无法读取旧标注文件 “{0}”：\n{1}").format(legacy_source_path, str(e)),
            )
            logger.error("读取旧标注文件失败: %s", e)
            return

        shapes_payload = []
        for shape in legacy_label.shapes:
            shape_dict = dict(shape)
            other = shape_dict.get("other_data")
            if not isinstance(other, dict):
                other = {}
            other.setdefault("pplxpl_sync", True)
            other.setdefault("source", "legacy")
            shape_dict["other_data"] = other
            shapes_payload.append(shape_dict)

        flags_payload = getattr(legacy_label, "flags", {}) or {}
        other_data = dict(getattr(legacy_label, "other_data", {}) or {})
        other_data["folderSync"] = True

        if not target_label_path:
            target_label_path = self._folder_label_path_for_image(image_path)
        target_norm = osp.normpath(target_label_path)
        target_dir = osp.dirname(target_norm)
        if target_dir and not osp.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self,
                    self.tr("迁移失败"),
                    self.tr("无法创建新标注目录：{0}\n错误：{1}").format(target_dir, str(e)),
                )
                return

        label_file = LabelFile()
        label_file.image_labels = legacy_label.image_labels
        try:
            label_file.save(
                filename=target_norm,
                shapes=shapes_payload,
                image_path=FOLDER_SYNC_SENTINEL,
                image_data=None,
                image_height=None,
                image_width=None,
                other_data=other_data,
                flags=flags_payload,
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                self.tr("迁移失败"),
                self.tr("写入新的文件夹标注失败：\n{0}").format(str(e)),
            )
            logger.error("保存新文件夹标注失败: %s", e)
            return

        for legacy_path in legacy_paths:
            try:
                if osp.normpath(legacy_path) == target_norm:
                    continue
                os.remove(legacy_path)
            except Exception as e:
                logger.warning("删除旧标注文件失败 %s: %s", legacy_path, e)

        try:
            for img_path in getattr(self, "image_list", []) or []:
                if osp.dirname(img_path) != folder_norm:
                    continue
                items = self.file_list_widget.findItems(img_path, Qt.MatchExactly)
                if items:
                    items[0].setCheckState(Qt.Checked)
        except Exception:
            pass

        self._legacy_migrated_folders.add(folder_norm)
        QtWidgets.QMessageBox.information(
            self,
            self.tr("迁移完成"),
            self.tr("已将旧标注合并为“{0}.json”，并删除原有文件。").format(folder_name),
        )
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

            try:
                norm_removed = osp.normpath(label_file)
                for idx in range(self.file_list_widget.count()):
                    item = self.file_list_widget.item(idx)
                    if not item:
                        continue
                    item_path = item.text()
                    try:
                        target_label = self._label_path_for_image(item_path)
                        if target_label and osp.normpath(target_label) == norm_removed:
                            item.setCheckState(Qt.Unchecked)
                    except Exception:
                        continue
            except Exception:
                pass

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
        # 优先静默自动保存，避免弹窗打断
        try:
            if getattr(self, 'image_path', None):
                label_file = self._label_path_for_image(self.image_path)
                if self.save_labels(label_file):
                    return True
        except Exception:
            pass
        # 回退：仅当自动保存失败时，才提示用户
        mb = QtWidgets.QMessageBox
        msg = self.tr(f'在关闭前，是否保存当前标注到 “{self.filename!r}”？')
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
        # 当开启同步时，将当前已存在的标注对象标记为可同步，
        # 并立即执行一次同步，避免“开启后第一次无效”的困惑
        try:
            if self.sync_pplxpl:
                for item in self.label_list:
                    s = item.shape()
                    if isinstance(getattr(s, 'other_data', None), dict):
                        s.other_data["pplxpl_sync"] = True
                if getattr(self, 'image_path', None):
                    try:
                        self._maybe_migrate_legacy_folder_annotations(self.image_path)
                    except Exception as e:
                        logger.warning("迁移旧文件夹标注失败: %s", e)
                # 立即同步一次（避免在加载/切换中触发）
                if (not getattr(self, '_suppress_sync', False)
                        and not getattr(self, '_is_loading', False)
                        and getattr(self, 'image_path', None)):
                    self.sync_annotations_to_folder()
        except Exception:
            # 保守降级，不影响主流程
            pass
        # 状态提示
        try:
            self.status(self.tr("文件夹标签同步: {}" ).format(self.tr("开启") if self.sync_pplxpl else self.tr("关闭")))
        except Exception:
            pass
        
    def _copy_view_state(self, src, dst):
        """Copy zoom and scroll state from src file to dst file."""
        if src in self.zoom_values:
            self.zoom_values[dst] = self.zoom_values[src]
        for orientation in self.scroll_values:
            if src in self.scroll_values[orientation]:
                self.scroll_values[orientation][dst] = self.scroll_values[orientation][
                    src
                ]
                
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

    # def remove_selected_point(self):
    #     self.canvas.remove_selected_point()
    #     self.canvas.update()
    #     if self.canvas.h_hape is not None and not self.canvas.h_hape.points:
    #         self.canvas.delete_shape(self.canvas.h_hape)
    #         self.remove_labels([self.canvas.h_hape])
    #         self.set_dirty()
    #         if self.no_shape():
    #             for act in self.actions.on_shapes_present:
    #                 act.setEnabled(False)
    # def _cache_pplxpl_result(self, cache_key, pixmap):
    #     """Cache PPL-XPL overlay result with size limit."""
    #     # 如果缓存已满，移除最旧的条目
    #     if len(self._pplxpl_cache) >= self._pplxpl_cache_max_size:
    #         # 移除第一个条目（FIFO）
    #         oldest_key = next(iter(self._pplxpl_cache))
    #         del self._pplxpl_cache[oldest_key]
        
    #     self._pplxpl_cache[cache_key] = pixmap

    def _get_current_shapes_and_flags(self):
        """Return current shapes and flags formatted for saving."""

        def format_shape(s):
            data = s.other_data.copy()
            # 与 save_labels 保持一致：基于当前标签集决定使用 label 或 labels
            primary = s.primary_label
            use_single_label = getattr(self, 'current_label_set_name', None) in [
                "砂岩",
                "砂岩铸体孔隙",
                "碳酸盐岩",
            ]
            payload = {
                "text": s.text,
                "points": [(p.x(), p.y()) for p in s.points],
                "group_id": s.group_id,
                "shape_type": s.shape_type,
                "flags": s.flags,
            }
            if use_single_label:
                payload["label"] = primary
            else:
                payload["labels"] = s.labels
            data.update(payload)
            return data

        shapes = []
        # 记录所有“可候选同步”的形状，用于兜底（当没有任何 shape 打标时）
        eligible_shapes = []
        has_flagged_shapes = False
        auto_prompt_labels = {
            AutoLabelingMode.ADD,
            AutoLabelingMode.REMOVE,
        }

        for item in self.label_list:
            s = item.shape()
            # 排除自动标注的特殊形状
            if s.primary_label in auto_prompt_labels:
                continue
            # 加入候选集合（非自动标注形状）
            eligible_shapes.append(s)
            # 优先仅同步带有 pplxpl_sync 标记的形状
            try:
                should_sync = False
                if isinstance(getattr(s, 'other_data', None), dict):
                    should_sync = bool(s.other_data.get("pplxpl_sync", False))
                if should_sync:
                    has_flagged_shapes = True
                    shapes.append(format_shape(s))
            except Exception:
                # 保守降级：忽略异常，但不影响其它形状
                continue
        # 兜底策略：当开启同步但没有任何形状被打标时，同步全部候选形状
        if not shapes and getattr(self, 'sync_pplxpl', False) and eligible_shapes:
            shapes = [format_shape(s) for s in eligible_shapes]
        # 完全禁用标志框保存功能
        flags = {}
        # for i in range(self.flag_widget.count()):
        #     item = self.flag_widget.item(i)
        #     key = item.text()
        #     flag = item.checkState() == Qt.Checked
        #     flags[key] = flag
        return shapes, flags

    def sync_annotations_to_folder(self):
        # 新增：导入/切换文件夹期间禁止同步
        if getattr(self, '_suppress_sync', False) or getattr(self, '_is_loading', False):
            return
        if not self.sync_pplxpl or not self.image_list or not getattr(self, 'image_path', None):
            return

        shapes, flags = self._get_current_shapes_and_flags()
        # 没有需要同步的形状则直接返回，避免清空其它图片的标注
        if not shapes:
            return

        try:
            current_label_path = self._label_path_for_image(self.image_path)
            folder_label_path = self._folder_label_path_for_image(self.image_path)
            using_folder_label = (
                current_label_path
                and folder_label_path
                and osp.normpath(current_label_path) == osp.normpath(folder_label_path)
            )
        except Exception:
            current_label_path = None
            using_folder_label = False

        if using_folder_label and current_label_path:
            label_dir = osp.dirname(current_label_path)
            if label_dir and not osp.exists(label_dir):
                os.makedirs(label_dir, exist_ok=True)
            self.other_data["folderSync"] = True

            image_height = None
            image_width = None
            try:
                if hasattr(self, "image") and self.image is not None and not self.image.isNull():
                    image_height = self.image.height()
                    image_width = self.image.width()
            except Exception:
                image_height = None
                image_width = None

            label_file = LabelFile()
            label_file.image_labels = self.other_data.get("image_labels", [])
            other_data = dict(self.other_data)
            label_file.save(
                filename=current_label_path,
                shapes=shapes,
                image_path=FOLDER_SYNC_SENTINEL,
                image_data=None,
                image_height=image_height,
                image_width=image_width,
                other_data=other_data,
                flags=flags,
            )
            # 将同一 JSON 的所有文件标记为已同步
            for img in self.image_list:
                try:
                    if not img:
                        continue
                    label_path = self._label_path_for_image(img)
                    if (
                        label_path
                        and osp.normpath(label_path) == osp.normpath(current_label_path)
                    ):
                        items = self.file_list_widget.findItems(img, Qt.MatchExactly)
                        if items:
                            items[0].setCheckState(Qt.Checked)
                except Exception:
                    continue
            return

        # 避免覆盖当前图片：仅对其它图片写入同步结果
        try:
            current_img_path = osp.abspath(self.image_path) if self.image_path else None
        except Exception:
            current_img_path = None

        for img in self.image_list:
            try:
                if current_img_path and osp.abspath(img) == current_img_path:
                    continue
            except Exception:
                pass
            label_path = self._label_path_for_image(img)
            label_dir = osp.dirname(label_path)
            if label_dir and not osp.exists(label_dir):
                os.makedirs(label_dir, exist_ok=True)
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

            other_data = dict(self.other_data)
            other_data.pop("folderSync", None)
            label_file = LabelFile()
            label_file.image_labels = self.other_data.get("image_labels", [])
            label_file.save(
                filename=label_path,
                shapes=shapes,
                image_path=osp.relpath(img, osp.dirname(label_path)),
                image_data=img_data,
                image_height=image_height,
                image_width=image_width,
                other_data=other_data,
                flags=flags,
            )


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
        # 在删除前强制同步选择状态，确保从标签列表的选择同步到画布
        self._no_selection_slot = True
        selected_shapes = []
        for item in self.label_list.selected_items():
            selected_shapes.append(item.shape())
        if selected_shapes:
            self.canvas.select_shapes(selected_shapes)
        else:
            self.canvas.deselect_shape()
        self._no_selection_slot = False
        
        yes, no = QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No
        msg = self.tr(
            "You are about to permanently delete {} polygons, proceed anyway?"
        ).format(len(self.canvas.selected_shapes))
        if yes == QtWidgets.QMessageBox.warning(
            self, self.tr("Attention"), msg, yes | no, yes
        ):
            # 兜底：删除前关闭圈选模式，避免残留圈选叠加层
            try:
                self.canvas.set_circle_selection_mode(False)
            except Exception:
                pass
            self.remove_labels(self.canvas.delete_selected())
            self.set_dirty()
            
            # 确保删除后正确清理所有选择状态
            self._no_selection_slot = True
            self.label_list.clearSelection()
            self._no_selection_slot = False
            
            # 清空统计面板选择并抑制下一次自动选择，避免删除后重新选中同标签对象
            try:
                if hasattr(self, 'stats_detail_list') and self.stats_detail_list is not None:
                    self.stats_detail_list.blockSignals(True)
                    self.stats_detail_list.clearSelection()
                    self.stats_detail_list.blockSignals(False)
                self._stats_persist_selected_labels = set()
                self._canvas_persist_selected_sigs = []
                self._selection_from_stats = False
                self._suppress_stats_autoselect_once = True
            except Exception:
                pass
            # ✅ 更新统计信息
            self.update_statistics()
            if self.no_shape():
                for act in self.actions.on_shapes_present:
                    act.setEnabled(False)

    def batch_delete_shapes(self):
        """批量删除选中的形状"""
        if not self.canvas.selected_shapes:
            QtWidgets.QMessageBox.information(
                self, 
                self.tr("提示"), 
                self.tr("请先选择要删除的标注对象")
            )
            return
            
        yes, no = QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No
        msg = self.tr(
            "您将永久删除 {} 个标注对象，是否继续？"
        ).format(len(self.canvas.selected_shapes))
        
        if yes == QtWidgets.QMessageBox.warning(
            self, self.tr("批量删除确认"), msg, yes | no, no
        ):
            # ✅ 优化的批量删除流程
            shapes_count = len(self.canvas.selected_shapes)
            
            # 创建进度对话框（当删除对象较多时）
            progress = None
            if shapes_count > 10:
                progress = QtWidgets.QProgressDialog(
                    self.tr("正在删除对象..."), 
                    self.tr("取消"), 
                    0, 100, self
                )
                progress.setWindowModality(QtCore.Qt.WindowModal)
                progress.setValue(20)
                progress.show()
                QtWidgets.QApplication.processEvents()
            
            try:
                # 兜底：删除前关闭圈选模式，避免残留圈选叠加层
                try:
                    self.canvas.set_circle_selection_mode(False)
                except Exception:
                    pass
                # 删除选中的形状
                deleted_shapes = self.canvas.delete_selected()
                
                if progress:
                    progress.setValue(60)
                    QtWidgets.QApplication.processEvents()
                
                self.remove_labels(deleted_shapes)
                
                if progress:
                    progress.setValue(80)
                    QtWidgets.QApplication.processEvents()
                
                self.set_dirty()
                # 清空统计面板选择并抑制下一次自动选择，避免删除后重新选中同标签对象
                try:
                    if hasattr(self, 'stats_detail_list') and self.stats_detail_list is not None:
                        self.stats_detail_list.blockSignals(True)
                        self.stats_detail_list.clearSelection()
                        self.stats_detail_list.blockSignals(False)
                    self._stats_persist_selected_labels = set()
                    self._canvas_persist_selected_sigs = []
                    self._selection_from_stats = False
                    self._suppress_stats_autoselect_once = True
                except Exception:
                    pass
                self.update_statistics()
                # 最终兜底：直接重新加载当前图像，确保所有状态一致
                try:
                    if self.image_path:
                        self.load_file(self.image_path)
                except Exception:
                    pass
                
                # 清除选择状态
                self.label_list.clearSelection()
                
                if progress:
                    progress.setValue(100)
                    QtWidgets.QApplication.processEvents()
                
            finally:
                if progress:
                    progress.close()
            
            # 显示删除结果
            QtWidgets.QMessageBox.information(
                self, 
                self.tr("删除完成"), 
                self.tr("已删除 {} 个标注对象").format(len(deleted_shapes))
            )
            
            if self.no_shape():
                for act in self.actions.on_shapes_present:
                    act.setEnabled(False)

    def batch_set_labels(self):
        """批量设置标签"""
        if not self.canvas.selected_shapes:
            QtWidgets.QMessageBox.information(
                self, 
                self.tr("提示"), 
                self.tr("请先选择要设置标签的标注对象")
            )
            return
        
        # 弹出标签选择对话框
        text, flags, group_id = self.label_dialog.pop_up(
            text="",
            flags={},
            group_id=None,
        )
        
        # 允许仅设置群组编号
        if (text is None or not text.strip()) and group_id is None:
            return
        
        # 解析标签
        labels = [t.strip() for t in text.split(",") if t.strip()]
        
        # 验证标签
        for lb in labels:
            if not self.validate_label(lb):
                self.error_message(
                    self.tr("无效标签"),
                    self.tr("标签 '{}' 无效").format(lb),
                )
                return
        
        # 批量设置标签
        count = 0
        for shape in self.canvas.selected_shapes:
            if shape:
                if labels:
                    shape.labels = labels
                shape.flags = flags
                if group_id is not None:
                    shape.group_id = group_id
                
                # 添加到标签历史
                for lb in labels:
                    self.label_dialog.add_label_history(lb)
                
                # 更新唯一标签列表
                for lb in labels:
                    if not self.unique_label_list.find_items_by_label(lb):
                        unique_label_item = self.unique_label_list.create_item_from_label(lb)
                        self.unique_label_list.addItem(unique_label_item)
                        rgb = self._get_rgb_by_label(lb)
                        self.unique_label_list.set_item_label(unique_label_item, lb, rgb)
                
                # 更新颜色
                self._update_shape_color(shape)
                count += 1
        
        # 更新显示
        self.label_list.clear()
        for shape in self.canvas.shapes:
            self.add_label(shape)
        
        self.set_dirty()
        self.update_statistics()
        
        # 显示设置结果
        QtWidgets.QMessageBox.information(
            self, 
            self.tr("批量标签设置完成"), 
            self.tr("已为 {} 个对象设置标签: {}").format(count, text)
        )

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
            # 清理文件路径中的换行符和特殊字符
            clean_file = file.replace('\n', '').replace('\r', '').strip() if isinstance(file, str) else str(file).replace('\n', '').replace('\r', '').strip()
            
            if clean_file in self.image_list or not clean_file.lower().endswith(tuple(extensions)):
                continue
            # label_file = osp.splitext(clean_file)[0] + ".json"
            # label_file = self._label_path_for_image(clean_file)
            # if self.output_dir:
            #     label_file_without_path = osp.basename(label_file)
            #     label_file = osp.join(self.output_dir, label_file_without_path)
            label_file = self._label_path_for_image(clean_file)
            item = QtWidgets.QListWidgetItem(clean_file)
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

        # 先彻底清空状态，并在导入期间屏蔽同步与保存
        try:
            self.reset_state()
            self.set_clean()
            self.canvas.setEnabled(False)
        except Exception:
            pass
        self._suppress_sync = True
        self._is_loading = True

        self.last_open_dir = dirpath
        self.filename = None
        self.file_list_widget.clear()
        for filename in self.scan_all_images(dirpath):
            if pattern and pattern not in filename:
                continue
            # 清理文件路径中的换行符和特殊字符
            clean_filename = filename.replace('\n', '').replace('\r', '').strip() if isinstance(filename, str) else str(filename).replace('\n', '').replace('\r', '').strip()
            label_file = self._label_path_for_image(clean_filename)
            item = QtWidgets.QListWidgetItem(clean_filename)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if QtCore.QFile.exists(label_file) and LabelFile.is_label_file(label_file):
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self.file_list_widget.addItem(item)
        try:
            self.open_next_image(load=load)
        finally:
            # 导入完成后取消屏蔽
            self._is_loading = False
            self._suppress_sync = False

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

    def toggle_hsv_widget(self):
        """Toggle hsv widget visibility."""
        if QtCore.QFile.exists(str(self.filename)) and osp.exists(self.filename):
            dialog = PoreMaskExtractorDialog(img_p=self.filename)
            dialog.exec_()
            self.load_file(self.filename)
        else:
            dialog = PoreMaskExtractorDialog()
            dialog.exec_()
    @pyqtSlot()
    def new_shapes_from_auto_labeling(self, auto_labeling_result):
        """Apply auto labeling results to the current image."""
        if not self.image or not self.image_path:
            return
        new_shapes = list(auto_labeling_result.shapes or [])
        if new_shapes:
            sync_flag = bool(getattr(self, "sync_pplxpl", False))
            for shape in new_shapes:
                try:
                    if not isinstance(getattr(shape, "other_data", None), dict):
                        shape.other_data = {}
                    shape.other_data["pplxpl_sync"] = sync_flag
                    shape.other_data.setdefault("source", "auto_prompt")
                except Exception:
                    pass
        # Clear existing shapes
        if auto_labeling_result.replace:
            self.load_shapes([], replace=True)
            self.label_list.clear()
            self.load_shapes(new_shapes, replace=True)
        else:  # Just update existing shapes
            # 与官方一致：仅移除旧的自动标注对象（OBJECT），保留提示点/矩形
            if new_shapes:
                for shape in list(self.canvas.shapes):
                    if shape.label == AutoLabelingMode.OBJECT:
                        try:
                            item = self.label_list.find_item_by_shape(shape)
                            self.label_list.remove_item(item)
                        except Exception:
                            pass
                        try:
                            self.canvas.shapes.remove(shape)
                        except ValueError:
                            pass
                # 添加新的自动标注结果
                self.load_shapes(new_shapes, replace=False)

        self.set_dirty()

    def clear_auto_labeling_marks(self, preserve_objects=False):
        """Clear auto labeling marks from the current image.

        Parameters
        ----------
        preserve_objects : bool
            When True, only remove prompt markers (ADD/REMOVE) while keeping the
            generated object polygons so they can coexist with手绘标注.
        """
        removable_labels = {
            AutoLabelingMode.ADD,
            AutoLabelingMode.REMOVE,
        }
        if not preserve_objects:
            removable_labels.add(AutoLabelingMode.OBJECT)

        # Clean up label list
        for shape in list(self.canvas.shapes):
            if shape.label in removable_labels:
                try:
                    item = self.label_list.find_item_by_shape(shape)
                    self.label_list.remove_item(item)
                except ValueError:
                    pass

        # Clean up unique label list entries related to auto-label markers
        for shape_label in removable_labels:
            for item in list(self.unique_label_list.find_items_by_label(shape_label)):
                self.unique_label_list.takeItem(self.unique_label_list.row(item))

        # Remove shapes from the canvas
        self.canvas.shapes = [
            shape for shape in self.canvas.shapes if shape.label not in removable_labels
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
            return shape.label

        # Get the last label from the label list
        for item in reversed(self.label_list):
            shape = item.data(Qt.UserRole)
            if shape.label not in [
                AutoLabelingMode.OBJECT,
                AutoLabelingMode.ADD,
                AutoLabelingMode.REMOVE,
            ]:
                return shape.label

        # No label is found
        return ""

    def finish_auto_labeling_object(self):
        """Finish auto labeling object."""
        has_object = any(
            shape.label == AutoLabelingMode.OBJECT for shape in self.canvas.shapes
        )

        # If there is no object, do nothing
        if not has_object:
            return

        # Ask a label for the object
        text, flags, group_id = "", {}, None
        last_label = self.find_last_label()
        if self._config["auto_use_last_label"] and last_label:
            text = last_label
        else:
            # 兼容 QTextEdit 和 QLineEdit
            if hasattr(self.label_dialog.edit, 'toPlainText'):
                previous_text = self.label_dialog.edit.toPlainText()  # QTextEdit
            else:
                previous_text = self.label_dialog.edit.text()  # QLineEdit
                
            text, flags, group_id = self.label_dialog.pop_up(
                text="",  # ✅ 自动标注时始终从空白状态开始
                flags={},
                group_id=None,
            )
            if not text and group_id is None:
                # 兼容 QTextEdit 和 QLineEdit
                if hasattr(self.label_dialog.edit, 'setPlainText'):
                    self.label_dialog.edit.setPlainText(previous_text)  # QTextEdit
                else:
                    self.label_dialog.edit.setText(previous_text)  # QLineEdit
                return

        if text:
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
            if shape.label == AutoLabelingMode.OBJECT:
                updated_shapes = True
                if text:
                    shape.label = text
                shape.flags = flags
                shape.group_id = group_id
                # Update unique label list
                for lb in shape.labels:
                    if not self.unique_label_list.find_items_by_label(lb):
                        unique_label_item = self.unique_label_list.create_item_from_label(lb)
                        self.unique_label_list.addItem(unique_label_item)
                        rgb = self._get_rgb_by_label(lb)
                        self.unique_label_list.set_item_label(
                            unique_label_item, lb, rgb
                        )

                # Update label list
                self._update_shape_color(shape)
                try:
                    item = self.label_list.find_item_by_shape(shape)
                    if shape.group_id is None:
                        color = shape.fill_color.getRgb()[:3]
                        item.setText(
                            '{} <font color="#{:02x}{:02x}{:02x}">●</font>'.format(
                                html.escape(shape.label), *color
                            )
                        )
                    else:
                        item.setText(f"{shape.label}")
                except ValueError:
                    # Shape not found in label list, skip silently
                    pass

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
            # 拖拽顶点/形状时不切换模式、不弹提示，保持当前编辑上下文
            try:
                if getattr(self.canvas, 'moving_shape', False):
                    return
                if hasattr(self.canvas, 'selected_vertex') and self.canvas.selected_vertex():
                    return
            except Exception:
                pass
            # Enable text editing for object or image depending on selection count
            selected_count = len(self.canvas.selected_shapes)
            if selected_count >= 1:
                # 对象文本（支持多选）：标题统一为 Object Text
                self.shape_text_label.setText(self.tr("Object Text"))
                self.shape_text_edit.textChanged.disconnect()
                if selected_count == 1:
                    base_text = self.canvas.selected_shapes[0].text
                else:
                    texts = [str(getattr(s, 'text', '') or '') for s in self.canvas.selected_shapes]
                    first_text = texts[0] if texts else ''
                    base_text = first_text if all(t == first_text for t in texts) else ''
                self.shape_text_edit.setPlainText(base_text)
                # 同步右侧复选框为该对象文本内容（若多选且不一致，则以空文本为准）
                if hasattr(self, 'tag_checkboxes'):
                    for cb in self.tag_checkboxes:
                        cb.blockSignals(True)
                self.sync_checkboxes_with_text(base_text)
                if hasattr(self, 'tag_checkboxes'):
                    for cb in self.tag_checkboxes:
                        cb.blockSignals(False)
                self.shape_text_edit.textChanged.connect(self.shape_text_changed)
                # 模式切换到“对象文本”时提示（统一文案，不区分多选）
                try:
                    previous_mode = getattr(self, '_last_text_edit_mode', None)
                    if previous_mode != 'object':
                        self._show_mode_hint(self.tr("当前为对象文本编辑模式：将修改选中对象的文本。"))
                    self._last_text_edit_mode = 'object'
                except Exception:
                    pass
            else:
                self.shape_text_label.setText(self.tr("Image Text"))
                self.shape_text_edit.textChanged.disconnect()
                image_text = self.other_data.get("image_text", "")
                self.shape_text_edit.setPlainText(image_text)
                # 根据图像文本同步复选框
                if hasattr(self, 'tag_checkboxes'):
                    for cb in self.tag_checkboxes:
                        cb.blockSignals(True)
                self.sync_checkboxes_with_text(image_text)
                if hasattr(self, 'tag_checkboxes'):
                    for cb in self.tag_checkboxes:
                        cb.blockSignals(False)
                self.shape_text_edit.textChanged.connect(self.shape_text_changed)
                # 模式切换到“图像文本”时提示
                try:
                    previous_mode = getattr(self, '_last_text_edit_mode', None)
                    if previous_mode != 'image':
                        self._show_mode_hint(self.tr("当前为图像文本编辑模式：将影响整张图像的文本。"))
                    self._last_text_edit_mode = 'image'
                except Exception:
                    pass
            self.shape_text_edit.setDisabled(False)
        else:
            self.shape_text_edit.setDisabled(True)
            self.shape_text_label.setText(
                self.tr("Switch to Edit mode for text editing")
            )
            # 退出编辑时重置模式，便于下次进入时再次提示
            try:
                self._last_text_edit_mode = None
            except Exception:
                pass
            self.shape_text_edit.textChanged.disconnect()
            self.shape_text_edit.setPlainText("")
            self.shape_text_edit.textChanged.connect(self.shape_text_changed)

    def _show_mode_hint(self, message):
        """在屏幕中间显示非阻塞轻提示，自动淡出。"""
        # 圈选模式下抑制模式提示，避免遮挡对话框
        try:
            if getattr(self, '_suppress_mode_hint', False) or getattr(self.canvas, 'create_mode', '') == 'circle_select':
                return
            # 若已有提示标签，先移除
            if hasattr(self, '_mode_hint_label') and self._mode_hint_label is not None:
                self._mode_hint_label.deleteLater()
                self._mode_hint_label = None

            flags = (
                QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.Tool
                | QtCore.Qt.WindowStaysOnTopHint
            )
            hint_label = QtWidgets.QLabel(None, flags)
            hint_label.setText(message)
            hint_label.setObjectName("modeHintLabel")
            hint_label.setStyleSheet(
                """
                QLabel#modeHintLabel {
                    background-color: rgba(0, 0, 0, 160);
                    color: white;
                    border-radius: 8px;
                    padding: 10px 16px;
                    font-size: 13px;
                }
                """
            )
            hint_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            hint_label.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
            hint_label.setWindowOpacity(0.96)
            hint_label.adjustSize()

            # 定位到屏幕中间（优先使用窗口所在屏幕）
            screen_geo = None
            try:
                target_window = None
                if hasattr(self, 'main_window') and self.main_window is not None:
                    target_window = self.main_window
                else:
                    target_window = self.window()
                if target_window and target_window.windowHandle() and target_window.windowHandle().screen():
                    screen_geo = target_window.windowHandle().screen().availableGeometry()
            except Exception:
                screen_geo = None
            if screen_geo is None:
                primary = QtGui.QGuiApplication.primaryScreen()
                screen_geo = primary.availableGeometry() if primary else QtCore.QRect(0, 0, 800, 600)

            size = hint_label.size()
            x = screen_geo.left() + (screen_geo.width() - size.width()) // 2
            y = screen_geo.top() + (screen_geo.height() - size.height()) // 2
            hint_label.move(x, y)
            hint_label.show()

            self._mode_hint_label = hint_label

            # 使用定时器淡出并销毁
            def _fade_and_close():
                if self._mode_hint_label is None:
                    return
                animation = QtCore.QPropertyAnimation(self._mode_hint_label, b"windowOpacity", self)
                animation.setDuration(300)
                animation.setStartValue(self._mode_hint_label.windowOpacity())
                animation.setEndValue(0.0)
                def _cleanup():
                    if self._mode_hint_label:
                        self._mode_hint_label.deleteLater()
                        self._mode_hint_label = None
                animation.finished.connect(_cleanup)
                animation.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

            QtCore.QTimer.singleShot(1400, _fade_and_close)
        except Exception:
            # 兜底：如轻提示失败，避免影响主流程
            pass

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
        # self.flag_dock.close()  # 注释掉，因为flag_dock已被禁用
        self.label_dock.close()
        self.shape_dock.close()
        self.file_dock.close()
        self.tools_dock.close()
        self.statistics_dock.close()  # ✅ 也关闭统计dock

        # Re-add them in the desired order/position
        self.main_window.addDockWidget(Qt.LeftDockWidgetArea, self.tools_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_text_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.shape_dock)
        # self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)  # 注释掉，因为flag_dock已被禁用
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)
        self.main_window.addDockWidget(Qt.RightDockWidgetArea, self.statistics_dock)

        # ✅ 移除内部main_window显示，只由外层MainWindow负责显示
        logger.info("Dock layout reset completed - no window display needed")

        # Show all docks
        self.tools_dock.show()
        self.file_dock.show()
        self.shape_dock.show()
        self.label_dock.show()
        # self.flag_dock.hide()  # 注释掉，因为flag_dock已被禁用
        self.shape_text_dock.show()
        self.statistics_dock.show()

        # Make sure tools dock is visible
        self.tools_dock.raise_()

        # Connect dock signals to save state when changed and update orientation
        self.tools_dock.dockLocationChanged.connect(self.on_tools_dock_location_changed)
        self.shape_text_dock.dockLocationChanged.connect(self.save_dock_state)
        # self.flag_dock.dockLocationChanged.connect(self.save_dock_state)  # 注释掉，因为flag_dock已被禁用
        self.label_dock.dockLocationChanged.connect(self.save_dock_state)
        self.shape_dock.dockLocationChanged.connect(self.save_dock_state)
        self.file_dock.dockLocationChanged.connect(self.save_dock_state)
        self.statistics_dock.dockLocationChanged.connect(self.save_dock_state)  # ✅ 连接统计dock信号

        # Also connect visibility changes
        self.tools_dock.visibilityChanged.connect(self.save_dock_state)
        self.shape_text_dock.visibilityChanged.connect(self.save_dock_state)
        # self.flag_dock.visibilityChanged.connect(self.save_dock_state)  # 注释掉，因为flag_dock已被禁用
        self.label_dock.visibilityChanged.connect(self.save_dock_state)
        self.shape_dock.visibilityChanged.connect(self.save_dock_state)
        self.file_dock.visibilityChanged.connect(self.save_dock_state)
        self.statistics_dock.visibilityChanged.connect(self.save_dock_state)  # ✅ 连接统计dock可见性信号

        # ✅ 简化dock尺寸设置
        base_dock_width = 250
        tools_width = 5
        stats_width = 200
        logger.info(f"Using standard dock sizes: base={base_dock_width}, stats={stats_width}")

        # Apply a workaround to ensure proper sizes
        self.main_window.resizeDocks(
            [
                self.tools_dock,
                self.shape_text_dock,
                # self.flag_dock,  # 注释掉，因为flag_dock已被禁用
                self.label_dock,
                self.shape_dock,
                self.file_dock,
                self.statistics_dock,  # ✅ 包含统计dock
            ],
            [tools_width, base_dock_width, base_dock_width, base_dock_width, base_dock_width, stats_width],
            Qt.Horizontal,
        )

        # ✅ 确保窗口最大化
        QtCore.QTimer.singleShot(50, self._ensure_safe_window_geometry)

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
        self.current_label_set_name = name  # 更新当前标签集合名称
        save_config(self._config)
        self.update_unique_label_list()
        self.update_label_dialog_labels()
        
        # ✅ 更新标签集菜单的勾选状态
        self.update_label_set_menu_checks(name)
        
        # 更新文本编辑器中的标签选择框显示状态
        self.refresh_text_editor_tags()
        self.update_text_editor_tags_visibility()
    
    def update_label_set_menu_checks(self, selected_name):
        """更新标签集菜单中的勾选状态"""
        if hasattr(self, 'label_set_actions'):
            for name, action in self.label_set_actions.items():
                action.setChecked(name == selected_name)
    
    def _detect_current_label_set(self):
        """智能检测当前正在使用的标签集合名称"""
        labels = self._config.get("labels", [])
        if labels is None:
            labels = []
        current_labels = set(labels)
        
        # 检查每个标签集，找到与当前labels匹配的
        if "label_sets" in self._config:
            for name, labels in self._config["label_sets"].items():
                # ✅ 跳过"岩浆岩结构-构造"、"火山碎屑岩-构造"、"砂岩铸体孔隙-组分"和"碳酸盐岩-构造"，不自动切换到它们
                if name in ["岩浆岩结构-构造", "火山碎屑岩-构造", "砂岩铸体孔隙-组分", "碳酸盐岩-构造"]:
                    continue
                if set(labels) == current_labels:
                    return name
        
        # 如果没有找到匹配的，返回默认值
        return "默认标签"
    
    def update_statistics(self):
        """更新标注统计信息"""
        # 在刷新列表前，记住当前与历史持久选择的标签名
        try:
            ui_selected = set()
            if hasattr(self, 'stats_detail_list') and self.stats_detail_list is not None:
                for it in self.stats_detail_list.selectedItems():
                    name = it.data(QtCore.Qt.UserRole)
                    if name:
                        ui_selected.add(str(name))
        except Exception:
            ui_selected = set()
        persisted = set(getattr(self, '_stats_persist_selected_labels', set()))
        to_reselect = set(persisted) | set(ui_selected)
        
        # 检查是否需要抑制一次自动选择（删除后避免重新选中同标签对象）
        if getattr(self, '_suppress_stats_autoselect_once', False):
            ui_selected = set()
            persisted = set()
            to_reselect = set()
            self._suppress_stats_autoselect_once = False  # 一次性标志

        if not hasattr(self, 'canvas') or not hasattr(self.canvas, 'shapes'):
            # ✅ 如果canvas还未初始化，显示空统计
            if hasattr(self, 'stats_total_label'):
                self.stats_total_label.setText(self.tr("总标注数: 0"))
                self.stats_types_label.setText(self.tr("标签种类: 0"))
                self.stats_detail_list.clear()
                self.stats_shapes_list.clear()
            # ✅ 确保label_to_shapes字典存在
            self.label_to_shapes = {}
            return
        
        shapes = self.canvas.shapes if hasattr(self.canvas, 'shapes') else []
        
        # 计算总数
        total_count = len(shapes)
        
        # 统计标签类型和数量，同时保存标签到形状的映射
        label_counts = {}
        shape_type_counts = {}
        self.label_to_shapes = {}  # ✅ 存储标签到形状对象的映射
        
        # 自动标注内部标签白名单（不展示在统计中）
        autolabel_internal = {
            "AUTOLABEL_ADD",
            "AUTOLABEL_REMOVE",
            "AUTOLABEL_OBJECT",
            "AUTOLABEL_POINT",
            "AUTOLABEL_RECTANGLE",
            "AUTOLABEL_REMOVE_POINT",
        }

        for shape in shapes:
            # 统一收集需要统计的标签：优先 labels，其次 label，最后 primary_label
            labels_to_count = []
            if hasattr(shape, 'labels') and shape.labels:
                labels_to_count = [lb.strip() for lb in shape.labels if lb and lb.strip()]
            elif hasattr(shape, 'label') and shape.label:
                if isinstance(shape.label, str):
                    labels_to_count = [shape.label.strip()] if shape.label.strip() else []
                else:
                    try:
                        labels_to_count = [str(shape.label).strip()]
                    except Exception:
                        labels_to_count = []
            elif hasattr(shape, 'primary_label') and shape.primary_label:
                labels_to_count = [shape.primary_label.strip()] if str(shape.primary_label).strip() else []

            for label in labels_to_count:
                if not label or label in autolabel_internal:
                    continue
                label_counts[label] = label_counts.get(label, 0) + 1
                if label not in self.label_to_shapes:
                    self.label_to_shapes[label] = []
                self.label_to_shapes[label].append(shape)
            
            # 统计形状类型
            if hasattr(shape, 'shape_type') and shape.shape_type:
                shape_type = shape.shape_type
                shape_type_counts[shape_type] = shape_type_counts.get(shape_type, 0) + 1
        
        # 更新UI显示
        self.stats_total_label.setText(self.tr(f"总标注数: {total_count}"))
        self.stats_types_label.setText(self.tr(f"标签种类: {len(label_counts)}"))
        
        # 更新详细标签列表，并在刷新后恢复用户的选择
        self.stats_detail_list.blockSignals(True)
        try:
            self.stats_detail_list.clear()
            for label, count in sorted(label_counts.items()):
                item_text = f"{label}: {count}个"
                item = QtWidgets.QListWidgetItem(item_text)
                # ✅ 将标签名称存储为item的数据，方便后续获取
                item.setData(QtCore.Qt.UserRole, label)
                self.stats_detail_list.addItem(item)
            # 恢复选择（仅恢复“原有UI选择”，不因持久集合而改变标签列表的勾选）
            for idx in range(self.stats_detail_list.count()):
                it = self.stats_detail_list.item(idx)
                name = it.data(QtCore.Qt.UserRole)
                if name in ui_selected:
                    it.setSelected(True)
        finally:
            self.stats_detail_list.blockSignals(False)
        
        # 更新形状类型统计
        self.stats_shapes_list.clear()
        shape_type_names = {
            'polygon': '多边形',
            'rectangle': '矩形',
            'circle': '圆形',
            'point': '点',
            'line': '线',
            'linestrip': '线条'
        }
        
        for shape_type, count in sorted(shape_type_counts.items()):
            type_name = shape_type_names.get(shape_type, shape_type)
            item_text = f"{type_name}: {count}个"
            self.stats_shapes_list.addItem(item_text)
        
        # 按持久选择的标签名 + 几何签名，静默选择当前图像中的对应对象（批量冻结以提升切图性能）
        try:
            # 基于标签名的恢复
            exists = [n for n in to_reselect if n in self.label_to_shapes] if to_reselect else []
            # 基于几何签名的恢复（与当前图像的对象做边界框近似匹配）
            match_by_sig = []
            try:
                sigs = getattr(self, '_canvas_persist_selected_sigs', []) or []
                if sigs:
                    # 预先构建当前图像所有形状的bbox索引
                    current_bboxes = []
                    for shp in self.canvas.shapes:
                        try:
                            br = shp.bounding_rect()
                            bbox = (int(br.x()), int(br.y()), int(br.width()), int(br.height()))
                            current_bboxes.append((shp, getattr(shp, 'shape_type', 'polygon'), bbox))
                        except Exception:
                            continue
                    # 容差阈值（像素）
                    tol = 3
                    for sig in sigs:
                        st, (x, y, w, h) = sig
                        for shp, st2, (x2, y2, w2, h2) in current_bboxes:
                            if st == st2 and abs(x-x2) <= tol and abs(y-y2) <= tol and abs(w-w2) <= tol and abs(h-h2) <= tol:
                                match_by_sig.append(shp)
                                break
            except Exception:
                pass

            if exists or match_by_sig:
                freeze_ok = True
                try:
                    self.setUpdatesEnabled(False)
                    self.canvas.setUpdatesEnabled(False)
                    if hasattr(self, 'label_list') and self.label_list:
                        self.label_list.begin_bulk_update()
                except Exception:
                    freeze_ok = False
                try:
                    # 先按签名精确匹配选择
                    if match_by_sig:
                        target_set = set(match_by_sig)
                        self.canvas.selected_shapes = match_by_sig[:]
                        for shape in self.canvas.shapes:
                            shape.selected = shape in target_set
                        # 同步右侧对象列表
                        try:
                            self.label_list.clearSelection()
                            for shape in match_by_sig:
                                try:
                                    item = self.label_list.find_item_by_shape(shape)
                                    if item:
                                        self.label_list.select_item(item)
                                except ValueError:
                                    pass
                        except Exception:
                            pass
                    # 若用户手动选择产生的签名为空或数量极少（例如仅标签选择），再按标签名补选
                    elif exists:
                        self._apply_stats_selection_to_canvas(exists, silent=True)
                finally:
                    try:
                        if freeze_ok and hasattr(self, 'label_list') and self.label_list:
                            self.label_list.end_bulk_update()
                        if freeze_ok:
                            self.canvas.setUpdatesEnabled(True)
                            self.setUpdatesEnabled(True)
                    except Exception:
                        pass
        except Exception:
            pass
    
    def on_statistics_selection_changed(self):
        """统计窗口中标签选择变化时的处理：同步持久选择集合"""
        try:
            selected_names = set()
            for it in self.stats_detail_list.selectedItems():
                name = it.data(QtCore.Qt.UserRole)
                if name:
                    selected_names.add(str(name))
            self._stats_persist_selected_labels = selected_names
            # 标记选择来源于统计面板
            self._selection_from_stats = True
        except Exception:
            # 出错时不影响交互
            pass

    def _apply_stats_selection_to_canvas(self, label_names, silent=True):
        """根据标签名集合选择画布对象，并同步右侧对象列表。

        参数：
        - label_names: 可迭代的标签名
        - silent: True 表示不弹出完成提示
        """
        if not label_names:
            return
        if not hasattr(self, 'label_to_shapes'):
            return
        
        # 收集目标形状
        all_shapes_to_select = []
        for name in label_names:
            shapes = self.label_to_shapes.get(name, [])
            if shapes:
                all_shapes_to_select.extend(shapes)
        if not all_shapes_to_select:
            return
        
        # 选择到画布
        target_set = set(all_shapes_to_select)
        self.canvas.selected_shapes = all_shapes_to_select[:]
        for shape in self.canvas.shapes:
            shape.selected = shape in target_set
        
        # 同步右侧对象列表
        try:
            self.label_list.clearSelection()
            for shape in all_shapes_to_select:
                try:
                    item = self.label_list.find_item_by_shape(shape)
                    if item:
                        self.label_list.select_item(item)
                except ValueError:
                    pass
        except Exception:
            pass
        
        # 发出信号并刷新
        try:
            self.canvas.selection_changed.emit(all_shapes_to_select)
            self.canvas.update()
        except Exception:
            pass
        
        if not silent:
            try:
                label_text = ", ".join(label_names)
                QtWidgets.QMessageBox.information(
                    self,
                    self.tr("选择完成"),
                    self.tr("已选择 {} 个标签为 '{}' 的对象").format(len(all_shapes_to_select), label_text)
                )
            except Exception:
                pass
    
    def on_stats_select_all_clicked(self):
        """选择全部按钮点击处理（切换全选/全不选）"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("请先在上面的标签列表中点击选择标签（Ctrl+点击可多选）")
            )
            return
        
        # 收集目标形状
        all_shapes_to_select = []
        label_names = []
        for item in selected_items:
            label_name = item.data(QtCore.Qt.UserRole)
            if label_name and hasattr(self, 'label_to_shapes'):
                shapes = self.label_to_shapes.get(label_name, [])
                if shapes:
                    all_shapes_to_select.extend(shapes)
                label_names.append(label_name)
        
        if not all_shapes_to_select:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("没有找到选中标签的对象")
            )
            return
        
        target_set = set(all_shapes_to_select)
        current_set = set(self.canvas.selected_shapes)
        
        # 如果当前选中与目标完全一致，则执行“全不选”
        if current_set == target_set and len(current_set) == len(target_set):
            freeze_ok = True
            try:
                self.setUpdatesEnabled(False)
                self.canvas.setUpdatesEnabled(False)
                if hasattr(self, 'label_list') and self.label_list:
                    self.label_list.begin_bulk_update()
            except Exception:
                freeze_ok = False
            try:
                for shape in self.canvas.shapes:
                    shape.selected = False
                self.canvas.selected_shapes = []
                try:
                    self.label_list.clearSelection()
                except Exception:
                    pass
                # 清除“标签详情”高亮与持久选择，避免下次仍然全选
                try:
                    if hasattr(self, 'stats_detail_list') and self.stats_detail_list is not None:
                        self.stats_detail_list.blockSignals(True)
                        self.stats_detail_list.clearSelection()
                        self.stats_detail_list.blockSignals(False)
                except Exception:
                    pass
                try:
                    self._stats_persist_selected_labels = set()
                    self._selection_from_stats = False
                except Exception:
                    pass
                self.canvas.selection_changed.emit([])
                self.canvas.update()
            finally:
                try:
                    if freeze_ok and hasattr(self, 'label_list') and self.label_list:
                        self.label_list.end_bulk_update()
                    if freeze_ok:
                        self.canvas.setUpdatesEnabled(True)
                        self.setUpdatesEnabled(True)
                except Exception:
                    pass
            return
        
        # 否则执行“全选”
        freeze_ok = True
        try:
            self.setUpdatesEnabled(False)
            self.canvas.setUpdatesEnabled(False)
            if hasattr(self, 'label_list') and self.label_list:
                self.label_list.begin_bulk_update()
        except Exception:
            freeze_ok = False

        try:
            # 选择到画布
            self.canvas.selected_shapes = all_shapes_to_select[:]
            selecting_all = False
            try:
                selecting_all = len(target_set) == len(self.canvas.shapes) and target_set == set(self.canvas.shapes)
            except Exception:
                selecting_all = False

            if selecting_all:
                # 快路径：全量选择
                for shape in self.canvas.shapes:
                    shape.selected = True
            else:
                for shape in self.canvas.shapes:
                    shape.selected = shape in target_set

            # 同步右侧对象列表
            try:
                self.label_list.clearSelection()
                if selecting_all:
                    # 快路径：直接全选
                    try:
                        self.label_list.selectAll()
                    except Exception:
                        # 回退逐项选择
                        for shape in all_shapes_to_select:
                            try:
                                item = self.label_list.find_item_by_shape(shape)
                                if item:
                                    self.label_list.select_item(item)
                            except ValueError:
                                pass
                else:
                    for shape in all_shapes_to_select:
                        try:
                            item = self.label_list.find_item_by_shape(shape)
                            if item:
                                self.label_list.select_item(item)
                        except ValueError:
                            pass
            except Exception:
                pass

            self.canvas.selection_changed.emit(all_shapes_to_select)
            self.canvas.update()
        finally:
            try:
                if freeze_ok and hasattr(self, 'label_list') and self.label_list:
                    self.label_list.end_bulk_update()
                if freeze_ok:
                    self.canvas.setUpdatesEnabled(True)
                    self.setUpdatesEnabled(True)
            except Exception:
                pass
        
        label_text = ", ".join(label_names)
        QtWidgets.QMessageBox.information(
            self,
            self.tr("选择完成"),
            self.tr("已选择 {} 个标签为 '{}' 的对象").format(len(all_shapes_to_select), label_text)
        )
    
    def on_stats_delete_clicked(self):
        """删除标签按钮点击处理"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("请先在上面的标签列表中点击选择标签（Ctrl+点击可多选）")
            )
            return
        
        # 支持多选
        if len(selected_items) == 1:
            # 单个标签删除
            self.statistics_delete_current_label()
        else:
            # 多个标签删除
            self.statistics_delete_multiple_labels()
    
    def on_stats_relabel_clicked(self):
        """更改标签按钮点击处理"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("请先在上面的标签列表中点击选择标签（Ctrl+点击可多选）")
            )
            return
        
        # 支持多选
        if len(selected_items) == 1:
            # 单个标签更改
            self.statistics_relabel_current_label()
        else:
            # 多个标签更改
            self.statistics_relabel_multiple_labels()

    def on_stats_show_only_clicked(self):
        """仅显示按钮点击处理（按所选标签进行过滤）"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("请先在上面的标签列表中点击选择标签（Ctrl+点击可多选）")
            )
            return
        labels = []
        for it in selected_items:
            name = it.data(QtCore.Qt.UserRole)
            if name:
                labels.append(str(name))
        if not labels:
            return
        self._apply_visibility_by_labels(labels, mode='only')
        self.status(self.tr("已仅显示所选标签"))

    def on_stats_hide_clicked(self):
        """隐藏按钮点击处理（按所选标签隐藏）"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("请先在上面的标签列表中点击选择标签（Ctrl+点击可多选）")
            )
            return
        labels = []
        for it in selected_items:
            name = it.data(QtCore.Qt.UserRole)
            if name:
                labels.append(str(name))
        if not labels:
            return
        self._apply_visibility_by_labels(labels, mode='hide')
        self.status(self.tr("已隐藏所选标签"))

    def on_stats_clear_filter_clicked(self):
        """清除过滤，恢复全部显示"""
        try:
            # 批量更新，提高效率
            if hasattr(self, 'label_list') and self.label_list is not None:
                self.label_list.begin_bulk_update()
            for item in self.label_list:
                shp = item.shape()
                item.setCheckState(Qt.Checked)
                try:
                    if hasattr(self, 'canvas'):
                        self.canvas.set_shape_visible(shp, True)
                except Exception:
                    pass
        finally:
            try:
                if hasattr(self, 'label_list') and self.label_list is not None:
                    self.label_list.end_bulk_update()
            except Exception:
                pass
        try:
            if hasattr(self, 'canvas'):
                self.canvas.update()
        except Exception:
            pass
        self.status(self.tr("已清除过滤，显示全部标签"))
    
    def on_statistics_label_double_clicked(self, item):
        """双击标签时选择所有该标签的对象"""
        if not item:
            return
        
        label_name = item.data(QtCore.Qt.UserRole)
        if label_name and hasattr(self, 'label_to_shapes'):
            shapes_to_select = self.label_to_shapes.get(label_name, [])
            if shapes_to_select:
                # 选择所有该标签的形状
                self.canvas.selected_shapes = shapes_to_select[:]
                for shape in self.canvas.shapes:
                    shape.selected = shape in shapes_to_select
                
                # 更新label_list选择状态
                self.label_list.clearSelection()
                for shape in shapes_to_select:
                    item = self.label_list.find_item_by_shape(shape)
                    if item:
                        self.label_list.select_item(item)
                
                # 发送选择变化信号
                self.canvas.selection_changed.emit(shapes_to_select)
                self.canvas.update()
                
                QtWidgets.QMessageBox.information(
                    self,
                    self.tr("选择完成"),
                    self.tr("已选择 {} 个标签为 '{}' 的对象").format(len(shapes_to_select), label_name)
                )
    
    def show_statistics_context_menu(self, pos):
        """显示统计窗口的右键菜单"""
        item = self.stats_detail_list.itemAt(pos)
        if not item:
            return
        
        # 获取当前选中的项目
        selected_items = self.stats_detail_list.selectedItems()
        
        menu = QtWidgets.QMenu(self)
        
        if len(selected_items) == 1:
            # 单个标签操作
            label_name = item.data(QtCore.Qt.UserRole)
            if not label_name:
                return
            
            select_action = menu.addAction(self.tr(f"选择所有 '{label_name}' 对象"))
            select_action.triggered.connect(lambda: self.statistics_select_current_label(label_name))
            
            # 可见性操作
            menu.addSeparator()
            show_only_action = menu.addAction(self.tr(f"仅显示标签 '{label_name}'"))
            show_only_action.triggered.connect(lambda: self.statistics_show_only_current_label(label_name))
            hide_action = menu.addAction(self.tr(f"隐藏标签 '{label_name}'"))
            hide_action.triggered.connect(lambda: self.statistics_hide_current_label(label_name))
            clear_filter_action = menu.addAction(self.tr("清除过滤（全部显示）"))
            clear_filter_action.triggered.connect(self.on_stats_clear_filter_clicked)

            menu.addSeparator()
            
            delete_action = menu.addAction(self.tr(f"删除所有 '{label_name}' 对象"))
            delete_action.triggered.connect(lambda: self.statistics_delete_current_label(label_name))
            
            relabel_action = menu.addAction(self.tr(f"更改 '{label_name}' 标签"))
            relabel_action.triggered.connect(lambda: self.statistics_relabel_current_label(label_name))
            
        else:
            # 多个标签操作
            label_names = []
            for selected_item in selected_items:
                label_name = selected_item.data(QtCore.Qt.UserRole)
                if label_name:
                    label_names.append(label_name)
            
            if not label_names:
                return
            
            label_text = ", ".join(label_names)
            
            select_action = menu.addAction(self.tr(f"选择所有 '{label_text}' 对象"))
            select_action.triggered.connect(lambda: self.statistics_select_multiple_labels())
            
            # 可见性操作
            menu.addSeparator()
            show_only_action = menu.addAction(self.tr("仅显示所选标签"))
            show_only_action.triggered.connect(self.statistics_show_only_multiple_labels)
            hide_action = menu.addAction(self.tr("隐藏所选标签"))
            hide_action.triggered.connect(self.statistics_hide_multiple_labels)
            clear_filter_action = menu.addAction(self.tr("清除过滤（全部显示）"))
            clear_filter_action.triggered.connect(self.on_stats_clear_filter_clicked)

            menu.addSeparator()
            
            delete_action = menu.addAction(self.tr(f"删除所有 '{label_text}' 对象"))
            delete_action.triggered.connect(lambda: self.statistics_delete_multiple_labels())
            
            relabel_action = menu.addAction(self.tr(f"更改 '{label_text}' 标签"))
            relabel_action.triggered.connect(lambda: self.statistics_relabel_multiple_labels())
        
        menu.exec_(self.stats_detail_list.mapToGlobal(pos))
    
    def statistics_select_current_label(self, label_name=None):
        """选择当前标签的所有对象"""
        if label_name is None:
            current_item = self.stats_detail_list.currentItem()
            if not current_item:
                return
            label_name = current_item.data(QtCore.Qt.UserRole)

    def _apply_visibility_by_labels(self, label_names, mode='only'):
        """按标签名集合应用可见性过滤。

        参数:
            label_names (list[str]): 标签名列表
            mode (str): 'only' 仅显示; 'hide' 隐藏
        """
        if not hasattr(self, 'canvas') or not hasattr(self.canvas, 'shapes'):
            return
        if not hasattr(self, 'label_list'):
            return
        if not hasattr(self, 'label_to_shapes'):
            return

        # 目标形状集合
        target_shapes = set()
        for name in label_names or []:
            shapes = self.label_to_shapes.get(name, [])
            for s in shapes:
                target_shapes.add(s)

        all_shapes = set(self.canvas.shapes)
        if mode == 'only':
            show_set = target_shapes
            hide_set = all_shapes - target_shapes
        elif mode == 'hide':
            hide_set = target_shapes
            show_set = all_shapes - target_shapes
        else:
            return

        # 批量更新列表与画布
        try:
            self.label_list.begin_bulk_update()
            for item in self.label_list:
                shp = item.shape()
                vis = True if shp in show_set else False
                item.setCheckState(Qt.Checked if vis else Qt.Unchecked)
                try:
                    self.canvas.set_shape_visible(shp, vis)
                except Exception:
                    pass
        finally:
            self.label_list.end_bulk_update()

        try:
            self.canvas.update()
        except Exception:
            pass

    def statistics_show_only_current_label(self, label_name=None):
        """仅显示单个标签"""
        if label_name is None:
            current_item = self.stats_detail_list.currentItem()
            if not current_item:
                return
            label_name = current_item.data(QtCore.Qt.UserRole)
        if not label_name:
            return
        self._apply_visibility_by_labels([str(label_name)], mode='only')
        self.status(self.tr(f"仅显示标签 '{label_name}'"))

    def statistics_hide_current_label(self, label_name=None):
        """隐藏单个标签"""
        if label_name is None:
            current_item = self.stats_detail_list.currentItem()
            if not current_item:
                return
            label_name = current_item.data(QtCore.Qt.UserRole)
        if not label_name:
            return
        self._apply_visibility_by_labels([str(label_name)], mode='hide')
        self.status(self.tr(f"已隐藏标签 '{label_name}'"))

    def statistics_show_only_multiple_labels(self):
        """仅显示多个标签"""
        selected_items = self.stats_detail_list.selectedItems()
        labels = []
        for it in selected_items:
            name = it.data(QtCore.Qt.UserRole)
            if name:
                labels.append(str(name))
        if not labels:
            return
        self._apply_visibility_by_labels(labels, mode='only')
        self.status(self.tr("仅显示所选标签"))

    def statistics_hide_multiple_labels(self):
        """隐藏多个标签"""
        selected_items = self.stats_detail_list.selectedItems()
        labels = []
        for it in selected_items:
            name = it.data(QtCore.Qt.UserRole)
            if name:
                labels.append(str(name))
        if not labels:
            return
        self._apply_visibility_by_labels(labels, mode='hide')
        self.status(self.tr("已隐藏所选标签"))
        
        if label_name and hasattr(self, 'label_to_shapes'):
            shapes_to_select = self.label_to_shapes.get(label_name, [])
            if shapes_to_select:
                # 选择所有该标签的形状
                self.canvas.selected_shapes = shapes_to_select[:]
                for shape in self.canvas.shapes:
                    shape.selected = shape in shapes_to_select
                
                # 更新label_list选择状态
                self.label_list.clearSelection()
                for shape in shapes_to_select:
                    item = self.label_list.find_item_by_shape(shape)
                    if item:
                        self.label_list.select_item(item)
                
                # 发送选择变化信号
                self.canvas.selection_changed.emit(shapes_to_select)
                self.canvas.update()
                
                QtWidgets.QMessageBox.information(
                    self,
                    self.tr("选择完成"),
                    self.tr("已选择 {} 个标签为 '{}' 的对象").format(len(shapes_to_select), label_name)
                )
            else:
                QtWidgets.QMessageBox.information(
                    self,
                    self.tr("提示"),
                    self.tr("没有找到标签为 '{}' 的对象").format(label_name)
                )
    
    def statistics_delete_current_label(self, label_name=None):
        """删除当前标签的所有对象 - 修复版本"""
        if label_name is None:
            current_item = self.stats_detail_list.currentItem()
            if not current_item:
                return
            label_name = current_item.data(QtCore.Qt.UserRole)
        
        if label_name:
            # 重新扫描当前画布，精确匹配该标签的对象，避免使用可能过期的映射
            target_label = label_name.strip()
            shapes_to_delete = []
            for s in self.canvas.shapes:
                eff_labels = []
                if hasattr(s, 'labels') and s.labels:
                    eff_labels = [str(x).strip() for x in s.labels if str(x).strip()]
                elif hasattr(s, 'label') and s.label:
                    raw = str(s.label).strip()
                    eff_labels = [t.strip() for t in raw.split(',') if t.strip()]
                elif hasattr(s, 'primary_label') and s.primary_label:
                    eff_labels = [str(s.primary_label).strip()]
                if target_label in eff_labels:
                    shapes_to_delete.append(s)
            if not shapes_to_delete:
                QtWidgets.QMessageBox.information(
                    self,
                    self.tr("提示"),
                    self.tr("没有找到标签为 '{}' 的对象").format(label_name)
                )
                return
            
            yes, no = QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No
            msg = self.tr(
                "您将永久删除所有标签为 '{}' 的 {} 个对象，是否继续？"
            ).format(label_name, len(shapes_to_delete))
            
            if yes == QtWidgets.QMessageBox.warning(
                self, self.tr("批量删除确认"), msg, yes | no, no
            ):
                # 🚀 性能优化：禁用UI更新，批量处理
                self.setUpdatesEnabled(False)
                self.canvas.setUpdatesEnabled(False)
                
                try:
                    # 1. 创建进度对话框（当删除对象较多时）
                    progress = None
                    if len(shapes_to_delete) > 20:
                        progress = QtWidgets.QProgressDialog(
                            self.tr("正在删除对象..."), 
                            self.tr("取消"), 
                            0, 100, self
                        )
                        progress.setWindowModality(QtCore.Qt.WindowModal)
                        progress.setValue(0)
                        progress.show()
                    
                    # 2. 批量设置选择状态 - 使用集合提高性能
                    shapes_to_delete_set = set(shapes_to_delete)
                    self.canvas.selected_shapes = shapes_to_delete[:]
                    
                    # 批量设置选择状态，减少循环次数
                    for shape in self.canvas.shapes:
                        shape.selected = shape in shapes_to_delete_set
                    
                    if progress:
                        progress.setValue(20)
                        if progress.wasCanceled():
                            return
                    
                    # 3. 使用 Canvas 内置删除，保证内部状态一致
                    original_count = len(self.canvas.shapes)
                    deleted_shapes = self.canvas.delete_selected()
                    deleted_count = len(deleted_shapes)
                    
                    if progress:
                        progress.setValue(40)
                        if progress.wasCanceled():
                            return
                    
                    # 4. 批量处理label_list的删除 - 使用现有的remove_labels方法
                    self.remove_labels(deleted_shapes)
                    
                    if progress:
                        progress.setValue(60)
                        if progress.wasCanceled():
                            return
                    
                    # 5. 清除选择状态并恢复背景可见（避免其他轮廓被隐藏）
                    # 无论当前是否还有选中，都强制恢复显示所有轮廓
                    try:
                        self.canvas.set_hiding(False)
                        for s in self.canvas.shapes:
                            s.selected = False
                        self.canvas.selected_shapes = []
                        self.canvas.selection_changed.emit([])
                    except Exception:
                        pass
                    self.label_list.clearSelection()
                    
                    if progress:
                        progress.setValue(80)
                        if progress.wasCanceled():
                            return
                    
                    # 6. 标记为修改
                    self.set_dirty()
                    
                    if progress:
                        progress.setValue(100)
                    
                finally:
                    # 恢复UI更新
                    self.setUpdatesEnabled(True)
                    self.canvas.setUpdatesEnabled(True)
                    
                    if progress:
                        progress.close()
                
                # 7. 单次更新显示和统计
                # 彻底刷新：以当前 shapes 重载到画布，清空可见性缓存
                try:
                    self.canvas.visible.clear()
                except Exception:
                    pass
                try:
                    self.canvas.load_shapes(self.canvas.shapes, replace=True)
                except Exception:
                    self.canvas.update()
                # 同步重建对象列表，确保与画布一致
                try:
                    self.label_list.clear()
                    for shp in self.canvas.shapes:
                        self.add_label(shp)
                except Exception:
                    pass
                self.update_statistics()
                
                QtWidgets.QMessageBox.information(
                    self,
                    self.tr("删除完成"),
                    self.tr("已删除 {} 个标签为 '{}' 的对象").format(deleted_count, label_name)
                )
                
                if self.no_shape():
                    for act in self.actions.on_shapes_present:
                        act.setEnabled(False)

        # ✅ 彻底一致性刷新：强制保存并重新加载当前文件，避免任何残留状态
        try:
            self._force_full_refresh_after_edit()
        except Exception:
            pass
    
    def statistics_relabel_current_label(self, label_name=None):
        """更改当前标签的名称"""
        if label_name is None:
            current_item = self.stats_detail_list.currentItem()
            if not current_item:
                return
            label_name = current_item.data(QtCore.Qt.UserRole)
        
        if label_name and hasattr(self, 'label_to_shapes'):
            shapes_to_relabel = self.label_to_shapes.get(label_name, [])
            if not shapes_to_relabel:
                QtWidgets.QMessageBox.information(
                    self,
                    self.tr("提示"),
                    self.tr("没有找到标签为 '{}' 的对象").format(label_name)
                )
                return
            
            # 弹出标签选择对话框
            text, flags, group_id = self.label_dialog.pop_up(
                text=label_name,  # 预填充当前标签
                flags={},
                group_id=None,
            )
            
            if text is None or not text.strip():
                return
            
            # 解析新标签
            new_labels = [t.strip() for t in text.split(",") if t.strip()]
            
            # 验证新标签
            for lb in new_labels:
                if not self.validate_label(lb):
                    self.error_message(
                        self.tr("无效标签"),
                        self.tr("标签 '{}' 无效").format(lb),
                    )
                    return
            
            # 批量更改标签
            count = 0
            for shape in shapes_to_relabel:
                if shape:
                    shape.labels = new_labels
                    shape.flags = flags
                    if group_id is not None:
                        shape.group_id = group_id
                    
                    # 添加到标签历史
                    for lb in new_labels:
                        self.label_dialog.add_label_history(lb)
                    
                    # 更新唯一标签列表
                    for lb in new_labels:
                        if not self.unique_label_list.find_items_by_label(lb):
                            unique_label_item = self.unique_label_list.create_item_from_label(lb)
                            self.unique_label_list.addItem(unique_label_item)
                            rgb = self._get_rgb_by_label(lb)
                            self.unique_label_list.set_item_label(unique_label_item, lb, rgb)
                    
                    # 更新颜色
                    self._update_shape_color(shape)
                    count += 1
            
            # 更新显示
            self.label_list.clear()
            for shape in self.canvas.shapes:
                self.add_label(shape)
            
            self.set_dirty()
            self.update_statistics()
            # 最终兜底：直接重新加载当前图像，确保所有状态一致
            try:
                if self.image_path:
                    self.load_file(self.image_path)
            except Exception:
                pass
            
            # 显示更改结果
            QtWidgets.QMessageBox.information(
                self,
                self.tr("批量标签更改完成"),
                self.tr("已将 {} 个对象的标签从 '{}' 更改为 '{}'").format(count, label_name, text)
            )
    
    def statistics_select_multiple_labels(self):
        """选择多个标签的所有对象"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            return
        
        all_shapes_to_select = []
        label_names = []
        
        for item in selected_items:
            label_name = item.data(QtCore.Qt.UserRole)
            if label_name and hasattr(self, 'label_to_shapes'):
                shapes = self.label_to_shapes.get(label_name, [])
                all_shapes_to_select.extend(shapes)
                label_names.append(label_name)
        
        if not all_shapes_to_select:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("没有找到选中标签的对象")
            )
            return
        
        # 选择所有该标签的形状
        self.canvas.selected_shapes = all_shapes_to_select[:]
        for shape in self.canvas.shapes:
            shape.selected = shape in all_shapes_to_select
        
        # 更新label_list选择状态
        self.label_list.clearSelection()
        for shape in all_shapes_to_select:
            try:
                item = self.label_list.find_item_by_shape(shape)
                if item:
                    self.label_list.select_item(item)
            except ValueError:
                # Shape not found in label list, skip silently
                pass
        
        # 发送选择变化信号
        self.canvas.selection_changed.emit(all_shapes_to_select)
        self.canvas.update()
        
        # 显示选择结果
        label_text = ", ".join(label_names)
        QtWidgets.QMessageBox.information(
            self,
            self.tr("选择完成"),
            self.tr("已选择 {} 个标签为 '{}' 的对象").format(len(all_shapes_to_select), label_text)
        )
    
    def statistics_delete_multiple_labels(self):
        """删除多个标签的所有对象"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            return
        
        all_shapes_to_delete = []
        label_names = []
        
        # 构造待删除标签集合
        target_labels = []
        for item in selected_items:
            name = item.data(QtCore.Qt.UserRole)
            if name:
                target_labels.append(str(name).strip())
        label_names = target_labels[:]

        # 扫描画布，匹配包含任意目标标签的对象
        target_set = set(target_labels)
        for s in self.canvas.shapes:
            eff_labels = []
            if hasattr(s, 'labels') and s.labels:
                eff_labels = [str(x).strip() for x in s.labels if str(x).strip()]
            elif hasattr(s, 'label') and s.label:
                raw = str(s.label).strip()
                eff_labels = [t.strip() for t in raw.split(',') if t.strip()]
            elif hasattr(s, 'primary_label') and s.primary_label:
                eff_labels = [str(s.primary_label).strip()]
            if eff_labels and any(lb in target_set for lb in eff_labels):
                all_shapes_to_delete.append(s)
        
        if not all_shapes_to_delete:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("没有找到选中标签的对象")
            )
            return
        
        # 确认删除
        yes, no = QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No
        label_text = ", ".join(label_names)
        msg = self.tr(
            "您将永久删除所有标签为 '{}' 的 {} 个对象，是否继续？"
        ).format(label_text, len(all_shapes_to_delete))
        
        if yes == QtWidgets.QMessageBox.warning(
            self, self.tr("批量删除确认"), msg, yes | no, no
        ):
            # 🚀 性能优化：禁用UI更新，批量处理
            self.setUpdatesEnabled(False)
            self.canvas.setUpdatesEnabled(False)
            
            try:
                # 创建进度对话框
                progress = QtWidgets.QProgressDialog(
                    self.tr("正在删除对象..."), 
                    self.tr("取消"), 
                    0, 
                    100, 
                    self
                )
                progress.setWindowModality(Qt.WindowModal)
                progress.setAutoClose(True)
                progress.show()
                
                original_count = len(self.canvas.shapes)

                # 1. 设置选中并调用内置删除
                shapes_to_remove = [s for s in all_shapes_to_delete if s in self.canvas.shapes]
                self.canvas.selected_shapes = shapes_to_remove[:]
                for s in self.canvas.shapes:
                    s.selected = s in self.canvas.selected_shapes
                deleted_shapes = self.canvas.delete_selected()
                
                if progress:
                    progress.setValue(20)
                    if progress.wasCanceled():
                        return
                
                # 2. 批量处理label_list的删除 - 使用现有的remove_labels方法
                self.remove_labels(deleted_shapes)
                
                if progress:
                    progress.setValue(60)
                    if progress.wasCanceled():
                        return
                
                # 4. 清除选择状态并恢复背景可见（避免其他轮廓被隐藏）
                # 无论当前是否还有选中，都强制恢复显示所有轮廓
                try:
                    self.canvas.set_hiding(False)
                    for s in self.canvas.shapes:
                        s.selected = False
                    self.canvas.selected_shapes = []
                    self.canvas.selection_changed.emit([])
                except Exception:
                    pass
                self.label_list.clearSelection()
                
                if progress:
                    progress.setValue(80)
                    if progress.wasCanceled():
                        return
                
                # 5. 标记为修改
                self.set_dirty()
                
                if progress:
                    progress.setValue(100)
                
            finally:
                # 恢复UI更新
                self.setUpdatesEnabled(True)
                self.canvas.setUpdatesEnabled(True)
                
                if progress:
                    progress.close()
            
            # 6. 单次更新显示和统计
            try:
                self.canvas.visible.clear()
            except Exception:
                pass
            try:
                self.canvas.load_shapes(self.canvas.shapes, replace=True)
            except Exception:
                self.canvas.update()
            # 同步重建对象列表，确保与画布一致
            try:
                self.label_list.clear()
                for shp in self.canvas.shapes:
                    self.add_label(shp)
            except Exception:
                pass
            self.update_statistics()
            
            deleted_count = len(deleted_shapes)
            QtWidgets.QMessageBox.information(
                self,
                self.tr("删除完成"),
                self.tr("已删除 {} 个标签为 '{}' 的对象").format(deleted_count, label_text)
            )
            
            if self.no_shape():
                for act in self.actions.on_shapes_present:
                    act.setEnabled(False)

            # ✅ 彻底一致性刷新：强制保存并重新加载当前文件
            try:
                self._force_full_refresh_after_edit()
            except Exception:
                pass

    
    def statistics_relabel_multiple_labels(self):
        """更改多个标签的名称"""
        selected_items = self.stats_detail_list.selectedItems()
        if not selected_items:
            return
        
        all_shapes_to_relabel = []
        label_names = []
        
        for item in selected_items:
            label_name = item.data(QtCore.Qt.UserRole)
            if label_name and hasattr(self, 'label_to_shapes'):
                shapes = self.label_to_shapes.get(label_name, [])
                all_shapes_to_relabel.extend(shapes)
                label_names.append(label_name)
        
        if not all_shapes_to_relabel:
            QtWidgets.QMessageBox.information(
                self,
                self.tr("提示"),
                self.tr("没有找到选中标签的对象")
            )
            return
        
        # 弹出标签选择对话框
        label_text = ", ".join(label_names)
        text, flags, group_id = self.label_dialog.pop_up(
            text="",  # 不预填充，让用户输入新标签
            flags={},
            group_id=None,
        )
        
        if text is None or not text.strip():
            return
        
        # 解析新标签
        new_labels = [t.strip() for t in text.split(",") if t.strip()]
        
        # 验证新标签
        for lb in new_labels:
            if not self.validate_label(lb):
                self.error_message(
                    self.tr("无效标签"),
                    self.tr("标签 '{}' 无效").format(lb),
                )
                return
        
        # 批量更改标签
        count = 0
        for shape in all_shapes_to_relabel:
            if shape:
                shape.labels = new_labels
                shape.flags = flags
                if group_id is not None:
                    shape.group_id = group_id
                
                # 添加到标签历史
                for lb in new_labels:
                    self.label_dialog.add_label_history(lb)
                
                # 更新唯一标签列表
                for lb in new_labels:
                    if not self.unique_label_list.find_items_by_label(lb):
                        unique_label_item = self.unique_label_list.create_item_from_label(lb)
                        self.unique_label_list.addItem(unique_label_item)
                        rgb = self._get_rgb_by_label(lb)
                        self.unique_label_list.set_item_label(unique_label_item, lb, rgb)
                
                # 更新颜色
                self._update_shape_color(shape)
                count += 1
        
        # 更新显示
        self.label_list.clear()
        for shape in self.canvas.shapes:
            self.add_label(shape)
        
        self.set_dirty()
        self.update_statistics()
        
        # 显示更改结果
        QtWidgets.QMessageBox.information(
            self,
            self.tr("批量标签更改完成"),
            self.tr("已将 {} 个对象的标签从 '{}' 更改为 '{}'").format(count, label_text, text)
        )
    
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

    def _validate_screen_geometry(self):
        """简化的屏幕几何验证 - 始终返回True，使用全屏策略"""
        logger.debug("Using simplified screen validation - always allowing dock state restore")
        return True

    def _ensure_safe_window_geometry(self):
        """确保窗口几何在安全范围内，简化版本"""
        try:
            # ✅ 不再操作内部main_window，只由外层MainWindow负责窗口显示
            logger.debug("Window geometry check completed - external MainWindow handles display")
                
        except Exception as e:
            logger.error(f"Error ensuring safe window geometry: {e}")

    def load_dock_state(self):
        """Load dock state from config with better error handling and screen validation."""
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

        # ✅ 简化：直接尝试加载，失败时使用默认布局
        if not self._validate_screen_geometry():
            logger.info("Using default maximized layout")
            self.reset_dock_layout()
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
                    # hasattr(self, "flag_dock"),  # 注释掉，因为flag_dock已被禁用
                    hasattr(self, "label_dock"),
                    hasattr(self, "shape_dock"),
                    hasattr(self, "file_dock"),
                    hasattr(self, "statistics_dock"),  # ✅ 包含新添加的统计dock
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
            # self.flag_dock.setVisible(True)  # 注释掉，因为flag_dock已被禁用
            self.label_dock.setVisible(True)
            self.shape_dock.setVisible(True)
            self.file_dock.setVisible(True)
            self.statistics_dock.setVisible(True)  # ✅ 确保统计dock也可见

            # Try to restore state
            if self.main_window.restoreState(dock_state):
                logger.info("✓ Dock state loaded successfully")
                
                # ✅ 状态恢复后验证和调整窗口几何
                QtCore.QTimer.singleShot(50, self._ensure_safe_window_geometry)
                
                # ✅ 简化dock尺寸设置
                base_dock_width = 250
                tools_width = 5
                stats_width = 200
                logger.debug(f"Post-restore dock sizing: base={base_dock_width}, stats={stats_width}")
                
                # Apply a workaround for proper dock resizing
                self.main_window.resizeDocks(
                    [
                        self.tools_dock,
                        self.shape_text_dock,
                        # self.flag_dock,  # 注释掉，因为flag_dock已被禁用
                        self.label_dock,
                        self.shape_dock,
                        self.file_dock,
                        self.statistics_dock,  # ✅ 包含统计dock
                    ],
                    [tools_width, base_dock_width, base_dock_width, base_dock_width, base_dock_width, stats_width],
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
        # Determine where the dock currently resides
        area = self.main_window.dockWidgetArea(self.tools_dock)

        # Switch orientation based on dock area
        if area in (Qt.TopDockWidgetArea, Qt.BottomDockWidgetArea):
            self.tools.setOrientation(Qt.Horizontal)
        else:
            # Update dock size to fit all toolbar actions
            self.update_toolbar_size(area)

        # Force toolbar to update its layout
        self.tools.update()

        # Save the dock state
        self.save_dock_state()

    def update_toolbar_scale(self):
        """Scale toolbar icon size so all actions fit the available space."""
        if not hasattr(self, "_base_icon_size"):
            return

        # Base scaling relative to a 1024px wide window
        window_scale = self.main_window.width() / 1024
        window_scale = max(0.5, min(1.5, window_scale))

        base_size = int(self._base_icon_size * window_scale)

        # Helper to apply an icon size and get the resulting hint
        def compute_hint(size):
            self.tools.setIconSize(QtCore.QSize(size, size))
            self.tools.adjustSize()
            return self.tools.sizeHint()

        area = self.main_window.dockWidgetArea(self.tools_dock)

        hint = compute_hint(base_size)
        icon_size = base_size

        if area in (Qt.TopDockWidgetArea, Qt.BottomDockWidgetArea):
            available = self.main_window.width() - 50
            if hint.width() > available:
                factor = available / hint.width()
                icon_size = max(16, int(base_size * factor))
                hint = compute_hint(icon_size)
        else:
            available = self.main_window.height() - 150
            if hint.height() > available:
                factor = available / hint.height()
                icon_size = max(16, int(base_size * factor))
                hint = compute_hint(icon_size)

        self._icon_size = icon_size
        self._dock_width = int(icon_size + 4)  # 减少边距：从16改为4，让工具栏更紧凑

        # Update dock orientation and size with the new icon dimensions
        self.on_tools_dock_location_changed()
        self.update_toolbar_size(area)

    def update_toolbar_size(self, area=None):
        """Resize the dock widget so all toolbar actions remain visible."""
        if area is None:
            area = self.main_window.dockWidgetArea(self.tools_dock)

        self.tools.adjustSize()
        size = self.tools.sizeHint()

        if area in (Qt.TopDockWidgetArea, Qt.BottomDockWidgetArea):
            self.tools_dock.setMinimumHeight(size.height())
            self.tools_dock.setMaximumHeight(size.height())
            self.tools_dock.setMinimumWidth(size.width())
            self.tools_dock.setMaximumWidth(size.width())
        else:
            width = max(size.width(), self._dock_width)
            self.tools_dock.setMinimumWidth(width)
            self.tools_dock.setMaximumWidth(width)
            # Ensure the dock resizes vertically to show all actions
            self.main_window.resizeDocks([self.tools_dock], [size.height()], Qt.Vertical)
            self.tools_dock.setMinimumHeight(0)
            self.tools_dock.setMaximumHeight(16777215)

            # If floating, Qt returns 0 for dock area
            if not area:
                self.tools_dock.resize(size.width(), 300)
            else:
                # Adjust horizontal size when docked left or right
                self.main_window.resizeDocks([self.tools_dock], [size.width()], Qt.Horizontal)

    def current_image_path(self):
        """返回当前图像路径（去除隐藏字符）"""
        if self.image_path:
            return str(self.image_path).replace("\n", "").replace("\r", "").strip()
        return self.image_path

    def segment_all_instances(self):
        """Segment current image with SAM-2 并立即显示结果。"""
        # ---------- 必要 import ----------
        import sys
        import os
        from PyQt5.QtWidgets import QProgressDialog, QInputDialog, QMessageBox, QApplication
        from PyQt5.QtCore import Qt

        # ---------- 工具：同步下载并校验 ----------
        def download_with_check(url: str, dst: Path, title: str) -> bool:
            """下载 url→dst 并校验文件大小；成功 True，取消/失败 False。"""
            import urllib.request
            try:
                with urllib.request.urlopen(url) as resp:
                    total = int(resp.getheader("Content-Length", "0"))
                    dlg = QProgressDialog(title, "取消", 0, total, self)
                    dlg.setWindowModality(Qt.WindowModal)
                    dlg.setWindowTitle("模型下载")
                    dlg.show()

                    chunk = 1 << 20  # 1 MiB
                    done = 0
                    with open(dst, "wb") as f:
                        while True:
                            buf = resp.read(chunk)
                            if not buf:
                                break
                            f.write(buf)
                            done += len(buf)
                            dlg.setValue(done)
                            QApplication.processEvents()
                            if dlg.wasCanceled():
                                f.close()
                                dst.unlink(missing_ok=True)
                                return False
                    dlg.close()

                    if total and dst.stat().st_size != total:
                        dst.unlink(missing_ok=True)
                        QMessageBox.warning(self, "下载失败",
                                            "文件大小与服务器不一致，已删除残缺文件。")
                        return False
                return True
            except Exception as e:
                dst.unlink(missing_ok=True)
                QMessageBox.critical(self, "下载失败", str(e))
                return False
        
        try:
            import torch
        except ModuleNotFoundError:
            QMessageBox.critical(
                self,
                "缺少依赖",
                "SAM-2 一键分割需要安装 PyTorch。\n"
                "请先安装 torch 后再尝试运行该功能。",
            )
            return

        device = "cuda" if torch.cuda.is_available() else "cpu"

        simplify_cfg = {}
        if hasattr(self, "_config"):
            simplify_cfg = self._config.get("sam2_simplify", {}) or {}
        polygon_eps = max(1e-5, float(simplify_cfg.get("epsilon_factor", 0.003)))
        target_points = max(3, int(simplify_cfg.get("target_points", 400)))
        dedup_tol = max(0.1, float(simplify_cfg.get("dedup_tolerance", 0.8)))
        
        # === 性能诊断 ===
        # 性能诊断已完成，移除 print 输出

        # ---------- 0. 当前图像 ----------
        image_path = self.current_image_path()
        if not image_path:
            QMessageBox.warning(self, "未找到图像", "请先在界面打开一张图像。")
            return

        if _ensure_sam2_package() is None:
            QMessageBox.critical(
                self,
                "缺少依赖",
                "SAM-2 一键分割需要本地安装 `sam2` Python 包。\n"
                "请先安装官方 sam2 库或将其所在目录加入 PYTHONPATH 后再试。",
            )
            return

        # ---------- 1. 模型尺寸 ----------
        size_choices = [
            ("tiny", "tiny"),
            ("smally", "small"),
            ("base+", "base_plus"),
            ("large", "large"),
        ]
        size_map = {label: key for label, key in size_choices}
        size2cfg = {
            "tiny": ("sam2.1/sam2.1_hiera_t", "sam2.1_hiera_tiny.pt"),
            "small": ("sam2.1/sam2.1_hiera_s", "sam2.1_hiera_small.pt"),
            "base_plus": ("sam2.1/sam2.1_hiera_b+", "sam2.1_hiera_base_plus.pt"),
            "large": ("sam2.1/sam2.1_hiera_l", "sam2.1_hiera_large.pt"),
        }
        base_url = ("https://dl.fbaipublicfiles.com/segment_anything_2/092824/"
                    "sam2.1_hiera_{}.pt")

        current_variant = getattr(self, "_sam2_variant", "large")
        default_idx = 0
        for idx, (_, key) in enumerate(size_choices):
            if key == current_variant:
                default_idx = idx
                break
        display_options = [label for label, _ in size_choices]
        size_label, ok = QInputDialog.getItem(
            self, "选择 SAM-2 模型尺寸", "Model size:", display_options, default_idx, False
        )
        if not ok:
            return

        size_key = size_map[size_label]
        cfg_name, ckpt_file = size2cfg[size_key]
        ckpt_url = base_url.format(size_key)
        
        # 修改：优先使用exe目录的models，但在不可写时回退到用户目录
        env_model_dir = os.getenv("ANYLABELING_MODELS_DIR")
        candidate_dirs = []
        if env_model_dir:
            candidate_dirs.append(Path(env_model_dir).expanduser())
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidate_dirs.append(exe_dir / "models")
        else:
            candidate_dirs.append(Path.cwd() / "models")
            try:
                candidate_dirs.append(Path(__file__).resolve().parents[2] / "models")
            except IndexError:
                pass
        candidate_dirs.append(Path.home() / ".anylabeling" / "models")

        model_dir = None
        for candidate in candidate_dirs:
            if candidate is None:
                continue
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except PermissionError as exc:
                logger.warning("[SAM2] Model directory not writable (permission denied): %s", candidate)
                logger.debug("[SAM2] Permission error detail: %s", exc)
                continue
            except OSError as exc:
                logger.warning("[SAM2] Failed to prepare SAM-2 model directory %s: %s", candidate, exc)
                continue
            if os.access(candidate, os.W_OK):
                model_dir = candidate
                break
            logger.warning("[SAM2] Model directory exists but is not writable: %s", candidate)

        if model_dir is None:
            QMessageBox.critical(
                self,
                "路径不可写",
                "无法为 SAM-2 模型准备可写目录。\n"
                "请调整权限或在环境变量 ANYLABELING_MODELS_DIR 中指定可写路径。",
            )
            return

        logger.warning("[SAM2] Using model directory: %s", model_dir)

        ckpt_path = model_dir / ckpt_file

        # ---------- 2. checkpoint ----------
        if not ckpt_path.exists():
            if not download_with_check(ckpt_url, ckpt_path, f"下载 {ckpt_file}"):
                return

        # ---------- 3. UI 忙碌 ----------
        logger.warning(
            "[SAM2] Segment-all requested. current_image=%s, variant=%s",
            image_path,
            size_key,
        )
        self.actions.segment_all.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.status(f"{size_label} 模型分割中…")
        try:
            self.canvas.set_loading(True, self.tr("SAM-2 分割中，请稍候…"))
        except Exception:
            pass
        QApplication.processEvents()

        try:  # —— 外层 try —— 发生任何错误都能进入 finally 恢复光标
            import cv2, json, torch, numpy as np, traceback
            import gc  # 添加垃圾回收
            from anylabeling.views.labeling.shape import Shape
            from PyQt5 import QtCore

            def _dedup_ring(points):
                if points is None or len(points) == 0:
                    return np.zeros((0, 2), dtype=np.float32)
                filtered = [points[0]]
                for pt in points[1:]:
                    if np.hypot(pt[0] - filtered[-1][0], pt[1] - filtered[-1][1]) >= dedup_tol:
                        filtered.append(pt)
                if len(filtered) > 2 and np.hypot(
                    filtered[0][0] - filtered[-1][0],
                    filtered[0][1] - filtered[-1][1],
                ) < dedup_tol:
                    filtered.pop()
                if not filtered:
                    return np.zeros((0, 2), dtype=np.float32)
                return np.asarray(filtered, dtype=np.float32)

            def _simplify_contour(contour):
                if contour is None or len(contour) < 3:
                    return None
                contour = contour.astype(np.float32)
                perim = cv2.arcLength(contour, True)
                if perim <= 0:
                    return None

                epsilon = max(perim * polygon_eps, 1.0)
                approx = cv2.approxPolyDP(contour, epsilon, True)
                attempts = 0
                while len(approx) > target_points and attempts < 5:
                    epsilon *= 1.5
                    approx = cv2.approxPolyDP(contour, epsilon, True)
                    attempts += 1
                approx = approx.reshape(-1, 2)
                simplified = _dedup_ring(approx)
                if simplified is None or len(simplified) < 3:
                    return None
                return simplified

            from sam2.sam2_image_predictor import SAM2ImagePredictor
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

            # 清理内存
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # -------- 4. 权重加载（最多 1 次重试） --------
            try_load = 0
            while try_load < 2:
                try:
                    need_reload = (
                        not hasattr(self, "_sam2_variant")
                        or self._sam2_variant != size_key
                        or self._sam2_predictor is None
                    )
                    if need_reload:
                        model = _load_sam2_model(cfg_name, ckpt_path, device)
                        self._sam2_predictor = SAM2ImagePredictor(model)
                        self._sam2_mask_gen = None
                        self._sam2_variant = size_key
                        self._sam2_mask_gen_relaxed = {}
                    pps = 64
                    if size_key in ("tiny", "small"):
                        pps = 32

                    if (
                            self._sam2_mask_gen is None
                            or getattr(self, "_sam2_mask_gen_pps", None) != pps
                    ):
                        self._sam2_mask_gen = SAM2AutomaticMaskGenerator(
                            self._sam2_predictor.model,
                            points_per_side=pps,
                            pred_iou_thresh=0.9,
                            stability_score_thresh=0.92,
                            min_mask_region_area=256,
                        )
                        self._sam2_mask_gen_pps = pps

                    # -------- 5. 图像 & masks --------
                    # 清理路径中的换行符和其他无效字符
                    import os, re
                    from PyQt5.QtWidgets import QMessageBox as QtWidgets_QMessageBox
                    # 基础清理隐藏字符
                    raw_path = str(image_path)
                    # 1) 把换行符当成目录分隔符
                    raw_path = raw_path.replace('\n', os.sep)

                    # 2) 去掉其余控制字符并两端空白
                    clean_path = re.sub(r'[\r\t\f\v]', '', raw_path).strip()
                    
                    logger.warning("[SAM2] Resolving image source…")
                    # 构建候选图像列表：优先画布，其次磁盘
                    sources = []
                    pixmap = getattr(getattr(self, "canvas", None), "pixmap", None)
                    if pixmap and not pixmap.isNull():
                        logger.warning(
                            "[SAM2] Using canvas pixmap as segmentation input (sync=%s).",
                            getattr(self, "sync_pplxpl", False),
                        )
                        qimage = pixmap.toImage()
                        try:
                            canvas_img = qt_img_to_rgb_cv_img(qimage)
                        except Exception:
                            qimage = qimage.convertToFormat(QtGui.QImage.Format_RGB32)
                            canvas_img = qt_img_to_rgb_cv_img(qimage)
                        if canvas_img is not None:
                            if canvas_img.ndim == 2:
                                canvas_img = cv2.cvtColor(canvas_img, cv2.COLOR_GRAY2RGB)
                            elif canvas_img.ndim == 3:
                                if canvas_img.shape[2] == 4:
                                    canvas_img = cv2.cvtColor(canvas_img, cv2.COLOR_RGBA2RGB)
                                elif canvas_img.shape[2] == 1:
                                    canvas_img = cv2.cvtColor(canvas_img, cv2.COLOR_GRAY2RGB)
                                elif canvas_img.shape[2] > 3:
                                    canvas_img = canvas_img[:, :, :3]
                            canvas_img = np.ascontiguousarray(canvas_img, dtype=np.uint8)
                            sources.append(("canvas", canvas_img))
                    else:
                        logger.warning("[SAM2] Canvas pixmap unavailable, will try disk file.")

                    disk_img = None
                    disk_exists = os.path.exists(clean_path)
                    if disk_exists:
                        try:
                            img_bgr = cv2.imread(clean_path)
                            if img_bgr is None:
                                with open(clean_path, "rb") as f:
                                    img_data = np.frombuffer(f.read(), dtype=np.uint8)
                                    img_bgr = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
                        except Exception as e:
                            raise IOError(
                                f"读取图像时发生错误：{e}\n原始路径：{image_path}\n清理后路径：{clean_path}"
                            )
                        if img_bgr is None:
                            raise IOError(
                                f"无法读取图像（可能是格式不支持或文件损坏）：\n原始路径：{image_path}\n清理后路径：{clean_path}"
                            )
                        disk_img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                        disk_img = np.ascontiguousarray(disk_img, dtype=np.uint8)
                        sources.append(("disk", disk_img))
                        logger.warning("[SAM2] Prepared disk image source: %s", clean_path)
                    else:
                        logger.warning("[SAM2] Disk source not found: %s", clean_path)

                    if not sources:
                        QtWidgets_QMessageBox.warning(
                            self,
                            "图像不存在",
                            f"无法找到用于分割的图像源：\n{clean_path}",
                        )
                        logger.warning("[SAM2] Aborting segmentation: no valid image sources for %s", clean_path)
                        break
                    else:
                        logger.warning(
                            "[SAM2] Image sources prepared: %s",
                            ", ".join(name for name, _ in sources),
                        )

                    img_rgb = None
                    masks = None
                    source_used = None
                    used_mode = None
                    last_error = None

                    relaxed_cache = getattr(self, "_sam2_mask_gen_relaxed", {})
                    relaxed_pps = max(pps // 2, 16)
                    relaxed_key = (size_key, relaxed_pps)

                    for source_name, candidate_img in sources:
                        start_time = time.perf_counter()
                        h, w = candidate_img.shape[:2]
                        logger.warning(
                            "[SAM2] Input image size (%s): %s x %s", source_name, w, h
                        )

                        attempt_configs = [
                            (
                                "strict",
                                self._sam2_mask_gen,
                                1024,
                            )
                        ]

                        relaxed_gen = relaxed_cache.get(relaxed_key)
                        if relaxed_gen is None:
                            try:
                                relaxed_gen = SAM2AutomaticMaskGenerator(
                                    self._sam2_predictor.model,
                                    points_per_side=relaxed_pps,
                                    pred_iou_thresh=0.7,
                                    stability_score_thresh=0.6,
                                    min_mask_region_area=64,
                                )
                                relaxed_cache[relaxed_key] = relaxed_gen
                                logger.warning(
                                    "[SAM2] Created relaxed mask generator (pps=%s) for variant=%s",
                                    relaxed_pps,
                                    size_key,
                                )
                            except Exception as e:
                                logger.warning(
                                    "[SAM2] Failed to create relaxed generator: %s",
                                    e,
                                )
                                relaxed_gen = None
                        if relaxed_gen is not None:
                            attempt_configs.append(
                                (
                                    "relaxed",
                                    relaxed_gen,
                                    1536,
                                )
                            )

                        masks = None
                        used_mode = None

                        for mode_name, generator, max_wh in attempt_configs:
                            if generator is None:
                                continue
                            mode_start = time.perf_counter()
                            logger.warning(
                                "[SAM2] Attempting mask generation (source=%s, mode=%s, max_wh=%s, pps=%s)",
                                source_name,
                                mode_name,
                                max_wh,
                                getattr(generator, "points_per_side", "n/a"),
                            )

                            try:
                                need_resize = max(h, w) > max_wh
                                input_img = candidate_img
                                if need_resize:
                                    scale = max_wh / max(h, w)
                                    target_size = (int(w * scale), int(h * scale))
                                    logger.warning(
                                        "[SAM2] %s generator downscaling %s source to %s",
                                        mode_name,
                                        source_name,
                                        target_size,
                                    )
                                    input_img = cv2.resize(
                                        candidate_img,
                                        target_size,
                                        interpolation=cv2.INTER_LINEAR,
                                    )
                                    status_text = (
                                        "正在生成掩码（缩放模式）..."
                                        if mode_name == "strict"
                                        else "正在生成掩码（补救模式）..."
                                    )
                                else:
                                    status_text = (
                                        "正在生成掩码..."
                                        if mode_name == "strict"
                                        else "正在生成掩码（补救模式）..."
                                    )

                                self.status(status_text)
                                QApplication.processEvents()

                                masks = generator.generate(input_img)

                                if masks:
                                    if need_resize:
                                        for m in masks:
                                            m["segmentation"] = cv2.resize(
                                            m["segmentation"].astype(np.uint8),
                                            (w, h),
                                            interpolation=cv2.INTER_NEAREST,
                                        ).astype(bool)
                                    elapsed_mode = time.perf_counter() - mode_start
                                    used_mode = mode_name
                                    logger.warning(
                                        "[SAM2] Mask generation succeeded (source=%s, mode=%s, count=%s, elapsed=%.2fs)",
                                        source_name,
                                        mode_name,
                                        len(masks),
                                        elapsed_mode,
                                    )
                                    break

                                logger.warning(
                                    "[SAM2] %s generator returned no masks for %s source.",
                                    mode_name,
                                    source_name,
                                )
                            except Exception as e:
                                last_error = e
                                masks = None
                                elapsed_mode = time.perf_counter() - mode_start
                                logger.warning(
                                    "[SAM2] Mask generation failed (%s) on %s source after %.2fs: %s",
                                    mode_name,
                                    source_name,
                                    elapsed_mode,
                                    e,
                                )
                                continue

                        if masks:
                            img_rgb = candidate_img
                            source_used = source_name
                            elapsed_total = time.perf_counter() - start_time
                            logger.warning(
                                "[SAM2] Using masks from %s source (variant=%s, mode=%s, count=%s, elapsed=%.2fs)",
                                source_used,
                                size_key,
                                used_mode,
                                len(masks),
                                elapsed_total,
                            )
                            break

                        if source_name == "canvas" and any(
                            s[0] == "disk" for s in sources
                        ):
                            logger.warning(
                                "[SAM2] Canvas source unsuccessful, trying disk image."
                            )
                            continue

                    self._sam2_mask_gen_relaxed = relaxed_cache

                    if img_rgb is None or masks is None:
                        if last_error is not None:
                            raise last_error
                        logger.warning("[SAM2] No masks generated from any source.")
                        QtWidgets_QMessageBox.information(
                            self, "无结果", "未检测到实例掩码。"
                        )
                        return

                    # -------- 6. 生成shapes数据 --------
                    base_shapes = []
                    # print(f"开始处理 {len(masks)} 个掩码...")
                    for i, m in enumerate(masks):
                        seg = (m["segmentation"] * 255).astype(np.uint8)
                        cnts, _ = cv2.findContours(
                            seg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
                        )
                        # print(f"掩码 {i+1}: 找到 {len(cnts)} 个轮廓")
                        for cnt in cnts:
                            simplified = _simplify_contour(cnt)
                            if simplified is None or len(simplified) < 3:
                                continue
                            pts = [[float(x), float(y)] for x, y in simplified]
                            base_shapes.append({
                                "label": "keli",
                                "points": pts,
                                "type": "polygon",
                                "line_color": None,
                                "fill_color": None,
                            })
                    # print(f"生成了 {len(base_shapes)} 个形状")

                    logger.warning("[SAM2] Extracted contours: %s", len(base_shapes))
                    if not base_shapes:
                        logger.warning("[SAM2] No contours extracted from masks.")
                        QtWidgets_QMessageBox.information(
                            self, "无结果", "未能从 SAM-2 分割结果中提取任何轮廓。"
                        )
                        logger.warning("[SAM2] Aborting segmentation: no valid contours.")
                        return

                    sync_flag = bool(getattr(self, "sync_pplxpl", False))

                    def _build_shape(shape_data):
                        shp = Shape(
                            labels=[shape_data.get("label", "")],
                            text="",
                            shape_type=shape_data.get("type", "polygon"),
                        )
                        for x, y in shape_data["points"]:
                            shp.add_point(QtCore.QPointF(float(x), float(y)))
                        shp.close()
                        shp.line_width = Shape.line_width
                        try:
                            if not isinstance(getattr(shp, "other_data", None), dict):
                                shp.other_data = {}
                        except Exception:
                            shp.other_data = {}
                        try:
                            shp.other_data["pplxpl_sync"] = sync_flag
                            shp.other_data.setdefault("source", "sam2")
                        except Exception:
                            pass
                        return shp

                    def _remove_existing_label_shapes(label):
                        if not hasattr(self, "canvas") or not getattr(self.canvas, "shapes", None):
                            return
                        shapes_to_remove = [
                            shape
                            for shape in list(self.canvas.shapes)
                            if getattr(shape, "primary_label", "") == label
                        ]
                        if not shapes_to_remove:
                            return
                        # 先从画布移除
                        for shape in shapes_to_remove:
                            try:
                                self.canvas.delete_shape(shape)
                            except Exception:
                                try:
                                    self.canvas.shapes.remove(shape)
                                except ValueError:
                                    pass
                        # 再更新对象列表
                        label_list = getattr(self, "label_list", None)
                        if label_list is not None:
                            try:
                                label_list.begin_bulk_update()
                            except Exception:
                                pass
                        try:
                            self.remove_labels(shapes_to_remove)
                        finally:
                            if label_list is not None:
                                try:
                                    label_list.end_bulk_update()
                                except Exception:
                                    pass
                        if hasattr(self, "unique_label_list"):
                            try:
                                if not any(getattr(shape, "primary_label", "") == label for shape in self.canvas.shapes):
                                    for item in self.unique_label_list.find_items_by_label(label):
                                        row = self.unique_label_list.row(item)
                                        self.unique_label_list.takeItem(row)
                            except Exception:
                                pass
                        self._suppress_stats_autoselect_once = True
                        self.update_statistics()

                    # 清理旧的 mask/keli 轮廓，加载新的分割结果
                    logger.warning("[SAM2] Removing existing mask/keli shapes before loading new results.")
                    _remove_existing_label_shapes("mask")
                    _remove_existing_label_shapes("keli")
                    new_shapes = [_build_shape(data) for data in base_shapes]
                    if not new_shapes:
                        logger.warning("[SAM2] No new shapes generated after conversion.")
                        QtWidgets_QMessageBox.information(
                            self, "无结果", "未生成任何可用于显示的轮廓。"
                        )
                        return

                    self.load_shapes(new_shapes, replace=False)
                    self.set_dirty()
                    self.paint_canvas()
                    QApplication.processEvents()
                    self.status(f"检测到 {len(new_shapes)} 个轮廓，正在保存结果…")
                    QApplication.processEvents()
                    logger.warning("[SAM2] Loaded %s shapes onto canvas.", len(new_shapes))

                    # -------- 7. 查找同一样本的所有偏光角度图像 --------
                    current_path = Path(clean_path)
                    sample_dir = current_path.parent
                    
                    # 获取基础文件名（去除偏光角度后缀）
                    base_name = current_path.stem
                    # 清理文件名中的换行符和特殊字符
                    base_name = base_name.replace('\n', '').replace('\r', '').strip()
                    # 移除可能的偏光角度标识（如_S, _P, _0, _45, _90等）
                    import re
                    base_name = re.sub(r'_[SP]$|_\d+$', '', base_name)
                    
                    # 查找所有相关的偏光角度图像
                    all_images = []
                    
                    # 为了避免glob模式问题，使用os.listdir方法
                    import os
                    try:
                        # 获取目录下所有文件
                        all_files = os.listdir(str(sample_dir))
                        for filename in all_files:
                            file_path = sample_dir / filename
                            if file_path.is_file():
                                # 检查文件扩展名
                                ext = file_path.suffix.lower()
                                if ext in ['.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp']:
                                    # 检查文件名是否以base_name开头
                                    clean_filename = filename.replace('\n', '').replace('\r', '').strip()
                                    clean_stem = file_path.stem.replace('\n', '').replace('\r', '').strip()
                                    if clean_stem.startswith(base_name):
                                        all_images.append(file_path)
                    except Exception as e:
                        # print(f"读取目录时出错: {e}")
                        # 作为备选方案，至少包含当前文件
                        all_images = [current_path]
                    
                    if not all_images:
                        all_images = [current_path]  # 至少包含当前图像
                    
                    # -------- 8. 保存分割结果到所有相关图像 --------
                    saved_files = set()
                    label_targets = {}
                    for img_file in all_images:
                        try:
                            label_path_str = self._label_path_for_image(str(img_file))
                        except Exception:
                            label_path_str = str(img_file.with_suffix(".json"))
                        label_path = Path(label_path_str)
                        label_targets.setdefault(label_path, []).append(img_file)

                    shapes_payload, flags_payload = self._get_current_shapes_and_flags()
                    if not shapes_payload:
                        sync_flag = bool(getattr(self, "sync_pplxpl", False))
                        shapes_payload = []
                        for s in base_shapes:
                            raw_labels = s.get("labels")
                            if raw_labels is None:
                                raw_label = s.get("label", "")
                                raw_labels = [raw_label] if raw_label else []
                            points = [
                                [float(pt[0]), float(pt[1])]
                                for pt in s.get("points", [])
                            ]
                            shapes_payload.append(
                                {
                                    "labels": raw_labels,
                                    "text": "",
                                    "points": points,
                                    "shape_type": s.get("type", "polygon"),
                                    "flags": {},
                                    "other_data": {
                                        "pplxpl_sync": sync_flag,
                                        "source": "sam2",
                                    },
                                }
                            )
                        flags_payload = {}

                    for label_path, images in label_targets.items():
                        if not images:
                            continue
                        representative_image = str(images[0])
                        try:
                            use_folder_label = self._should_use_folder_label_file(representative_image)
                        except Exception:
                            use_folder_label = False

                        label_dir = label_path.parent
                        if label_dir and not label_dir.exists():
                            label_dir.mkdir(parents=True, exist_ok=True)

                        if use_folder_label:
                            image_path_value = FOLDER_SYNC_SENTINEL
                            image_height = self.image.height() if hasattr(self, "image") and self.image else h
                            image_width = self.image.width() if hasattr(self, "image") and self.image else w
                        else:
                            try:
                                image_path_value = os.path.relpath(
                                    representative_image,
                                    str(label_path.parent),
                                )
                            except ValueError:
                                image_path_value = representative_image
                            image_height = h
                            image_width = w

                        label_file = LabelFile()
                        label_file.image_labels = self.other_data.get("image_labels", [])
                        other_data = dict(self.other_data)
                        if use_folder_label:
                            other_data["folderSync"] = True
                        else:
                            other_data.pop("folderSync", None)

                        label_file.save(
                            filename=str(label_path),
                            shapes=shapes_payload,
                            image_path=image_path_value,
                            image_data=None,
                            image_height=image_height,
                            image_width=image_width,
                            other_data=other_data,
                            flags=flags_payload,
                        )
                        saved_files.add(str(label_path))

                        # 更新文件列表勾选状态
                        for img_file in images:
                            try:
                                items = self.file_list_widget.findItems(str(img_file), Qt.MatchExactly)
                                if items:
                                    items[0].setCheckState(Qt.Checked)
                            except Exception:
                                continue
                        
                    saved_list = sorted(saved_files)
                    QtWidgets_QMessageBox.information(
                        self, "完成", 
                        f"分割完成（{size_label}）\n"
                        f"已更新 {len(saved_files)} 个标注文件：\n" + 
                        "\n".join([f"• {Path(f).name}" for f in saved_list[:5]]) +
                        (f"\n... 共{len(saved_files)}个文件" if len(saved_files) > 5 else "")
                    )
                    self.status("SAM-2 分割完成")
                    logger.warning("[SAM2] Completed. Saved %s files.", len(saved_files))
                    break  # 成功 → 退出 while

                except (RuntimeError, torch.serialization.pickle.UnpicklingError):
                    # 权重损坏 → 删除重下再试
                    try_load += 1
                    ckpt_path.unlink(missing_ok=True)
                    if try_load >= 2 or not download_with_check(
                            ckpt_url, ckpt_path, "权重损坏，重新下载…"
                    ):
                        raise  # 重试也失败/取消 → 抛给外层 except

        except Exception as e:  # —— 外层 except ——
            import traceback
            error_msg = f"一键分割时发生错误:\n{str(e)}\n\n详细错误信息:\n{traceback.format_exc()}"
            logger.error("[SAM2] Segment-all failed: %s", error_msg)
            # print(error_msg)  # 在控制台输出详细错误信息
            
            # 🚀 优化：更安全的错误显示
            try:
                QtWidgets_QMessageBox.critical(self, "一键分割错误", error_msg)
            except Exception as msg_error:
                # print(f"显示错误对话框失败: {msg_error}")
                # 如果对话框显示失败，至少确保应用不会崩溃
                pass

        finally:  # —— 外层 finally ——
            # 🚀 优化：更安全的内存清理
            try:
                import gc
                gc.collect()
            except:
                pass
            
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except:
                pass
            
            # 恢复UI状态
            def _restore_ui_state():
                try:
                    QApplication.restoreOverrideCursor()
                except Exception:
                    pass
                try:
                    self.canvas.set_loading(False)
                except Exception:
                    pass
                try:
                    self.status("")
                except Exception:
                    pass
                try:
                    self.actions.segment_all.setEnabled(True)
                except Exception:
                    pass

            try:
                QtCore.QTimer.singleShot(0, _restore_ui_state)
            except Exception:
                _restore_ui_state()

    # def _update_canvas_image_simple(self, filename):
    #     """Simplified image update for cases without custom shapes."""
    #     image_data = LabelFile.load_image_file(filename)
    #     image = QtGui.QImage.fromData(image_data) if image_data else QtGui.QImage()

    #     pixmap = None
    #     if hasattr(self, 'sync_pplxpl') and self.sync_pplxpl:
    #         pixmap = self._load_pplxpl_overlay(osp.dirname(filename))
    #     if pixmap is None:
    #         pixmap = QtGui.QPixmap.fromImage(image)

    #     if pixmap.isNull():
    #         return False

    #     self.image = pixmap.toImage()
    #     self.image_path = filename
    #     self.image_data = image_data
        
    #     # 只加载JSON文件中的标注
    #     if hasattr(self, 'sync_pplxpl') and self.sync_pplxpl:
    #         label_file = osp.splitext(filename)[0] + ".json"
    #         if self.output_dir:
    #             label_file_without_path = osp.basename(label_file)
    #             label_file = osp.join(self.output_dir, label_file_without_path)
        
    #         if QtCore.QFile.exists(label_file) and LabelFile.is_label_file(label_file):
    #             try:
    #                 self.label_file = LabelFile(label_file)
    #                 self.canvas.load_pixmap(pixmap, clear_shapes=True)
    #                 self.load_labels(self.label_file.shapes)
    #                 self.paint_canvas()
    #                 self.prev_image_size = (self.image.width(), self.image.height())
    #                 return True
    #             except Exception:
    #                 pass
        
    #     # 清空canvas并加载图像
    #     self.canvas.load_pixmap(pixmap, clear_shapes=True)
    #     self.paint_canvas()
    #     self.prev_image_size = (self.image.width(), self.image.height())
    #     return True

    def crop_image(self, _):
        """裁剪图像 - 根据文件夹同步模式决定行为"""
        if not self.image_data:
            QtWidgets.QMessageBox.warning(self, self.tr("Warning"), self.tr("Please load an image first."))
            return
        
        # 检查是否存在标注轮廓
        if not self.no_shape():
            QtWidgets.QMessageBox.warning(
                self, 
                "警告", 
                "当前图像存在标注轮廓，无法执行裁剪操作。\n请先删除所有标注轮廓后再进行裁剪。"
            )
            return
        
        # 根据文件夹同步模式决定裁剪行为
        if hasattr(self, 'sync_pplxpl') and self.sync_pplxpl:
            # 文件夹同步模式：应用到文件夹中的所有图像
            dialog = CropDialog(
                self.image_data,
                self.on_crop_folder_applied,
                parent=self,
                is_folder_mode=True,
            )
        else:
            # 单图像模式：只裁剪当前图像
            dialog = CropDialog(
                self.image_data,
                self.on_crop_applied,
                parent=self,
                is_folder_mode=False,
            )
        # 注入上一张/下一张回调，便于在裁剪时切换图像
        # 为裁剪界面注入“上一张/下一张”的取图回调：
        # 返回下一张/上一张的原始图像数据（bytes）或QImage，由对话框内部直接刷新，不关闭弹窗。
        # 传递整个文件列表与当前路径，交由对话框内部自行读取与切换（不卡主线程，标题可显示文件名）
        try:
            dialog.set_image_list(self.image_list, self.image_path)
        except Exception:
            # 回退到旧接口（极端情况）
            def fetch_prev():
                try:
                    if len(self.image_list) == 0:
                        return None
                    current_path = self.image_path
                    idx = self.image_list.index(current_path) if current_path in self.image_list else -1
                    if idx <= 0:
                        return None
                    prev_path = self.image_list[idx - 1]
                    with open(prev_path, 'rb') as f:
                        return f.read()
                except Exception:
                    return None

            def fetch_next():
                try:
                    if len(self.image_list) == 0:
                        return None
                    current_path = self.image_path
                    idx = self.image_list.index(current_path) if current_path in self.image_list else -1
                    if idx < 0 or idx >= len(self.image_list) - 1:
                        return None
                    next_path = self.image_list[idx + 1]
                    with open(next_path, 'rb') as f:
                        return f.read()
                except Exception:
                    return None
            dialog.set_navigate_callbacks(fetch_prev, fetch_next)
        
        dialog.exec_()

    def on_crop_applied(self, qimage, crop_rect):
        """当裁剪应用到当前图像时的回调"""
        try:
            # 保存裁剪后的图像到文件
            if self.filename:
                # 转换为PIL图像并保存
                pil_img = utils.img_qt_to_pil(qimage)
                pil_img.save(self.filename)
            
            # 更新图像数据
            self.image_data = utils.img_qt_to_data(qimage)
            self.image = qimage
            
            # 更新canvas
            pixmap = QtGui.QPixmap.fromImage(qimage)
            self.canvas.load_pixmap(pixmap, clear_shapes=True)
            self.paint_canvas()
            
            # 调整标注坐标
            self.adjust_shapes_for_crop(crop_rect)
            
            # 标记为已修改
            self.set_dirty()
            
            QtWidgets.QMessageBox.information(
                self, 
                self.tr("Success"), 
                self.tr("Image cropped successfully.")
            )
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, 
                self.tr("Error"), 
                self.tr("Failed to crop image: {}").format(str(e))
            )

    def on_crop_folder_applied(self, qimage, crop_rect):
        """当裁剪应用到文件夹时的回调"""
        try:
            # 获取当前图像所在文件夹
            current_dir = osp.dirname(self.filename)
            if not current_dir:
                QtWidgets.QMessageBox.warning(
                    self, 
                    self.tr("Warning"), 
                    self.tr("Cannot determine image folder.")
                )
                return
            
            # 获取文件夹中的所有图像文件
            image_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif']
            image_files = []
            
            for file in os.listdir(current_dir):
                if any(file.lower().endswith(ext) for ext in image_extensions):
                    image_files.append(osp.join(current_dir, file))
            
            if not image_files:
                QtWidgets.QMessageBox.warning(
                    self, 
                    self.tr("Warning"), 
                    self.tr("No image files found in the folder.")
                )
                return
            
            # 确认对话框
            reply = QtWidgets.QMessageBox.question(
                self,
                self.tr("Confirm Crop"),
                self.tr("Apply crop to {} images in the folder?").format(len(image_files)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No
            )
            
            if reply != QtWidgets.QMessageBox.Yes:
                return
            
            # 创建进度对话框
            progress = QtWidgets.QProgressDialog(
                self.tr("Cropping images..."),
                self.tr("Cancel"),
                0,
                len(image_files),
                self
            )
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            
            processed_count = 0
            for i, image_path in enumerate(image_files):
                progress.setValue(i)
                QtWidgets.QApplication.processEvents()
                
                if progress.wasCanceled():
                    break
                
                try:
                    # 加载图像
                    img_data = LabelFile.load_image_file(image_path)
                    if not img_data:
                        continue
                    
                    # 转换为PIL图像
                    pil_img = utils.img_data_to_pil(img_data)
                    
                    # 裁剪图像
                    cropped_img = pil_img.crop((
                        crop_rect.x(),
                        crop_rect.y(),
                        crop_rect.x() + crop_rect.width(),
                        crop_rect.y() + crop_rect.height()
                    ))
                    
                    # 保存裁剪后的图像
                    cropped_img.save(image_path)
                    
                    processed_count += 1
                    
                except Exception as e:
                    print(f"Failed to crop {image_path}: {e}")
                    continue
            
            progress.setValue(len(image_files))
            
            # 更新当前图像
            self.on_crop_applied(qimage, crop_rect)
            
            QtWidgets.QMessageBox.information(
                self,
                self.tr("Success"),
                self.tr("Successfully cropped {} images.").format(processed_count)
            )
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to crop folder: {}").format(str(e))
            )

    def adjust_shapes_for_crop(self, crop_rect):
        """调整标注形状以适应裁剪后的图像"""
        if not hasattr(self, 'label_list') or not self.label_list:
            return
        
        # 获取裁剪区域
        crop_x, crop_y = crop_rect.x(), crop_rect.y()
        crop_width, crop_height = crop_rect.width(), crop_rect.height()
        
        # 调整所有形状的坐标
        for i in range(len(self.label_list)):
            item = self.label_list[i]
            if hasattr(item, 'shape'):
                shape = item.shape()
                if shape:
                    # 调整形状的点坐标
                    new_points = []
                    for point in shape.points:
                        new_x = point.x() - crop_x
                        new_y = point.y() - crop_y
                        
                        # 确保点在新的图像范围内
                        new_x = max(0, min(new_x, crop_width))
                        new_y = max(0, min(new_y, crop_height))
                        
                        new_points.append(QtCore.QPointF(new_x, new_y))
                    
                    # 更新形状的点
                    shape.points = new_points
                    if hasattr(shape, "invalidate_path_cache"):
                        shape.invalidate_path_cache()
                    shape.close()
        
        # 更新canvas
        self.canvas.update()
        self.update_statistics()

    def _can_invert_shape(self, shape):
        """检查形状是否可以进行反选操作"""
        if not shape:
            return False
        # 只有封闭的多边形、矩形和圆形可以进行反选
        return shape.shape_type in ["polygon", "rectangle", "circle"] and shape.is_closed()

    def invert_selection(self):
        """反选功能已取消。"""
        self.error_message(self.tr("功能已取消"), self.tr("反选功能已被禁用"))

    def _get_shape_points_as_polygon(self, shape):
        """将任意支持的封闭形状转换为多边形点列表"""
        from PyQt5.QtCore import QPointF
        import math
        if not shape or not self._can_invert_shape(shape):
            return []
        if shape.shape_type == "polygon":
            return [QPointF(p.x(), p.y()) for p in shape.points]
        if shape.shape_type == "rectangle" and len(shape.points) >= 2:
            p1, p2 = shape.points[0], shape.points[1]
            x1, y1 = min(p1.x(), p2.x()), min(p1.y(), p2.y())
            x2, y2 = max(p1.x(), p2.x()), max(p1.y(), p2.y())
            return [QPointF(x1, y1), QPointF(x2, y1), QPointF(x2, y2), QPointF(x1, y2)]
        if shape.shape_type == "circle" and len(shape.points) >= 2:
            cx = (shape.points[0].x() + shape.points[1].x()) / 2.0
            cy = (shape.points[0].y() + shape.points[1].y()) / 2.0
            rx = abs(shape.points[1].x() - shape.points[0].x()) / 2.0
            ry = abs(shape.points[1].y() - shape.points[0].y()) / 2.0
            pts = []
            for i in range(72):  # 72点，5°一个点
                ang = (i * 5.0) * math.pi / 180.0
                pts.append(QPointF(cx + rx * math.cos(ang), cy + ry * math.sin(ang)))
            return pts
        return []

    def _create_background_with_all_holes(self):
        """创建一个带洞的背景轮廓：
        - 外边界：整张图像矩形
        - 内洞：当前画布上所有封闭形状（polygon/rectangle/circle）
        这样效果就是“除了所有已有轮廓外，其余区域作为一个新的轮廓”，
        既满足“不覆盖其它既有标注”，也包含当前选中颗粒作为洞。
        """
        from PyQt5.QtCore import QPointF

        if not hasattr(self, 'image') or self.image.isNull():
            return None

        w, h = self.image.width(), self.image.height()

        # 构建外边界
        outer = [QPointF(0, 0), QPointF(w - 1, 0), QPointF(w - 1, h - 1), QPointF(0, h - 1)]
        bg = Shape(shape_type="polygon")
        for p in outer:
            bg.add_point(p)
        bg.close()

        # 收集所有洞（所有封闭形状）
        holes = []
        for s in self.canvas.shapes:
            if not self._can_invert_shape(s):
                continue
            pts = self._get_shape_points_as_polygon(s)
            if pts and len(pts) >= 3:
                holes.append([[float(p.x()), float(p.y())] for p in pts])

        if holes:
            bg.other_data = bg.other_data or {}
            bg.other_data["holes"] = holes
            if hasattr(bg, "invalidate_path_cache"):
                bg.invalidate_path_cache()
        # 允许“洞内点击也选中”的特性，用于反选背景轮廓
        if bg.other_data is None:
            bg.other_data = {}
        bg.other_data["select_through_holes"] = True

        # 反选生成的形状：稍微加密外环与洞的点（不改变外观，仅插入中间点）
        try:
            densify_cfg = self._config.get("invert_densify", {}) if hasattr(self, '_config') else {}
            enabled = densify_cfg.get("enabled", True)
            max_len = float(densify_cfg.get("max_segment_length", 10.0))  # 反选默认稍微更密
            include_holes = bool(densify_cfg.get("include_holes", True))
            if enabled:
                bg.densify_edges(max_len, include_holes=include_holes)
        except Exception:
            pass

        return bg


    
    def _create_inverted_shapes_from_mask(self, roi_shape=None):
        """使用mask算法创建反选形状

        roi_shape: 可选，用于限定反选的ROI，仅在该形状范围内生成反选区域；
        如果为None，则在整幅图像范围内进行（可能会得到一个大的背景区域）。
        """
        try:
            import cv2
            import numpy as np
            from PyQt5.QtCore import QPointF
        except ImportError:
            self.error_message(
                self.tr("导入错误"),
                self.tr("需要安装 OpenCV 库才能使用反选功能")
            )
            return []
        
        # 获取图像尺寸
        image_width = self.image.width()
        image_height = self.image.height()
        
        # 创建空白mask
        if roi_shape is not None and self._can_invert_shape(roi_shape):
            # 在选中形状范围内做反选：ROI内部为白色，其余为黑色
            mask = np.zeros((image_height, image_width), dtype=np.uint8)
            roi_mask = self._shape_to_mask(roi_shape, image_width, image_height)
            mask[roi_mask > 0] = 255
        else:
            # 全图范围：所有像素都是白色/255，表示未标注
            mask = np.ones((image_height, image_width), dtype=np.uint8) * 255
        
        # 将所有已有标注区域在mask中标记为黑色（0）
        for shape in self.canvas.shapes:
            if not self._can_invert_shape(shape):
                continue
            # 如果提供了ROI，仅处理与ROI相交的形状
            if roi_shape is not None:
                # 粗略通过外接矩形快速判定是否相交
                def bbox(points):
                    xs = [p.x() for p in points]
                    ys = [p.y() for p in points]
                    return min(xs), min(ys), max(xs), max(ys)
                rx1, ry1, rx2, ry2 = bbox(roi_shape.points[:2] if roi_shape.shape_type in ["rectangle", "circle"] else roi_shape.points)
                sx1, sy1, sx2, sy2 = bbox(shape.points[:2] if shape.shape_type in ["rectangle", "circle"] else shape.points)
                if sx2 < rx1 or sx1 > rx2 or sy2 < ry1 or sy1 > ry2:
                    continue
            # 保留被选中的形状作为边界，不将其挖空
            if roi_shape is not None and shape is roi_shape:
                continue
            # 将形状转换为mask并从总mask中减去
            shape_mask = self._shape_to_mask(shape, image_width, image_height)
            mask[shape_mask > 0] = 0
        
        # 保存处理前的mask用于调试
        mask_before_morphology = mask.copy()
        
        # 对未标注区域进行形态学处理（适中的腐蚀膨胀，保持形状细节）
        kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        
        # 轻微腐蚀：去除小的噪声区域但保持主要形状
        mask = cv2.erode(mask, kernel_small, iterations=1)
        # 轻微膨胀：恢复边界
        mask = cv2.dilate(mask, kernel_small, iterations=1)
        
        # 启用调试模式：保存mask图像以便检查
        debug_enabled = True  # 设置为 False 关闭调试
        if debug_enabled:
            try:
                cv2.imwrite("debug_mask_original.png", np.ones((image_height, image_width), dtype=np.uint8) * 255)
                cv2.imwrite("debug_mask_with_shapes.png", mask_before_morphology)
                cv2.imwrite("debug_mask_final.png", mask)
                logger.info("调试图像已保存: debug_mask_*.png")
            except Exception as e:
                logger.warning(f"保存调试图像失败: {e}")
        
        # 查找轮廓（使用层次结构以支持内部洞）
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        
        logger.info(f"找到 {len(contours)} 个轮廓")
        
        # 将轮廓转换为Shape对象
        inverted_shapes = []
        for i, contour in enumerate(contours):
            # 只处理外轮廓（忽略内部洞，hierarchy[0][i][3] == -1 表示外轮廓）
            if hierarchy is None or len(hierarchy) == 0:
                parent_idx = -1
            else:
                parent_idx = hierarchy[0][i][3]
            if parent_idx != -1:
                continue
                
            # 过滤掉太小的轮廓（面积小于500像素，增加阈值避免细碎片段）
            area = cv2.contourArea(contour)
            if area < 500:
                logger.info(f"跳过小轮廓，面积: {area}")
                continue
            
            logger.info(f"处理轮廓 {i}, 面积: {area}")
            
            # 适度简化轮廓（保持形状细节）
            epsilon = 0.002 * cv2.arcLength(contour, True)  # 减少简化程度
            simplified_contour = cv2.approxPolyDP(contour, epsilon, True)
            
            logger.info(f"原始点数: {len(contour)}, 简化后点数: {len(simplified_contour)}")
            
            # 创建多边形形状，并查找其内部洞（子轮廓）
            shape = Shape(shape_type="polygon")
            
            # 添加轮廓点
            for point in simplified_contour:
                x, y = point[0]
                shape.add_point(QPointF(float(x), float(y)))
            
            shape.close()

            # 收集作为“洞”的子轮廓
            holes = []
            if hierarchy is not None and len(hierarchy) > 0:
                child_idx = hierarchy[0][i][2]  # 第一个子轮廓
                while child_idx != -1:
                    hole_contour = contours[child_idx]
                    hole_area = cv2.contourArea(hole_contour)
                    if hole_area >= 200:  # 忽略极小洞
                        epsilon_h = 0.002 * cv2.arcLength(hole_contour, True)
                        simp_hole = cv2.approxPolyDP(hole_contour, epsilon_h, True)
                        holes.append([[float(pt[0][0]), float(pt[0][1])] for pt in simp_hole])
                    child_idx = hierarchy[0][child_idx][0]  # 下一个兄弟

            if holes:
                shape.other_data = shape.other_data or {}
                shape.other_data["holes"] = holes
                if hasattr(shape, "invalidate_path_cache"):
                    shape.invalidate_path_cache()

            # 反选（掩码）生成的形状：稍微加密外环与洞的点（不改变外观，仅插入中间点）
            try:
                densify_cfg = self._config.get("invert_densify", {}) if hasattr(self, '_config') else {}
                enabled = densify_cfg.get("enabled", True)
                max_len = float(densify_cfg.get("max_segment_length", 10.0))
                include_holes = bool(densify_cfg.get("include_holes", True))
                if enabled:
                    shape.densify_edges(max_len, include_holes=include_holes)
            except Exception:
                pass

            inverted_shapes.append(shape)
            
            # 调试信息：打印形状的边界框
            min_x = min(p.x() for p in shape.points)
            max_x = max(p.x() for p in shape.points)
            min_y = min(p.y() for p in shape.points)
            max_y = max(p.y() for p in shape.points)
            logger.info(f"创建多边形: 点数={len(shape.points)}, 边界框=({min_x:.1f},{min_y:.1f})-({max_x:.1f},{max_y:.1f})")
        
        return inverted_shapes
    
    def _shape_to_mask(self, shape, width, height):
        """将形状转换为二值mask"""
        try:
            import cv2
            import numpy as np
        except ImportError:
            return np.zeros((height, width), dtype=np.uint8)
        
        mask = np.zeros((height, width), dtype=np.uint8)
        
        if shape.shape_type == "polygon":
            # 多边形
            points = np.array([[int(p.x()), int(p.y())] for p in shape.points], dtype=np.int32)
            cv2.fillPoly(mask, [points], 255)
            
        elif shape.shape_type == "rectangle":
            # 矩形
            if len(shape.points) >= 2:
                p1, p2 = shape.points[0], shape.points[1]
                x1, y1 = int(min(p1.x(), p2.x())), int(min(p1.y(), p2.y()))
                x2, y2 = int(max(p1.x(), p2.x())), int(max(p1.y(), p2.y()))
                cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
                
        elif shape.shape_type == "circle":
            # 圆形
            if len(shape.points) >= 2:
                center_x = int((shape.points[0].x() + shape.points[1].x()) / 2)
                center_y = int((shape.points[0].y() + shape.points[1].y()) / 2)
                radius_x = int(abs(shape.points[1].x() - shape.points[0].x()) / 2)
                radius_y = int(abs(shape.points[1].y() - shape.points[0].y()) / 2)
                radius = int((radius_x + radius_y) / 2)  # 平均半径
                cv2.circle(mask, (center_x, center_y), radius, 255, -1)
        
        return mask
    
    def enable_shared_edges(self, value):
        """Enable or disable shared edge functionality (clipping and vertex reuse)"""
        print(f"[MENU] enable_shared_edges called with value={value}")
        logger.info(f"[MENU] enable_shared_edges called with value={value}")
        self.canvas.set_shared_edges_enabled(value)
        self._config["shared_edges_enabled"] = value
        save_config(self._config)
    
    
    def toggle_edge_snapping(self, value):
        """Enable or disable edge snapping when drawing"""
        print(f"[MENU] toggle_edge_snapping called with value={value}")
        logger.info(f"[MENU] toggle_edge_snapping called with value={value}")
        self.canvas.set_edge_snapping_enabled(value)
        self._config["edge_snapping_enabled"] = value
        save_config(self._config)

    
