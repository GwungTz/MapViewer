# 报告数据规范（schema_version = 1）

本文件描述 `*.mapview` 报告目录的磁盘格式。报告只包含静态文件，可由任意
静态服务器托管；`mapview-cli serve` 与 PyQt6 启动器使用绑定 `127.0.0.1`
的本地服务器。

## 1. 目录结构

```text
<stem>.mapview/
├── index.html                    # 页面骨架，不内嵌 MAP 数据
├── favicon.svg
├── manifest.json                 # 元数据、汇总、文件引用（唯一未压缩的 JSON）
├── assets/
│   ├── app.js                    # 视图状态、导航、搜索、配置弹窗
│   ├── treemap.js                # squarify 布局 + Canvas 2D 渲染
│   ├── data-worker.js            # fetch / gunzip / JSONL / LRU / 搜索
│   └── style.css
└── data/
    ├── tree-index.json.gz        # chunk key -> 页面文件、记录数、哈希
    ├── search-index.jsonl.gz     # 全局搜索索引
    ├── region/
    │   ├── root.jsonl.gz         # 首屏：根 + 执行区域 + object 摘要
    │   └── chunks/
    │       ├── rchunk00001.jsonl.gz
    │       └── rchunk00001.p002.jsonl.gz ...
    └── module/
        ├── root.jsonl.gz         # 首屏：根 + 分类 + 模块摘要（含 parts）
        └── chunks/
            └── mchunk00001.jsonl.gz ...
```

- 所有地址与大小均为 **整数 byte**；地址在 UI 中显示为 `0xXXXXXXXX`。
  超出 JavaScript 安全整数（2^53-1）的输入在解析阶段被拒绝，避免浏览器静默丢失精度。
- 文本编码统一 UTF-8；JSON 使用紧凑分隔符（`,`, `:`）。
- 每个 `.jsonl.gz` 是独立的 gzip 流；不支持在分片内部 seek，因此分片数量
  多于单个超大文件。

## 2. manifest.json

```json
{
  "schema_version": 1,
  "generator": {"name": "mapview-local", "version": "0.1.0"},
  "generator_version": "0.1.0",
  "generated_at": "2026-11-27T03:54:15+00:00",
  "source": {
    "file_name": "firmware.map",
    "size_bytes": 3640,
    "sha256": "fe97a6...",
    "format": "keil-ac5",
    "compiler": "Component: ARM Compiler 5.06 ...",
    "compiler_version": "AC5",
    "encoding": "utf-8"
  },
  "summary": {
    "rom": {"used": 414, "total": 1015808, "percent": 0.0408},
    "ram": {"used": 80, "total": 131072, "percent": 0.061}
  },
  "grand_totals": {
    "code": 294, "ro_data": 96, "rw_data": 24, "zi_data": 56,
    "total_ro": 390, "total_rw": 80, "total_rom": 414
  },
  "views": {
    "region": {
      "label": "Region View",
      "root_id": "rroot",
      "root_chunk": "data/region/root.jsonl.gz",
      "root_records": 11,
      "root_compressed_bytes": 462,
      "root_sha256": "ff3d4f...",
      "total_bytes": 472,
      "padding_bytes": 2,
      "object_count": 8,
      "objects_embedded": true,
      "node_count": 23,
      "section_count": 12,
      "chunk_count": 1
    },
    "module": {
      "label": "Module View",
      "root_id": "mroot",
      "root_chunk": "data/module/root.jsonl.gz",
      "root_records": 7,
      "root_compressed_bytes": 389,
      "root_sha256": "be6707...",
      "total_bytes": 470,
      "node_count": 18,
      "module_count": 4,
      "section_count": 11,
      "chunk_count": 1
    }
  },
  "tree_index": "data/tree-index.json.gz",
  "tree_index_sha256": "11bf7d...",
  "tree_index_compressed_bytes": 459,
  "search_index": "data/search-index.jsonl.gz",
  "search_index_sha256": "869991...",
  "search_index_compressed_bytes": 592,
  "search_index_records": 16,
  "compression": "gzip",
  "encoding": "utf-8",
  "warnings": [{"line": 12, "message": "Unrecognised memory map line; it was skipped"}],
  "warning_count": 1,
  "stats": {
    "sections": 13, "objects": 8, "records": 41, "chunks": 4,
    "compressed_bytes": 1800, "uncompressed_bytes": 9400,
    "timings_ms": {"parse": 1, "transform": 39, "write": 12, "verify": 8, "total": 77}
  }
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `schema_version` | 当前固定为 `1`；不兼容变更必须递增 |
| `source.sha256` | 原始 `.map` 字节的 SHA-256；网页内存配置以它为 key |
| `summary` | ROM/RAM 用量与容量（Keil 取 IROM/IRAM 执行区域，GNU ld 取 Memory Configuration）；`total = 0` 表示 MAP 未声明可识别的 ROM/Flash 或 RAM 区域，容量未知，UI 显示 `used / –` |
| `grand_totals` | 链接器报告/组件表汇总的 Code/RO/RW/ZI 与总计 |
| `views.*.root_chunk` | 首屏唯一需要的 node 分片 |
| `views.region.total_bytes` | Region 视图中实际布局的字节数（包含 PAD） |
| `views.region.padding_bytes` | 其中的 PAD 字节数；`region.total - module.total` 通常等于它 |
| `views.module.total_bytes` | 组件表汇总（Code+RO+RW+ZI），与 `grand_totals` 的来源一致 |
| `views.region.objects_embedded` | `true` 表示 object 摘要位于 root 分片；`false` 表示已迁移到 region 子分片（超大对象数时自动触发） |
| `*_sha256` | 对 **压缩文件字节** 计算，浏览器下载后可校验 |
| `warning_count` | 完整警告数量；`warnings` 最多保存前 200 条 |

## 3. Node 记录（JSONL 每行一条）

```json
{"id":"rsec00000004","parent_id":"robj0000003","view":"region","kind":"section",
 "name":"main","size":166,"category":"code","address":134250594,
 "object_file":"main.o","child_count":0,"children_chunk":null,
 "element_id":"e00000004"}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 报告内唯一、稳定。两视图使用不同前缀 |
