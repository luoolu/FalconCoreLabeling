### Summary

- The toolbar now scales icons and width using the screen’s DPI for better visibility across screen resolutions

- The toolbar orientation handler adjusts height and width based on calculated DPI values, ensuring consistent appearance when dock positions change

- Additional toolbars created by the application apply these DPI-aware icon sizes and widths to maintain consistency

- The main window’s menu bar font size adapts to the display DPI so menu items remain clear on all screens

#### 需求
目前在window是适配分辨率的，但是在toolbar上面的图标是不适配的，需要在toolbar上面适配分辨率，
同时toolbar上面的图标需要适配分辨率，无论怎么缩放都应该看到所有的图标；