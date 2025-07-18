### Summary

- Introduced a new configuration option keep_prev_loc with a default value of true to store previous canvas positions across images

- Added a “Keep Previous Location” action in the interface, enabling users to toggle whether the canvas position persists when switching images

- Implemented logic in load_file to reuse zoom and scroll settings from the prior image when the image dimensions match, otherwise displaying a warning about mismatched sizes
### 
已有keep_prev_scale,现在需要增加keep previous location,
前提是切换的下一张图像尺寸跟切换图像前的尺寸保持一致，否则给出提示；
- 增加keep_prev_loc菜单键或按钮;
- 缩放比例和在canvas上的位置同时保持一致，用于观察PPL-XPL图像里面的颗粒的变化；
- 默认前提是切换前后的图像尺寸是一样的；
- 目标是实现切换图像后，能看到不同偏光角度下的同一位置的物体的光性变化情况；
- 默认是开启

## PPL-XPL图像标注
当PPL-XPL标注同步按钮启用时，打开文件夹，就把当前文件夹的所有图片（通常是1张单偏光+6张正交偏光，忽略子文件夹）
一起叠加放在canvas上， 当切换下一张图片时，打开状态使用切换前的图片的缩放状态和画布位置；
- 目标是实现PPL-XPL图像的颗粒标注，因为颗粒在不同偏光图像上的位置没变；
- 默认打开文件夹下的多个偏光角度图像的尺寸是一样的，当发现不一样时，给出友好提升，确保鲁棒性；
Q:
- 切换图像，视窗里面没变化；
- canvas上面应该是放了多张图像，切换应显示不同的偏光角度图像；


