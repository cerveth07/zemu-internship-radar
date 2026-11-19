# BOSS 城市映射

本版收录 336 项，即 335 个城市或地区和“全国”。与上一版逐项核对，收录项的界面名称和九位查询参数全部相同。生成器只读取以下固定版本的开放材料。BOSS 城市接口快照仅用于内部兼容性对照，源码包、Git 历史和重建输入均不含该快照。

## 数据来源和许可

| 材料 | 使用范围 | 随包许可 |
| --- | --- | --- |
| [modood/Administrative-divisions-of-China](https://github.com/modood/Administrative-divisions-of-China/tree/c49d495b40ac73eb1a66f6eeae5f8fd10696f035/dist) | 使用中国行政区划名称、层级与行政代码，限定城市所在省和上级区划。上游说明数据来自国家统计局。 | [WTFPL v2](../notices/city-map/administrative-divisions-LICENSE.txt) |
| [wb04307201/cn-weather-mcp](https://github.com/wb04307201/cn-weather-mcp/blob/62e117a781e650b5657867373117fe6a586e68eb/src/main/java/cn/wubo/cn/weather/mcp/WeatherService.java) | 读取上游 Java 代码中列明的天气站名称、站号与顺序，提取省和站组前缀。项目修改了命名、排序以及编码格式，并按兼容结果筛选。 | [Apache-2.0](../notices/city-map/cn-weather-mcp-LICENSE.txt) |
| [ufownl/auto-zhipin](https://github.com/ufownl/auto-zhipin/blob/09e6af1ab35e8dd2ea420e860fccc4b95642223c/README.md) | 文档给出了在地址栏观察到的“全国”查询参数。只使用这一个已明确说明的九位标识。 | [BSD-3-Clause](../notices/city-map/auto-zhipin-LICENSE.txt) |

再分发本表时请一并保留本说明及上述完整许可、版权声明和免责条款。项目的根 MIT 许可证适用于项目自有实现。这些资料的许可与访问招聘平台服务的条件分别见[授权说明](../LICENSE-STATUS.md)。

## 每一条怎样核实

不同系统的九位编码并不通用。先用公开区划和天气站材料生成候选值，再与此前版本逐项比较名称和完整九位值。只纳入两者都相同的记录。城市和区县在省内关联，单独的天气站组须唯一。襄樊与襄阳按国函〔2010〕129号的更名对应。以下是各类规则实际纳入的项数。

| 规则 | 项数 | 候选值的构成 |
| --- | ---: | --- |
| `station-group` | 252 | 取匹配天气站号前七位并补两位零。 |
| `admin-order` | 55 | 以省内行政区划代码排序，将从 1 起的两位序号接在五位省前缀后，再补两位零。 |
| `station-order` | 3 | 对省内已匹配区划按天气站组号排序，按同样格式取序号。 |
| `station-by-level` | 19 | 先按地级市、其他地级区划、省直辖县级区划分组，再按站组号排序取序号。 |
| `station-source-order` | 6 | 按已匹配站组在固定版本天气源码中首次出现的位置排序取序号。 |
| `documented-url` | 1 | 取 BSD 许可示例中的“全国”参数。 |

[逐项选择清单](../tools/city-map/selections.json)记录每个显示名、区划键及采用的规则。规则用于重建这份经过比对的固定表；新增地区仍需核对实际的名称与参数。以上证据验证与此前版本的兼容性，平台今后的变更要另行复核。

## 暂缺的 38 项

东沙群岛、丽江、五家渠、伊犁哈萨克自治州、克孜勒苏柯尔克孜自治州、凉山彝族自治州、北屯市、博尔塔拉蒙古自治州、双河市、台湾、吐鲁番、哈密、喀什地区、嘉峪关、图木舒克、塔城地区、大理白族自治州、巴音郭楞蒙古自治州、德宏傣族景颇族自治州、怒江傈僳族自治州、楚雄彝族自治州、澳门、甘孜藏族自治州、白杨市、石河子、红河哈尼族彝族自治州、胡杨河市、西双版纳傣族自治州、迪庆藏族自治州、锡林郭勒盟、阿克苏地区、阿勒泰地区、阿拉尔、陇南、香港、黄山、黔东南苗族侗族自治州、黔南布依族苗族自治州。

这些名称未进入本版的 BOSS 城市选择列表。旧清单或已保存的检索条件用到它们时，可改选受支持地区。按岗位原始链接读取或收藏仍使用原有的链接流程，名称检索可用范围以上表为准。

## 重建

在源码根目录运行以下命令可从上游重建独立副本并比对。日常运行使用包内静态 JSON。

```powershell
python tools/city-map/rebuild.py --output "$env:TEMP/radar-cities-rebuilt.json"
git diff --no-index --ignore-space-at-eol -- tools/boss-cities.json "$env:TEMP/radar-cities-rebuilt.json"
```

[生成脚本](../tools/city-map/rebuild.py)固定了上游提交、文件路径及 SHA-256，输入校验不符会停止。离线时可将相同文件放入一个目录，以该脚本 `INPUTS` 的键为文件名，在命令中添加 `--inputs 目录`。
