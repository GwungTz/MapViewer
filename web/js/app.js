/*
 * Map View Local - report application.
 *
 * Loads the manifest, drives the data worker, maintains a lazy node cache and
 * navigation stack, and feeds the Canvas treemap.  All text coming from the
 * MAP file is inserted with textContent, never innerHTML.
 */
(function () {
  'use strict';

  const Treemap = window.MapViewTreemap;
  const formatBytes = Treemap.formatBytes;
  const formatAddress = Treemap.formatAddress;
  const CATEGORY_LABELS = Treemap.CATEGORY_LABELS;
  const PART_ORDER = ['code', 'rodata', 'rwdata', 'zidata'];

  const el = {
    back: document.getElementById('btn-back'),
    title: document.getElementById('report-title'),
    breadcrumb: document.getElementById('breadcrumb'),
    romFill: document.getElementById('rom-fill'),
    romText: document.getElementById('rom-text'),
    ramFill: document.getElementById('ram-fill'),
    ramText: document.getElementById('ram-text'),
    configButton: document.getElementById('btn-config'),
    viewRegion: document.getElementById('view-region'),
    viewModule: document.getElementById('view-module'),
    searchInput: document.getElementById('search-input'),
    searchClear: document.getElementById('search-clear'),
    legendCode: document.getElementById('legend-code'),
    legendRodata: document.getElementById('legend-rodata'),
    legendRwdata: document.getElementById('legend-rwdata'),
    legendZidata: document.getElementById('legend-zidata'),
    legendPad: document.getElementById('legend-pad'),
    legendMeta: document.getElementById('report-meta'),
    host: document.getElementById('treemap-host'),
    canvas: document.getElementById('treemap-canvas'),
    overlay: document.getElementById('treemap-overlay'),
    empty: document.getElementById('empty-state'),
    loading: document.getElementById('loading'),
    loadingText: document.getElementById('loading-text'),
    errorBanner: document.getElementById('error-banner'),
    errorTitle: document.getElementById('error-title'),
    errorMessage: document.getElementById('error-message'),
    errorClose: document.getElementById('error-close'),
    searchPanel: document.getElementById('search-panel'),
    searchSummary: document.getElementById('search-summary'),
    searchResults: document.getElementById('search-results'),
    searchClose: document.getElementById('search-close'),
    tooltip: document.getElementById('tooltip'),
    toast: document.getElementById('toast'),
    configDialog: document.getElementById('config-dialog'),
    configRom: document.getElementById('config-rom'),
    configRam: document.getElementById('config-ram'),
    configReported: document.getElementById('config-reported'),
    configSave: document.getElementById('config-save'),
  };

  const state = {
    manifest: null,
    baseUrl: null,
    view: 'region',
    path: [],
    nodes: new Map(),
    children: new Map(),
    loadedParents: new Set(),
    fetchedChunks: new Set(),
    rootChunks: new Set(),
    highlightId: null,
    memory: { romKb: 0, ramKb: 0 },
    searchSeq: 0,
    searchDebounce: null,
    pending: new Map(),
    chunkPromises: new Map(),
    toastTimer: null,
  };

  let renderer = null;
  let worker = null;
  let nextRequestId = 1;
  let workerResolve = null;
  let workerReject = null;
  const debugInfo = {
    renderSeq: 0,
    lastRenderMs: 0,
    lastSearch: null,
    searchStartedAt: 0,
    lastError: null,
  };
  window.__MAPVIEW_READY__ = false;
  const workerReady = new Promise(function (resolve, reject) {
    workerResolve = resolve;
    workerReject = reject;
  });

  function appError(code, message, chunkId) {
    const error = new Error(message + (chunkId ? ' [' + chunkId + ']' : ''));
    error.code = code;
    error.chunkId = chunkId || null;
    return error;
  }

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------

  async function boot() {
    setupRenderer();
    bindUi();
    showEmpty('Loading report…');
    try {
      const manifest = await loadManifest();
      state.manifest = manifest;
      state.memory = loadMemoryOverrides();
      applyManifestUi();
      startWorker(manifest);
      await workerReady;
      await ensureViewRoot('region');
      render();
    } catch (error) {
      showFatal(error);
    }
  }

  async function loadManifest() {
    const manifestUrl = new URL('manifest.json', document.baseURI);
    let response;
    try {
      response = await fetch(manifestUrl.href, { cache: 'no-cache' });
    } catch (error) {
      throw appError(
        'MANIFEST_INVALID',
        'Cannot load manifest.json (' + error.message + '). Is the local report server running?'
      );
    }
    if (!response.ok) {
      throw appError(
        'MANIFEST_INVALID',
        'Cannot load manifest.json: HTTP ' + response.status + ' ' + response.statusText
      );
    }
    let manifest;
    try {
      manifest = await response.json();
    } catch (error) {
      throw appError('MANIFEST_INVALID', 'manifest.json is not valid JSON: ' + error.message);
    }
    if (!manifest || manifest.schema_version !== 1) {
      throw appError(
        'MANIFEST_INVALID',
        'Unsupported report schema_version ' +
          (manifest && manifest.schema_version) +
          '; this viewer expects schema_version 1.'
      );
    }
    if (!manifest.views || !manifest.views.region || !manifest.views.module) {
      throw appError('MANIFEST_INVALID', 'manifest.json has no region/module views.');
    }
    state.baseUrl = new URL('./', manifestUrl).href;
    return manifest;
  }

  function startWorker(manifest) {
    if (typeof Worker !== 'function') {
      workerReject(appError('WORKER_UNAVAILABLE', 'This browser does not support Web Workers.'));
      return;
    }
    try {
      worker = new Worker(new URL('assets/data-worker.js', document.baseURI), {
        name: 'mapview-data',
      });
    } catch (error) {
      workerReject(appError('WORKER_UNAVAILABLE', 'Cannot start the data worker: ' + error.message));
      return;
    }
    worker.onmessage = handleWorkerMessage;
    worker.onerror = function (event) {
      const message = event && event.message ? event.message : 'Data worker failed';
      const error = appError('WORKER_ERROR', message);
      if (workerReject) {
        const reject = workerReject;
        workerReject = null;
        workerResolve = null;
        reject(error);
      }
      for (const entry of state.pending.values()) {
        entry.reject(error);
      }
      state.pending.clear();
      refreshLoading();
      showError(error);
    };
    worker.postMessage({ type: 'INIT', baseUrl: state.baseUrl, manifest: manifest });
  }

  // ---------------------------------------------------------------------
  // Worker I/O
  // ---------------------------------------------------------------------

  function handleWorkerMessage(event) {
    const message = event.data || {};
    switch (message.type) {
      case 'READY': {
        if (workerResolve) {
          const resolve = workerResolve;
          workerResolve = null;
          workerReject = null;
          resolve();
        }
        break;
      }
      case 'CHUNK_READY': {
        const entry = state.pending.get(message.requestId);
        if (!entry) return;
        state.pending.delete(message.requestId);
        entry.resolve(message.records || []);
        refreshLoading();
        break;
      }
      case 'CHUNK_ERROR': {
        const entry = state.pending.get(message.requestId);
        if (!entry) return;
        state.pending.delete(message.requestId);
        entry.reject(appError(message.code || 'CHUNK_ERROR', message.message, message.chunkId));
        refreshLoading();
        break;
      }
      case 'LOAD_PROGRESS': {
        const entry = state.pending.get(message.requestId);
        if (!entry) return;
        const percent =
          message.total > 0 ? Math.floor((message.loaded / message.total) * 100) : null;
        el.loadingText.textContent =
          entry.label + (percent !== null ? '… ' + percent + '%' : '…');
        break;
      }
      case 'SEARCH_RESULTS': {
        if (message.requestId !== state.searchSeq) return;
        showSearchResults(message);
        break;
      }
      case 'SEARCH_ERROR': {
        if (message.requestId !== state.searchSeq) return;
        showSearchError(message);
        break;
      }
      case 'WORKER_ERROR': {
        showError(appError(message.code || 'WORKER_ERROR', message.message || 'Data worker error'));
        break;
      }
      default:
        break;
    }
  }

  function request(message, label) {
    return new Promise(function (resolve, reject) {
      if (!worker) {
        reject(appError('WORKER_NOT_READY', 'The data worker is not running.'));
        return;
      }
      const requestId = nextRequestId;
      nextRequestId += 1;
      state.pending.set(requestId, { label: label, resolve: resolve, reject: reject });
      setLoading(label + '…');
      worker.postMessage(Object.assign({}, message, { requestId: requestId }));
    });
  }

  function refreshLoading() {
    if (state.pending.size === 0) {
      el.loading.hidden = true;
      return;
    }
    const first = state.pending.values().next().value;
    el.loading.hidden = false;
    el.loadingText.textContent = first.label + '…';
  }

  function setLoading(text) {
    el.loading.hidden = false;
    el.loadingText.textContent = text;
  }

  // ---------------------------------------------------------------------
  // Data model
  // ---------------------------------------------------------------------

  function ingest(records) {
    for (const record of records) {
      if (!record || typeof record.id !== 'string') continue;
      if (state.nodes.has(record.id)) continue;
      state.nodes.set(record.id, record);
      if (record.parent_id) {
        let list = state.children.get(record.parent_id);
        if (!list) {
          list = [];
          state.children.set(record.parent_id, list);
        }
        list.push(record);
        state.loadedParents.add(record.parent_id);
      }
    }
  }

  function moduleParts(node) {
    if (node._parts) return node._parts;
    const parts = [];
    const source = node.parts || {};
    for (const category of PART_ORDER) {
      const size = Number(source[category] || 0);
      if (size <= 0) continue;
      const part = {
        id: node.id + ':part:' + category,
        parent_id: node.id,
        view: 'module',
        kind: 'part',
        name: CATEGORY_LABELS[category] || category,
        size: size,
        category: category,
        address: null,
        object_file: node.object_file,
        child_count: 0,
        children_chunk: node.children_chunk || null,
        category_filter: category,
        synthetic: true,
      };
      parts.push(part);
      state.nodes.set(part.id, part);
    }
    node._parts = parts;
    return parts;
  }

  function getChildren(node) {
    if (!node) return [];
    if (node.kind === 'module') return moduleParts(node);
    if (node.kind === 'part') {
      const raw = state.children.get(node.parent_id) || [];
      return raw.filter(function (child) {
        return child.kind === 'section' && child.category === node.category_filter;
      });
    }
    return state.children.get(node.id) || [];
  }

  async function loadChunk(chunkId) {
    if (!chunkId) {
      throw appError('CHUNK_FETCH_FAILED', 'Node has no child chunk.');
    }
    if (state.fetchedChunks.has(chunkId)) return;
    if (state.chunkPromises.has(chunkId)) {
      await state.chunkPromises.get(chunkId);
      return;
    }
    const promise = request({ type: 'LOAD_CHUNK', chunkId: chunkId }, 'Loading ' + chunkId)
      .then(function (records) {
        ingest(records);
        state.fetchedChunks.add(chunkId);
      })
      .finally(function () {
        state.chunkPromises.delete(chunkId);
        refreshLoading();
      });
    state.chunkPromises.set(chunkId, promise);
    await promise;
  }

  async function ensureChildren(node) {
    if (node.kind === 'module') {
      return moduleParts(node).length > 0;
    }
    if (node.kind === 'part') {
      if (!node.children_chunk) return false;
      if (!state.loadedParents.has(node.parent_id)) {
        await loadChunk(node.children_chunk);
      }
      return getChildren(node).length > 0;
    }
    if (state.loadedParents.has(node.id)) {
      return getChildren(node).length > 0;
    }
    if (!node.children_chunk) return false;
    await loadChunk(node.children_chunk);
    return getChildren(node).length > 0;
  }

  async function ensureViewRoot(view) {
    if (state.rootChunks.has(view)) return;
    const entry = state.manifest.views[view];
    const records = await request(
      {
        type: 'LOAD_ROOT',
        view: view,
        path: entry.root_chunk,
        sha256: entry.root_sha256,
      },
      'Loading ' + (entry.label || view)
    );
    ingest(records);
    state.rootChunks.add(view);
  }

  function viewRootId() {
    return state.manifest.views[state.view].root_id;
  }

  function currentRoot() {
    if (state.path.length === 0) return state.nodes.get(viewRootId()) || null;
    return state.nodes.get(state.path[state.path.length - 1]) || null;
  }

  // ---------------------------------------------------------------------
  // Rendering / navigation
  // ---------------------------------------------------------------------

  function setupRenderer() {
    renderer = Treemap.createRenderer(el.canvas, el.host, {
      overlayCanvas: el.overlay,
      onRender: function (milliseconds) {
        debugInfo.renderSeq += 1;
        debugInfo.lastRenderMs = milliseconds;
      },
      onHover: function (node, event) {
        if (node && event) showTooltip(node, event);
        else hideTooltip();
      },
      onNodeClick: function (node) {
        drill(node);
      },
    });
    if (typeof ResizeObserver === 'function') {
      let resizePending = false;
      const observer = new ResizeObserver(function () {
        if (resizePending) return;
        resizePending = true;
        const run = function () {
          resizePending = false;
          hideTooltip();
          if (renderer) renderer.resize();
        };
        if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
        else setTimeout(run, 16);
      });
      observer.observe(el.host);
    } else {
      window.addEventListener('resize', function () {
        hideTooltip();
        if (renderer) renderer.resize();
      });
    }
  }

  function render() {
    const root = currentRoot();
    if (!root) {
      showEmpty('No data loaded.');
      return;
    }
    const levels = state.path.length === 0 ? 2 : 1;
    renderer.setData({
      root: root,
      getChildren: getChildren,
      levels: levels,
      highlightId: state.highlightId,
    });
    window.__MAPVIEW_READY__ = true;
    updateBreadcrumb(root);
    el.back.hidden = state.path.length === 0;
    const children = getChildren(root);
    if (children.length === 0) {
      showEmpty(
        state.path.length === 0
          ? 'This view contains no data.'
          : 'No detail data below this node.'
      );
    } else {
      el.empty.hidden = true;
    }
    hideTooltip();
  }

  function showEmpty(text) {
    el.empty.hidden = false;
    el.empty.textContent = text;
  }

  async function drill(node) {
    if (!node) return;
    const root = currentRoot();
    if (root && node.id === root.id) return;
    try {
      const hasChildren = await ensureChildren(node);
      if (!hasChildren) {
        toast('No detail data below this node.');
        return;
      }
      state.path.push(node.id);
      state.highlightId = null;
      render();
    } catch (error) {
      showError(error);
    } finally {
      refreshLoading();
    }
  }

  function goBack() {
    if (state.path.length === 0) return;
    state.path.pop();
    state.highlightId = null;
    render();
  }

  function jumpPath(keep) {
    if (keep < 0 || keep > state.path.length) return;
    if (keep === state.path.length) return;
    state.path = state.path.slice(0, keep);
    state.highlightId = null;
    render();
  }

  async function switchView(view) {
    if (!state.manifest) return;
    if (state.view === view) return;
    state.view = view;
    state.path = [];
    state.highlightId = null;
    updateViewButtons();
    hideTooltip();
    try {
      await ensureViewRoot(view);
      render();
    } catch (error) {
      showError(error);
    } finally {
      refreshLoading();
    }
  }

  function updateViewButtons() {
    const pairs = [
      [el.viewRegion, 'region'],
      [el.viewModule, 'module'],
    ];
    for (const [button, view] of pairs) {
      const active = state.view === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
  }

  function updateBreadcrumb(root) {
    el.breadcrumb.textContent = '';
    const label = state.manifest.views[state.view].label || 'View';
    const viewButton = document.createElement('button');
    viewButton.type = 'button';
    viewButton.textContent = label;
    viewButton.title = 'Back to the ' + label;
    viewButton.addEventListener('click', function () {
      jumpPath(0);
    });
    el.breadcrumb.appendChild(viewButton);

    for (let index = 0; index < state.path.length; index += 1) {
      const separator = document.createElement('span');
      separator.className = 'crumb-sep';
      separator.textContent = '/';
      el.breadcrumb.appendChild(separator);

      const node = state.nodes.get(state.path[index]);
      const name = node ? node.name : state.path[index];
      if (index === state.path.length - 1) {
        const current = document.createElement('span');
        current.className = 'crumb-current';
        current.textContent = name;
        current.title = name + (node ? ' · ' + formatBytes(node.size) : '');
        el.breadcrumb.appendChild(current);
      } else {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = name;
        button.title = name;
        const keep = index + 1;
        button.addEventListener('click', function () {
          jumpPath(keep);
        });
        el.breadcrumb.appendChild(button);
      }
    }

    if (state.path.length === 0) {
      const separator = document.createElement('span');
      separator.className = 'crumb-sep';
      separator.textContent = '·';
      el.breadcrumb.appendChild(separator);
      const current = document.createElement('span');
      current.className = 'crumb-current';
      current.textContent = formatBytes(root.size);
      current.title = 'Total size in this view';
      el.breadcrumb.appendChild(current);
    }
  }

  // ---------------------------------------------------------------------
  // Tooltip
  // ---------------------------------------------------------------------

  function tooltipRow(label, value) {
    const row = document.createElement('div');
    row.className = 'tooltip-row';
    const key = document.createElement('span');
    key.textContent = label;
    const val = document.createElement('span');
    val.className = 'value';
    val.textContent = value;
    row.appendChild(key);
    row.appendChild(val);
    return row;
  }

  function showTooltip(node, event) {
    el.tooltip.textContent = '';
    const title = document.createElement('div');
    title.className = 'tooltip-title';
    title.textContent = node.name;
    el.tooltip.appendChild(title);

    el.tooltip.appendChild(tooltipRow('Size', formatBytes(node.size)));
    if (node.category) {
      el.tooltip.appendChild(
        tooltipRow('Category', CATEGORY_LABELS[node.category] || node.category)
      );
    }
    if (node.kind === 'module' && node.parts) {
      const parts = PART_ORDER.filter(function (key) {
        return Number(node.parts[key] || 0) > 0;
      }).map(function (key) {
        return (CATEGORY_LABELS[key] || key) + ' ' + formatBytes(node.parts[key]);
      });
      if (parts.length) el.tooltip.appendChild(tooltipRow('Parts', parts.join(' · ')));
    }
    if (node.object_file && node.object_file !== node.name) {
      el.tooltip.appendChild(tooltipRow('Object', node.object_file));
    }
    if (node.address !== null && node.address !== undefined) {
      const address = formatAddress(node.address);
      if (address) el.tooltip.appendChild(tooltipRow('Address', address));
    }
    if (node.kind === 'section' && node.element_id) {
      el.tooltip.appendChild(tooltipRow('Element', node.element_id));
    }
    if (node.child_count > 0) {
      const loaded = getChildren(node).length;
      el.tooltip.appendChild(
        tooltipRow('Children', loaded + ' loaded / ' + node.child_count + ' total')
      );
      const hint = document.createElement('div');
      hint.className = 'tooltip-hint';
      hint.textContent = 'Click to drill down';
      el.tooltip.appendChild(hint);
    }

    el.tooltip.hidden = false;
    positionTooltip(event);
  }

  function positionTooltip(event) {
    const margin = 8;
    const rect = el.tooltip.getBoundingClientRect();
    let left = event.clientX + 14;
    let top = event.clientY + 14;
    if (left + rect.width > window.innerWidth - margin) {
      left = Math.max(margin, event.clientX - rect.width - 14);
    }
    if (top + rect.height > window.innerHeight - margin) {
      top = Math.max(margin, event.clientY - rect.height - 14);
    }
    el.tooltip.style.left = left + 'px';
    el.tooltip.style.top = top + 'px';
  }

  function hideTooltip() {
    el.tooltip.hidden = true;
  }

  // ---------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------

  function onSearchInput() {
    const query = el.searchInput.value;
    el.searchClear.hidden = query.length === 0;
    clearTimeout(state.searchDebounce);
    state.searchDebounce = setTimeout(function () {
      performSearch(query);
    }, 260);
  }

  function performSearch(query) {
    const trimmed = String(query || '').trim();
    state.searchSeq += 1;
    if (!trimmed) {
      closeSearch();
      return;
    }
    debugInfo.searchStartedAt = performance.now();
    openSearchPanel('Searching…');
    if (!worker) {
      showSearchError({ message: 'The data worker is not running.' });
      return;
    }
    worker.postMessage({ type: 'SEARCH', requestId: state.searchSeq, query: trimmed });
  }

  function openSearchPanel(summary) {
    el.searchPanel.hidden = false;
    el.searchSummary.textContent = summary;
  }

  function closeSearch() {
    el.searchPanel.hidden = true;
    el.searchResults.textContent = '';
    el.searchInput.value = '';
    el.searchClear.hidden = true;
    state.searchSeq += 1;
    state.highlightId = null;
    if (renderer) renderer.setHighlight(null);
    hideTooltip();
  }

  function showSearchResults(message) {
    const total = Number(message.total || 0);
    const results = Array.isArray(message.results) ? message.results : [];
    debugInfo.lastSearch = {
      query: String(message.query || ''),
      total: total,
      shown: results.length,
      truncated: !!message.truncated,
      durationMs: Math.round(performance.now() - (debugInfo.searchStartedAt || performance.now())),
    };
    el.searchSummary.textContent =
      total +
      ' result' +
      (total === 1 ? '' : 's') +
      (message.truncated ? ' (showing first ' + results.length + ')' : '') +
      ' for “' +
      message.query +
      '”';
    el.searchResults.textContent = '';

    if (results.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'search-empty';
      empty.textContent = 'No matching symbols, objects or modules.';
      el.searchResults.appendChild(empty);
      return;
    }

    for (const record of results) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'search-result';

      const title = document.createElement('span');
      title.className = 'search-result-title';
      const badge = document.createElement('span');
      badge.className =
        'badge ' + (record.category && CATEGORY_LABELS[record.category] ? record.category : '');
      badge.textContent = record.kind === 'module' ? 'module' : 'section';
      const name = document.createElement('span');
      name.textContent = record.name;
      title.appendChild(badge);
      title.appendChild(name);
      button.appendChild(title);

      const meta = document.createElement('span');
      meta.className = 'search-result-meta';
      const chunks = [];
      chunks.push(record.view === 'module' ? 'Module View' : 'Region View');
      if (record.object_file && record.object_file !== record.name) {
        chunks.push(record.object_file);
      }
      const address = formatAddress(record.address);
      if (address) chunks.push(address);
      chunks.push(formatBytes(record.size));
      meta.textContent = chunks.join(' · ');
      button.appendChild(meta);

      button.addEventListener('click', function () {
        navigateToSearchResult(record);
      });
      item.appendChild(button);
      el.searchResults.appendChild(item);
    }
  }

  function showSearchError(message) {
    debugInfo.lastSearch = {
      query: el.searchInput.value,
      total: 0,
      shown: 0,
      truncated: false,
      error: true,
      durationMs: Math.round(performance.now() - (debugInfo.searchStartedAt || performance.now())),
    };
    el.searchSummary.textContent = 'Search failed';
    el.searchResults.textContent = '';
    const item = document.createElement('li');
    item.className = 'search-empty';
    item.textContent = message.message || 'Search index could not be loaded.';
    el.searchResults.appendChild(item);
    if (message.code) {
      showError(appError(message.code, message.message || 'Search failed', message.chunkId));
    }
  }

  async function navigateToSearchResult(record) {
    try {
      const view = record.view === 'module' ? 'module' : 'region';
      if (view !== state.view) {
        state.view = view;
        state.path = [];
        updateViewButtons();
      }
      state.highlightId = null;
      await ensureViewRoot(view);

      const pathIds = Array.isArray(record.path_ids) ? record.path_ids : [];
      if (pathIds.length < 2) {
        throw appError('SEARCH_TARGET_MISSING', 'Search result has no navigation path.');
      }
      for (let index = 1; index < pathIds.length - 1; index += 1) {
        const node = state.nodes.get(pathIds[index]);
        if (!node) {
          throw appError(
            'SEARCH_TARGET_MISSING',
            'Search path references a node that is not present in the report: ' + pathIds[index]
          );
        }
        await ensureChildren(node);
      }

      state.path = pathIds.slice(1, pathIds.length - 1);
      state.highlightId = record.id;
      render();
      el.searchPanel.hidden = true;
      if (state.nodes.has(record.id)) {
        const node = state.nodes.get(record.id);
        toast('Located: ' + node.name);
      }
    } catch (error) {
      showError(error);
    } finally {
      refreshLoading();
    }
  }

  // ---------------------------------------------------------------------
  // Summary, legend and memory configuration
  // ---------------------------------------------------------------------

  function applyManifestUi() {
    const manifest = state.manifest;
    const source = manifest.source || {};
    el.title.textContent = 'Map View · ' + (source.file_name || 'report');
    el.title.title =
      (source.file_name || '') +
      '\nFormat: ' +
      (source.format || '?') +
      '\nCompiler: ' +
      (source.compiler || '?') +
      '\nSHA-256: ' +
      (source.sha256 || '?');
    document.title = 'Map View · ' + (source.file_name || 'report');

    const totals = manifest.grand_totals || {};
    setText(el.legendCode, formatBytes(totals.code || 0));
    setText(el.legendRodata, formatBytes(totals.ro_data || 0));
    setText(el.legendRwdata, formatBytes(totals.rw_data || 0));
    setText(el.legendZidata, formatBytes(totals.zi_data || 0));
    const padding = manifest.views.region.padding_bytes;
    setText(el.legendPad, padding ? formatBytes(padding) : '—');

    const stats = manifest.stats || {};
    const meta = [];
    if (source.format) meta.push(source.format);
    if (source.compiler_version) meta.push(source.compiler_version);
    if (stats.sections != null) meta.push(stats.sections + ' sections');
    if (stats.objects != null) meta.push(stats.objects + ' objects');
    if (manifest.warning_count) meta.push(manifest.warning_count + ' warnings');
    setText(el.legendMeta, meta.join(' · '));
    if (manifest.warning_count && Array.isArray(manifest.warnings)) {
      el.legendMeta.title = manifest.warnings
        .slice(0, 20)
        .map(function (warning) {
          return (warning.line ? 'line ' + warning.line + ': ' : '') + warning.message;
        })
        .join('\n');
    }

    el.configReported.textContent =
      'Linker maximum: ROM ' +
      formatBytes(manifest.summary.rom.total) +
      ' · RAM ' +
      formatBytes(manifest.summary.ram.total);

    applySummary();
    updateViewButtons();
  }

  function setText(node, text) {
    if (node) node.textContent = text;
  }

  function applySummary() {
    const summary = state.manifest.summary;
    const romKb = Number(state.memory.romKb) || 0;
    const ramKb = Number(state.memory.ramKb) || 0;
    const romTotal = romKb > 0 ? romKb * 1024 : summary.rom.total;
    const ramTotal = ramKb > 0 ? ramKb * 1024 : summary.ram.total;
    setMemoryBar(el.romFill, el.romText, summary.rom.used, romTotal);
    setMemoryBar(el.ramFill, el.ramText, summary.ram.used, ramTotal);
  }

  function setMemoryBar(fill, text, used, total) {
    const percent = total > 0 ? (used / total) * 100 : 0;
    fill.style.width = Math.min(100, Math.max(0, percent)) + '%';
    if (total > 0) {
      text.textContent =
        formatBytes(used) + ' / ' + formatBytes(total) + ' (' + percent.toFixed(1) + '%)';
    } else {
      text.textContent = formatBytes(used) + ' / –';
    }
    fill.parentElement.setAttribute('aria-valuenow', String(Math.round(percent)));
  }

  function memoryStorageKey() {
    const sha = state.manifest && state.manifest.source ? state.manifest.source.sha256 : 'unknown';
    return 'mapview.memory.v1:' + sha;
  }

  function loadMemoryOverrides() {
    try {
      const raw = window.localStorage.getItem(memoryStorageKey());
      if (!raw) return { romKb: 0, ramKb: 0 };
      const parsed = JSON.parse(raw);
      return {
        romKb: Math.max(0, Math.floor(Number(parsed.romKb) || 0)),
        ramKb: Math.max(0, Math.floor(Number(parsed.ramKb) || 0)),
      };
    } catch (error) {
      return { romKb: 0, ramKb: 0 };
    }
  }

  function openConfig() {
    if (!state.manifest) return;
    el.configRom.value = String(state.memory.romKb || 0);
    el.configRam.value = String(state.memory.ramKb || 0);
    el.configReported.textContent =
      'Linker maximum: ROM ' +
      formatBytes(state.manifest.summary.rom.total) +
      ' · RAM ' +
      formatBytes(state.manifest.summary.ram.total);
    el.configDialog.showModal();
  }

  function saveConfig() {
    const romKb = Math.max(0, Math.floor(Number(el.configRom.value) || 0));
    const ramKb = Math.max(0, Math.floor(Number(el.configRam.value) || 0));
    state.memory = { romKb: romKb, ramKb: ramKb };
    try {
      if (romKb === 0 && ramKb === 0) {
        window.localStorage.removeItem(memoryStorageKey());
      } else {
        window.localStorage.setItem(
          memoryStorageKey(),
          JSON.stringify({ romKb: romKb, ramKb: ramKb })
        );
      }
    } catch (error) {
      toast('Could not save the memory override in localStorage.');
    }
    applySummary();
    el.configDialog.close();
  }

  // ---------------------------------------------------------------------
  // Errors / toast
  // ---------------------------------------------------------------------

  function showError(error) {
    const code = error && error.code ? error.code : 'REPORT_ERROR';
    const message = error && error.message ? error.message : String(error);
    debugInfo.lastError = { code: code, message: message };
    el.errorTitle.textContent = code;
    el.errorMessage.textContent = message;
    el.errorBanner.hidden = false;
    if (window.console && console.error) console.error(code, message);
  }

  function showFatal(error) {
    showError(error);
    showEmpty(
      'The report could not be opened.\n' +
        (error && error.message ? error.message : '') +
        '\n\nMake sure the Map View launcher / report server is still running and regenerate the report if the problem persists.'
    );
    el.back.hidden = true;
  }

  function toast(message) {
    el.toast.textContent = message;
    el.toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(function () {
      el.toast.hidden = true;
    }, 2200);
  }

  // ---------------------------------------------------------------------
  // UI bindings
  // ---------------------------------------------------------------------

  function bindUi() {
    el.back.addEventListener('click', goBack);
    el.viewRegion.addEventListener('click', function () {
      switchView('region');
    });
    el.viewModule.addEventListener('click', function () {
      switchView('module');
    });
    el.searchInput.addEventListener('input', onSearchInput);
    el.searchInput.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        closeSearch();
      } else if (event.key === 'Enter') {
        const first = el.searchResults.querySelector('.search-result');
        if (first) first.click();
      }
    });
    el.searchClear.addEventListener('click', closeSearch);
    el.searchClose.addEventListener('click', closeSearch);
    el.configButton.addEventListener('click', openConfig);
    el.configSave.addEventListener('click', saveConfig);
    el.errorClose.addEventListener('click', function () {
      el.errorBanner.hidden = true;
    });

    window.addEventListener('keydown', function (event) {
      const target = event.target;
      const inField =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable);
      if (event.key === 'Escape') {
        if (!el.configDialog.open && !el.searchPanel.hidden) {
          closeSearch();
        }
        return;
      }
      if (inField) return;
      if (event.key === 'Backspace' || (event.altKey && event.key === 'ArrowLeft')) {
        event.preventDefault();
        goBack();
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        el.searchInput.focus();
        el.searchInput.select();
      } else if (event.key === '/') {
        event.preventDefault();
        el.searchInput.focus();
      }
    });

    window.addEventListener('unhandledrejection', function (event) {
      if (event.reason && event.reason.code) showError(event.reason);
    });
  }

  window.__mapviewDebug = {
    getStatus: function () {
      const root = currentRoot();
      return {
        ready: !!window.__MAPVIEW_READY__,
        hasManifest: !!state.manifest,
        view: state.view,
        path: state.path.slice(),
        rootId: root ? root.id : null,
        nodeCount: state.nodes.size,
        loadedChunks: state.rootChunks.size + state.fetchedChunks.size,
        highlightId: state.highlightId,
        children: root ? getChildren(root).length : 0,
        renderSeq: debugInfo.renderSeq,
        lastRenderMs: debugInfo.lastRenderMs,
        lastSearch: debugInfo.lastSearch,
        lastError: debugInfo.lastError,
      };
    },
    drillFirstChild: function () {
      const root = currentRoot();
      if (!root) return;
      const children = getChildren(root);
      if (children.length === 0) return;
      let best = children[0];
      for (const child of children) {
        if (Number(child.size) > Number(best.size)) best = child;
      }
      drill(best);
    },
    drillNode: function (nodeId) {
      const node = state.nodes.get(nodeId);
      if (node) drill(node);
    },
    goBack: goBack,
    setView: switchView,
    search: performSearch,
    clearSearch: closeSearch,
  };

  boot();
})();
