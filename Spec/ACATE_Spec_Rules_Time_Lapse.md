# ACATE_Spec_Rules_Time_Lapse

**ACATE 规则：延时**

本文规定 AI 如何判断工具找出的延时。

<a id="toc"></a>
## 目录

**[§1 延时的认定](#s1)**\
　　[§1.1 延时候选](#s1-1)　[§1.2 延时的确认](#s1-2)

**[§2 命名](#s2)**\
　　[§2.1 延时文件夹](#s2-1)

<a id="s1"></a>
## §1 延时的认定

[返回顶部](#toc)

<a id="s1-1"></a>
### §1.1 延时候选

[返回顶部](#toc)

- 读取器找出延时候选，写入延时文件（[ACATE_Spec_Contracts〈§4 延时文件〉](ACATE_Spec_Contracts.md#s4)）。
- 审查图工具测量抖动，标出待判断帧，绘制延时总图（[ACATE_Spec_Review_Images〈§4.1 延时总图〉](ACATE_Spec_Review_Images.md#s4-1)）。

<a id="s1-2"></a>
### §1.2 延时的确认

[返回顶部](#toc)

- AI 查看延时总图，判断两件事：
  1. 这是不是延时：画面几乎一样、是同一机位连续拍下的，才是延时；
  2. 标出的帧是否排除：排除的写入 `exclude`，保留的写入 `reviewed`。
- 排除的帧留在照片文件夹。
- 连在两端的问题帧才排除；中段的问题帧不排除，写入 `conclusion`。
- 游客走过、灯亮起、云和光线的变化，都属于画面内容，不是问题。
- 判断不了的，画延时判断图，由用户决定（[ACATE_Spec_Review_Images〈§4.2 延时判断图〉](ACATE_Spec_Review_Images.md#s4-2)）。

　　[例]\
　　　　要排除的帧：架设、收起相机时的帧，镜头被挡住的帧

<a id="s2"></a>
## §2 命名

[返回顶部](#toc)

<a id="s2-1"></a>
### §2.1 延时文件夹

[返回顶部](#toc)

- 延时文件夹名的写法见[ACATE_Spec_Workbook〈§2.2 文件夹名〉](ACATE_Spec_Workbook.md#s2-2)。
- 帧数和焦段由工作簿工具写入。
