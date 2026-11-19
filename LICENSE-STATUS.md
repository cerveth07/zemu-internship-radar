# 授权范围与第三方材料

核查日期为 2026 年 11 月 19 日。维护者已确认有权将项目自有代码（含采集与收藏连接器）、文档、提示词、合成名单与样例、桌面和网页品牌 PNG 及界面内联 SVG 以 cerveth07 名义按 [MIT](LICENSE) 许可。

## 随源码提供的第三方材料

| 路径 | 来源与处理 |
| --- | --- |
| `vendor/internship-workbench/` | 上游组件采用 MIT，保留[原作者版权声明及许可](vendor/internship-workbench/LICENSE)。 |
| `vendor/offer-harvester/` | 文本处理函数改编自上游 MIT 项目，保留[原作者版权声明及许可](vendor/offer-harvester/LICENSE)。 |
| `notices/` | 保留开发和构建依赖的许可与通知。源码安装依赖时适用各自许可证。[来源记录](notices/README.txt) |
| `tools/boss-cities.json` | 城市映射使用下列开放材料生成：modood 行政区划数据（WTFPL v2）、cn-weather-mcp 天气站标识（Apache-2.0）、auto-zhipin 的“全国”查询示例（BSD-3-Clause）。本包已随附三个上游的完整许可。映射的再分发须保留相应的声明及许可。[数据来源、加工和许可](docs/boss-city-mapping.md) |

两个复用组件的版本、文件和改编范围见[第三方源码清单](vendor/README.md)。

## OCR 测试图

`assets/ocr-smoke.b64` 是一张 Base64 编码的 PNG，用于检查中文 OCR 是否真正读出文字。源码候选版使用两行项目自拟文字“实习岗位测试”和“本地文字识别”，由[生成脚本](tools/make_ocr_fixture.py)排版。图中只有文字像素，字体和 OCR 模型均由使用者按需另行获取。

重建时使用 Pillow 和 Noto Sans CJK SC Regular（Sans2.004）。下载固定版本的 OTF 后运行 `python tools/make_ocr_fixture.py 路径/NotoSansCJKsc-Regular.otf`。脚本会校验字体文件的 SHA-256。OFL 官方 FAQ 1.1 允许将字体用于创建图像。[字体原文件](https://github.com/notofonts/noto-cjk/blob/523d033d6cb47f4a80c58a35753646f5c3608a78/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf) · [字体许可](https://github.com/notofonts/noto-cjk/blob/523d033d6cb47f4a80c58a35753646f5c3608a78/LICENSE) · [OFL FAQ](https://openfontlicense.org/ofl-faq/)

## 平台接入和账号操作

- **BOSS 直聘。** 用户协议限制未经许可通过第三方工具接入服务和取得信息，列举的活动包括通过第三方软件登录和浏览职位。未查到对本项目自动采集或使用登录账号收藏的公开授权。用户在界面确认收藏表示同意该次账号操作，平台对第三方接入的要求仍适用。[BOSS 直聘用户协议](https://about.zhipin.com/agreement?id=registerprotocol)
- **实习僧。** 用户协议第三章第（二）节限制脚本自动访问或登录，限制未经许可的第三方接入、抓取和批量检索，并对转发资料另设同意条件。未查到对本项目的公开特许。[实习僧用户协议](https://www.shixiseng.com/rule)

项目许可证授予使用、修改和分发相应源码的权利。真实平台接入、账号操作及再次分发岗位内容的权利根据平台约定和相关权利人的授权判断。上述公开条款不能用来证明项目已获得特定合作协议。
