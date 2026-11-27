# 性能测试记录

测试日期：2026-11-27
机器：Windows 11，Python 3.14.4，Chrome（headless，Playwright 驱动，1440×900 视口）
数据：`tools/generate_synthetic_map.py` 生成的 GNU ld 格式合成 MAP（**非真实固件**）
命令：

```bash
python tools/benchmark.py --scenario small  --verify basic --keep
python tools/benchmark.py --scenario medium --verify basic --keep
python tools/benchmark.py --scenario large  --verify basic --keep
python tools/browser_benchmark.py build/perf/report_<N>.mapview
```

数据形状：4 个输出 section（`.text/.rodata/.data/.bss`），object 名
`build/module_XXXXX.o`，每个 object 在每类 section 中各有若干 16–384 字节的
输入段。Large 档的 region 视图对象数超过 50,000，触发“对象摘要移出 root 分片”
策略。

## 1. 生成流水线

| 档位 | section | region 节点 | module 节点 | MAP | 报告 | 分片/记录 | root 分片（region / module） | 解析 | 转换 | 写入 | 校验 basic | 总计 |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| Small | 50,000 | 58,005 | 52,002 | 3.8 MiB | 2.8 MiB | 52 / 110,007 | 124.8 KiB / 47.9 KiB | 867 ms | 256 ms | 3,453 ms | 996 ms | 5,610 ms |
| Medium | 200,000 | 232,005 | 208,002 | 15.3 MiB | 11.2 MiB | 202 / 440,007 | 497.1 KiB / 190.8 KiB | 3,455 ms | 1,363 ms | 15,001 ms | 4,197 ms | 24,117 ms |
| Large | 1,000,000 | 1,160,005 | 1,040,002 | 76.3 MiB | 55.4 MiB | 1,006 / 2,200,007 | 0.3 KiB / 952.0 KiB | 21,495 ms | 7,143 ms | 82,324 ms | 21,266 ms | 132,692 ms |

说明：

- 写入时间是最大头（单个 shard 独立 gzip，`compresslevel=6`，`mtime=0`）。
  这是“报告可复现、损坏可定位”的代价；如追求更快的生成速度，可在
  `generate_report(..., compresslevel=1)` 中降低压缩级别（体积会增大）。
- Large 的校验为 `basic`（解压 + 行解析 + 哈希/计数）；`full` 会额外建立
  ID/父子/汇总图，内存与时间更高。
- 生成过程不阻塞 Qt 主线程：PyQt6 通过 `QThread` + `CancellationToken` 调用同一
  纯 Python 流水线，进度通过信号更新。

## 2. 浏览器首屏与交互

同一批报告通过 `ReportServer` 托管，Playwright 启动本机 Chrome（headless）：

| 档位 | 首屏（加载到首次绘制） | 首次加载分片数 | 首屏布局耗时 | 下钻（最大 region） | 返回 | Module View | resize 重绘 | 搜索（冷/热） | JS 堆峰值 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| Small | 302 ms | 1（root） | 26 ms | 28 ms | 49 ms | 41 ms | 79 ms | 180 ms / 63 ms | 11.8 MB |
| Medium | 502 ms | 1（root） | 119 ms | 55 ms | 137 ms | 110 ms | 81 ms | 600 ms / 55 ms | 29.9 MB |
| Large | 228 ms | 1（root） | 8 ms | 493 ms | 136 ms | 484 ms | 223 ms | 2,861 ms / 123 ms | 58.4 MB |

- `首屏` 只请求 `manifest.json` + `region/root.jsonl.gz`；Large 的 root 分片
  只有 0.3 KiB，所以首屏极快，但点击 region 时才下载 40,000 个 object 摘要
  （下钻 482 ms）。
- `搜索（冷）` 包含首次下载 + gzip 解压 + JSONL 解析 + 小写预计算；`热` 为
  纯扫描。Large 的搜索索引约 1.04M 条记录，热搜索 123 ms。
- `resize` 只重新布局/绘制，不重新下载或解析分片。
- JS 堆峰值来自 `performance.memory.usedJSHeapSize`，仅 Chrome 提供；包含
  Worker 之外的页面与缓存的节点对象。

## 3. 目标对照

| 目标 | 结果 |
|---|---|
| 首屏只解析 manifest + root 分片 | 是（`loadedChunks=1`，无 tree/search index 请求） |
| 100k 节点级首屏约 2 s | Medium（232k 节点）首屏 502 ms；Large root 迁移后 228 ms |
| 详情按需加载，单次交互不重新解压全量 | 是；下钻/返回走 Worker LRU 与页面节点缓存 |
| 搜索结果上限与耗时记录 | 最多展示 100 条；Large 热搜索 123 ms，冷 2.9 s |
| 不能靠海量 DOM 卡死 | 全部矩形绘制在单个 Canvas 上；40,000 block 布局 200 ms 左右 |

## 4. 未测量 / 限制

- 合成数据不包含真实固件的长符号名、深层库路径、极端碎片化分布；真实
  `.map` 的 root 分片通常远小于 Large 合成数据。
- headless Chrome 的 Canvas 走软件渲染；真实 GPU 环境可能更快。
- 首次打开还受操作系统文件缓存、Defender 扫描影响，生成时间会有 10–30% 波动。
- 浏览器首次绘制（FCP/LCP）未单独采集；`首屏` 以“应用完成第一次 treemap
  绘制”为准（`window.__MAPVIEW_READY__`）。
