### Summary

Adjusted SAM‑2 mask generator to reduce points per side when using the tiny or small model and cached the chosen value for reuse, improving performance with these sizes

Added logic to resize images larger than 2048 px before mask generation and then scale the masks back to the original size, which helps speed up processing for large images

'''
diff --git a/anylabeling/views/mainwindow.py b/anylabeling/views/mainwindow.py
index 7e71cf3e11fa1025afbe5e675a7a5d10655646e8..51d9116c26cdd2fcdb788f24fd714adca3c32c62 100755
--- a/anylabeling/views/mainwindow.py
+++ b/anylabeling/views/mainwindow.py
@@ -262,68 +262,88 @@ class MainWindow(QMainWindow):
 
             # -------- 4. Hydra 初始化 --------
             if GlobalHydra.instance().is_initialized():
                 GlobalHydra.instance().clear()
             cfg_root = resources.files("sam2") / "configs"
             initialize_config_dir(config_dir=str(cfg_root), version_base="1.2")
 
             # -------- 5. 加载 / 切换模型 --------
             need_reload = (
                     not hasattr(self, "_sam2_variant") or
                     self._sam2_variant != size or
                     self._sam2_predictor is None
             )
             if need_reload:
                 model = build_sam2(
                     config_file=cfg_name,
                     ckpt_path=str(ckpt_path),
                     device=device,
                 )
                 model.eval()
                 self._sam2_predictor = SAM2ImagePredictor(model)
                 self._sam2_mask_gen = None
                 self._sam2_variant = size
 
             # -------- 6. Mask 生成器 --------
-            if self._sam2_mask_gen is None:
+            pps = 64
+            if size in ("tiny", "small"):
+                pps = 32
+
+            if (
+                self._sam2_mask_gen is None
+                or getattr(self, "_sam2_mask_gen_pps", None) != pps
+            ):
                 self._sam2_mask_gen = SAM2AutomaticMaskGenerator(
                     self._sam2_predictor.model,
-                    points_per_side=64,
+                    points_per_side=pps,
                     pred_iou_thresh=0.9,
                     stability_score_thresh=0.92,
                     min_mask_region_area=256,
                 )
+                self._sam2_mask_gen_pps = pps
 
             # -------- 7. 读取图像 --------
             img_bgr = cv2.imread(str(image_path))
             if img_bgr is None:
                 raise IOError(f"无法读取图像：{image_path}")
             img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
             h, w = img_rgb.shape[:2]
 
-            # -------- 8. 生成 masks --------
-            masks = self._sam2_mask_gen.generate(img_rgb)
+            # -------- 8. 生成 masks (可缩放) --------
+            MAX_WH = 2048
+            if max(h, w) > MAX_WH:
+                scale = MAX_WH / max(h, w)
+                img_small = cv2.resize(img_rgb, (int(w * scale), int(h * scale)))
+                masks = self._sam2_mask_gen.generate(img_small)
+                for m in masks:
+                    m["segmentation"] = cv2.resize(
+                        m["segmentation"].astype(np.uint8),
+                        (w, h),
+                        interpolation=cv2.INTER_NEAREST,
+                    ).astype(bool)
+            else:
+                masks = self._sam2_mask_gen.generate(img_rgb)
             if not masks:
                 QMessageBox.information(self, "无结果", "未检测到任何实例掩码。")
                 return
 
             # -------- 9. 转 Shape & 保存 JSON --------
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
                 cnts, _ = cv2.findContours(seg, cv2.RETR_EXTERNAL,
                                            cv2.CHAIN_APPROX_SIMPLE)
                 for cnt in cnts:
                     pts = cnt.squeeze(1).tolist()
                     if len(pts) < 3:
                         continue
                     shp = Shape(labels=["mask"], text="", shape_type="polygon")
                     for x, y in pts:

'''











