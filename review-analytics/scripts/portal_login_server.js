// Long-lived login helper for the 顾客说 portal. Opens the login page, requests the SMS code,
// screenshots the captcha, then waits over local HTTP for the captcha text and the SMS code the
// user relays. On success it saves the session to scripts/state.json (what pull_portal.js uses).
// Drive it through scripts/portal.sh, not by hand:
//   POST /captcha  body: chars      POST /sms  body: code      GET /   status      POST /quit
const { chromium } = require(require('child_process').execSync('npm root -g').toString().trim() + '/playwright');
const http = require('http');
const path = require('path');

const LOGIN_URL = 'https://cs.mlkee.com/portal/customersays/68937a1bce18c9164c557a98/6882d4c699553aa3d9aea3fa';
const PORTAL_TABLE_URL = LOGIN_URL + '/6882d4c699553aa3d9aea3fb';
const OUT = __dirname;                       // captcha.png / state.json live next to this script
const PHONE = process.argv[2];
const PORT = 8765;
let page, ctx, browser, status = 'starting';
const log = m => { status = m; console.log(new Date().toISOString(), m); };
const onLoginPage = () => /\/portal\/(network|login)/.test(page.url());

async function saveIfLoggedIn() {
  // The table page redirects to the login page when there is no session; that is the real test.
  await page.goto(PORTAL_TABLE_URL, { waitUntil: 'networkidle', timeout: 90000 }).catch(() => {});
  if (onLoginPage()) return false;
  await ctx.storageState({ path: path.join(OUT, 'state.json') });
  return true;
}

(async () => {
  browser = await chromium.launch({ proxy: { server: process.env.HTTPS_PROXY } });
  ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
  page = await ctx.newPage();
  await page.goto(LOGIN_URL, { waitUntil: 'networkidle', timeout: 90000 });
  await page.waitForTimeout(3000);
  if (!onLoginPage()) {
    if (await saveIfLoggedIn()) { log('already-logged-in'); return; }
  }
  await page.getByText('验证码', { exact: true }).first().click();
  await page.mouse.click(720, 342);
  await page.keyboard.type(PHONE);
  await page.getByText('获取验证码').click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: path.join(OUT, 'captcha.png'), clip: { x: 536, y: 280, width: 368, height: 341 } });
  log('captcha-ready');
})().catch(e => log('error: ' + e.message));

http.createServer((req, res) => {
  let body = '';
  req.on('data', c => body += c);
  req.on('end', async () => {
    try {
      if (req.method === 'GET') { res.end(status + '\n'); return; }
      const v = body.trim();
      if (req.url === '/captcha') {
        await page.getByPlaceholder('不区分大小写').fill(v);
        await page.getByText('确认', { exact: true }).click();
        await page.waitForTimeout(3000);
        await page.screenshot({ path: path.join(OUT, 'after_captcha.png') });
        log('captcha-submitted');
      } else if (req.url === '/sms') {
        await page.getByText('验证码', { exact: true }).nth(1).click().catch(() => {});
        await page.keyboard.type(v);
        await page.getByText('登录/注册', { exact: true }).click();
        await page.waitForTimeout(8000);
        await page.screenshot({ path: path.join(OUT, 'after_sms.png') });
        log((await saveIfLoggedIn()) ? 'logged-in' : 'login-failed');
      } else if (req.url === '/shot') {
        await page.screenshot({ path: path.join(OUT, 'now.png') }); log('shot');
      } else if (req.url === '/quit') { res.end('bye\n'); await browser.close(); process.exit(0); }
      res.end(status + '\n');
    } catch (e) { log('error: ' + e.message); res.end(status + '\n'); }
  });
}).listen(PORT, '127.0.0.1');
