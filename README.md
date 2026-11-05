# README

**ACATE V1.0.1 工具包**

ACATE（ACAT Essential）引导 AI 整理旅行素材，产出供人核对的行程表和逐文件的移动指南。执行时从 [ACATE_Spec_Workflow](Spec/ACATE_Spec_Workflow.md) 开始。

ACAT 服务于大型素材库的归档级整理，ACATE 更轻量灵活，适用于日常整理，并具备跨平台兼容性，两者的功能对比见 [ACATE_Compare](Compare/ACATE_Compare.png)。ACAT 即 A Cat，取意于《星空列车与白的旅行》中的女主角，猫耳少女，诺瓦；E 取自 Essential。Acate 亦是《埃涅阿斯纪》中埃涅阿斯的忠实旅伴 Achates 的意大利语名称。

本工具包采用 MIT 许可，条款见 [LICENSE](LICENSE)。

## 开始之前

- 系统：Windows 10 及以上、macOS、Linux。
- AI Agent：须能读写本地文件、运行本地命令、读取图片内容并联网查询。
- 软件：Git、Python（3.14 及以上）、Node.js（24 及以上）、ExifTool、FFmpeg。相关依赖包见[ACATE_Spec_Tools〈§4 运行环境〉](Spec/ACATE_Spec_Tools.md#s4)。
- 工作流：建议复制一份素材，使用副本进行整理，原片最好脱机冷备份。工具包本身对素材严格只读，但无法约束 Agent 自身的行为，因此出于素材安全考虑，强烈建议采用此隔离工作流。

## 文档

| 组 | 文档 | 作用 |
|---|---|---|
| 总纲 | [ACATE_Spec_Principles](Spec/ACATE_Spec_Principles.md) | 全部规范共用的原则 |
|  | [ACATE_Spec_Workflow](Spec/ACATE_Spec_Workflow.md) | 从素材到成果的工作流，每一步做什么、交什么 |
|  | [ACATE_Spec_Glossary](Spec/ACATE_Spec_Glossary.md) | 术语的定义 |
| 规则 | [ACATE_Spec_Rules_Media_and_Sources](Spec/ACATE_Spec_Rules_Media_and_Sources.md) | 整理范围、查看画面与辅助资料 |
|  | [ACATE_Spec_Rules_Places](Spec/ACATE_Spec_Rules_Places.md) | 地点的认定、命名与坐标 |
|  | [ACATE_Spec_Rules_Itinerary](Spec/ACATE_Spec_Rules_Itinerary.md) | 行程段、分界与时间、交通 |
|  | [ACATE_Spec_Rules_Time_Lapse](Spec/ACATE_Spec_Rules_Time_Lapse.md) | 延时的认定与确认 |
| 成果与工具 | [ACATE_Spec_Workbook](Spec/ACATE_Spec_Workbook.md) | 行程表、目标文件夹和移动指南 |
|  | [ACATE_Spec_Review_Images](Spec/ACATE_Spec_Review_Images.md) | 提交用户判断的审查图的内容 |
|  | [ACATE_Spec_Contracts](Spec/ACATE_Spec_Contracts.md) | 运行目录与各运行文件的格式 |
|  | [ACATE_Spec_Tools](Spec/ACATE_Spec_Tools.md) | 读取器、审查图工具与工作簿工具的用法 |
| 维护 | [ACATE_Spec_Development](Spec/ACATE_Spec_Development.md) | 工具包的组成、规范的写法、测试与发布 |
|  | [ACATE_Spec_Version_Evolution](Spec/ACATE_Spec_Version_Evolution.md) | 各版本的演变 |
