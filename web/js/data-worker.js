/*
 * Map View Local - data worker.
 *
 * Owns network I/O, gzip decompression, incremental JSONL parsing, the shard
 * LRU and the global search index.  It never touches the DOM.
 */
'use strict';

let baseUrl = null;
let manifest = null;
let treeIndex = null;
let treeIndexPromise = null;
let searchIndex = null;
let searchIndexPromise = null;
let latestSearchId = 0;

const MAX_CACHE_BYTES = 96 * 1024 * 1024; // decompressed estimate
const cache = new Map();
let cacheBytes = 0;
const inflight = new Map();

function post(message) {
  self.postMessage(message);
}

function makeError(code, message, chunkId) {
  const error = new Error(message);
  error.code = code;
  error.chunkId = chunkId || null;
  return error;
}

function errorMessage(error) {
  if (!error) return 'Unknown worker error';
  return error.message || String(error);
}

function postError(error, requestId, chunkId, type) {
  post({
    type: type || 'CHUNK_ERROR',
    requestId: requestId,
    chunkId: chunkId || (error && error.chunkId) || null,
    code: (error && error.code) || 'WORKER_ERROR',
    message: errorMessage(error),
  });
}

function urlFor(path) {
  return new URL(path, baseUrl).href;
}

async function sha256Hex(bytes) {
  if (!self.crypto || !self.crypto.subtle) return null;
  const digest = await self.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

async function fetchBytes(path, requestId, chunkId, sha256) {
  const url = urlFor(path);
  let response;
  try {
    response = await fetch(url, { cache: 'no-store' });
  } catch (error) {
    throw makeError(
      'CHUNK_FETCH_FAILED',
      'Cannot fetch ' + path + ': ' + errorMessage(error) + '. Is the Map View launcher still running?',
      chunkId
    );
  }
  if (!response.ok) {
    throw makeError(
      'CHUNK_FETCH_FAILED',
      'Cannot fetch ' + path + ': HTTP ' + response.status + ' ' + response.statusText,
      chunkId
    );
  }

  const total = Number(response.headers.get('Content-Length')) || 0;
  let bytes;
  if (!response.body || typeof response.body.getReader !== 'function') {
    bytes = new Uint8Array(await response.arrayBuffer());
  } else {
    const reader = response.body.getReader();
    const parts = [];
    let loaded = 0;
    let lastPost = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parts.push(value);
        loaded += value.length;
        const now = Date.now();
        if (now - lastPost > 120) {
          lastPost = now;
          post({ type: 'LOAD_PROGRESS', requestId, chunkId, loaded, total });
        }
      }
    } catch (error) {
      throw makeError(
        'CHUNK_FETCH_FAILED',
        'Download interrupted for ' + path + ': ' + errorMessage(error),
        chunkId
      );
    }
    const merged = new Uint8Array(loaded);
    let offset = 0;
    for (const part of parts) {
      merged.set(part, offset);
      offset += part.length;
    }
    bytes = merged;
    if (total > 0) {
      post({ type: 'LOAD_PROGRESS', requestId, chunkId, loaded, total });
    }
  }

  if (sha256) {
    let digest = null;
    try {
      digest = await sha256Hex(bytes);
    } catch (error) {
      digest = null;
    }
    if (digest && digest.toLowerCase() !== String(sha256).toLowerCase()) {
      throw makeError(
        'CHUNK_INTEGRITY_FAILED',
        'SHA-256 mismatch for ' + path + '. The report may be corrupted; regenerate it.',
        chunkId
      );
    }
  }
  return bytes;
}

function decompressedStream(bytes, chunkId) {
  if (typeof DecompressionStream !== 'function') {
    throw makeError(
      'CHUNK_DECOMPRESS_FAILED',
      'This browser has no DecompressionStream (gzip) support. Use a current Chrome, Edge, Firefox or Safari.',
      chunkId
    );
  }
  try {
    return new Blob([bytes])
      .stream()
      .pipeThrough(new DecompressionStream('gzip'))
      .pipeThrough(new TextDecoderStream('utf-8', { fatal: true }));
  } catch (error) {
    throw makeError(
      'CHUNK_DECOMPRESS_FAILED',
      'Cannot start gzip decompression for ' + chunkId + ': ' + errorMessage(error),
      chunkId
    );
  }
}

