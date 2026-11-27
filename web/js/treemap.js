/*
 * Map View Local - Canvas 2D treemap renderer.
 *
 * The squarify / worst-aspect layout is a port of upstream media/treemap.js.
 * Unlike upstream, this renderer lays out only the current root's visible
 * levels (at most two: group labels + blocks) and draws every rectangle on a
 * single canvas, so 100k nodes never become 100k DOM elements.
 */
(function (global) {
  'use strict';

  const COLORS = {
    code: '#4A90D9',
    rodata: '#50B86C',
    rwdata: '#E8943A',
    zidata: '#E05252',
    pad: '#8A8D91',
  };

  const CATEGORY_LABELS = {
    code: 'Code',
    rodata: 'RO-Data',
    rwdata: 'RW-Data',
    zidata: 'ZI-Data',
    pad: 'PAD',
  };

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return '–';
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return bytes + ' B';
  }

  function formatAddress(address) {
    if (address === null || address === undefined) return null;
    const value = Number(address);
    if (!Number.isFinite(value) || value < 0) return null;
    return '0x' + value.toString(16).toUpperCase().padStart(8, '0');
  }

  function sortChildren(children) {
    return children
      .filter((child) => Number(child.size) > 0)
      .slice()
      .sort((a, b) => Number(b.size) - Number(a.size));
  }

  function createRenderer(canvas, host, options) {
    const opts = options || {};
    const ctx = canvas.getContext('2d', { alpha: true });
    const overlayCanvas = opts.overlayCanvas || null;
    const octx = overlayCanvas ? overlayCanvas.getContext('2d', { alpha: true }) : null;
    let width = 0;
    let height = 0;
    let dpr = 1;
    let state = {
      root: null,
      getChildren: function () {
        return [];
      },
      levels: 1,
      highlightId: null,
    };
    let blocks = [];
    let groups = [];
    let grid = new Map();
    let gridCell = 48;
    let hoveredId = null;
    const styles = global.getComputedStyle(host);

    function ensureSize() {
      const rect = host.getBoundingClientRect();
      const nextWidth = Math.max(1, Math.floor(rect.width));
      const nextHeight = Math.max(1, Math.floor(rect.height));
      const nextDpr = Math.min(global.devicePixelRatio || 1, 3);
      if (nextWidth !== width || nextHeight !== height || nextDpr !== dpr) {
        width = nextWidth;
        height = nextHeight;
        dpr = nextDpr;
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        canvas.style.width = width + 'px';
        canvas.style.height = height + 'px';
        if (overlayCanvas) {
          overlayCanvas.width = canvas.width;
          overlayCanvas.height = canvas.height;
          overlayCanvas.style.width = width + 'px';
          overlayCanvas.style.height = height + 'px';
        }
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (octx) octx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function cssVar(name, fallback) {
      const value = styles.getPropertyValue(name);
      return value && value.trim() ? value.trim() : fallback;
    }

    function layout() {
      ensureSize();
      blocks = [];
      groups = [];
      grid = new Map();
      if (!state.root) return;

      const rootChildren = sortChildren(state.getChildren(state.root));
      if (rootChildren.length === 0) return;

      const pseudo = {
        value: rootChildren.reduce((sum, node) => sum + Number(node.size), 0),
        children: rootChildren.map(function (node) {
          return { node: node, value: Number(node.size), children: null };
        }),
        depth: 0,
      };

      if (state.levels >= 2) {
        for (const layoutNode of pseudo.children) {
          const kids = sortChildren(state.getChildren(layoutNode.node));
          if (kids.length > 0) {
            layoutNode.children = kids.map(function (node) {
              return { node: node, value: Number(node.size), children: null };
            });
          }
        }
      }

      squarify(pseudo, 0, 0, width, height, 0);
      collect(pseudo);
      buildGrid();
    }

    function squarify(node, x, y, w, h, depth) {
      node.x = x;
      node.y = y;
      node.w = w;
      node.h = h;
      node.depth = depth;
      if (!node.children || node.children.length === 0) return;

      const padding = depth === 0 ? 4 : depth === 1 ? 22 : depth === 2 ? 18 : 4;
      const gap = 3;
      const innerX = x + gap;
      const innerY = y + padding;
      const innerW = w - gap * 2;
      const innerH = h - padding - gap;
      if (innerW <= 4 || innerH <= 4) return;

      layoutRow(node.children, innerX, innerY, innerW, innerH, node.value, depth);
    }

    function layoutRow(children, x, y, w, h, totalValue, parentDepth) {
      if (!(totalValue > 0)) return;
      const totalArea = w * h;
      let cx = x;
      let cy = y;
      let remainW = w;
      let remainH = h;
      let remainValue = totalValue;
      const items = children.slice();

      while (items.length > 0) {
        const isWide = remainW >= remainH;
        const side = isWide ? remainH : remainW;
        if (side <= 0) break;

        const remainArea = (remainValue / totalValue) * totalArea;
        const row = [];
        let rowValue = 0;
        let worst = Infinity;

        for (let i = 0; i < items.length; i += 1) {
          const item = items[i];
          const testValue = rowValue + item.value;
          const testWorst = worstAspect(
            row.concat(item),
            testValue,
            side,
            remainArea,
            remainValue
          );
          if (testWorst > worst && row.length > 0) break;
          row.push(item);
          rowValue = testValue;
          worst = testWorst;
        }

        items.splice(0, row.length);
        if (!(rowValue > 0) || !(remainValue > 0)) break;

        const rowThickness = (rowValue / remainValue) * (isWide ? remainW : remainH);
        let offset = 0;
        const itemGap = 2;
        for (const item of row) {
          const fraction = item.value / rowValue;
          const itemLength = fraction * side;
          if (isWide) {
            squarify(
              item,
              cx,
              cy + offset + itemGap,
              rowThickness - itemGap,
              itemLength - itemGap,
              parentDepth + 1
            );
          } else {
            squarify(
              item,
              cx + offset + itemGap,
              cy,
              itemLength - itemGap,
              rowThickness - itemGap,
              parentDepth + 1
            );
          }
          offset += itemLength;
        }

        if (isWide) {
          cx += rowThickness;
          remainW -= rowThickness;
        } else {
          cy += rowThickness;
          remainH -= rowThickness;
        }
        remainValue -= rowValue;
      }
    }

    function worstAspect(row, rowSum, side, remainArea, remainValue) {
      if (row.length === 0 || rowSum === 0 || side === 0 || remainValue === 0) {
        return Infinity;
      }
      const thickness = (rowSum / remainValue) * (remainArea / side);
      if (thickness === 0) return Infinity;
      let worst = 0;
      for (const item of row) {
        const itemLength = (item.value / rowSum) * side;
        if (itemLength === 0) continue;
        const ratio = Math.max(itemLength / thickness, thickness / itemLength);
        if (ratio > worst) worst = ratio;
      }
      return worst;
    }

    function collect(layoutNode) {
      if (!layoutNode.children || layoutNode.children.length === 0) {
        if (layoutNode.node) {
          blocks.push({
            node: layoutNode.node,
            x: layoutNode.x,
            y: layoutNode.y,
            w: layoutNode.w,
            h: layoutNode.h,
            depth: layoutNode.depth,
            category: layoutNode.node.category,
          });
        }
        return;
      }

      if (layoutNode.depth >= 1 && layoutNode.node) {
        groups.push({
          node: layoutNode.node,
          x: layoutNode.x,
          y: layoutNode.y,
          w: layoutNode.w,
          h: layoutNode.h,
          label: {
            x: layoutNode.x + 3,
            y: layoutNode.y + 2,
            w: Math.max(0, layoutNode.w - 6),
            h: 20,
          },
        });
      }

      for (const child of layoutNode.children) collect(child);
    }

    function buildGrid() {
      grid = new Map();
      gridCell = 48;
      for (let index = 0; index < blocks.length; index += 1) {
        const block = blocks[index];
        if (block.w <= 0 || block.h <= 0) continue;
        const x0 = Math.floor(block.x / gridCell);
        const x1 = Math.floor((block.x + block.w) / gridCell);
        const y0 = Math.floor(block.y / gridCell);
        const y1 = Math.floor((block.y + block.h) / gridCell);
        for (let gx = x0; gx <= x1; gx += 1) {
          for (let gy = y0; gy <= y1; gy += 1) {
            const key = gx + ':' + gy;
            let bucket = grid.get(key);
            if (!bucket) {
              bucket = [];
              grid.set(key, bucket);
            }
            bucket.push(index);
          }
        }
      }
    }

    function pointInRect(px, py, x, y, w, h) {
      return px >= x && px <= x + w && py >= y && py <= y + h;
    }

    function findNodeAt(px, py) {
      for (let i = groups.length - 1; i >= 0; i -= 1) {
        const label = groups[i].label;
        if (pointInRect(px, py, label.x, label.y, label.w, label.h)) {
          return { node: groups[i].node, kind: 'group' };
        }
      }
      const gx = Math.floor(px / gridCell);
      const gy = Math.floor(py / gridCell);
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          const bucket = grid.get(gx + dx + ':' + gy + dy);
          if (!bucket) continue;
          for (let i = bucket.length - 1; i >= 0; i -= 1) {
            const block = blocks[bucket[i]];
            if (pointInRect(px, py, block.x, block.y, block.w, block.h)) {
              return { node: block.node, kind: 'block' };
            }
          }
        }
      }
      return null;
    }

    function roundRect(x, y, w, h, radius) {
      const r = Math.max(0, Math.min(radius, w / 2, h / 2));
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.lineTo(x + w - r, y);
      ctx.quadraticCurveTo(x + w, y, x + w, y + r);
      ctx.lineTo(x + w, y + h - r);
      ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
      ctx.lineTo(x + r, y + h);
      ctx.quadraticCurveTo(x, y + h, x, y + h - r);
      ctx.lineTo(x, y + r);
      ctx.quadraticCurveTo(x, y, x + r, y);
      ctx.closePath();
    }

    function truncateText(text, maxWidth, font) {
      ctx.font = font;
      if (ctx.measureText(text).width <= maxWidth) return text;
      let low = 0;
      let high = text.length;
      const ellipsis = '…';
      while (low < high) {
        const mid = Math.ceil((low + high) / 2);
        const candidate = text.slice(0, mid) + ellipsis;
        if (ctx.measureText(candidate).width <= maxWidth) low = mid;
        else high = mid - 1;
      }
      return text.slice(0, low) + ellipsis;
    }

    function draw() {
      ensureSize();
      ctx.clearRect(0, 0, width, height);
      if (blocks.length === 0 && groups.length === 0) return;

      const groupBg = cssVar('--app-group-bg', 'rgba(255,255,255,0.04)');
      const groupBorder = cssVar('--app-group-border', 'rgba(255,255,255,0.16)');
      const groupLabel = cssVar('--app-fg', '#dfe1e5');
      const blockBorder = cssVar('--app-block-border', 'rgba(255,255,255,0.2)');

      for (const group of groups) {
        if (group.w < 4 || group.h < 4) continue;
        ctx.fillStyle = groupBg;
        roundRect(group.x + 1, group.y + 1, group.w - 2, group.h - 2, 4);
        ctx.fill();
        ctx.strokeStyle = groupBorder;
        ctx.lineWidth = 1;
        ctx.stroke();

        if (group.w < 36 || group.h < 22) continue;
        const font = '600 11px "Segoe UI", "Microsoft YaHei UI", system-ui, sans-serif';
        const label = truncateText(
          group.node.name + '  ' + formatBytes(group.node.size),
          group.label.w - 8,
          font
        );
        ctx.save();
        ctx.beginPath();
        ctx.rect(group.label.x, group.label.y, group.label.w, group.label.h);
        ctx.clip();
        ctx.fillStyle = groupLabel;
        ctx.font = font;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, group.label.x + 4, group.label.y + group.label.h / 2);
        ctx.restore();
      }

      for (const block of blocks) {
        if (block.w < 1 || block.h < 1) continue;
        const color = COLORS[block.category] || COLORS.code;
        ctx.fillStyle = color;
        ctx.fillRect(block.x, block.y, block.w, block.h);

        ctx.strokeStyle = blockBorder;
        ctx.lineWidth = 1;
        ctx.strokeRect(block.x + 0.5, block.y + 0.5, block.w - 1, block.h - 1);

        const area = block.w * block.h;
        if (area < 700 || block.w < 38 || block.h < 16) continue;
        const fontSize = Math.max(9, Math.min(14, block.w / 8, block.h / 2.4));
        const font =
          '600 ' + fontSize.toFixed(0) + 'px "Segoe UI", "Microsoft YaHei UI", system-ui, sans-serif';
        const label = truncateText(block.node.name, block.w - 8, font);

        ctx.save();
        ctx.beginPath();
        ctx.rect(block.x, block.y, block.w, block.h);
        ctx.clip();
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.font = font;
        ctx.fillStyle = 'rgba(255,255,255,0.97)';
        ctx.shadowColor = 'rgba(0,0,0,0.55)';
        ctx.shadowBlur = 2;
        ctx.shadowOffsetY = 1;
        ctx.fillText(label, block.x + 4, block.y + 3);

        if (block.h >= 34 && block.w >= 52) {
          const sizeFont = '500 10px "Segoe UI", system-ui, sans-serif';
          const sizeLabel = truncateText(
            formatBytes(block.node.size),
            block.w - 8,
            sizeFont
          );
          ctx.font = sizeFont;
          ctx.fillStyle = 'rgba(255,255,255,0.78)';
          ctx.fillText(sizeLabel, block.x + 4, block.y + 3 + fontSize + 2);
        }
        ctx.restore();
      }

      drawOverlay();
    }

    function drawOverlay() {
      if (!octx) return;
      octx.clearRect(0, 0, width, height);
      const highlightId = state.highlightId;
      for (const block of blocks) {
        const highlighted = highlightId && block.node.id === highlightId;
        const hovered = hoveredId && block.node.id === hoveredId;
        if (!highlighted && !hovered) continue;
        octx.strokeStyle = highlighted ? '#ffffff' : 'rgba(255,255,255,0.92)';
        octx.lineWidth = highlighted ? 2.5 : 1.5;
        if (highlighted) {
          octx.shadowColor = 'rgba(255,255,255,0.9)';
          octx.shadowBlur = 10;
        }
        octx.strokeRect(block.x + 1, block.y + 1, block.w - 2, block.h - 2);
        octx.shadowBlur = 0;
      }
      for (const group of groups) {
        const highlighted = highlightId && group.node.id === highlightId;
        const hovered = hoveredId && group.node.id === hoveredId;
        if (!highlighted && !hovered) continue;
        octx.strokeStyle = 'rgba(255,255,255,0.95)';
        octx.lineWidth = 2;
        octx.strokeRect(group.x + 1, group.y + 1, group.w - 2, group.h - 2);
      }
    }

    function render() {
      const started = global.performance ? global.performance.now() : Date.now();
      layout();
      draw();
      if (opts.onRender) {
        const elapsed = (global.performance ? global.performance.now() : Date.now()) - started;
        opts.onRender(Math.round(elapsed));
      }
    }

    function setData(data) {
      state = Object.assign({}, state, data);
      hoveredId = null;
      render();
    }

    function setHighlight(nodeId) {
      state.highlightId = nodeId || null;
      drawOverlay();
    }

    function resize() {
      render();
    }

    function handleMove(event) {
      const rect = host.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const hit = findNodeAt(x, y);
      const nextId = hit ? hit.node.id : null;
      if (nextId !== hoveredId) {
        hoveredId = nextId;
        drawOverlay();
      }
      canvas.style.cursor = hit ? 'pointer' : 'default';
      if (opts.onHover) opts.onHover(hit ? hit.node : null, event, hit ? hit.kind : null);
    }

    function handleLeave() {
      if (hoveredId !== null) {
        hoveredId = null;
        drawOverlay();
      }
      canvas.style.cursor = 'default';
      if (opts.onHover) opts.onHover(null);
    }

    function handleClick(event) {
      const rect = host.getBoundingClientRect();
      const hit = findNodeAt(event.clientX - rect.left, event.clientY - rect.top);
      if (hit && opts.onNodeClick) opts.onNodeClick(hit.node, hit.kind);
    }

    canvas.addEventListener('mousemove', handleMove);
    canvas.addEventListener('mouseleave', handleLeave);
    canvas.addEventListener('click', handleClick);

    return {
      setData: setData,
      setHighlight: setHighlight,
      resize: resize,
      render: render,
      getState: function () {
        return state;
      },
    };
  }

  global.MapViewTreemap = {
    createRenderer: createRenderer,
    COLORS: COLORS,
    CATEGORY_LABELS: CATEGORY_LABELS,
    formatBytes: formatBytes,
    formatAddress: formatAddress,
  };
})(window);