'''
diff --git a/anylabeling/configs/anylabeling_config.yaml b/anylabeling/configs/anylabeling_config.yaml
index c7521e654a87c48c81c0f45c755a58a33104c1e5..27fc2a3f97f4ee45f188eb3cb150fbc326162687 undefined
--- a/anylabeling/configs/anylabeling_config.yaml
+++ b/anylabeling/configs/anylabeling_config.yaml
@@ -1,32 +1,33 @@
 language: en_US
 theme: system
 auto_save: true
 display_label_popup: true
 store_data: false
 keep_prev: false
 keep_prev_scale: false
+keep_prev_loc: true
 keep_prev_brightness: false
 keep_prev_contrast: false
 auto_use_last_label: false
 pplxpl_sync: false
 show_cross_line: true
 show_groups: true
 show_texts: true
 logger_level: info
 
 flags: null
 label_flags: null
 labels: null
 label_sets:
   default: ["object"]
   砂岩: ['01_陆源碎屑*', '01-00_未知陆屑类*', '01-00-01_未知陆屑', '01-00-02_菱铁矿化陆屑', '01-00-03_硅化陆屑', '01-00-04_钙化碎屑/灰化碎屑', '01-00-05_云化碎屑', '01-00-06_泥化碎屑(粘土矿物交代)', '01-01_石英类*', '01-01-01_石英', '01-01-02_燧石', '01-01-03_玉髓', '01-01-04_加大石英', '01-01-05_方解石交代石英', '01-01-06_白云石交代石英', '01-01-07_高岭石交代石英', '01-01-08_绢云母交代石英', '01-01-09_绿泥石交代石英', '01-02_长石类*', '01-02-00_未分长石*', '01-02-00-01_长石', '01-02-00-02_加大长石', '01-02-00-03_方解石交代长石', '01-02-00-04_白云石交代长石', '01-02-00-05_高岭石交代长石', '01-02-00-06_绢云母交代长石', '01-02-00-07_绿泥石交代长石', '01-02-00-08_绿帘石交代长石', '01-02-00-09_菱铁矿交代长石', '01-02-01_斜长石', '01-02-02_钾长石*', '01-02-02-01_正长石', '01-02-02-02_微斜长石', '01-02-02-03_条纹长石', '01-03_岩屑*', '01-03-01_沉积岩*', '01-03-01-01_碳酸盐岩*', '01-03-01-01-01_灰岩', '01-03-01-01-02_云岩', '01-03-01-02_硅质岩', '01-03-01-03_砂岩', '01-03-01-04_粉砂岩', '01-03-01-05_泥岩', '01-03-02_火山碎屑岩*', '01-03-02-01_凝灰岩', '01-03-02-02_沉凝灰岩', '01-03-02-03_熔结凝灰岩', '01-03-03_喷出岩*', '01-03-03-01_基性喷出岩*', '01-03-03-01-01_玄武岩', '01-03-03-01&02_中基性喷出岩', '01-03-03-02_中性喷出岩*', '01-03-03-02-01_粗面岩', '01-03-03-02-02_粗安岩', '01-03-03-02-03_安山岩', '01-03-03-03_酸性喷出岩*', '01-03-03-03-01_英安岩', '01-03-03-03-02_流纹岩', '01-03-04_侵入岩*', '01-03-04-01_基性侵入岩*', '01-03-04-01-01_辉绿岩', '01-03-04-01-02_辉绿玢岩', '01-03-04-01-03_辉长岩', '01-03-04-01-04_苏长岩', '01-03-04-01-05_斜长岩', '01-03-04-02_中性侵入岩*', '01-03-04-02-01_正长岩', '01-03-04-02-02_正长斑岩', '01-03-04-02-03_二长岩', '01-03-04-02-04_二长斑岩', '01-03-04-02-05_闪长岩', '01-03-04-02-06_闪长玢岩', '01-03-04-03_酸性侵入岩*', '01-03-04-03-01_花岗闪长斑岩', '01-03-04-03-02_花岗闪长岩', '01-03-04-03-03_花岗斑岩', '01-03-04-03-04_花岗岩', '01-03-05_变质岩*', '01-03-05-01_石英岩', '01-03-05-02_脉石英', '01-03-05-03_变质石英岩', '01-03-05-04_长英质变质岩/高变岩', '01-03-05-05_麻粒岩', '01-03-05-05-01_长英质麻粒岩', '01-03-05-05-02_石英质麻粒岩', '01-03-05-06_浅粒岩', '01-03-05-07_变粒岩', '01-03-05-08_片麻岩', '01-03-05-09_片岩', '01-03-05-10_千枚岩', '01-03-05-11_板岩', '01-03-05-12_变质砂岩', '01-03-06_云母*', '01-03-06-01_白云母', '01-03-06-02_黑云母', '01-03-06-03_水白云母', '01-03-06-04_绿泥石化黑云母', '01-03-06-05_高岭石化云母', '01-03-06-06_菱铁矿化云母', '01-03-07_绿泥石颗粒', '01-03-08_海绿石', '01-03-09_重矿物*', '01-03-09-01_透明重矿物*', '01-03-09-01-01_角闪石', '01-03-09-01-02_锆石', '01-03-09-01-03_石榴石', '01-03-09-01-04_绿帘石', '01-03-09-01-05_黝帘石', '01-03-09-01-06_金红石', '01-03-09-01-07_十字石', '01-03-09-01-08_榍石', '01-03-09-01-09_电气石', '01-03-09-01-10_闪锌矿', '01-03-09-01-11_磷灰石', '01-03-09-02_不透明重矿物*', '01-03-09-02-01_磁铁矿', '01-03-09-02-02_赤铁矿', '01-03-09-02-03_板钛矿', '01-03-09-02-04_白钛矿', '02_非陆源碎屑*', '02-01_内源屑*', '02-01-01_生屑*', '02-01-01-01_碳酸盐生屑', '02-01-01-02_磷酸盐生屑', '02-01-02_鲕粒', '02-01-03_内碎屑*', '02-01-03-01_砾屑', '02-01-03-02_砂屑', '02-01-03-03_粉屑', '02-01-03-04_泥屑', '02-01-04_球粒', '02-01-05_团块', '02-01-06_絮凝粒', '02-01-07_砂质屑', '02-01-08_粉砂质屑', '02-01-09_泥质屑', '02-02_火山碎屑', '02-03_炭屑', '03_填隙物*', '03-01_杂基*', '03-01-01_泥质杂基类*', '03-01-01-01_泥质杂基', '03-01-01-01-01_蒙脱石杂基', '03-01-01-01-02_伊蒙混层杂基', '03-01-01-01-03_伊利石杂基', '03-01-01-01-04_绿泥石杂基', '03-01-01-01-05_高岭石杂基', '03-01-01-02_泥铁质杂基', '03-01-02_碳酸盐杂基*', '03-01-02-01_灰泥杂基', '03-01-02-02_云泥杂基', '03-01-03_凝灰质杂基', '03-02_胶结物*', '03-02-01_自生碳酸盐*', '03-02-01-01_方解石类*', '03-02-01-01-01_方解石', '03-02-01-01-02_含铁方解石', '03-02-01-01-03_铁方解石', '03-02-01-02_白云石类*', '03-02-01-02-01_白云石', '03-02-01-02-02_含铁白云石', '03-02-01-02-03_铁白云石', '03-02-01-03_菱铁矿', '03-02-01-04_菱镁矿', '03-02-02_自生硫酸盐*', '03-02-02-01_石膏', '03-02-02-02_硬石膏', '03-02-02-03_重晶石', '03-02-02-04_天青石', '03-02-02-05_黄钾铁矾', '03-02-03_自生硫化物*', '03-02-03-01_黄铁矿', '03-02-04_自生卤化物*', '03-02-04-01_萤石', '03-02-05_自生黏土矿物*', '03-02-05-01_蒙脱石（蒙皂石）', '03-02-05-02_伊蒙混层/网状黏土', '03-02-05-03_伊利石*', '03-02-05-03-01_孔隙充填伊利石', '03-02-05-03-02_薄膜伊利石', '03-02-05-04_绿泥石类*', '03-02-05-04-01_孔隙充填绿泥石', '03-02-05-04-02_绿泥石薄膜', '03-02-05-05_高岭石', '03-02-06_自生硅质*', '03-02-06-01_石英次生加大', '03-02-06-02_自生石英', '03-02-06-03_自生隐晶石英', '03-02-07_自生长石质*', '03-02-07-01_长石次生加大', '03-02-07-02_自生长石', '03-02-08_自生沸石*', '03-02-08-01_方沸石', '03-02-08-02_菱沸石', '03-02-08-03_浊沸石', '03-02-08-04_片沸石', '03-02-08-05_辉沸石', '03-02-09_自生氧化铁', '03-02-09-01_褐铁矿', '03-03_有机质']
   碳酸盐岩: ['01-01-00_未知粒屑', '01-01-01_内碎屑', '01-01-01-01_残余内碎屑', '01-01-02_角砾', '01-01-03_鲕粒', '01-01-03-01_正常鲕', '01-01-03-02_负鲕', '01-01-03-03_复鲕', '01-01-03-04_表鲕', '01-01-03-05_变形鲕', '01-01-03-06_残余鲕', '01-01-03-07_放射鲕', '01-01-03-08_椭形鲕', '01-01-03-09_偏心鲕', '01-01-03-10_破碎鲕', '01-01-03-11_多晶鲕', '01-01-03-12_单晶鲕', '01-01-03-13_假鲕', '01-01-03-14_豆鲕', '01-01-03-15_藻鲕', '01-01-04_球粒', '01-01-04-01_藻球粒', '01-01-04-02_粪球粒', '01-01-04-03_其他球粒', '01-01-05_团块', '01-01-05-01_菌藻团块', '01-01-05-02_泥质团块', '01-01-05-03_其它团块', '01-01-06_核形石', '01-01-07_凝块石', '01-01-08_陆源碎屑', '01-01-08-01_陆源石英', '01-01-08-02_陆源长石', '01-01-08-03_岩屑', '01-01-08-04_云母', '01-01-08-05_陆源泥质', '01-01-09_鲕灰岩碎屑', '01-01-09-01_鲕灰岩砾屑', '01-02-00_未知生屑', '01-02-01_有孔虫', '01-02-01-01_蜓类', '01-02-01-01-01_纺锤蜓', '01-02-01-01-01-01_半纺锤蜓', '01-02-01-01-01-02_希瓦格蜓', '01-02-01-01-01-03_古蜓', '01-02-01-01-02_费伯克蜓超科', '01-02-01-01-02-01_费伯克蜓', '01-02-01-01-02-02_假桶蜓', '01-02-01-01-02-03_新希瓦格蜓', '01-02-01-01-02-04_新米斯蜓', '01-02-01-01-03_拉切尔蜓', '01-02-01-01-04_喇叭蜓', '01-02-01-01-05_南京蜓', '01-02-01-02_非蜓类有孔虫', '01-02-01-03_内卷虫', '01-02-01-03-01_小伞虫', '01-02-01-03-02_肿瘤虫', '01-02-01-03-03_巴东虫', '01-02-01-03-04_厚壁虫', '01-02-01-03-05_柯兰尼虫', '01-02-01-03-06_古串珠虫', '01-02-01-03-07_筛串虫', '01-02-01-03-08_四排虫', '01-02-01-03-09_球瓣虫', '01-02-01-03-10_布雷迪虫', '01-02-01-04_砂盘虫', '01-02-01-04-01_砂盘虫', '01-02-01-04-02_球旋虫', '01-02-01-05_曲房虫', '01-02-01-05-01_串珠虫', '01-02-01-05-02_园皿虫', '01-02-01-05-03_园笠虫', '01-02-01-06_拟砂户虫', '01-02-01-06-01_拟砂户虫', '01-02-01-06-02_古球虫', '01-02-01-06-03_筛球虫', '01-02-01-07_节房虫', '01-02-01-07-01_节房虫', '01-02-01-07-02_叶形虫', '01-02-01-08_小粟虫', '01-02-01-09_蜂巢虫', '01-02-01-10_货币虫', '01-02-01-11_抱球虫', '01-02-01-12_圆片虫超科', '01-02-01-12-01_圆片虫', '01-02-01-12-02_鳞环虫', '01-02-02_介形虫', '01-02-03_双壳类', '01-02-04_腹足类', '01-02-05_头足类', '01-02-06_腕足类', '01-02-07_放射虫', '01-02-08_海绵', '01-02-08-01_海绵骨针', '01-02-08-02_串管海绵', '01-02-08-03_硬海绵', '01-02-08-04_纤维海绵', '01-02-09_古杯', '01-02-10_珊瑚', '01-02-10-01_板状珊瑚', '01-02-10-02_板床珊瑚', '01-02-10-03_四射珊瑚', '01-02-11_苔藓虫', '01-02-12_三叶虫', '01-02-13_层孔虫', '01-02-14_钙球', '01-02-15_棘皮类', '01-02-15-01_棘屑', '01-02-15-02_海胆', '01-02-15-03_海林擒', '01-02-15-04_海蛇尾', '01-02-15-05_海百合', '01-02-16_管壳石', '01-02-17_藻类', '01-02-17-01_蓝细菌（菌藻类）', '01-02-17-02_钙藻', '01-02-17-02-01_绿藻', '01-02-17-02-01-01_绒枝藻', '01-02-17-02-01-01-01_米齐藻', '01-02-17-02-01-01-02_蠕孔藻', '01-02-17-02-01-01-03_始角藻', '01-02-17-02-01-01-04_圆孔藻', '01-02-17-02-01-01-05_德文藻', '01-02-17-02-01-02_管藻', '01-02-17-02-01-02-01_松藻', '01-02-17-02-01-02-02_仙人掌藻', '01-02-17-02-01-03_裸松藻', '01-02-17-02-01-03-01_二叠钙藻', '01-02-17-02-01-03-02_裸松藻', '01-02-17-02-02_红藻', '01-02-17-02-02-01_管孔藻', '01-02-17-02-02-01-01_管孔藻', '01-02-17-02-02-01-02_拟刺毛藻', '01-02-17-02-02-01-03_密孔藻', '01-02-17-02-02-02_珊瑚藻', '01-02-17-02-02-03_红箕藻', '01-02-17-02-02-03-01_石叶藻', '01-02-17-02-02-03-02_石枝藻', '01-02-17-02-02-03-03_红箕藻', '01-02-17-02-02-04_翁格达藻', '01-02-17-02-02-05_楔状藻', '01-02-17-02-03_褐藻', '01-02-17-02-04_轮藻', '01-02-17-03_藻屑', '01-02-17-03-01_残余藻屑', '01-02-17-04_伞藻', '01-02-17-05_硅藻', '01-02-18_螺']
   岩浆岩: ['01_矿物*——标注层1（01-01~01-04）', '01-01_造岩矿物*', '01-01-01_石英', '01-01-02_长石*', '01-01-02-01_碱性长石*', '01-01-02-01-01_正长石', '01-01-02-01-02_透长石', '01-01-02-01-03_微斜长石', '01-01-02-01-04_歪长石', '01-01-02-01-05_条纹长石', '01-01-02-01-06_反条纹长石', '01-01-02-02_斜长石*', '01-01-02-02-01_钠长石', '01-01-02-02-02_奥长石', '01-01-02-02-03_中长石', '01-01-02-02-04_拉长石', '01-01-02-02-05_培长石', '01-01-02-02-06_钙长石', '01-01-03_云母*', '01-01-03-01_白云母', '01-01-03-02_锂云母', '01-01-03-03_金云母', '01-01-03-04_黑云母', '01-01-04_角闪石*', '01-01-04-01_普通角闪石', '01-01-04-02_透闪石', '01-01-04-03_阳起石', '01-01-04-04_钠闪石', '01-01-05_辉石*', '01-01-05-01_斜方辉石*', '01-01-05-01-01_顽火辉石', '01-01-05-01-02_古铜辉石', '01-01-05-01-03_紫苏辉石', '01-01-05-02_单斜辉石*', '01-01-05-02-01_普通辉石', '01-01-05-02-02_透辉石', '01-01-05-02-03_霓辉石', '01-01-05-02-04_霓石', '01-01-06_橄榄石', '01-01-07_似长石*', '01-01-07-01_霞石族', '01-01-07-02_白榴石', '01-01-08_碳酸盐*', '01-01-08-01_方解石', '01-01-08-02_白云石', '01-02_副矿物*', '01-02-01_透明副矿物*', '01-02-01-01_石榴子石', '01-02-01-02_磷灰石_', '01-02-01-03_尖晶石', '01-02-01-04_榍石', '01-02-01-05_锆石', '01-02-01-06_电气石', '01-02-01-07_褐帘石', '01-02-01-08_萤石', '01-02-01-09_独居石', '01-02-01-10_锐钛矿', '01-02-01-11_金红石', '01-02-02_不透明副矿物*', '01-02-02-01_磁铁矿', '01-02-02-02_黄铁矿', '01-02-02-03_赤褐铁矿', '01-02-02-04_铬铁矿', '01-02-02-05_钛铁矿', '01-02-02-06_白钛矿', '01-03_基质*', '01-03-01_显微晶质基质', '01-03-02_隐晶质基质', '01-03-03_玻璃质基质', '01-04_火山碎屑', '01-05_次生矿物*——标注层2', '01-05-01_次生蛋白石', '01-05-02_次生玉髓', '01-05-03_次生石英', '01-05-04_次生长石', '01-05-05_次生黝帘石', '01-05-06_次生绿帘石', '01-05-07_次生伊丁石', '01-05-08_次生蛇纹石', '01-05-09_次生葡萄石', '01-05-10_次生绢云母', '01-05-11_次生黏土矿物*', '01-05-11-01_次生绿泥石', '01-05-11-02_次生伊利石', '01-05-11-03_次生高岭石', '01-05-12_次生方解石', '02_结构*', '02-01_侵入岩结构*', '02-01-01_晶体自形程度*——标注层3', '02-01-01-01_自形粒状结构', '02-01-01-02_自形-半自形粒状结构', '02-01-01-03_半自形粒状结构', '02-01-01-04_半自形-他形粒状结构', '02-01-01-05_他形粒状结构', '02-01-02_矿物结晶程度*——标注层4', '02-01-02-01_纯晶粒结构*', '02-01-02-01-01_等粒结构*', '02-01-02-01-01-01_巨粒结构', '02-01-02-01-01-02_粗粒结构', '02-01-02-01-01-03_中粒结构', '02-01-02-01-01-04_细粒结构', '02-01-02-01-01-05_微粒结构', '02-01-02-01-02_不等粒结构', '02-01-02-01-03_似斑状结构', '02-01-02-02_侵入岩斑状结构', '02-01-02-03_侵入岩显微晶质结构', '02-01-02-04_侵入岩隐晶质结构', '02-01-03_矿物共生方式*——标注层5', '02-01-03-01_花斑结构', '02-01-03-02_花岗结构', '02-01-03-03_二长结构', '02-01-03-04_似粗面结构', '02-01-03-05_辉长结构', '02-01-03-06_辉长辉绿结构', '02-01-03-07_辉绿结构', '02-01-03-08_粒状镶嵌结构', '02-01-03-09_嵌晶结构', '02-01-03-10_嵌晶含长结构', '02-01-03-11_包含结构', '02-01-03-12_海绵陨铁结构', '02-01-03-13_填隙结构', '02-01-03-14_网状结构', '02-01-03-15_交代结构', '02-01-03-16_条纹结构', '02-01-03-17_文象结构', '02-01-03-18_蠕虫结构', '02-01-03-19_反应边结构', '02-02_喷出岩结构*', '02-02-01_主结构*——标注层6', '02-02-01-01_聚斑结构', '02-02-01-02_喷出岩斑状结构', '02-02-01-03_玻基斑状结构', '02-02-01-04_显微斑状结构', '02-02-02_基质结构*——标注层7', '02-02-02-01_喷出岩显微晶质结构', '02-02-02-02_喷出岩隐晶质结构', '02-02-02-03_玻璃质结构', '02-02-02-04_霏细结构', '02-02-02-05_球粒结构', '02-02-02-06_球颗结构', '02-02-02-07_粗面结构', '02-02-02-08_交织结构', '02-02-02-09_玻晶(基)交织结构', '02-02-02-10_间粒结构', '02-02-02-11_间粒-间隐结构', '02-02-02-12_间隐结构', '02-02-02-13_显微文象结构', '02-02-02-14_显微嵌晶结构', '02-02-02-15_环边假象结构', '02-02-02-16_鬣刺结构', '03_构造*', '03-01_侵入岩构造*——标注层8', '03-01-01_侵入岩块状构造', '03-01-02_带状构造', '03-01-03_流动构造', '03-01-04_斑杂构造', '03-01-05_球状构造', '03-02_喷出岩构造*——标注层9', '03-02-01_喷出岩块状构造', '03-02-02_似层状构造', '03-02-03_流纹构造', '03-02-04_珍珠构造', '03-02-05_气孔构造', '03-02-06_杏仁构造', '03-02-05&06附_气孔杏仁充填物*——借归标注层2', '03-02-05&06附-01_蛋白石气孔杏仁充填物', '03-02-05&06附-02_隐晶石英气孔杏仁充填物', '03-02-05&06附-03_石英气孔杏仁充填物', '03-02-05&06附-04_长石气孔杏仁充填物', '03-02-05&06附-05_沸石气孔杏仁充填物', '03-02-05&06附-06_方解石气孔杏仁充填物', '03-02-05&06附-07_白云石气孔杏仁充填物', '03-02-05&06附-08_菱铁矿气孔杏仁充填物', '03-02-05&06附-09_黄铁矿气孔杏仁充填物', '03-02-05&06附-10_高岭石气孔杏仁充填物', '03-02-05&06附-11_伊利石气孔杏仁充填物', '03-02-05&06附-12_绿泥石气孔杏仁充填物', '03-02-05&06附-13_泥质气孔杏仁充填物', '03-02-05&06附-14_泥铁质气孔杏仁充填物', '03-02-05&06附-15_褐铁矿气孔杏仁充填物', '10_储集空间*——标注层10', '10-01_孔*', '10-01-01_原生孔*', '10-01-01-01_原生气孔', '10-01-01-02_残余气孔', '10-01-01-03_粒间孔', '10-01-02_次生孔*', '10-01-02-01_晶内溶蚀孔', '10-01-02-02_晶间溶蚀孔', '10-01-02-03_基质溶蚀孔', '10-01-02-04_次生矿物溶蚀孔', '10-01-02-05_火山灰溶蚀孔', '10-01-02-06_杏仁体溶蚀孔', '10-02_缝*', '10-02-01_原生缝*', '10-02-01-01_矿物炸裂缝', '10-02-01-02_冷凝收缩缝', '10-02-01-03_层间缝', '10-02-02_次生缝*', '10-02-02-01_构造缝', '10-02-02-02_残余构造缝', '10-02-02-03_风化淋滤缝', '10-02-02-04_粒内缝', '10-02-02-05_贴粒缝', '10-02附_缝充填物*——借归标注层2', '10-02附-01_蛋白石脉', '10-02附-02_隐晶石英脉', '10-02附-03_石英脉', '10-02附-04_长石脉', '10-02附-05_沸石脉', '10-02附-06_方解石脉', '10-02附-07_白云石脉', '10-02附-08_菱铁矿脉', '10-02附-09_黄铁矿脉', '10-02附-10_高岭石脉', '10-02附-11_伊利石脉', '10-02附-12_绿泥石脉', '10-02附-13_泥质脉', '10-02附-14_泥铁质脉', '10-02附-15_褐铁矿脉']
 contour_width: 2          # default stroke (px)
 mask_opacity: 0.3         # default alpha [0‑1]
 file_search: null
 sort_labels: true
 validate_label: null
 
 default_shape_color: [0, 255, 0]
 shape_color: auto  # null, 'auto', 'manual'

'''
'''
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 10aab3fca5105eae3eb962ff087dfb71b8e3a2ac..fb5628c6a8827f8a8b7d5e4acff196649a9db6ba undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -602,50 +602,58 @@ class LabelingWidget(LabelDialog):
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
+        keep_prev_loc = create_action(
+            self.tr("&Keep Previous Location"),
+            self.enable_keep_prev_loc,
+            tip=self.tr("Keep previous canvas location"),
+            checkable=True,
+            checked=self._config.get("keep_prev_loc", True),
+            enabled=True,
+        )
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
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 10aab3fca5105eae3eb962ff087dfb71b8e3a2ac..fb5628c6a8827f8a8b7d5e4acff196649a9db6ba undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -839,50 +847,51 @@ class LabelingWidget(LabelDialog):
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
+            keep_prev_loc=keep_prev_loc,
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
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 10aab3fca5105eae3eb962ff087dfb71b8e3a2ac..fb5628c6a8827f8a8b7d5e4acff196649a9db6ba undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -1023,50 +1032,51 @@ class LabelingWidget(LabelDialog):
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
+                keep_prev_loc,
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
 
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 10aab3fca5105eae3eb962ff087dfb71b8e3a2ac..fb5628c6a8827f8a8b7d5e4acff196649a9db6ba undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -1225,50 +1235,51 @@ class LabelingWidget(LabelDialog):
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
+        self.prev_image_size = None
 
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
         # or simply:
         # self.restoreGeometry(settings['window/geometry']
 
         # Populate the File menu dynamically.
         self.update_file_menu()
 
         # Since loading the file may take some time,
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 10aab3fca5105eae3eb962ff087dfb71b8e3a2ac..fb5628c6a8827f8a8b7d5e4acff196649a9db6ba undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -2094,50 +2105,55 @@ class LabelingWidget(LabelDialog):
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
 
+    def enable_keep_prev_loc(self, enabled):
+        self._config["keep_prev_loc"] = enabled
+        self.actions.keep_prev_loc.setChecked(enabled)
+        save_config(self._config)
+
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
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 10aab3fca5105eae3eb962ff087dfb71b8e3a2ac..fb5628c6a8827f8a8b7d5e4acff196649a9db6ba undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -2217,50 +2233,52 @@ class LabelingWidget(LabelDialog):
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
 
+        prev_size = self.prev_image_size
+
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
 
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index 10aab3fca5105eae3eb962ff087dfb71b8e3a2ac..fb5628c6a8827f8a8b7d5e4acff196649a9db6ba undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -2295,111 +2313,144 @@ class LabelingWidget(LabelDialog):
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
+        new_size = (self.image.width(), self.image.height())
+        same_size = prev_size is None or prev_size == new_size
+        if (
+            not same_size
+            and self._config.get("keep_prev_loc", True)
+        ):
+            QtWidgets.QMessageBox.warning(
+                self,
+                self.tr("Different Image Size"),
+                self.tr(
+                    "Cannot keep previous location because image sizes differ."
+                ),
+            )
         if self._config["keep_prev"]:
             prev_shapes = self.canvas.shapes
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
+        prev_filename = self.recent_files[0] if self.recent_files else None
         if self.filename in self.zoom_values:
             self.zoom_mode = self.zoom_values[self.filename][0]
             self.set_zoom(self.zoom_values[self.filename][1])
-        elif is_initial_load or not self._config["keep_prev_scale"]:
+        elif (
+            is_initial_load
+            or not self._config["keep_prev_scale"]
+            or not same_size
+            or not prev_filename
+            or prev_filename not in self.zoom_values
+        ):
             self.adjust_scale(initial=True)
+        else:
+            self.zoom_mode = self.zoom_values[prev_filename][0]
+            self.set_zoom(self.zoom_values[prev_filename][1])
         # set scroll values
         for orientation in self.scroll_values:
             if self.filename in self.scroll_values[orientation]:
                 self.set_scroll(
                     orientation, self.scroll_values[orientation][self.filename]
                 )
+            elif (
+                self._config.get("keep_prev_loc", True)
+                and same_size
+                and prev_filename in self.scroll_values[orientation]
+            ):
+                self.set_scroll(
+                    orientation, self.scroll_values[orientation][prev_filename]
+                )
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
 
+        self.prev_image_size = new_size
+
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
 
     def paint_canvas(self):
         assert not self.image.isNull(), "cannot paint null image"
         self.canvas.scale = 0.01 * self.zoom_widget.value()
         self.canvas.adjustSize()

'''
## Summary