async function decompressText(bytes, chunkId) {
  const stream = decompressedStream(bytes, chunkId);
  const reader = stream.getReader();
  let text = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      text += value;
    }
  } catch (error) {
    throw makeError(
      'CHUNK_DECOMPRESS_FAILED',
      'gzip decompression or UTF-8 decoding failed for ' + chunkId + ': ' + errorMessage(error),
      chunkId
    );
  } finally {
    try {
      reader.releaseLock();
    } catch (ignored) {
      /* noop */
    }
  }
  return text;
}

async function decompressJsonl(bytes, chunkId) {
  const stream = decompressedStream(bytes, chunkId);
  const reader = stream.getReader();
  const records = [];
  let buffer = '';
  let lineNo = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        lineNo += 1;
        if (!line.trim()) continue;
        try {
          records.push(JSON.parse(line));
        } catch (error) {
          throw makeError(
            'JSONL_PARSE_FAILED',
            'Invalid JSON in ' + chunkId + ' line ' + lineNo + ': ' + errorMessage(error),
            chunkId
          );
        }
      }
    }
    if (buffer.trim()) {
      lineNo += 1;
      try {
        records.push(JSON.parse(buffer));
      } catch (error) {
        throw makeError(
          'JSONL_PARSE_FAILED',
          'Invalid JSON in ' + chunkId + ' line ' + lineNo + ': ' + errorMessage(error),
          chunkId
        );
      }
    }
  } catch (error) {
    if (error && error.code) throw error;
    throw makeError(
      'CHUNK_DECOMPRESS_FAILED',
      'gzip decompression or UTF-8 decoding failed for ' + chunkId + ': ' + errorMessage(error),
      chunkId
    );
  } finally {
    try {
      reader.releaseLock();
    } catch (ignored) {
      /* noop */
    }
  }
  return records;
}

function cacheGet(key) {
  const entry = cache.get(key);
  if (!entry) return null;
  cache.delete(key);
  cache.set(key, entry);
  return entry.records;
}

function cacheSet(key, records) {
  const estimated = records.length * 192;
  if (cache.has(key)) {
    cacheBytes -= cache.get(key).estimated;
    cache.delete(key);
  }
  cache.set(key, { records: records, estimated: estimated });
  cacheBytes += estimated;
  while (cacheBytes > MAX_CACHE_BYTES && cache.size > 1) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey === undefined) break;
    const oldest = cache.get(oldestKey);
    cache.delete(oldestKey);
    cacheBytes -= oldest.estimated;
  }
}

async function loadJsonFile(path, sha256, requestId, chunkId) {
  const bytes = await fetchBytes(path, requestId, chunkId, sha256);
  const text = await decompressText(bytes, chunkId);
  try {
    return JSON.parse(text);
  } catch (error) {
    throw makeError(
      'JSONL_PARSE_FAILED',
      'Invalid JSON in ' + path + ': ' + errorMessage(error),
      chunkId
    );
  }
}

async function ensureTreeIndex(requestId) {
  if (treeIndex) return treeIndex;
  if (!treeIndexPromise) {
    treeIndexPromise = loadJsonFile(
      manifest.tree_index,
      manifest.tree_index_sha256,
      requestId,
      'tree-index'
    )
      .then((parsed) => {
        if (!parsed || parsed.schema_version !== manifest.schema_version || !parsed.chunks) {
          throw makeError('JSONL_PARSE_FAILED', 'Tree index schema mismatch', 'tree-index');
        }
        treeIndex = parsed;
        return parsed;
      })
      .catch((error) => {
        treeIndexPromise = null;
        throw error;
      });
  }
  return treeIndexPromise;
}