| `parent_id` | string/null | 父节点；只有 root 记录为 `null`。父记录必须比子记录先写入 |
| `view` | `region` / `module` | 所属视图 |
| `kind` | `root` / `region` / `object` / `section` / `category` / `module` | 节点种类 |
| `name` | string | 显示名称（函数名优先，其次 section 名） |
| `size` | integer | 字节数；内部节点等于子节点之和（模块节点等于 `parts` 之和） |
| `category` | `code`/`rodata`/`rwdata`/`zidata`/`pad`/null | 颜色分类；内部节点为最大子类 |
| `address` | integer/null | 可选起始地址 |
| `object_file` | string/null | 所属 object / library member |
| `child_count` | integer | 子节点数量（模块为 parts 数量） |
| `children_chunk` | string/null | 子节点分片 key；`null` 表示子节点已在父分片中 |
| `element_id` | string（叶子） | 跨视图共享的物理 section 标识 |
| `parts` | object（模块） | `{"code":n,"rodata":n,"rwdata":n,"zidata":n}`，未出现的分类为 0 |

Region 视图层级：`root -> execution region -> object file -> section`。
Module 视图层级：`root -> category -> module -> (code/rodata/rwdata/zidata)`。
Module 的四个 part 节点由前端根据 `parts` 即时生成（不写入 JSONL）；点击
part 时加载该模块的 section 明细并按 `category` 过滤。

所有字符串来自 MAP 文件，属于不可信文本；前端仅使用 `textContent`。

## 4. 分片策略

1. **首屏分片**：默认 `region/root.jsonl.gz` 保存根、执行区域和全部 object 摘要；
   `module/root.jsonl.gz` 保存根、分类和全部模块摘要。首屏只需要 manifest +
   root 分片，不需要 tree index、search index 或任何子分片。
   当 region 视图的 object 数超过 50,000 时，object 摘要会迁移到
   `data/region/chunks/robjchunkNNNNN.jsonl.gz`；root 中只保留执行区域摘要，
   `objects_embedded=false`，首屏显示区域块，点击区域时再加载 object。
