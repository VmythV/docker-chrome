// 用法: node test-connect.js
// 依赖: npm install playwright
const { chromium } = require('playwright');

(async () => {
  // 容器暴露的CDP端口，本地起容器用 localhost，远程服务器换成对应IP
  const browser = await chromium.connectOverCDP('http://localhost:9222');

  const context = browser.contexts()[0] || (await browser.newContext());
  const page = await context.newPage();

  await page.goto('https://example.com');
  console.log('页面标题:', await page.title());

  // 这里不关 browser，让 Chrome 容器继续跑，方便你在 noVNC 里接着看/操作
  // 如果想每次跑完就关闭页面，用 await page.close();
})();
