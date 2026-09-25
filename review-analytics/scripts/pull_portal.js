// Pull all rows of a 明道云 worksheet through the logged-in portal session.
// usage: node pull.js <name> <appId> <worksheetId> <viewId> [pageSize]
const { chromium } = require(require('child_process').execSync('npm root -g').toString().trim() + '/playwright');
const fs = require('fs');
const [name, appId, worksheetId, viewId, ps] = process.argv.slice(2);
const pageSize = Number(ps || 200);
const APP = '6d93892d-7231-43dd-8faa-b59fd4479163';
fs.mkdirSync('data', { recursive: true });

(async () => {
  const b = await chromium.launch({ proxy: { server: process.env.HTTPS_PROXY } });
  const ctx = await b.newContext({ storageState: 'state.json', locale: 'zh-CN' });
  const page = await ctx.newPage();
  let hdr = null, infoBody = null;
  page.on('request', r => {
    if (r.url().includes('GetFilterRows') && !hdr) hdr = r.headers();
    if (r.url().includes('GetWorksheetBaseInfo') && !infoBody) infoBody = r.postData();
  });
  const seen = page.waitForRequest(r => r.url().includes('GetFilterRows'), { timeout: 60000 }).catch(() => null);
  await page.goto(`https://cs.mlkee.com/portal/customersays/68937a1bce18c9164c557a98/6882d4c699553aa3d9aea3fa/6882d4c699553aa3d9aea3fb`, { waitUntil: 'networkidle', timeout: 90000 }).catch(() => {});
  await seen;
  await page.waitForTimeout(3000);
  if (!hdr) { console.log('no GetFilterRows request seen on page'); await b.close(); process.exit(1); }
  const headers = {}; for (const k of ['content-type', 'x-requested-with', 'authorization', 'accept-language']) if (hdr[k]) headers[k] = hdr[k];
  const post = async (path, body) => {
    const r = await ctx.request.post('https://cs.mlkee.com/wwwapi/Worksheet/' + path, { headers, data: body, timeout: 120000 });
    return r.json();
  };
  const info = await post('GetWorksheetBaseInfo', { ...JSON.parse(infoBody || '{"getTemplate":true,"getViews":true}'), worksheetId });
  fs.writeFileSync(`data/${name}.schema.json`, JSON.stringify(info.data && { name: info.data.name, controls: info.data.template && info.data.template.controls, views: info.data.views }, null, 1));
  const tot = await post('GetFilterRowsTotalNum', { worksheetId, appId: APP, viewId, status: 1, searchType: 1, keyWords: '', filterControls: [], fastFilters: [], navGroupFilters: [] });
  console.log(name, 'total', tot.data);
  const out = fs.createWriteStream(`data/${name}.ndjson`);
  let n = 0;
  for (let pageIndex = 1; ; pageIndex++) {
    let res;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        res = await post('GetFilterRows', { worksheetId, pageSize, pageIndex, status: 1, appId: APP, viewId, sortControls: [{ controlId: 'ctime', isAsc: true }], notGetTotal: true, searchType: 1, keyWords: '', filterControls: [], fastFilters: [], navGroupFilters: [] });
        if (res && res.data && Array.isArray(res.data.data)) break;
      } catch (e) { console.log('retry', pageIndex, e.message.slice(0, 80)); }
      await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
    }
    const rows = (res && res.data && res.data.data) || [];
    for (const r of rows) out.write(JSON.stringify(r) + '\n');
    n += rows.length;
    if (pageIndex % 10 === 0 || rows.length < pageSize) console.log(name, 'page', pageIndex, 'rows', n);
    if (rows.length < pageSize) break;
  }
  out.end();
  console.log(name, 'DONE', n);
  await b.close();
})();
