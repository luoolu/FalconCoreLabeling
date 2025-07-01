"""
Wrapper around LabelingWidget:
- 嵌入 LabelingWidget
- monkey-patch LabelingWidget.menu() / statusBar()，消除父链错误
- 提供 current_image_path() 等辅助方法
"""

from pathlib import Path
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QVBoxLayout, QWidget, QStatusBar
from .label_widget import LabelingWidget
from . import utils


# ----------------------------------------------------------------------
# Monkey-patch：确保 LabelingWidget 在任何父链情况下都能安全建菜单
# ----------------------------------------------------------------------
def _safe_menu(self, title, actions=None):
    """
    替换 LabelingWidget.menu().
    始终把菜单加到 **self.main_window.menuBar()**，这是 AnyLabeling
    原本显示菜单的位置；若尚无 menuBar() 则即时创建。
    """
    bar = self.main_window.menuBar()
    menu = bar.addMenu(title)
    if actions:
        utils.add_actions(menu, actions)
    return menu


def _safe_statusBar(self):
    """始终返回 self.main_window.statusBar()，不存在则临时创建。"""
    return self.main_window.statusBar() or QStatusBar()


if not hasattr(LabelingWidget, "_patched_safe_menu"):
    LabelingWidget.menu = _safe_menu
    LabelingWidget._patched_safe_menu = True

if not hasattr(LabelingWidget, "_patched_safe_statusbar"):
    LabelingWidget.statusBar = _safe_statusBar
    LabelingWidget._patched_safe_statusbar = True


# ----------------------------------------------------------------------
# LabelingWrapper
# ----------------------------------------------------------------------
class LabelingWrapper(QWidget):
    """Embed LabelingWidget and expose helpers"""

    def __init__(
        self,
        parent,
        config=None,
        filename=None,
        output=None,
        output_file=None,
        output_dir=None,
    ):
        super().__init__(parent)

        # 传 MainWindow 作为 parent，让 LabelingWidget 能找到 window()
        self.viewer = LabelingWidget(
            self.window(),
            config=config,
            filename=filename,
            output=output,
            output_file=output_file,
            output_dir=output_dir,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.viewer)

    # ------------------------------------------------------------------
    # helper：返回当前图像绝对路径
    # ------------------------------------------------------------------
    def current_image_path(self) -> str | None:
        # 官方 API
        for api in ("get_current_filename", "current_filename", "currentFile"):
            if hasattr(self.viewer, api):
                try:
                    p = getattr(self.viewer, api)()
                    if p and Path(p).is_file():
                        return str(Path(p).resolve())
                except Exception:
                    pass
        # 直接属性
        p = getattr(self.viewer, "filename", None)
        if p and Path(p).is_file():
            return str(Path(p).resolve())
        # canvas 常见字段
        canvas = getattr(self.viewer, "canvas", None)
        if canvas:
            for attr in ("image_path", "img_path", "filename"):
                p = getattr(canvas, attr, None)
                if p and Path(p).is_file():
                    return str(Path(p).resolve())
        return None
