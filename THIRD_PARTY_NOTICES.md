# Third-party notices

## Map View (upstream)

- Project: **Map View / Map View Embedded**
- Repository: <https://github.com/charcoal141/Map-View>
- Pinned commit: `e4bcb551981f4fbcfc97f1a46f90f2b8b6427d4c`
- License: MIT, Copyright (c) 2025 charcoal141
- Upstream license copy: [`third_party/Map-View/LICENSE`](third_party/Map-View/LICENSE)

The upstream repository is kept under `third_party/Map-View/` as a comparison
baseline. The following files were studied and ported / adapted:

| Upstream file | Use in this project |
|---|---|
| `src/parser/index.ts` | Behavioural port to `mapcore/parser/keil_parser.py` (Keil MDK / AC5 / AC6 / EIDE ArmLink) |
| `src/parser/esp32Parser.ts` | Behavioural port to `mapcore/parser/gnu_ld_parser.py` (GNU ld / ESP-IDF / ArtInChip) |
| `src/parser/types.ts` | Canonical model in `mapcore/parser/models.py` |
| `src/transformer/treeBuilder.ts` | Region tree, Module tree, category rules and memory summary in `mapcore/transform/` |
| `media/treemap.js` | squarify / worst-aspect layout, category colour map and drill-down model, rewritten for Canvas 2D and lazy shard loading in `web/js/treemap.js` |
| `media/style.css` | UI layout inspiration; `--vscode-*` variables replaced with `--app-*` tokens in `web/css/style.css` |
| `test/fixtures/eide-arm-link-map.txt` | Copied to `mapcore/tests/fixtures/eide_arm_link_map.txt` |

### Local modifications

- Parsers reimplemented in Python, with warnings for unrecognised data lines
  and explicit `UnsupportedMapFormat` / `EmptyMapData` failures.
- The numeric line after `Grand Totals` is parsed (upstream only reads numbers on
  the same line, leaving the Code/RO/RW/ZI buckets at zero for regular Keil
  output).
- Module view removes duplicate library-member entries that Keil reports in both
  the object table and the "Library Member Name" table.
- The web UI no longer uses `acquireVsCodeApi()`, `postMessage`, VS Code CSS
  variables or a full-tree `window.__DATA__` injection. It loads versioned
  gzip JSONL shards through a Web Worker and renders with Canvas 2D.
- All MAP-derived strings are inserted with `textContent`; no HTML is built from
  symbol names.

The upstream MIT license text is reproduced below.

```text
MIT License

Copyright (c) 2025 charcoal141

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
