// Log in to the 顾客说 portal with a phone verification code and save the session to state.json.
// The saved session is what scripts/pull_portal.js uses. "7天内免登录" is ticked, so re-run weekly.
//
//   node scripts/login_portal.js
//
// You will be asked for the phone number, the characters in captcha.png (written next to this
// script) and the SMS code. Requires playwright: `npm install -g playwright` (Chromium bundled).
const { chromium } = require('playwright');
const readline = require('readline');
const path = require('path');

const LOGIN_URL = 'https://cs.mlkee.com/portal/customersays/68937a1bce18c9164c557a98/6882d4c699553aa3d9aea3fa';
const ask = q => new Promise(res => { const rl = readline.createInterface({ input: process.stdin, output: process.stdout }); rl.question(q, a => { rl.close(); res(a.trim()); }); });

(async () => {
  const browser = await chromium.launch(process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {});
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
  const page = await ctx.newPage();
  await page.goto(LOGIN_URL, { waitUntil: 'networkidle', timeout: 60000 });
  if (!/\/portal\/(network|login)/.test(page.url())) { console.log('already logged in'); await ctx.storageState({ path: path.join(__dirname, 'state.json') }); await browser.close(); return; }

  await page.getByText('验证码', { exact: true }).first().click();
  const phone = await ask('手机号: ');
  await page.getByText('手机号', { exact: true }).click();
  await page.keyboard.type(phone);
  await page.getByText('获取验证码').click();
  await page.waitForTimeout(2500);
  const captcha = path.join(__dirname, 'captcha.png');
  await page.screenshot({ path: captcha, clip: { x: 536, y: 280, width: 368, height: 341 } });
  const chars = await ask(`请打开 ${captcha}，输入图形验证码: `);
  await page.getByPlaceholder('不区分大小写').fill(chars);
  await page.getByText('确认', { exact: true }).click();
  await page.waitForTimeout(2500);
  const code = await ask('短信验证码: ');
  await page.getByText('验证码', { exact: true }).nth(1).click().catch(() => {});
  await page.keyboard.type(code);
  await page.getByText('登录/注册', { exact: true }).click();
  await page.waitForTimeout(8000);
  if (/\/portal\/(network|login)/.test(page.url())) { console.log('登录失败，请重试'); await browser.close(); process.exit(1); }
  await ctx.storageState({ path: path.join(__dirname, 'state.json') });
  console.log('登录成功，会话已保存到 scripts/state.json');
  await browser.close();
})();
