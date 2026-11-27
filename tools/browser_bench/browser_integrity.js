/*
 * Verify that a corrupted shard produces a readable CHUNK_INTEGRITY_FAILED
 * error instead of a blank page.
 *
 * Usage: node browser_integrity.js <url>
 */
'use strict';

const fs = require('fs');
const { chromium } = require('playwright-core');

function findBrowser() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
  ];
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  throw new Error('No Chrome/Edge executable found. Set CHROME_PATH.');
}

(async () => {
  const url = process.argv[2];
  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
    args: ['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const status = () => page.evaluate(() => window.__mapviewDebug.getStatus());

  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => window.__MAPVIEW_READY__ === true, null, {
      timeout: 120000,
      polling: 50,
    });

    // Drill region (no shard yet), then the largest object (corrupted shard).
    const beforeRegion = await status();
    await page.evaluate(() => window.__mapviewDebug.drillFirstChild());
    await page.waitForFunction(
      (seq) => window.__mapviewDebug.getStatus().renderSeq > seq,
      beforeRegion.renderSeq,
      { timeout: 120000, polling: 50 }
    );
    await page.evaluate(() => window.__mapviewDebug.drillFirstChild());
    await page.waitForFunction(
      () => {
        const current = window.__mapviewDebug.getStatus();
        return current.lastError && current.lastError.code;
      },
      null,
      { timeout: 120000, polling: 50 }
    );
    const finalState = await status();
    if (finalState.lastError.code !== 'CHUNK_INTEGRITY_FAILED') {
      throw new Error('expected CHUNK_INTEGRITY_FAILED, got ' + JSON.stringify(finalState.lastError));
    }
    const banner = await page.evaluate(() => ({
      hidden: document.getElementById('error-banner').hidden,
      title: document.getElementById('error-title').textContent,
      message: document.getElementById('error-message').textContent,
    }));
    if (banner.hidden || !banner.message.includes('SHA-256')) {
      throw new Error('error banner not actionable: ' + JSON.stringify(banner));
    }
    if (errors.length) throw new Error('browser errors: ' + errors.join(' | '));
    console.log('INTEGRITY ERROR PASS', JSON.stringify(finalState.lastError));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