async function ensureSearchIndex(requestId) {
  if (searchIndex) return searchIndex;
  if (!searchIndexPromise) {
    searchIndexPromise = (async () => {
      const bytes = await fetchBytes(
        manifest.search_index,
        requestId,
        'search-index',
        manifest.search_index_sha256
      );
      const records = await decompressJsonl(bytes, 'search-index');
      if (manifest.search_index_records != null && records.length !== manifest.search_index_records) {
        throw makeError(
          'SEARCH_INDEX_FAILED',
          'Search index record count mismatch: expected ' +
            manifest.search_index_records +
            ', got ' +
            records.length,
          'search-index'
        );
      }
      // Pre-compute lowercase strings once so each query scans without
      // allocating 2N temporary strings.
      for (let index = 0; index < records.length; index += 1) {
        const record = records[index];
        record.nameLower = typeof record.name === 'string' ? record.name.toLowerCase() : '';
        record.objectLower =
          typeof record.object_file === 'string' ? record.object_file.toLowerCase() : '';
      }
      searchIndex = records;
      return records;
    })().catch((error) => {
      searchIndexPromise = null;
      throw error;
    });
  }
  return searchIndexPromise;
}

async function handleInit(message) {
  baseUrl = message.baseUrl;
  manifest = message.manifest;
  treeIndex = null;
  treeIndexPromise = null;
  searchIndex = null;
  searchIndexPromise = null;
  cache.clear();
  cacheBytes = 0;
  inflight.clear();
  latestSearchId = 0;
  if (!manifest || manifest.schema_version !== 1) {
    post({ type: 'WORKER_ERROR', code: 'MANIFEST_INVALID', message: 'Unsupported manifest schema' });
    return;
  }
  post({ type: 'READY' });
}

async function handleLoadRoot(message) {
  const key = 'root:' + message.view;
  const requestId = message.requestId;

  const cached = cacheGet(key);
  if (cached) {
    post({ type: 'CHUNK_READY', requestId, chunkId: key, records: cached, fromCache: true });
    return;
  }
  if (inflight.has(key)) {
    try {
      const records = await inflight.get(key);
      post({ type: 'CHUNK_READY', requestId, chunkId: key, records, fromCache: true });
    } catch (error) {
      postError(error, requestId, key);
    }
    return;
  }

  const promise = (async () => {
    const bytes = await fetchBytes(message.path, requestId, key, message.sha256);
    return decompressJsonl(bytes, key);
  })();
  inflight.set(key, promise);
  try {
    const records = await promise;
    cacheSet(key, records);
    post({ type: 'CHUNK_READY', requestId, chunkId: key, records, fromCache: false });
  } catch (error) {
    postError(error, requestId, key);
  } finally {
    inflight.delete(key);
  }
}

async function handleLoadChunk(message) {
  const key = message.chunkId;
  const requestId = message.requestId;

  const cached = cacheGet(key);
  if (cached) {
    post({ type: 'CHUNK_READY', requestId, chunkId: key, records: cached, fromCache: true });
    return;
  }
  if (inflight.has(key)) {
    try {
      const records = await inflight.get(key);
      post({ type: 'CHUNK_READY', requestId, chunkId: key, records, fromCache: true });
    } catch (error) {
      postError(error, requestId, key);
    }
    return;
  }

  const promise = (async () => {
    const index = await ensureTreeIndex(requestId);
    const entry = index.chunks[key];
    if (!entry || !entry.pages || entry.pages.length === 0) {
      throw makeError('CHUNK_FETCH_FAILED', 'Chunk ' + key + ' is not in the tree index', key);
    }
    const records = [];
    for (const page of entry.pages) {
      const bytes = await fetchBytes(page.path, requestId, key, page.sha256);
      const pageRecords = await decompressJsonl(bytes, key);
      if (pageRecords.length !== page.records) {
        throw makeError(
          'JSONL_PARSE_FAILED',
          'Chunk ' + key + ' page ' + page.path + ' has ' + pageRecords.length +
            ' records, expected ' + page.records,
          key
        );
      }
      for (const record of pageRecords) records.push(record);
    }
    if (records.length !== entry.records) {
      throw makeError(
        'JSONL_PARSE_FAILED',
        'Chunk ' + key + ' has ' + records.length + ' records, expected ' + entry.records,
        key
      );
    }
    return records;
  })();

  inflight.set(key, promise);
  try {
    const records = await promise;
    cacheSet(key, records);
    post({ type: 'CHUNK_READY', requestId, chunkId: key, records, fromCache: false });
  } catch (error) {
    postError(error, requestId, key);
  } finally {
    inflight.delete(key);
  }
}

