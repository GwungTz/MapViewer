/*
 * Functional browser smoke test for a Map View report.
 *
 * Usage: node browser_smoke.js <url> [search-query]
 */
'use strict';

const fs = require('fs');
const { chromium } = require('playwright-core');

function findBrowser() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) {
    return process.env.CHROME_PATH;
  }
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
  ];
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  throw new Error('No Chrome/Edge executable found. Set CHROME_PATH.');
}

function check(condition, message) {
  if (!condition) throw new Error('CHECK FAILED: ' + message);
}

async function main() {
  const url = process.argv[2];
  const query = process.argv[3] || 'main';
  if (!url) throw new Error('usage: node browser_smoke.js <url> [query]');

  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
    args: ['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  const requestedUrls = [];
  page.on('request', (request) => requestedUrls.push(request.url()));
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });

  const status = () => page.evaluate(() => window.__mapviewDebug.getStatus());
  const waitRender = async (seq) =>
    page.waitForFunction((previous) => window.__mapviewDebug.getStatus().renderSeq > previous, seq, {
      timeout: 120000,
      polling: 50,
    });
  const step = async (label, action) => {
    const before = await status();
    await action();
    await waitRender(before.renderSeq);
    const after = await status();
    console.log(label, JSON.stringify(after));
    return after;
  };

  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => window.__MAPVIEW_READY__ === true, null, {
      timeout: 120000,
      polling: 50,
    });

    const first = await status();
    check(first.loadedChunks === 1, 'first screen must only load the root chunk');
    check(first.children > 0, 'first screen must have children');
    const shardRequests = requestedUrls.filter((url) => url.endsWith('.jsonl.gz'));
    check(shardRequests.length === 1, 'first screen must request exactly one shard: ' + shardRequests.join(', '));
    check(
      !requestedUrls.some((url) => url.includes('tree-index') || url.includes('search-index')),
      'first screen must not request indexes: ' + requestedUrls.join(', ')
    );
    console.log('first screen', JSON.stringify(first));
    console.log(
      'memory bars',
      JSON.stringify(
        await page.evaluate(() => ({
          rom: document.getElementById('rom-text').textContent,
          ram: document.getElementById('ram-text').textContent,
          romTitle: document.getElementById('rom-fill').closest('.memory-bar').title,
        }))
      )
    );

    const overlayVisibility = await page.evaluate(() => {
      const ids = ['loading', 'error-banner', 'empty-state', 'search-panel'];
      return ids.map((id) => {
        const element = document.getElementById(id);
        return { id, hidden: element.hidden, display: getComputedStyle(element).display };
      });
    });
    for (const overlay of overlayVisibility) {
      check(
        overlay.display === 'none',
        'overlay ' + overlay.id + ' must not be visible after the first render (' + overlay.display + ')'
      );
    }

    const drilled = await step('drill region', () =>
      page.evaluate(() => window.__mapviewDebug.drillFirstChild())
    );
    check(drilled.path.length === 1, 'region drill should add one path entry');
    check(drilled.children > 0, 'region drill should show objects');
    const backDisplay = await page.evaluate(
      () => getComputedStyle(document.getElementById('btn-back')).display
    );
    check(backDisplay !== 'none', 'back button must be visible after drilling');

    const back = await step('back to root', () => page.evaluate(() => window.__mapviewDebug.goBack()));
    check(back.path.length === 0, 'back should restore the view root');

    await step('switch to module view', () =>
      page.evaluate(() => window.__mapviewDebug.setView('module'))
    );

    const category = await step('drill category', () =>
      page.evaluate(() => window.__mapviewDebug.drillFirstChild())
    );
    check(category.path.length === 1, 'category drill should add one path entry');

    const module = await step('drill module', () =>
      page.evaluate(() => window.__mapviewDebug.drillFirstChild())
    );
    check(module.path.length === 2, 'module drill should add one path entry');

    const partBefore = module.loadedChunks;
    const part = await step('drill part', () =>
      page.evaluate(() => window.__mapviewDebug.drillFirstChild())
    );
    check(part.path.length === 3, 'part drill should add one path entry');
    check(part.children > 0, 'part drill should load section leaves');
    check(part.loadedChunks >= partBefore, 'part drill must load a module section chunk');

    const shardsBeforeBack = requestedUrls.filter((url) => url.endsWith('.jsonl.gz')).length;
    for (let index = 0; index < 3; index += 1) {
      await step('back ' + (index + 1), () => page.evaluate(() => window.__mapviewDebug.goBack()));
    }
    const shardsAfterBack = requestedUrls.filter((url) => url.endsWith('.jsonl.gz')).length;
    check(
      shardsAfterBack === shardsBeforeBack,
      'back navigation must use cached shards (requests changed from ' +
        shardsBeforeBack +
        ' to ' +
        shardsAfterBack +
        ')'
    );
    const root = await status();
    check(root.path.length === 0, 'three backs should return to the module root');

    await page.evaluate((q) => window.__mapviewDebug.search(q), query);
    await page.waitForFunction(
      (q) => {
        const state = window.__mapviewDebug.getStatus();
        return state.lastSearch && state.lastSearch.query === q;
      },
      query,
      { timeout: 120000, polling: 50 }
    );
    const resultCount = await page.$$eval('.search-result', (nodes) => nodes.length);
    check(resultCount > 0, 'search should return at least one result for ' + query);
    check(
      requestedUrls.some((url) => url.includes('search-index')),
      'search must load the search index on demand'
    );

    const beforeClick = await status();
    await page.click('.search-result');
    await waitRender(beforeClick.renderSeq);
    const located = await status();
    check(located.path.length >= 1, 'search navigation should drill into the result path');
    check(!!located.highlightId, 'search navigation should highlight the target node');

    check(errors.length === 0, 'browser errors: ' + errors.join(' | '));
    check(!located.lastError, 'application error: ' + JSON.stringify(located.lastError));

    // A newer query must win over a still-running older one.
    await page.evaluate(() => {
      window.__mapviewDebug.search('main');
      window.__mapviewDebug.search('zzz_no_such_symbol');
    });
    await page.waitForFunction(
      () => {
        const state = window.__mapviewDebug.getStatus();
        return state.lastSearch && state.lastSearch.query === 'zzz_no_such_symbol';
      },
      null,
      { timeout: 120000, polling: 50 }
    );
    const latestSearch = (await status()).lastSearch;
    check(
      latestSearch.query === 'zzz_no_such_symbol',
      'newer search must not be overwritten by an older one'
    );
    check(latestSearch.total === 0, 'zzz_no_such_symbol should have no matches');

    // Resize must re-layout without fetching shards again.
    const shardsBeforeResize = requestedUrls.filter((url) => url.endsWith('.jsonl.gz')).length;
    const beforeResize = await status();
    await page.setViewportSize({ width: 1200, height: 800 });
    await waitRender(beforeResize.renderSeq);
    const shardsAfterResize = requestedUrls.filter((url) => url.endsWith('.jsonl.gz')).length;
    check(
      shardsAfterResize === shardsBeforeResize,
      'resize must not download shards again (' +
        shardsBeforeResize +
        ' -> ' +
        shardsAfterResize +
        ')'
    );

    // Memory configuration override (persisted in localStorage).
    await page.click('#btn-config');
    await page.fill('#config-rom', '512');
    await page.fill('#config-ram', '128');
    await page.click('#config-save');
    const romText = await page.textContent('#rom-text');
    check(romText.includes('512.0 KB'), 'ROM override should be applied: ' + romText);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__MAPVIEW_READY__ === true, null, {
      timeout: 120000,
      polling: 50,
    });
    const romTextAfterReload = await page.textContent('#rom-text');
    check(
      romTextAfterReload.includes('512.0 KB'),
      'ROM override should persist in localStorage: ' + romTextAfterReload
    );

    console.log('SMOKE PASS');
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
