// FLIP RADAR web dashboard smoke test (Node + Playwright, Chromium).
//
//   FLIPRADAR_URL=http://127.0.0.1:8000/ FLIPRADAR_TOKEN=<token> node tests/web_smoke/smoke.mjs
//
// Logs in, visits every view (desktop and 360 px phone width), opens an opportunity detail when one
// exists, runs the calculator, and fails on ANY browser console error (CSP violations included),
// uncaught page error, HTTP 5xx, visible error box, horizontal page overflow at phone width, or
// HTML injection (server strings must render as text).
//
// FLIPRADAR_SMOKE_MUTATE=1 additionally creates and deletes an alert rule and an inventory item with
// a sale.  Only use it against a throw-away database.
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

function loadPlaywright() {
  // ESM ignores NODE_PATH; createRequire honours it, so a globally installed playwright works.
  const require = createRequire(import.meta.url);
  return require('playwright');
}

const VIEWS = ['opportunities', 'calculator', 'inventory', 'analytics', 'sources', 'alerts', 'notifications', 'settings'];

export async function runSmoke({ url, token, mutate = false, log = console.log } = {}) {
  if (!url || !token) throw new Error('FLIPRADAR_URL and FLIPRADAR_TOKEN are required');
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch();
  const problems = [];
  const step = async (name, fn) => {
    try {
      await fn();
      log(`  ok   ${name}`);
    } catch (err) {
      problems.push(`${name}: ${err.message.split('\n')[0]}`);
      log(`  FAIL ${name}: ${err.message.split('\n')[0]}`);
    }
  };
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('console', (msg) => {
      if (msg.type() === 'error') problems.push(`console error: ${msg.text()} (${msg.location().url || 'n/a'})`);
    });
    page.on('pageerror', (err) => problems.push(`page error: ${err.message}`));
    page.on('response', (res) => {
      if (res.status() >= 500) problems.push(`HTTP ${res.status()} for ${res.request().method()} ${res.url()}`);
    });
    page.on('dialog', (d) => d.accept());

    const assertNoErrorBox = async (where) => {
      const boxes = await page.locator('#main .error-box').allTextContents();
      if (boxes.length) throw new Error(`${where}: error shown: ${boxes.join(' | ')}`);
    };
    const assertNoInjectedHtml = async (where) => {
      const injected = await page.locator('img[src="x"], #main script, .drawer script').count();
      if (injected) throw new Error(`${where}: server text was rendered as HTML (${injected} injected elements)`);
    };
    // Rendering bugs that leak JS values into the UI ("undefined", "NaN", "[object Object]", a bare "null").
    const assertNoJunkText = async (where) => {
      const junk = await page.evaluate(() => {
        const found = [];
        const text = (document.querySelector('#app') || document.body).innerText;
        for (const re of [/\bundefined\b/, /\bNaN\b/, /\[object Object\]/]) if (re.test(text)) found.push(String(re));
        for (const el of document.querySelectorAll('.topbar, #main td, #main dd, .tile-value, .drawer td, .drawer dd')) {
          if (/(^|\s)null(\s|$)/.test(el.innerText)) found.push(`null in ${el.tagName.toLowerCase()}: "${el.innerText.slice(0, 60)}"`);
        }
        return found.slice(0, 5);
      });
      if (junk.length) throw new Error(`${where}: junk text rendered: ${junk.join('; ')}`);
    };
    const gotoView = async (view) => {
      await page.click(`#sidenav a[data-route="${view}"]`);
      await page.waitForSelector(`#main[data-ready="${view}"]`);
    };

    await step('login', async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#login-form', { state: 'visible' });
      await page.waitForFunction(() => !/Checking server/.test(document.querySelector('#login-server').textContent));
      await page.fill('#login-token', token);
      await page.click('#login-submit');
      await page.waitForSelector('#app:not([hidden])');
      await page.waitForSelector('#main[data-ready="opportunities"]');
      const info = await page.textContent('#server-info');
      if (!/v\d/.test(info)) throw new Error(`server version not shown (got "${info}")`);
    });

    for (const view of VIEWS) {
      await step(`view ${view}`, async () => {
        await gotoView(view);
        const h1 = await page.textContent('#main h1');
        if (!h1 || !h1.trim()) throw new Error('no heading');
        await assertNoErrorBox(view);
        await assertNoInjectedHtml(view);
        await assertNoJunkText(view);
      });
    }

    await step('opportunity detail', async () => {
      await gotoView('opportunities');
      let link = page.locator('#main a.row-link').first();
      if ((await link.count()) === 0) {
        // opportunities whose buy listing is flagged suspicious are hidden by default; the empty state offers them
        const reveal = await page.waitForSelector('#main .hidden-suspicious button', { timeout: 4000 }).catch(() => null);
        if (reveal) {
          log('       (only suspicious-flagged opportunities exist: revealing them via the empty-state button)');
          await reveal.click();
          await page.waitForSelector('#main a.row-link');
          link = page.locator('#main a.row-link').first();
        }
      }
      if ((await link.count()) === 0) {
        const empty = await page.locator('#main .empty').count();
        if (!empty) throw new Error('neither rows nor the empty state are shown');
        log('       (no opportunities in this database: empty state verified)');
        return;
      }
      await link.click();
      await page.waitForSelector('.drawer[data-ready]');
      const sections = await page.locator('.drawer .section > h3').allTextContents();
      for (const needed of ['Cost breakdown', 'Score components', 'Risk flags', 'Explanation', 'Comparables']) {
        if (!sections.some((s) => s.startsWith(needed))) throw new Error(`missing detail section "${needed}" (have: ${sections.join(', ')})`);
      }
      await page.locator('.drawer .error-box').count().then((n) => {
        if (n) throw new Error('detail shows an error box');
      });
      const ext = page.locator('.drawer a[target="_blank"]').first();
      if ((await ext.count()) && (await ext.getAttribute('rel')) !== 'noopener noreferrer') throw new Error('external link without rel=noopener noreferrer');
      await assertNoInjectedHtml('opportunity detail');
      await assertNoJunkText('opportunity detail');
      await page.click('.drawer button:has-text("Generate AI summary")');
      await page.waitForSelector('.drawer .badges:has-text("validated")');
      await page.keyboard.press('Escape');
      await page.waitForSelector('.drawer', { state: 'hidden' });
    });

    await step('calculator', async () => {
      await gotoView('calculator');
      await page.fill('#main input[name="buy_price"]', '1000');
      await page.fill('#main input[name="sale_price"]', '2,000.50');
      await page.fill('#main input[name="target_roi"]', '20');
      await page.click('#main form[aria-labelledby="calc-title"] button[type="submit"]');
      await page.waitForSelector('.calc-result[data-ready]');
      const text = await page.textContent('.calc-result');
      if (!/Net profit/.test(text) || !/\$/.test(text)) throw new Error('result not rendered');
      // invalid money input must be rejected client side
      await page.fill('#main input[name="buy_price"]', '10.555');
      await page.click('#main form[aria-labelledby="calc-title"] button[type="submit"]');
      await page.waitForSelector('#main input[name="buy_price"][aria-invalid="true"]');
    });

    await step('analytics chart', async () => {
      await gotoView('analytics');
      // the SVG is drawn on the next animation frame (it needs the container width)
      await page.waitForSelector('#main .chart-wrap svg, #main .card:has-text("No sales or purchases")');
      if (await page.locator('#main .chart-wrap svg').count()) {
        const bars = await page.locator('#main .chart-wrap path').count();
        if (!bars) throw new Error('chart has no bars');
        await page.locator('#main .chart-wrap rect.hit').first().hover();
        await page.waitForSelector('#main .chart-tip:not([hidden])');
      }
      await page.selectOption('#main select[name="days"]', '90');
      await page.waitForFunction(() => !document.querySelector('#main .results.loading'));
      await assertNoErrorBox('analytics 90d');
    });

    if (mutate) {
      await step('alerts create/delete', async () => {
        await gotoView('alerts');
        await page.fill('#main input[name="name"]', 'smoke rule');
        await page.fill('#main input[name="min_profit"]', '250.75');
        await page.fill('#main input[name="min_roi"]', '12.5');
        await page.click('#main button:has-text("Create rule")');
        const row = page.locator('#main tr', { hasText: 'smoke rule' });
        await row.waitFor();
        const rowText = await row.textContent();
        if (!rowText.includes('$250.75') || !rowText.includes('12.50%')) throw new Error(`money/percent not round-tripped: ${rowText}`);
        await row.locator('button:has-text("Delete")').click();
        await row.waitFor({ state: 'detached' });
      });

      await step('inventory add/sale/delete', async () => {
        await gotoView('inventory');
        await page.click('#main details summary:has-text("Add item")');
        await page.fill('#main input[name="title"]', 'Smoke test item');
        await page.fill('#main input[name="quantity"]', '2');
        await page.fill('#main input[name="unit_cost"]', '100.10');
        await page.click('#main button:has-text("Add item")');
        await page.waitForSelector('#main[data-ready^="inventory-"]');
        await page.fill('#main input[name="unit_price"]', '150');
        await page.click('#main button:has-text("Record sale")');
        await page.waitForSelector('#main tr:has(button:has-text("Delete"))');
        await page.click('#main tr button:has-text("Delete")');
        await page.waitForSelector('#main p:has-text("No sales recorded yet.")');
        await page.click('#main button:has-text("Delete item")');
        await page.waitForSelector('#main[data-ready="inventory"]');
      });
    }

    if (mutate) {
      const successToast = (text) => page.waitForSelector(`.toast-success:has-text("${text}")`);

      await step('opportunity watch + mark bought', async () => {
        await gotoView('opportunities');
        const reveal = await page.$('#main .hidden-suspicious button');
        if (reveal) {
          await reveal.click();
          await page.waitForSelector('#main a.row-link');
        }
        const link = page.locator('#main a.row-link').first();
        if ((await link.count()) === 0) {
          log('       (no opportunities: skipped)');
          return;
        }
        await link.click();
        await page.waitForSelector('.drawer[data-ready]');
        await page.click('.drawer .actions button:has-text("Watch")');
        await successToast('Watching');
        await page.waitForSelector('.drawer[data-ready] .badges:has-text("watching")');
        await page.click('.drawer button:has-text("Mark bought")');
        await page.fill('.drawer input[name="quantity"]', '2');
        await page.click('.drawer button:has-text("Confirm purchase")');
        await successToast('Added to inventory');
        await page.keyboard.press('Escape');
        await page.waitForSelector('.drawer', { state: 'hidden' });
      });

      await step('tracked search create/edit/delete', async () => {
        await gotoView('sources');
        await page.fill('#main form[aria-label="Add tracked search"] input[name="query"]', 'smoke test query');
        await page.fill('#main form[aria-label="Add tracked search"] input[name="interval_minutes"]', '60');
        await page.click('#main button:has-text("Add search")');
        const row = page.locator('#main tr', { hasText: 'smoke test query' });
        await row.waitFor();
        await row.locator('button:has-text("Edit")').click();
        await page.fill('#main form[aria-label="Edit tracked search"] input[name="max_results"]', '25');
        await page.click('#main button:has-text("Save search")');
        await successToast('Search updated');
        await page.locator('#main tr', { hasText: 'smoke test query' }).locator('button:has-text("Delete")').click();
        await page.locator('#main tr', { hasText: 'smoke test query' }).waitFor({ state: 'detached' });
      });

      await step('CSV import + recompute', async () => {
        await page.setInputFiles('#main input[type="file"][name="file"]', {
          name: 'smoke.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from('title,price,condition,url\nSMOKE TEST item alpha 64gb,1234.50,used,https://example.invalid/a\n'),
        });
        await page.click('#main button:has-text("Upload & import")');
        await successToast('CSV imported');
        await page.click('#main button:has-text("Recompute opportunities")');
        await page.waitForSelector('#main .card:has-text("Recompute result")');
      });

      await step('fee profile create/edit/delete', async () => {
        await gotoView('settings');
        const f = '#main form[aria-label="New fee profile"]';
        await page.fill(`${f} input[name="key"]`, 'smoke_profile');
        await page.fill(`${f} input[name="name"]`, 'Smoke profile');
        await page.fill(`${f} input[name="commission"]`, '12.5');
        await page.click(`${f} button:has-text("Add tier")`);
        await page.click(`${f} button:has-text("Add tier")`);
        const maxes = page.locator(`${f} input[name="tier_max"]`);
        const fees = page.locator(`${f} input[name="tier_fee"]`);
        await maxes.nth(0).fill('299');
        await fees.nth(0).fill('25');
        await fees.nth(1).fill('0');
        await page.click(`${f} button:has-text("Create profile")`);
        const row = page.locator('#main tr', { hasText: 'smoke_profile' });
        await row.waitFor();
        const text = await row.textContent();
        if (!text.includes('12.50%') || !text.includes('$299.00') || !text.includes('$25.00')) throw new Error(`profile not round-tripped: ${text}`);
        await row.locator('button:has-text("Edit")').click();
        await page.fill('#main form[aria-label="Edit fee profile"] input[name="name"]', 'Smoke profile edited');
        await page.click('#main button:has-text("Save profile")');
        await page.locator('#main tr', { hasText: 'Smoke profile edited' }).waitFor();
        await page.locator('#main tr', { hasText: 'smoke_profile' }).locator('button:has-text("Delete")').click();
        await page.locator('#main tr', { hasText: 'smoke_profile' }).waitFor({ state: 'detached' });
        await page.click('#main button:has-text("Save settings")');
        await successToast('Settings saved');
      });

      await step('notifications mark all read', async () => {
        await gotoView('notifications');
        await page.click('#main button:has-text("Mark all read")');
        await successToast('marked as read');
        if (await page.locator('#main .notif.unread').count()) throw new Error('unread notifications remain');
      });
    }

    await step('phone width (360 px): no horizontal overflow, collapsible nav', async () => {
      await page.setViewportSize({ width: 360, height: 740 });
      for (const view of VIEWS) {
        await page.click('#nav-toggle');
        await page.waitForSelector('#sidenav.open');
        await page.click(`#sidenav a[data-route="${view}"]`);
        await page.waitForSelector('#sidenav', { state: 'hidden' });
        await page.waitForSelector(`#main[data-ready="${view}"]`);
        await page.waitForTimeout(150);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        if (overflow > 1) throw new Error(`${view}: page scrolls horizontally by ${overflow}px at 360px`);
      }
      await page.setViewportSize({ width: 1280, height: 900 });
    });

    await step('logout', async () => {
      await page.click('#logout-btn');
      await page.waitForSelector('#login-view:not([hidden])');
      const stored = await page.evaluate(() => sessionStorage.getItem('flipradar.token'));
      if (stored) throw new Error('token still in sessionStorage after logout');
    });

    await context.close();
  } finally {
    await browser.close();
  }
  return problems;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const problems = await runSmoke({
    url: process.env.FLIPRADAR_URL,
    token: process.env.FLIPRADAR_TOKEN,
    mutate: process.env.FLIPRADAR_SMOKE_MUTATE === '1',
  });
  if (problems.length) {
    console.error(`\nSMOKE FAILED (${problems.length} problem(s)):`);
    for (const p of problems) console.error(` - ${p}`);
    process.exit(1);
  }
  console.log('\nSMOKE PASSED');
}
