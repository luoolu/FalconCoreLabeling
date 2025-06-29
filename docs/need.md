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
在拖动滑块时立即实时更新所有实例的透明度；
目前是所有轮廓线是支持实时调节粗细，但mask透明度是只支持单个实例调节；需求是支持所有实例同时实时调节mask透明度;
替换anylabeling/views/labeling/label_widget.py
现在是鼠标放到实例上时，可以调节mask透明度;但希望的是跟实例轮廓线一样所有实例同时调节mask透明度，
而不是鼠标放到实例上时才能看到透明度调解后的mask;


'''
https://github.com/luoolu/FalconCoreLabeling;现在是鼠标放到实例上时，可以调节mask透明度;但希望的是跟实例轮廓线一样所有实例同时调节mask透明度，
而不是鼠标放到实例上时才能看到透明度调解后的mask;

给出需要修改的对应的地方和完整的函数，我复制粘贴使用

1,对；
2,希望在拖动滑块时立即实时更新所有实例的透明度；
3,你看看，我把label_widget.py传给你了
'''
'''
我会立即为您修改 `fill_opacity_changed` 函数，以确保滑块拖动时立即将透明度应用到所有实例的遮罩，无需选中或悬浮。稍后将返回完整的函数代码供您直接替换使用。


# FalconCoreLabeling 实时更新遮罩透明度解决方案

为实现拖动透明度滑块时所有实例的遮罩透明度即时更新，并且不需要选中或悬停即可生效，需要同时修改 **LabelingWidget** 类中的透明度更新逻辑，以及 **Canvas** 绘制遮罩的逻辑。以下是具体修改方案：

## 修改 LabelingWidget 中透明度同步更新逻辑

在 `label_widget.py` 中修改 `LabelingWidget` 类的透明度变化处理函数，使其在滑块拖动时将新的透明度值应用到所有 LabelingWidget 实例的所有 Shape 遮罩上。具体实现为：全局更新 `Shape` 类的填充颜色透明度，并遍历所有已实例化的 LabelingWidget，将每个 Shape 对象的填充颜色 alpha 值更新为当前滑块值，并立即重绘画布。修改后的函数代码如下：

```python
class LabelingWidget(LabelDialog):
    # ... 省略其他代码 ...

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
```

上述修改确保了当滑块 (`fill_opacity_slider`) 的值变化时，会实时将新的透明度应用到所有窗口的所有Shape遮罩上，并立即触发重绘生效。

## 修改 Canvas 绘制逻辑以始终填充遮罩

同时，需要修改 `canvas.py` 中 Canvas 的绘制逻辑，使得无论Shape是否被选中或悬停，始终使用填充颜色绘制遮罩。找到 Canvas 类的 `paintEvent` 方法，在绘制Shape的循环中，将 `shape.fill = shape.selected or shape == self.h_hape` 修改为 **始终为 True**。修改后的关键代码片段如下（突出显示了修改部分）：

```python
# 文件：canvas.py (Canvas.paintEvent 方法内部)
for shape in self.shapes:
    if (shape.selected or not self._hide_backround) and self.is_visible(shape):
        shape.fill = True  # 始终填充 Shape，不再依赖选中或悬停
        shape.paint(p)
```

通过上述改动，所有Shape将始终以填充颜色绘制（透明度由滑块值控制），不再需要鼠标悬停或选中才能显示遮罩效果。这样，拖动遮罩透明度滑块时，所有实例的遮罩透明度都会即时同步变化并在界面上实时体现。

'''

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
下面是一份完整的操作流程，假设你当前在 `anylabeling` 项目的根目录下、分支名为 `master`（如果是 `main`，把下面所有的 `master` 换成 `main` 即可）：

```bash
# 1. 确保工作区干净，所有改动都已提交
cd /path/to/anylabeling
git status
# 如果有未提交的改动，就：
git add .
git commit -m "feat: 增加轮廓线粗细、mask 透明度、实例多标签、整图打标、标签列表切换、多边形鼠标轨迹等功能"

# 2. 查看当前 remote
git remote -v
# 一般 clone 自上游仓库时，origin 指向的是官方仓库

# 3. 把原来的 origin 重命名为 upstream（可选，但强烈推荐，方便日后同步官方更新）
git remote rename origin upstream

# 4. 添加你自己的 fork 作为新的 origin
git remote add origin https://github.com/luoolu/FalconCoreLabeling.git
# 如果你已经配置了 SSH key，可以改成：
# git remote add origin git@github.com:luoolu/FalconCoreLabeling.git

# 5. 再次确认 remotes
git remote -v
# 应该能看到：
#   origin    https://github.com/luoolu/FalconCoreLabeling.git (fetch)
#   origin    https://github.com/luoolu/FalconCoreLabeling.git (push)
#   upstream  https://github.com/<official>/anylabeling.git    (fetch)
#   upstream  https://github.com/<official>/anylabeling.git    (push)

# 6. 将本地 master 分支推到你自己的 origin
git push -u origin master

# 7. （可选）如果你有其他分支／标签，也可以一并推送
git push origin --all
git push origin --tags
```

