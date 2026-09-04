const { chromium } = require('playwright-core');
(async () => {
  const url = process.argv[2];
  const waitMs = parseInt(process.argv[3] || '60000', 10);
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const lines = [];
  page.on('console', m => { const t = m.text(); lines.push(t); if (/^RESULT|^DONE|^ERR/.test(t)) console.log(t); });
  page.on('pageerror', e => console.log('PAGEERROR ' + e.message));
  await page.goto(url, { waitUntil: 'load' });
  const start = Date.now();
  while (Date.now() - start < waitMs) {
    if (lines.some(l => l.includes('DONE'))) break;
    await page.waitForTimeout(250);
  }
  if (!lines.some(l => l.includes('DONE'))) console.log('TIMEOUT after ' + waitMs + 'ms; lines=' + JSON.stringify(lines.slice(-5)));
  await browser.close();
})();
