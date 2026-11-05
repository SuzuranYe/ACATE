# ACATE_Spec_Tools

**ACATE 工具**

本文规定读取器、审查图工具、工作簿工具的命令与运行环境。

<a id="toc"></a>
## 目录

**[§1 读取器](#s1)**\
　　[§1.1 读取器的命令](#s1-1)

**[§2 审查图工具](#s2)**\
　　[§2.1 审查图工具的命令](#s2-1)　[§2.2 底图的取得](#s2-2)

**[§3 工作簿工具](#s3)**\
　　[§3.1 工作簿工具的命令](#s3-1)

**[§4 运行环境](#s4)**\
　　[§4.1 依赖](#s4-1)　[§4.2 系统](#s4-2)

<a id="s1"></a>
## §1 读取器

[返回顶部](#toc)

<a id="s1-1"></a>
### §1.1 读取器的命令

[返回顶部](#toc)

- `python Script/ACATE_Script_Read.py scan <源文件夹> <运行根目录>`：读取拍摄时间、定位和相机，生成代理图，写出 `Media.jsonl`。
- `python Script/ACATE_Script_Read.py find <运行根目录>`：找出延时候选，写出延时文件。
- 两个命令都以 JSON 输出结果，失败时 `ok` 为 `false`。
- 已有的代理图不重新生成，中断后可以继续运行。

<a id="s2"></a>
## §2 审查图工具

[返回顶部](#toc)

<a id="s2-1"></a>
### §2.1 审查图工具的命令

[返回顶部](#toc)

- `python Script/ACATE_Script_Review_Images.py <种类> <输入.json> <输出.png> --tiles <底图目录>`：绘制一张图。
- 种类有 `place_card`、`day_overview`、`boundaries`、`time_lapse`、`time_lapse_judge`。
- `python Script/ACATE_Script_Review_Images.py measure <延时文件> <输出.json>`：只测量，不绘图。
- 不给 `--tiles` 的，不联网，地图不铺底图。

<a id="s2-2"></a>
### §2.2 底图的取得

[返回顶部](#toc)

- 底图按需下载，存入 `--tiles` 指定的目录，再次使用时直接读取。
- 同一网站至多每 3 秒请求一次；网站拒绝的，不再请求，地图不铺底图，照常出图。

<a id="s3"></a>
## §3 工作簿工具

[返回顶部](#toc)

<a id="s3-1"></a>
### §3.1 工作簿工具的命令

[返回顶部](#toc)

- `node Script/ACATE_Script_Workbook.mjs build <运行根目录>`：读取 `Itinerary.json`、`Media.jsonl` 和延时文件，写出行程表和移动指南。
- 相邻两段没有首尾相接，或缺地点、坐标、交通方式的，工具报错，不写文件。

<a id="s4"></a>
## §4 运行环境

[返回顶部](#toc)

<a id="s4-1"></a>
### §4.1 依赖

[返回顶部](#toc)

| 依赖 | 用在 |
|---|---|
| Python 3.14 以上，`Pillow`、`numpy`、`opencv-python` | 读取器、审查图工具 |
| `rawpy`，可选 | 读取器：RAW 文件没有可用的内嵌预览时解码 |
| Node.js 24 以上，npm 包 `jszip` | 工作簿工具 |
| ExifTool | 读取器 |
| FFmpeg | 读取器 |

- `jszip` 不随工具包提供，在一个目录里运行 `npm install jszip` 安装一次，路径写进 `ACATE_NODE_MODULES`。

<a id="s4-2"></a>
### §4.2 系统

[返回顶部](#toc)

- Windows 10 及以上：照上述命令运行。
- macOS：命令中的 `python` 写作 `python3`；ExifTool 和 FFmpeg 可用 Homebrew 安装。
- Linux：命令中的 `python` 写作 `python3`；须安装 Noto Sans CJK 字体。
- 读取器跳过以点开头的文件和目录，其中包括 macOS 写在移动硬盘上的 `._` 文件。
- HEIC 由 macOS 的 `sips` 或 FFmpeg 转换。
- 审查图的字体：Windows 用微软雅黑，macOS 用冬青黑体或华文黑体，Linux 用 Noto Sans CJK，也可以用环境变量 `ACATE_FONT`、`ACATE_FONT_BOLD` 指定。