2. **子分片**：多个相邻父节点的子记录合并进一个 shard（默认每 shard 最多
   200 个父节点 / 5000 条记录 / 4 MiB 未压缩），避免产生数万个小文件。
   节点的 `children_chunk` 指向 shard key；UI 加载后按 `parent_id` 过滤。
3. **分页**：单个 shard 超过 5000 条或 4 MiB 时继续拆分页面：
   `rchunk00001.p002.jsonl.gz`。`tree-index` 中同一个 key 列出全部页面，
   UI 依次加载并合并。
4. **命名**：`rchunkNNNNN`（region section leaves）、`robjchunkNNNNN`（大报告的
   region object 摘要）、`mchunkNNNNN`（module section leaves）；节点 ID 前缀
   `rroot`/`rgn`/`robj`/`rsec`、`mroot`/`mcat`/`mmod`/`msec`。

## 5. tree-index.json.gz

```json
{
  "schema_version": 1,
  "generator_version": "0.1.0",
  "chunks": {
    "rroot": {
      "view": "region", "kind": "root", "records": 11,
      "pages": [{"path": "data/region/root.jsonl.gz", "records": 11,
                 "uncompressed_bytes": 1940, "compressed_bytes": 462,
                 "sha256": "ff3d4f..."}]
    },
    "rchunk00001": {
      "view": "region", "kind": "children", "records": 12,
      "pages": [{"path": "data/region/chunks/rchunk00001.jsonl.gz", "...": "..."}]
    }
  }
}
```

`tree-index` 本身也在 manifest 中记录哈希；首次需要子分片时才加载。

## 6. search-index.jsonl.gz

每条记录是一个可导航命中项：

```json
{"id":"rsec00000004","name":"main","object_file":"main.o","view":"region",
 "kind":"section","category":"code","address":134250594,"size":166,
 "path_ids":["rroot","rgn00001","robj0000003","rsec00000004"]}
```

- 名称与 object/library 名称做大小写无关子串匹配。
- 纯十六进制（`0x08001234`）或纯十进制查询按地址匹配；命中条件为
  `address <= q < address + size`，同时保留名称包含匹配。
- `path_ids` 用于从视图根逐级加载父分片并定位；模块记录的路径为
  `[mroot, category, module]`。
- UI 最多展示前 100 条，但 `total` 为全部命中数；快速输入时旧请求结果会被
  丢弃（requestId 比较）。

## 7. 完整性与原子性

- 每个 gzip 文件单独压缩，`mtime=0`，相同输入生成相同字节。
- 分片 `sha256`、`compressed_bytes`、`records`、`uncompressed_bytes` 均记录
  在 manifest 或 tree-index 中；浏览器对下载字节重新计算 SHA-256。
- 生成器先写入同级的隐藏临时目录，全部文件与索引写完并通过校验后才原子
  重命名为最终目录；替换旧报告时先备份再替换，失败会恢复。
- `mapview-cli verify --level basic|full`：
  - `basic`：manifest、文件哈希/大小、gzip 解压、JSONL 解析、记录数。
  - `full`：额外验证唯一 ID、父子关系、无环、节点 size 汇总、chunk 引用、
    search path 解析。

## 8. 错误码

Python：`UnsupportedMapFormat`、`MapParseError(line_no)`、`EmptyMapData`、
`TreeBuildError`、`ReportWriteError`、`ChunkIntegrityError(chunk)`、
`GenerationCancelled`。

浏览器：`MANIFEST_INVALID`、`CHUNK_FETCH_FAILED`、`CHUNK_DECOMPRESS_FAILED`、
`CHUNK_INTEGRITY_FAILED`、`JSONL_PARSE_FAILED`、`SEARCH_INDEX_FAILED`、
`WORKER_ERROR`。错误信息包含 chunk ID / 行号 / 可操作建议。

## 9. 兼容性

- 读取端只接受 `schema_version == 1`；收到其他版本时显示明确错误。
- 新增可选字段（例如新的 `kind` 或 `category`）属于向后兼容变更；删除或
  改变字段语义必须提升 schema 版本。
- `generator_version` 仅用于诊断，不参与兼容性判断。