function yieldToEventLoop() {
  // setTimeout(0) is clamped to ~4ms in many browsers; a MessageChannel yield
  // stays fast enough to let incoming SEARCH messages cancel a long scan.
  return new Promise((resolve) => {
    if (!yieldRuntime.channel) {
      setTimeout(resolve, 0);
      return;
    }
    yieldRuntime.queue.push(resolve);
    yieldRuntime.channel.port2.postMessage(0);
  });
}

const yieldRuntime = {
  channel:
    typeof MessageChannel === 'function'
      ? (() => {
          const channel = new MessageChannel();
          channel.port1.onmessage = () => {
            const resolve = yieldRuntime.queue.shift();
            if (resolve) resolve();
          };
          return channel;
        })()
      : null,
  queue: [],
};

function parseAddressQuery(query) {
  const match = /^(0x[0-9a-f]+|\d+)$/i.exec(query.trim());
  if (!match) return null;
  const text = match[1].toLowerCase();
  const value = text.startsWith('0x') ? parseInt(text.slice(2), 16) : parseInt(text, 10);
  return Number.isFinite(value) ? value : null;
}

async function handleSearch(message) {
  const requestId = message.requestId;
  latestSearchId = requestId;
  const query = String(message.query || '').trim();

  try {
    const index = await ensureSearchIndex(requestId);
    if (latestSearchId !== requestId) return;

    const needle = query.toLowerCase();
    const address = parseAddressQuery(query);
    const results = [];
    let total = 0;
    const limit = message.limit || 100;

    for (let i = 0; i < index.length; i += 1) {
      if ((i & 8191) === 0 && i > 0) {
        await yieldToEventLoop();
        if (latestSearchId !== requestId) return;
      }
      const record = index[i];
      let hit = false;
      if (address !== null) {
        const base = typeof record.address === 'number' ? record.address : null;
        if (base !== null) {
          const size = Math.max(Number(record.size) || 1, 1);
          hit = address >= base && address < base + size;
        }
        if (!hit && needle) {
          hit = (record.nameLower || '').indexOf(needle) >= 0;
        }
      } else if (!needle) {
        hit = true;
      } else {
        hit =
          (record.nameLower || '').indexOf(needle) >= 0 ||
          (record.objectLower || '').indexOf(needle) >= 0;
      }
      if (!hit) continue;
      total += 1;
      if (results.length < limit) results.push(record);
    }

    if (latestSearchId !== requestId) return;
    post({
      type: 'SEARCH_RESULTS',
      requestId: requestId,
      query: query,
      total: total,
      results: results,
      truncated: total > results.length,
    });
  } catch (error) {
    if (latestSearchId !== requestId) return;
    postError(error, requestId, 'search-index', 'SEARCH_ERROR');
  }
}

self.onmessage = function (event) {
  const message = event.data || {};
  switch (message.type) {
    case 'INIT':
      handleInit(message);
      break;
    case 'LOAD_ROOT':
      handleLoadRoot(message);
      break;
    case 'LOAD_CHUNK':
      handleLoadChunk(message);
      break;
    case 'SEARCH':
      handleSearch(message);
      break;
    case 'CLEAR_CACHE':
      cache.clear();
      cacheBytes = 0;
      break;
    default:
      break;
  }
};
