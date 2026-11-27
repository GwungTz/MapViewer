# 上游 Map View 审计与行为对照

## 1. 基线

| 项目 | 值 |
|---|---|
| 仓库 | <https://github.com/charcoal141/Map-View> |
| commit | `e4bcb551981f4fbcfc97f1a46f90f2b8b6427d4c` |
| License | MIT（Copyright (c) 2025 charcoal141） |
| 本地副本 | `third_party/Map-View/`（保留完整源码与 LICENSE） |
| 上游 package 版本 | `2.7.0`（不单独作为兼容依据） |

## 2. 复用 / 替换清单

| 上游模块 | 处理 | 本项目位置 |
|---|---|---|
| `src/parser/index.ts` | 逐行移植状态机、正则、AC5/AC6 函数名规则、EIDE 变体、Component Sizes | `mapcore/parser/keil_parser.py` |
| `src/parser/esp32Parser.ts` | 逐行移植阶段机、Memory Configuration、output section、entry/continuation、`*fill*`、符号过滤、object/library 名归一化 | `mapcore/parser/gnu_ld_parser.py` |
| `src/parser/types.ts` | dataclass 等价模型 | `mapcore/parser/models.py` |
| `src/transformer/treeBuilder.ts` | `build_region_tree` / `build_module_tree` / `build_memory_summary` / 分类规则 | `mapcore/transform/` |
| `media/treemap.js` | squarify、`layoutRow`、`worstAspect`、颜色映射、下钻模型 | `web/js/treemap.js`（Canvas 2D + 按需加载） |
| `media/style.css` | 布局结构参考；替换全部 `--vscode-*` | `web/css/style.css` |
| `test/fixtures/eide-arm-link-map.txt` | 复制为回归 fixture | `mapcore/tests/fixtures/eide_arm_link_map.txt` |
| `src/extension.ts`、`src/webview/WebviewProvider.ts` | **不复用** | 由 PyQt6 + 本地 HTTP 服务替代 |
| `acquireVsCodeApi()` / `postMessage` / `window.__DATA__` | **删除** | Worker + gzip JSONL 分片 |

## 3. 已保留的上游行为（包括特殊边界）

- 标准 Keil 执行区域表（`Base Addr / Size / Type / Attr / Idx / E / Section / Object`）
  实际由上游的 **EIDE 正则** 匹配，因此 `load_addr` 为 `null`；本实现保持一致。
  带独立 load address 列或 `COMPRESSED` / `-` 的行仍由主正则处理。
- 同名 section 合并规则（`mergeSameNameEntries`）：大小相加，只有当后一条
  目比之前累计值更大时才采用其地址；类型/属性取第一条。
- AC5 `i.main`、AC6 `.text.main` 函数名提取；AC6 `OUTLINED_FUNCTION_*` 不算函数名。
- GNU ld 中 `*fill*` 的分类顺序：在 `.text`/`.data`/`.rodata` 等父 section 内，
  fill 会先命中父 section 的类别；只有父 section 无匹配时才成为 `PAD`。
- `get_function_from_section(".iram0.text.bar") == "text.bar"`（正则的分组行为）。
- `Memory Configuration` 后固定跳过空行与表头两行。
- ESP-IDF/ArtInChip 的 object 名归一化（`lib.a(member)`、`.o`、`.obj`）。
- Keil 的 `Total RO/RW/ROM Size` 缺失时的回填公式。
- squarify 的行切分、间距、padding 与最差宽高比评估。

## 4. 有意的偏差（均为修复或契约强化）

| 偏差 | 原因 | 影响 |
|---|---|---|
| 解析 `Grand Totals` 后的纯数字行 | 上游只解析与标题同一行的数字，常规 Keil 输出会导致 Code/RO/RW/ZI 图例为 0 | 图例数值正确；`totalRO/RW/ROM` 不变 |
| Module 视图按 `(library, object)` 去重 | Keil 在 object 表和 `Library Member Name` 表重复列出库成员，上游直接拼接会双倍计算 | 模块总量与 `grand_totals` 一致 |
| 未知格式抛 `UnsupportedMapFormat` | 上游会静默返回空数据 | UI 给出明确失败，不生成空报告 |
| 无法识别的 `0x...` 数据行 / 越界 section 产生 warning + 行号 | 便于定位格式变体与损坏输入 | 有效记录不受影响 |
| BOM / GB18030 检测并报告 | Keil 在中文 Windows 上可能输出 GBK | 不静默丢字节 |
| 地址/大小进行负值与类型校验 | 数据契约要求 | 损坏输入不会进入报告 |
| Region 视图总量包含 PAD；Module 视图总量来自组件表 | 两种视图的语义本来不同 | manifest 用 `views.region.padding_bytes` 明确差额 |

## 5. 对照方法与结果

`tools/upstream_parity/` 使用 esbuild 把上游 `src/parser/index.ts` 打包为
CommonJS，然后用 `compare.js` 输出统计；`mapcore/tests/test_upstream_parity.py`
在 Node 与 bundle 可用时自动对比。

重建 bundle：

```bash
cd tools/upstream_parity
npm install
npx esbuild ../../third_party/Map-View/src/parser/index.ts \
  --bundle --platform=node --format=cjs --outfile=upstream-parser.cjs
```

四个 fixture 的对照结果（2026-11-27 实测）：

| fixture | 格式 | 执行区域 | section | 组件 | 库成员 | totalRO | totalRW | totalROM |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `keil_ac5.map` | AC5 | 2 | 13 | 4 | 1 | 390 | 80 | 414 |
| `keil_ac6.map` | AC6 | 1 | 6 | 2 | 0 | 144 | 0 | 144 |
| `eide_arm_link_map.txt` | AC5/EIDE | 2 | 6 | 2 | 0 | 350 | 64 | 366 |
| `gnu_ld_esp32.map` | GNU ld | 5 | 12 | 1 | 1 | 152 | 88 | 192 |

Python 与上游在区域数、section 数、组件数、库成员数、分类计数、函数名序列和
上述总计上完全一致。唯一有意的差异是上游 `grandTotals.code/roData/rwData/ziData`
在 Keil 输出中为 0，而 Python 解析了纯数字汇总行（见第 4 节）。

## 6. 未承诺的格式

上游 README 提到 ArtInChip / EIDE 兼容。本实现按 GNU ld / Keil 的代码路径
处理这些变体，但没有独立的新格式解析器。遇到其他链接器（例如 IAR）会明确
报 `UnsupportedMapFormat`，不会猜测字段。
