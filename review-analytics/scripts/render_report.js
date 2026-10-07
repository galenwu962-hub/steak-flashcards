// Render a weekly board or monthly report (HTML fragment from app.weekly / app.monthly) to a
// one-page PDF and a one-page PNG, in the light theme, for WeChat and the DingTalk folder.
//   node scripts/render_report.js <in.html> [--pdf out.pdf] [--png out.png]
// Fonts come from Google Fonts through $HTTPS_PROXY.
const { chromium } = require(require('child_process').execSync('npm root -g').toString().trim() + '/playwright');
const fs = require('fs');

const args = process.argv.slice(2);
const src = args[0];
const opt = k => { const i = args.indexOf(k); return i > 0 ? args[i + 1] : null; };
const pdf = opt('--pdf'), png = opt('--png');
if (!src || (!pdf && !png)) { console.error('usage: render_report.js <in.html> [--pdf out.pdf] [--png out.png]'); process.exit(1); }

(async () => {
  const body = fs.readFileSync(src, 'utf8');
  const html = `<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact}body{margin:0}</style></head><body>${body}</body></html>`;
  const browser = await chromium.launch({ proxy: { server: process.env.HTTPS_PROXY } });
  const page = await browser.newPage({ viewport: { width: 960, height: 1200 }, deviceScaleFactor: 2, colorScheme: 'light' });
  await page.setContent(html, { waitUntil: 'networkidle', timeout: 90000 });
  await page.evaluate(() => document.fonts.ready);
  if (!(await page.evaluate(() => document.fonts.check('16px "Noto Sans SC"', '店')))) console.warn('warning: Noto Sans SC not loaded');
  if (png) {
    await page.screenshot({ path: png, fullPage: true });
    console.log('png ->', png);
  }
  if (pdf) {
    await page.emulateMedia({ media: 'print', colorScheme: 'light' });
    const h = await page.evaluate(() => Math.ceil(document.documentElement.scrollHeight));
    await page.pdf({ path: pdf, width: '960px', height: (h + 2) + 'px', printBackground: true,
                     margin: { top: 0, bottom: 0, left: 0, right: 0 }, pageRanges: '1' });
    console.log('pdf ->', pdf);
  }
  await browser.close();
})();
