// Rasterize the app's favicon.svg for the title and end cards.
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
const svg = readFileSync(new URL("../../Frontend/static/favicon.svg", import.meta.url), "utf8");
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 512, height: 512 } });
await page.setContent(`<body style="margin:0;background:transparent">${svg}</body>`);
await page.locator("svg").screenshot({ path: new URL("../assets/logo.png", import.meta.url).pathname.slice(1), omitBackground: true });
await browser.close();