- Added new dependencies and imports for NumPy and OpenCV conversion utilities, enabling manipulation of image arrays

- Implemented _load_pplxpl_overlay to read multiple images, verify matching dimensions, stack them, and return an averaged overlay image

'''
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index fb5628c6a8827f8a8b7d5e4acff196649a9db6ba..924bbe604011352ca17735ed092626b49ffe3b48 undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -3,50 +3,55 @@ import html
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
+from anylabeling.views.labeling.utils.opencv import (
+    cv_img_to_qt_img,
+    qt_img_to_rgb_cv_img,
+)
+import numpy as np
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
 
 
diff --git a/anylabeling/views/labeling/label_widget.py b/anylabeling/views/labeling/label_widget.py
index fb5628c6a8827f8a8b7d5e4acff196649a9db6ba..924bbe604011352ca17735ed092626b49ffe3b48 undefined
--- a/anylabeling/views/labeling/label_widget.py
+++ b/anylabeling/views/labeling/label_widget.py
@@ -2845,50 +2850,83 @@ class LabelingWidget(LabelDialog):
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
 
+    def _load_pplxpl_overlay(self, files):
+        """Return stacked overlay image from given files.
+
+        Parameters
+        ----------
+        files : list[str]
+            Image paths to load and stack.
+
+        Returns
+        -------
+        QtGui.QImage | None
+            The overlay image or ``None`` if no valid image could be built.
+        """
+
+        images = []
+        for f in files:
+            img = QtGui.QImage(f)
+            if not img.isNull():
+                images.append(img)
+
+        if not images:
+            return None
+
+        w = images[0].width()
+        h = images[0].height()
+        if not all(img.width() == w and img.height() == h for img in images):
+            return None
+
+        arrs = [qt_img_to_rgb_cv_img(img) for img in images]
+        stacked = np.stack(arrs, axis=0)
+        overlay_arr = stacked.mean(axis=0).astype(np.uint8)
+        return cv_img_to_qt_img(overlay_arr)
+
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
 

'''