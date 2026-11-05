# ACATE_Spec_Contracts

**ACATE 数据合同**

本文规定运行根目录的结构和各运行文件的内容。

<a id="toc"></a>
## 目录

**[§1 运行根目录](#s1)**\
　　[§1.1 运行根目录结构](#s1-1)

**[§2 Media](#s2)**\
　　[§2.1 Media.jsonl](#s2-1)

**[§3 Itinerary](#s3)**\
　　[§3.1 Itinerary.json](#s3-1)

**[§4 延时文件](#s4)**\
　　[§4.1 Time_Lapse](#s4-1)

**[§5 审查图的输入](#s5)**\
　　[§5.1 输入样例](#s5-1)

<a id="s1"></a>
## §1 运行根目录

[返回顶部](#toc)

<a id="s1-1"></a>
### §1.1 运行根目录结构

[返回顶部](#toc)

```text
运行根目录/
├── Media.jsonl
├── Source.json
├── Proxy/
├── Time_Lapse/
├── Itinerary.json
├── ACATE_Timeline.xlsx
└── ACATE_Move_Guide.csv
```

<a id="s2"></a>
## §2 Media

[返回顶部](#toc)

<a id="s2-1"></a>
### §2.1 Media.jsonl

[返回顶部](#toc)

- 由读取器写出，一份素材一行，AI 不修改。

| 字段 | 说明 |
|---|---|
| `path` | 相对源文件夹的路径 |
| `kind` | `photo` 或 `video` |
| `raw` | 是否 RAW |
| `time` | 设备记下的拍摄时间 |
| `offset` | 文件记下的 UTC 偏移，没有的为 null |
| `gps` | 纬度、经度，没有的为 null |
| `camera`、`serial` | 相机型号、机身序列号 |
| `focal_mm` | 35mm 等效焦段 |
| `width`、`height`、`duration` | 宽、高、时长 |
| `proxy` | 代理图在运行根目录内的路径 |

<a id="s3"></a>
## §3 Itinerary

[返回顶部](#toc)

<a id="s3-1"></a>
### §3.1 Itinerary.json

[返回顶部](#toc)

- 由 AI 写入，是行程表和移动指南的依据。

```json
{
 "clock_offsets": [{"camera": "Canon EOS R5", "seconds": 3600}],
 "segments": [
  {"start": "2026-04-12 07:40:10", "end": "2026-04-12 09:15:20", "state": "停留",
   "place": "京都站", "coordinate": [34.98585, 135.7588]},
  {"start": "2026-04-12 09:15:20", "end": "2026-04-12 09:50:00", "state": "移动",
   "from": "京都站", "to": "清水寺", "transport": "巴士"}
 ]
}
```

| 字段 | 说明 |
|---|---|
| `clock_offsets` | 设备钟差，单位为秒，加到设备时间上即当地时间；写了 `kind` 的只用于 `photo` 或 `video` |
| `start`、`end` | 当地时间，可以写到秒，行程表四舍五入到分钟 |
| `state` | 停留或移动 |
| `place`、`coordinate` | 停留段的地点和坐标 |
| `from`、`to`、`transport` | 移动段的起点、终点和交通方式 |

<a id="s4"></a>
## §4 延时文件

[返回顶部](#toc)

<a id="s4-1"></a>
### §4.1 Time_Lapse

[返回顶部](#toc)

- `Time_Lapse/TL-YYYYMMDD-NN.json` 一条延时一个文件，由读取器写出，AI 填写其中几项。

| 字段 | 说明 |
|---|---|
| `sequence_id` | 延时编号 |
| `confirmed` | AI 确认后改为 `true`，只有 `true` 的列入移动指南 |
| `place_name` | 延时所在的地点，留空的取所在行程段的地点 |
| `camera`、`focal_mm`、`interval` | 相机、等效焦段、拍摄间隔 |
| `original_long_side` | 原片长边 |
| `exclude` | 排除的帧 |
| `reviewed` | 查看后保留的待判断帧 |
| `conclusion` | 结论，一项一行 |
| `frames` | 全部的帧 |
| `measurement` | 测量结果，可省；省略的由审查图工具测量 |

<a id="s5"></a>
## §5 审查图的输入

[返回顶部](#toc)

<a id="s5-1"></a>
### §5.1 输入样例

[返回顶部](#toc)

- 各种审查图的输入都是一个 JSON 文件，样例为 `Template/` 中与示例图同名的 JSON 文件。
- 延时总图的输入就是延时文件。
