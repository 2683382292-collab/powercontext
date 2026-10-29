import puppeteer from "puppeteer-core";
const browser = await puppeteer.launch({
  executablePath: "/usr/local/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
page.setDefaultTimeout(180000);
await page.goto("http://127.0.0.1:43123/", { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => document.querySelector("#import-status")?.textContent?.includes("模型已在本地就绪"), { timeout: 180000 });
const backend = await page.$eval("#backend", (el) => el.textContent);
await page.click("#sample");
await page.click("#import");
await page.waitForFunction(() => document.querySelector("#import-status")?.textContent?.includes("写入完成"), { timeout: 180000 });
const imported = await page.$eval("#import-status", (el) => el.textContent);
await page.type("#query", "想喝一碗热汤");
await page.click("#search");
await page.waitForFunction(() => document.querySelector("#results li"), { timeout: 60000 });
const search = await page.$eval("#search-status", (el) => el.textContent);
const first = await page.$eval("#results li", (el) => el.innerText);
console.log(JSON.stringify({ backend, imported, search, first }, null, 2));
await browser.close();