执行完成后，打开 [https://github.com/luoolu/FalconCoreLabeling](https://github.com/luoolu/FalconCoreLabeling) 查看，你的代码和提交就会同步到你自己的仓库里。以后如果想同步上游更新，只需：

```bash
git fetch upstream
git merge upstream/master     # 或者 rebase upstream/master
git push origin master
```

就可以保持自己 fork 的仓库与官方仓库的更新一致。

'''
(.venv) (base) luolu@loobuntu:~/PycharmProjects/anylabeling$ git push -u origin master
Username for 'https://github.com': luoolu
Password for 'https://luoolu@github.com': 
remote: Support for password authentication was removed on August 13, 2021.
remote: Please see https://docs.github.com/get-started/getting-started-with-git/about-remote-repositories#cloning-with-https-urls for information on currently recommended modes of authentication.
fatal: Authentication failed for 'https://github.com/luoolu/FalconCoreLabeling.git/'

'''
出现这个错误是因为 GitHub 已经不再支持通过账号密码进行 HTTPS 推送认证，需要使用 **Personal Access Token（PAT）** 或 **SSH Key** 来替代密码认证。下面分别给出两种方案的完整步骤，你可以任选其一。

---

## 方案一：使用 Personal Access Token (PAT)

1. **在 GitHub 上创建 PAT**

   * 登录到 GitHub 网站，点击右上角头像 → **Settings** → 左侧栏 **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token**。
   * 选择 **repo** 相关权限（至少要有 `repo` 全选），然后点击 **Generate token**。
   * 复制好生成的 token（注意：这个 token 只会显示一次，请妥善保存）。

2. **在本地使用 PAT 进行推送**
   在终端执行推送命令时，输入用户名和 token 即可把 token 当成「密码」使用：

   ```bash
   git push -u origin master
   # Username: luoolu
   # Password: <粘贴你的 PAT>
   ```

   或者，你可以将 remote URL 临时改为在 URL 中直接包含 token（**不推荐写死在脚本里，仅作临时演示**）：

   ```bash
   git remote set-url origin https://luoolu:<YOUR_TOKEN>@github.com/luoolu/FalconCoreLabeling.git
   git push -u origin master
   ```

3. **（可选）配置凭据管理器**
   安装并启用 Git 的 credential helper，之后第一次输入 token 后会被缓存，下次就不用再输：

   ```bash
   # 以 Linux 为例，安装并启用
   git config --global credential.helper store
   # 推送一次时输入用户名 + token，后续就会自动读取 ~/.git-credentials
   ```

---

## 方案二：使用 SSH Key（推荐长期使用）

1. **生成 SSH Key**（如果本地还没有的话）

   ```bash
   ssh-keygen -t ed25519 -C "luoolu@your_email.com"
   # 一路回车（使用默认路径），可选设置 passphrase
   ```

2. **把公钥复制到 GitHub**

   ```bash
   # 查看公钥内容
   cat ~/.ssh/id_ed25519.pub
   ```

   然后登录 GitHub → 头像 → **Settings** → 左侧 **SSH and GPG keys** → **New SSH key**，粘贴公钥内容并保存。

3. **将远程地址改为 SSH 地址**

   ```bash
   # 查看当前 remote
   git remote -v
   # 修改 origin 为 SSH 形式
   git remote set-url origin git@github.com:luoolu/FalconCoreLabeling.git
   ```

4. **测试并推送**

   ```bash
   # 测试 SSH 连接
   ssh -T git@github.com
   # 推送
   git push -u origin master
   ```

---

完成以上任一方案后，再次执行：

```bash
git push -u origin master
```

就可以成功将你本地的新功能推送到自己的 GitHub 仓库了。之后也可以照常：

```bash
# 同步上游更新
git fetch upstream
git merge upstream/master   # 或 git rebase upstream/master
git push origin master
```

祝你推送顺利！
