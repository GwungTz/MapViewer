# Map View Local

把嵌入式链接器生成的 `.map` 文件转换成可在浏览器中交互浏览的 Treemap 报告。
本工具是 [charcoal141/Map-View](https://github.com/charcoal141/Map-View) VS Code
插件的独立本地化版本：

- **PyQt6** 只负责选文件、后台生成、显示进度、启动 `127.0.0.1` 本地服务并打开默认浏览器；
- **Python** 负责解析 Keil MDK（AC5/AC6/EIDE）与 GNU ld / ESP-IDF / ArtInChip MAP，
  构建 Region / Module 视图并输出带版本号的压缩分片；
- **HTML/JavaScript** 负责 Treemap、下钻、Tooltip、搜索与 ROM/RAM 配置；
- 不依赖 VS Code、Node.js（最终用户侧）、CDN、QWebEngine 或互联网。

## 功能

- Region View：执行区域 → object 文件 → 函数/段；
- Module View：User Code / Third Party / Compiler Library / Chip Library /
  USB-Middleware / System Library 等分类 → 模块 → Code/RO/RW/ZI 明细；
- Canvas 2D squarify 布局，沿用上游的颜色与下钻模型；
- 大数据按需加载：首屏只请求 `manifest.json` + 根分片；
- Web Worker 内完成 gzip 解压、JSONL 增量解析、LRU 缓存与全局搜索；
- 全局搜索：符号、object、库、模块；支持 `0x08001234` 与十进制地址；
- ROM/RAM 用量条与容量配置（保存在浏览器 `localStorage`，按源文件 SHA-256 区分）；
- 报告目录可整体复制/归档，任何静态服务器都能托管。

## 环境要求

- 运行打包版：Windows 10/11（无需 Python / Node / VS Code）；
- 从源码运行：Python 3.10+，`pip install -e ".[gui]"`；
- 浏览器：支持 `DecompressionStream('gzip')`、`TextDecoderStream` 与 Web Worker
  的当前版本 Chrome / Edge / Firefox / Safari（Safari 16.4+）。

## 快速开始（图形界面）

```bash
python -m app.main
# 或安装后：
mapview
```

1. 选择 `.map` 文件（也可以直接拖进窗口）；
2. 确认报告目录（默认 `<MAP 所在目录>/<MAP 文件名>.mapview`）；
3. 选择校验级别并点击“生成报告”；
4. 生成完成后程序启动本地服务并自动打开浏览器；关闭启动器后，浏览器中尚未
   缓存的分片将无法继续加载。

## 命令行

```bash
# 仅生成报告（不需要 PyQt6 / 浏览器）
mapview-cli generate firmware.map
mapview-cli generate firmware.map -o D:/reports/firmware.mapview --verify full

# 校验已有报告
mapview-cli verify D:/reports/firmware.mapview --level full

# 在 127.0.0.1 上托管报告（0 = 随机空闲端口）
mapview-cli serve D:/reports/firmware.mapview --open

# 查看 manifest 摘要
mapview-cli manifest D:/reports/firmware.mapview
```

也可使用 `python -m mapcore.cli ...`。

## 支持格式与识别规则

格式按文件内容识别，不依赖扩展名：

| 格式 | 识别特征 | 说明 |
|---|---|---|
| Keil MDK AC5 / AC6 | 首行 `Component: ARM Compiler ...` / `Arm Compiler for Embedded`，或 `Memory Map of the image` | 含 AC5 `i.func` / AC6 `.text.func` 函数名规则、OUTLINED_FUNCTION、PAD、压缩区域、EIDE ArmLink 变体 |
| GNU ld / GCC / ESP-IDF / ArtInChip | `Memory Configuration` + `Linker script and memory map`，或 `Archive member included` / `LOAD` | 保留 output section、object member、`*fill*`、符号过滤、Memory Region |
| 其他（IAR 等） | 不匹配 | 明确报 `UnsupportedMapFormat`，不会生成空报告 |

解析规则来自上游源码 commit `e4bcb551981f4fbcfc97f1a46f90f2b8b6427d4c`，对照测试与
有意偏差见 [`docs/UPSTREAM_PARITY.md`](docs/UPSTREAM_PARITY.md)。

## 报告目录

```text
firmware.mapview/
├── index.html
├── manifest.json
├── assets/{app.js,treemap.js,data-worker.js,style.css}
└── data/
    ├── tree-index.json.gz
    ├── search-index.jsonl.gz
    ├── region/{root.jsonl.gz, chunks/*.jsonl.gz}
    └── module/{root.jsonl.gz, chunks/*.jsonl.gz}
```

完整 schema 见 [`docs/report_schema.md`](docs/report_schema.md)。要点：

- 首屏只加载 `manifest.json` 与 `region/root.jsonl.gz`（或 `module/root.jsonl.gz`）；
- 点击节点时才请求对应 shard，返回上级使用 Worker 缓存；
- 每个 shard 单独 gzip，记录 SHA-256/大小/记录数，损坏时 UI 报出具体 chunk；
- 生成使用临时目录 + 原子重命名，失败不会留下半成品报告。

## 性能

实测数据（合成数据、Windows + headless Chrome）见
[`docs/performance.md`](docs/performance.md)。摘要：

| 数据规模 | 生成流水线（basic 校验） | 浏览器首屏 | 热搜索 |
|---|---:|---:|---:|
| 50k sections / 58k region 节点 | 5.6 s | 302 ms | 63 ms |
| 200k sections / 232k region 节点 | 24.1 s | 502 ms | 55 ms |
| 1M sections / 1.16M region 节点 | 132.7 s | 228 ms（root 已迁移 object 摘要） | 123 ms |

生成时间主要花在逐 shard gzip；首屏始终只需要 manifest + 一个 root 分片。

## 分享与重新打开报告

- 报告目录是自包含的：复制整个 `<stem>.mapview` 目录即可分享；不要只复制 `index.html`。
- 重新打开：GUI 中点击“打开已有报告…”或双击“最近生成的报告”；也可以执行
  `mapview-cli serve <报告目录> --open`。
- 不要直接双击 `index.html` 以 `file://` 方式打开——浏览器会阻止读取
  `manifest.json`/`.jsonl.gz`（opaque origin + fetch 限制）。
- 报告目录可以压缩归档；解压后仍需通过本地 HTTP 服务打开。

## 常见问题

| 现象 | 处理 |
|---|---|
| 页面提示 `MANIFEST_INVALID` | 报告不完整或版本不匹配；重新生成 |
| `CHUNK_FETCH_FAILED` | 启动器/本地服务已关闭，或报告目录被移动；重新打开报告 |
| `CHUNK_INTEGRITY_FAILED` | 分片被修改或损坏；重新生成报告 |
| `UnsupportedMapFormat` | 该 `.map` 不是 Keil / GNU ld 格式；查看错误中的检测标记 |
| 报告目录替换失败（Access denied） | 关闭占用该目录的浏览器/资源管理器后重试 |
| 浏览器无 `DecompressionStream` | 升级到当前 Chrome/Edge/Firefox，或 Safari 16.4+ |

## 最近报告与日志

- 最近报告保存在 `QSettings`（Windows 注册表），双击列表项重新打开；
- 日志写入 `%LOCALAPPDATA%\MapViewer\logs\mapviewer.log`（滚动，最多 3 个备份）；
  菜单“文件 → 打开日志目录”。

## 开发

```bash
pip install -e ".[gui,dev,build]"

# 单元测试（含上游解析器对照，Node/bundle 可用时自动运行）
python -m pytest mapcore/tests -q

# 可选：构建上游解析器对照 bundle
cd tools/upstream_parity
npm install
npx esbuild ../../third_party/Map-View/src/parser/index.ts \
  --bundle --platform=node --format=cjs --outfile=upstream-parser.cjs

# 生成合成 MAP 与性能数据
python tools/generate_synthetic_map.py --sections 120000 --objects 4000 -o build/synthetic.map
python tools/benchmark.py --sections 120000 --objects 4000

# 可选：无头浏览器性能/功能验证（需要本机 Chrome/Edge + playwright-core）
cd tools/browser_bench && npm install && cd ../..
python tools/browser_smoke.py build/synthetic_report.mapview --query main
python tools/browser_benchmark.py build/synthetic_report.mapview

# 打包（PyInstaller，one-folder）
python -m PyInstaller --noconfirm mapviewer.spec
# 产物：dist/MapViewLocal/MapViewLocal.exe
# 打包后的自检：MapViewLocal.exe --smoke-test
```

### 目录结构

```text
app/            PyQt6 启动器（窗口、后台任务、QSettings、日志）
mapcore/
  parser/       格式识别、Keil / GNU ld 解析、编码处理、canonical model
  transform/    Region / Module 视图、分类规则、ROM/RAM 汇总
  export/       manifest、JSONL.gz 分片、tree index、搜索索引、校验
  tests/        fixtures 与 unittest/pytest 回归
web/            独立报告模板（HTML/CSS/Canvas/Worker）
tools/          合成数据、性能基准、上游对照
docs/           报告 schema、上游对照、性能记录
third_party/    固定 commit 的上游源码（MIT）
```

## 安全说明

- 本地服务只绑定 `127.0.0.1`，只读、只服务报告目录内文件，禁止目录列表与
  路径穿越；`.jsonl.gz` 以 `application/gzip` 原样返回，不额外声明
  `Content-Encoding`。
- 页面使用严格 CSP，无 CDN、无内联脚本、无 `eval`。
- MAP 中的符号/路径/库名一律通过 `textContent` 写入 DOM，Tooltip 不拼接 HTML。

## 已知限制

- 不提供“单文件 HTML 内嵌全部数据”模式：报告始终通过本地 HTTP 服务按需加载分片；
  直接双击 `index.html` 以 `file://` 打开会被浏览器的 fetch/CORS 策略阻止。
- ROM/RAM 只反映 MAP 中的信息；外部存储器或链接器未报告的区域不会出现。
- 浏览器内存容量覆盖保存在 `localStorage`，按源文件 SHA-256 区分；启动器会尽量复用
  上次的环回端口以保持同一 origin，若端口被占用则改用随机端口，此时历史覆盖值不可见。
- Keil 的 Component Sizes 与执行区域 section 明细是两套统计口径：Region 视图
  可能包含 PAD，Module 视图使用组件表；`manifest.json` 中
  `views.region.padding_bytes` 给出差额。
- Region/Module 搜索索引覆盖 region 叶子与模块；模块视图中的 part 是前端
  即时生成的聚合节点。

## 许可证

本项目 MIT。上游 Map View（MIT, Copyright (c) 2025 charcoal141）的来源、固定
commit 与本地修改见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；上游
许可证原文见 [`third_party/Map-View/LICENSE`](third_party/Map-View/LICENSE)。
