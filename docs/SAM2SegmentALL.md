## sam2+sam segment all
需求：
1，增加一键分割所有实例，效果逼近Meta AI官方线上的分割效果；
2，如果有GPU使用GPU,否则使用CPU进行分割;
3，合理的前后处理，提高分割的准确性;
给出代码实现，需要修改的脚本，至少是给出完整函数和位置，方便复制粘贴替换；

## 结果差，没有更新结果到界面
现在是能走到QMessageBox.information(self, "完成", f"分割完成，已保存：{json_path}")，提示分割完成；
但是没有更新到界面上，需要重新打开图像才能看到结果；希望点击segment_all_button，转圈等待，分割完成把结果直接展示在界面上；
另外是分割效果特别差，远不如线上demo的效果

## add checkpoints dir 
checkpoints/
├── sam2.1_hiera_large.pt
└── sam2.1_hiera_large.yaml
## anylabeling_data/models
1,现在segment_all_instances 的SAM-2模型是从checkpoints/下面获取，不利于打包迁移；
├── sam2.1_hiera_large.pt
└── sam2.1_hiera_large.yaml
2,改为从anylabeling_data/models路径下获取，跟自动标注一样，没有就自动下载；
改为从"https://github.com/facebookresearch/sam2"下载yaml,
"https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt"下载模型文件
https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt
https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt
https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt
https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt
## modify anylabeling/views/mainwindow.py
'''
def segment_all_instances(self):
    """Segment with SAM-2 and immediately display result on canvas"""
    # Busy Cursor & disable button
    self.segment_button.setEnabled(False)
    self.app.setOverrideCursor(Qt.WaitCursor)
    QTimer.singleShot(50, QEventLoop().processEvents)

    try:
        # 1️⃣ 获取图像路径
        image_path = self.labeling_widget.current_image_path()
        if not image_path:
            QMessageBox.warning(self, "未找到图像", "请先在界面打开一张图像。")
            return

        # 2️⃣ 懒加载依赖
        import torch
        import cv2
        import numpy as np
        import json
        from pathlib import Path
        from PyQt5 import QtCore
        from hydra import initialize_config_dir
        from hydra.core.global_hydra import GlobalHydra
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        from anylabeling.views.labeling.shape import Shape  # Shape 定义 :contentReference[oaicite:2]{index=2}

        # 3️⃣ 读取图像
        img_bgr = cv2.imread(image_path)
        if img_bgr is None:
            QMessageBox.critical(self, "错误", f"无法读取图像 {image_path}")
            return
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        h, w = img_rgb.shape[:2]

        # 4️⃣ 初始化 / 复用 SAM-2，确保 Hydra 能加载项目根目录下的 checkpoints 目录
        base_dir = Path(__file__).resolve().parents[2]
        ckpt_dir = base_dir / "checkpoints"
        cfg_name = "sam2.1_hiera_large"
        ckpt = ckpt_dir / f"{cfg_name}.pt"
        if not ckpt.exists() or not (ckpt_dir / f"{cfg_name}.yaml").exists():
            QMessageBox.critical(
                self,
                "模型缺失",
                "请将 sam2.1_hiera_large.pt 与 sam2.1_hiera_large.yaml\n"
                "放入项目根目录下的 checkpoints/ 目录，或修改代码路径。",
            )
            return

        # 清理已有 Hydra 状态并注册 checkpoints 目录
        if GlobalHydra().is_initialized():
            GlobalHydra().clear()
        initialize_config_dir(config_dir=str(ckpt_dir), version_base="1.2")

        if self._sam2_predictor is None:
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            model = build_sam2(cfg_name, str(ckpt))
            self._sam2_predictor = SAM2ImagePredictor(model.to(device))

        if self._sam2_mask_gen is None:
            self._sam2_mask_gen = SAM2AutomaticMaskGenerator(
                self._sam2_predictor.model,
                points_per_side=64,
                pred_iou_thresh=0.9,
                stability_score_thresh=0.92,
                min_mask_region_area=256,
            )

        # 5️⃣ 生成 mask（视图限制最大 2048）
        MAX_WH = 2048
        if max(h, w) > MAX_WH:
            scale = MAX_WH / max(h, w)
            img_small = cv2.resize(img_rgb, (int(w * scale), int(h * scale)))
            masks = self._sam2_mask_gen.generate(img_small)
            # 放缩回原尺寸
            for m in masks:
                m["segmentation"] = cv2.resize(
                    m["segmentation"].astype(np.uint8),
                    (w, h),
                    interpolation=cv2.INTER_NEAREST,
                ).astype(bool)
        else:
            masks = self._sam2_mask_gen.generate(img_rgb)

        # 6️⃣ mask → Shape 对象列表 + JSON 保存结构
        shape_objs = []
        json_data = {
            "version": "1.0",
            "shapes": [],
            "imagePath": image_path,
            "imageData": None,
            "imageHeight": h,
            "imageWidth": w,
            "imageLabels": [],
            "image_labels": [],
        }

        for mask in masks:
            seg = mask["segmentation"]
            mask_np = seg.numpy() if hasattr(seg, "numpy") else seg

            contours, _ = cv2.findContours(
                (mask_np * 255).astype(np.uint8),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            for contour in contours:
                pts = contour.squeeze(1).tolist()
                if len(pts) < 3:
                    continue

                # —— 构建 Shape 对象 —— 
                shape = Shape(
                    labels=["mask"],         # 主标签
                    text="",                 # 可不使用 text 字段
                    shape_type="polygon",    # 多边形类型
                    group_id=None,           # 暂不分组
                )
                # 添加顶点并闭合
                for x, y in pts:
                    shape.add_point(QtCore.QPointF(x, y))
                shape.close()
                shape_objs.append(shape)

                # 同步构建 JSON dict
                json_data["shapes"].append({
                    "label": shape.primary_label,
                    "points": pts,
                    "type": shape.shape_type,
                    "line_color": None,
                    "fill_color": None,
                })

        # 7️⃣ 保存 JSON 文件
        json_path = Path(image_path).with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # 8️⃣ 立即显示到 UI
        # 注意：load_shapes 接受 Shape 对象列表
        self.labeling_widget.viewer.load_shapes(shape_objs, replace=False)

        QMessageBox.information(self, "完成", f"分割完成，已保存：{json_path}")

    finally:
        self.app.restoreOverrideCursor()
        self.segment_button.setEnabled(True)

'''
## anylabeling_data/models get model
### anylabeling/views/mainwindow.py
### def segment_all_instances(self)
'''
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
        ckpt_url  = ("https://dl.fbaipublicfiles.com/segment_anything_2/092824/"
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
                "imageWidth":  w,
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
                            "type":  shp.shape_type,
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

'''