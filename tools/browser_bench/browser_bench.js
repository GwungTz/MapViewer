/*
 * Headless browser benchmark for a Map View report.
 *
 * Usage:
 *   node browser_bench.js <url> [output.json] [screenshot.png]
 *
 * Requires playwright-core and an installed Chrome/Edge (CHROME_PATH overrides).
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

function findBrowser() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) {
    return process.env.CHROME_PATH;
  }
  const candidates =
    process.platform === 'win32'
      ? [
          'C:/Program Files/Google/Chrome/Application/chrome.exe',
          'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
          'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
          'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
        ]
      : [
          '/usr/bin/google-chrome',
          '/usr/bin/chromium',
          '/usr/bin/chromium-browser',
          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error('No Chrome/Edge executable found. Set CHROME_PATH.');
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function status(page) {
  return page.evaluate(() => window.__mapviewDebug.getStatus());
}

async function waitRender(page, previousSeq, timeout) {
  await page.waitForFunction(
    (seq) => window.__mapviewDebug.getStatus().renderSeq > seq,
    previousSeq,
    { timeout: timeout || 180000, polling: 50 }
  );
}

async function waitSearch(page, query, timeout) {
  await page.waitForFunction(
    (q) => {
      const s = window.__mapviewDebug.getStatus();
      return s.lastSearch && s.lastSearch.query === q;
    },
    query,
    { timeout: timeout || 180000, polling: 50 }
  );
}

async function main() {
  const url = process.argv[2];
  const outputPath = process.argv[3] || '';
  const screenshotPath = process.argv[4] || '';
  if (!url) {
    console.error('usage: node browser_bench.js <url> [output.json] [screenshot.png]');
    process.exit(2);
  }

  const executablePath = findBrowser();
  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: [
      '--disable-gpu',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--enable-precise-memory-info',
    ],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text());
  });

  const metrics = { url, browser: path.basename(executablePath), viewport: '1440x900' };

  try {
    const started = Date.now();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => window.__MAPVIEW_READY__ === true, null, {
      timeout: 180000,
      polling: 50,
    });
    metrics.firstScreenMs = Date.now() - started;
    metrics.firstScreenStatus = await status(page);
    metrics.firstScreenHeapBytes = await page.evaluate(() =>
      performance.memory ? performance.memory.usedJSHeapSize : null
    );

    // Drill into the largest first-level node.
    const beforeDrill = await status(page);
    const drillStarted = Date.now();
    await page.evaluate(() => window.__mapviewDebug.drillFirstChild());
    await waitRender(page, beforeDrill.renderSeq);
    metrics.drillMs = Date.now() - drillStarted;
    metrics.drillStatus = await status(page);
    metrics.drillHeapBytes = await page.evaluate(() =>
      performance.memory ? performance.memory.usedJSHeapSize : null
    );

    // Go back (should use cached data).
    const beforeBack = await status(page);
    const backStarted = Date.now();
    await page.evaluate(() => window.__mapviewDebug.goBack());
    await waitRender(page, beforeBack.renderSeq);
    metrics.backMs = Date.now() - backStarted;

    // Module view first render.
    const beforeModule = await status(page);
    const moduleStarted = Date.now();
    await page.evaluate(() => window.__mapviewDebug.setView('module'));
    await waitRender(page, beforeModule.renderSeq);
    metrics.moduleViewMs = Date.now() - moduleStarted;
    metrics.moduleViewStatus = await status(page);

    // Cold search (loads the search index).
    const coldStarted = Date.now();
    await page.evaluate(() => window.__mapviewDebug.search('a'));
    await waitSearch(page, 'a');
    metrics.searchColdMs = Date.now() - coldStarted;
    metrics.searchColdStatus = (await status(page)).lastSearch;

    // Warm search.
    const warmStarted = Date.now();
    await page.evaluate(() => window.__mapviewDebug.search('module_00'));
    await waitSearch(page, 'module_00');
    metrics.searchWarmMs = Date.now() - warmStarted;
    metrics.searchWarmStatus = (await status(page)).lastSearch;

    // Resize redraw (must not re-download data).
    const beforeResize = await status(page);
    const resizeStarted = Date.now();
    await page.setViewportSize({ width: 1280, height: 820 });
    await sleep(50);
    await waitRender(page, beforeResize.renderSeq);
    metrics.resizeMs = Date.now() - resizeStarted;
    metrics.resizeStatus = await status(page);
    metrics.finalHeapBytes = await page.evaluate(() =>
      performance.memory ? performance.memory.usedJSHeapSize : null
    );

    if (screenshotPath) {
      await page.screenshot({ path: screenshotPath, fullPage: false });
      metrics.screenshot = screenshotPath;
    }
  } finally {
    metrics.browserErrors = browserErrors;
    await browser.close();
  }

  if (outputPath) {
    fs.writeFileSync(outputPath, JSON.stringify(metrics, null, 2), 'utf8');
  }
  console.log(JSON.stringify(metrics, null, 2));
  if (metrics.browserErrors && metrics.browserErrors.length) process.exitCode = 3;
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
