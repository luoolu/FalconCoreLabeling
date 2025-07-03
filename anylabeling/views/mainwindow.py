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
    dlg.close()


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
        self.menuBar()

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

        # ---------- 状态栏 ----------
        status = QStatusBar()
        status.showMessage(f"{__appname__} – {__appdescription__}")
        self.setStatusBar(status)

        # ---------- SAM-2 缓存 ----------
        self._sam2_predictor = None
        self._sam2_mask_gen = None

        # ---------- 延迟插入菜单 ----------
        QTimer.singleShot(0, self._insert_segment_menu)

    # ------------------------------------------------------------------
    # showEvent：窗口展示后再做一次彻底清理
    # ------------------------------------------------------------------
    def showEvent(self, event: QEvent):
        super().showEvent(event)
        # 立即 & 200 ms 后各清理一次，保证捕获所有延迟创建的工具栏
        QTimer.singleShot(0, self._remove_segment_toolbar)
        QTimer.singleShot(200, self._remove_segment_toolbar)

    # ------------------------------------------------------------------
    # 插入 “Segment All” 到菜单栏
    # ------------------------------------------------------------------
    def _insert_segment_menu(self):
        menubar = self.menuBar()
        actions = menubar.actions()
        help_idx = None
        for i, act in enumerate(actions):
            if act.text().replace("&", "").lower() in ("help", "帮助"):
                help_idx = i
                break

        if help_idx is not None and help_idx + 1 < len(actions):
            menubar.insertAction(actions[help_idx + 1], self.segment_action)
        else:
            menubar.addAction(self.segment_action)

    # ------------------------------------------------------------------
    # 删除旧的工具栏
    # ------------------------------------------------------------------
    def _remove_segment_toolbar(self):
        for tb in self.findChildren(QToolBar):
            # 检查 QAction
            for act in list(tb.actions()):
                if act.text() == "Segment All":
                    tb.removeAction(act)

            # 检查内部 QPushButton
            remove_tb = False
            for btn in tb.findChildren(QPushButton):
                if btn.text() == "Segment All":
                    remove_tb = True
                    break

            # 若 toolbar 只剩无用内容，则移除整条工具栏
            if remove_tb or (not tb.actions() and not tb.findChildren(QPushButton)):
                self.removeToolBar(tb)

    # ------------------------------------------------------------------
    # Segment-All 主逻辑（配置目录指向 sam2/configs）
    # ------------------------------------------------------------------
    def segment_all_instances(self):
        """Segment current image with SAM-2 and draw masks."""
        # ---------- ① 当前图像 ----------
        image_path = self.labeling_widget.current_image_path()
        if not image_path:
            QMessageBox.warning(self, "未找到图像", "请先在界面打开一张图像。")
            return

        # ---------- ② checkpoint ----------
        from pathlib import Path
        ckpt_url = ("https://dl.fbaipublicfiles.com/segment_anything_2/092824/"
                    "sam2.1_hiera_large.pt")
        model_dir = Path.home() / "anylabeling_data" / "models"
        ckpt_path = model_dir / "sam2.1_hiera_large.pt"
        model_dir.mkdir(parents=True, exist_ok=True)

        if not ckpt_path.exists():
            if not download_with_progress(self, ckpt_url, ckpt_path, "SAM-2 权重"):
                return  # 用户取消

        # ---------- ③ UI 忙碌 ----------
        self.segment_action.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage("正在分割，请稍候…")

        try:
            # -------- 依赖 --------
            import cv2, json, torch, numpy as np, traceback
            from importlib import resources
            from hydra import initialize_config_dir
            from hydra.core.global_hydra import GlobalHydra
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
            from anylabeling.views.labeling.shape import Shape
            from PyQt5 import QtCore

            device = "cuda" if torch.cuda.is_available() else "cpu"

            # -------- Hydra 初始化（指向 sam2/configs） --------
            if GlobalHydra.instance().is_initialized():
                GlobalHydra.instance().clear()
            cfg_root = resources.files("sam2") / "configs"
            initialize_config_dir(config_dir=str(cfg_root), version_base="1.2")

            # -------- 构建模型 --------
            if self._sam2_predictor is None:
                model = build_sam2(
                    config_file="sam2.1/sam2.1_hiera_l",  # 相对 configs 根目录
                    ckpt_path=str(ckpt_path),
                    device=device,
                )
                model.eval()
                self._sam2_predictor = SAM2ImagePredictor(model)

            # -------- Mask 生成器 --------
            if self._sam2_mask_gen is None:
                self._sam2_mask_gen = SAM2AutomaticMaskGenerator(
                    self._sam2_predictor.model,
                    points_per_side=64,
                    pred_iou_thresh=0.9,
                    stability_score_thresh=0.92,
                    min_mask_region_area=256,
                )

            # -------- 读取图像 --------
            img_bgr = cv2.imread(str(image_path))
            if img_bgr is None:
                raise IOError(f"无法读取图像：{image_path}")
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            h, w = img_rgb.shape[:2]

            # -------- 生成 masks --------
            masks = self._sam2_mask_gen.generate(img_rgb)
            if not masks:
                QMessageBox.information(self, "无结果", "未检测到任何实例掩码。")
                return

            # -------- 转 Shape & 保存 JSON --------
            shapes, js = [], {
                "version": "1.0",
                "shapes": [],
                "imagePath": str(image_path),
                "imageData": None,
                "imageHeight": h,
                "imageWidth": w,
                "imageLabels": [],
                "image_labels": [],
            }

            for m in masks:
                seg = (m["segmentation"] * 255).astype(np.uint8)
                cnts, _ = cv2.findContours(seg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for cnt in cnts:
                    pts = cnt.squeeze(1).tolist()
                    if len(pts) < 3:
                        continue
                    shp = Shape(labels=["mask"], text="", shape_type="polygon")
                    for x, y in pts:
                        shp.add_point(QtCore.QPointF(x, y))
                    shp.close()
                    shapes.append(shp)
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
            QMessageBox.information(self, "完成", f"分割完成，已保存：\n{json_path}")

        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "异常", str(e))

        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
            self.segment_action.setEnabled(True)





