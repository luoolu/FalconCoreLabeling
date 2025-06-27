## 基于版本
anylabeling v0.4.29

## 需求
需求1:目前是一个标注目标只能有一个标签，现在的需求1是使得单个标注目标可以打多个标签；
需求2:是增加可以给整张图打一个标签；
需求3:是增加在用户标注界面可以配置标签列表和切换已经内置的多个标签列表的功能；
切换标签列表后，标注框下的标签会随着标签列表的切换而切换；用户可以在列表中选择多个标签。
在标签列表选择一个标签后，在标注框下的标签会自动切换到选择的标签，在标签列表增加第二个标签时，
标注框中的标签会自动切换到第二个标签。 但需求是用户可以在标签列表中选择多个标签，还得方便删除选错的标签；
目前是选中列表中的一个标签后，手动写分隔符,手动输入的标签，保存的标注json中就保存了选中的标签和手动输入的标签；
但从选中列表中的一个标签后，手动写分隔符,再从标签列表中选择第二个标签，保存的标注json中还是最新选中的第二个标签，就一个标签;
切换label_sets后，标注框下的标签会随着label_sets的切换而切换，但现在标注一个实例后，
切换label_sets后，标注框的下拉列表中还有上一个实例的标签;
删除~/.anylabelingrc重新生成后，切换label_sets后，标注框下的下拉列表是空的，但右边的标签列表是都有的；
'''
anylabeling/views/labeling/label_widget.py
1711 line

    def update_label_dialog_labels(self):
        """Refresh label dialog list from current config."""
        self.label_dialog.label_list.clear()
        if self._config.get("labels"):
            self.label_dialog.label_list.addItems(self._config["labels"])
        if self.label_dialog._sort_labels:
            self.label_dialog.label_list.sortItems()

3144 line

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
'''
需求4:增加轮廓线粗细、填充mask透明度可在界面调节的功能;
需求5:增加可以使用SAM+SAM2模型对整张图像一键分割所有实例的功能--框架不合适；
segment all能得到结果，但是特别差，需要优化;已经分割出来的，锯齿状、实例连片明显；
需要的效果是逼近meta线上版本的效果;应该是需要参数调整和后处理；
很多明显的实例连成一片为一个实例，轮廓锯齿状明显；
mask_generator = SamAutomaticMaskGenerator(
    model=sam,
    points_per_side=64,
    points_per_batch=64,
    pred_iou_thresh=0.95,
    stability_score_thresh=0.90,
    stability_score_offset=1.0,
    box_nms_thresh=0.7,
    crop_n_layers=2,
    crop_nms_thresh=0.7,
    crop_overlap_ratio=512/1500,
    crop_n_points_downscale_factor=1,
    min_mask_region_area=500,
)
需求6:增加Drawing Polygon时，可以跟踪鼠标移动的轨迹的功能，而不需要点击鼠标左键一个一个的打点；
- [x] Freehand polygon drawing by holding the left mouse button.
- Freehand polygons begin immediately after the first click
- no need to hold the button.
- double-clicking to finish the shape.
选择create Polygon后，选中第一个点后，还是需要点击鼠标左键一个一个的打点，没有实现跟踪鼠标移动的轨迹的功能;
目前还是需要点击鼠标左键一个一个的打点;应该是选择多边形，点击鼠标左键选择开始点后，就可以跟踪鼠标移动的轨迹，
双击鼠标左键结束多边形的绘制;
## 更新的功能点
1,增加了轮廓线粗细、填充mask透明度可在界面调节的功能;
2,增加了给每个实例打多个标签的功能（Ctrl键）;
3,增加了给整张图打一个标签的功能;
4,增加了在用户标注界面可以配置标签列表和切换已经内置的多个标签列表的功能;
5,增加多边形标注时，可以跟踪鼠标移动的轨迹的功能;
## usage
### 给整张图打标签
在标注界面中给整张图打标签的步骤如下：

在菜单栏选择 Edit → Set Image Label。

在弹出的对话框中输入想要的标签，多个标签用英文逗号分隔。

点击确定后，这些标签会被记录到当前图片的 image_labels 字段中，并更新唯一标签列表。

这样就能在界面里为整张图片设置标签了。

#### 从列表中选择多个标签

you must hold Ctrl (or Shift) when selecting the second item to keep the first one selected.
Otherwise the first selection is cleared, and the edit box is overwritten with only the new label, so the JSON ends up with just the latest label.

## dev launch
python -m anylabeling.app
## update new sets
Edit or remove ~/.anylabelingrc so it holds the updated label_sets.

Or start the program with --config path/to/your_config.yaml.

Alternatively, run anylabeling --reset-config to regenerate the config file.

After launching with the updated configuration, the “Label Sets” menu will reflect your new sets.

## push to github
