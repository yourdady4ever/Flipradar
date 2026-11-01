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

import { bpToInput, centsToInput } from '../../flipradar/web/static/app.js';

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
    // Console errors are failures, except the "Failed to load resource" lines a step provokes on purpose.
    let expectedConsole = [];
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      if (expectedConsole.some((re) => re.test(msg.text()))) return;
      problems.push(`console error: ${msg.text()} (${msg.location().url || 'n/a'})`);
    });
    const expectingConsole = async (patterns, fn) => {
      expectedConsole = patterns;
      try {
        return await fn();
      } finally {
        await page.waitForTimeout(100); // let late console events of the provoked requests arrive
        expectedConsole = [];
      }
    };
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
    // Reads the API directly (outside the page, so nothing shows up in its console or network log).
    const apiGet = async (apiPath) => {
      const res = await page.request.get(new URL(`api/v1${apiPath}`, page.url()).href, { headers: { Authorization: `Bearer ${token}` } });
      return { status: res.status(), body: await res.json().catch(() => null) };
    };
    const isApi = (suffix) => (u) => new URL(u).pathname.endsWith(`/api/v1${suffix}`);
    // Serves GET /sources with the first source's status replaced, as after a failed collection run.
    const staleSourcesRoute = (marker) => async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      const list = await response.json();
      if (Array.isArray(list) && list.length) Object.assign(list[0], { last_status: 'error', last_error: marker });
      return route.fulfill({ response, json: list });
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

    await step('saved token survives a transient /auth/check failure; only a 401 forgets it', async () => {
      const authCheck = isApi('/auth/check');
      const stored = () => page.evaluate(() => sessionStorage.getItem('flipradar.token'));
      try {
        await expectingConsole([/Failed to load resource/], async () => {
          for (const [label, handler, message] of [
            ['HTTP 429', (route) => route.fulfill({ status: 429, json: { detail: 'SMOKE: simulated rate limit' } }), 'SMOKE: simulated rate limit'],
            ['network error', (route) => route.abort('connectionrefused'), 'Cannot reach'],
          ]) {
            await page.route(authCheck, handler);
            try {
              await page.reload({ waitUntil: 'domcontentloaded' });
              await page.waitForSelector('#login-view:not([hidden])');
              await page.waitForSelector(`#login-message:has-text("${message}")`);
            } finally {
              await page.unroute(authCheck, handler);
            }
            if ((await stored()) !== token) throw new Error(`${label}: the saved token was removed from sessionStorage`);
          }
          await page.click('#login-retry');
          await page.waitForSelector('#main[data-ready="opportunities"]');
          const reject = (route) => route.fulfill({ status: 401, json: { detail: 'SMOKE: simulated revoked token' } });
          await page.route(authCheck, reject);
          try {
            await page.reload({ waitUntil: 'domcontentloaded' });
            await page.waitForSelector('#login-message:has-text("no longer valid")');
          } finally {
            await page.unroute(authCheck, reject);
          }
          if (await stored()) throw new Error('HTTP 401: the rejected token was kept');
          if (await page.isVisible('#login-retry')) throw new Error('HTTP 401: retry offered without a saved token');
        });
      } finally {
        // leave the dashboard signed in for the next steps, whatever happened above
        if (await page.isVisible('#login-view')) {
          await page.fill('#login-token', token);
          await page.click('#login-submit');
        }
        await page.waitForSelector('#main[data-ready="opportunities"]');
      }
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

    await step('opportunities: a page emptied meanwhile falls back to the last page with rows', async () => {
      await gotoView('opportunities');
      const row = (id) => ({ id, buy_title: `SMOKE row ${id}`, product_title: 'SMOKE product', buy_source_key: 'smoke', buy_total_cents: 100000, expected_sale_cents: 150000, net_profit_cents: 20000, roi_bp: 2000, score: 50, confidence: 'medium', risk_level: 'none', demand_level: 'unknown', status: 'seen', is_demo: true });
      let total = 26;
      const list = isApi('/opportunities');
      const handler = (route) => {
        const q = new URL(route.request().url()).searchParams;
        const limit = Number(q.get('limit'));
        const offset = Number(q.get('offset'));
        const items = [];
        for (let i = offset; i < Math.min(total, offset + limit); i++) items.push(row(i + 1));
        return route.fulfill({ json: { items, total, limit, offset } });
      };
      await page.route(list, handler);
      try {
        await page.selectOption('#main select[name="limit"]', '25');
        await page.click('#main form[role="search"] button[type="submit"]');
        await page.waitForSelector('#main .pager:has-text("of 26")');
        total = 25; // row 26 was dismissed (or bought) before the user pages forward
        await page.click('#main .pager button:has-text("Next")');
        await page.waitForSelector('#main .pager:has-text("of 25")');
        await page.waitForFunction(() => !document.querySelector('#main .results.loading'));
        const pager = await page.textContent('#main .pager');
        if (!pager.includes('1–25 of 25') || (await page.locator('#main .empty').count())) {
          throw new Error(`emptied page shown instead of the last page with rows: "${pager}"`);
        }
      } finally {
        await page.unroute(list, handler);
        await page.click('#main form[role="search"] button:has-text("Reset")');
        await page.waitForFunction(() => !document.querySelector('#main .results.loading'));
      }
    });

    await step('source cards are fetched again on every visit', async () => {
      await gotoView('sources');
      const marker = 'SMOKE: status changed since the last visit';
      const sourcesApi = isApi('/sources');
      const handler = staleSourcesRoute(marker);
      await page.route(sourcesApi, handler);
      try {
        await gotoView('alerts');
        await gotoView('sources');
        await page.locator('#main .source-card', { hasText: marker }).first().waitFor({ timeout: 5000 });
      } finally {
        await page.unroute(sourcesApi, handler);
      }
    });

    await step('analysis settings expose the thresholds the engine uses', async () => {
      await gotoView('settings');
      const st = (await apiGet('/settings')).body;
      const value = (sel) => page.inputValue(`#main form[aria-label="Analysis settings"] ${sel}`);
      for (const [sel, want] of [
        ['input[name="min_roi"]', bpToInput(st.min_roi_bp)],
        ['input[name="min_profit"]', centsToInput(st.min_profit_cents)],
        ['select[name="pricing_strategy"]', st.pricing_strategy],
        ['select[name="default_fee_profile_key"]', st.default_fee_profile_key],
        ['select[name="default_fee_profile_key_used"]', st.default_fee_profile_key_used],
        ['input[name="unknown_shipping"]', centsToInput(st.unknown_shipping_assumption_cents)],
        ['input[name="target_roi"]', bpToInput(st.target_roi_bp)],
      ]) {
        const got = await value(sel);
        if (got !== want) throw new Error(`${sel}: shows "${got}", the API has "${want}"`);
      }
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
      // FX rates from key-less public sources must be shown with their attribution (FxOut.attribution).
      const fx = await apiGet('/fx/latest');
      await expectingConsole(fx.status === 404 ? [/Failed to load resource/] : [], async () => {
        await page.click('#main button:has-text("Show latest FX rate")');
        if (fx.status !== 200) {
          await page.waitForSelector('#main .notice:has-text("No FX rate stored")');
          log('       (no FX rate stored: attribution check skipped)');
          return;
        }
        const latest = page.locator('#main section.card:has(h2:text-is("Latest stored FX rate"))');
        await latest.waitFor();
        if (fx.body.attribution && !(await latest.textContent()).includes(fx.body.attribution)) {
          throw new Error(`latest FX rate shown without its attribution "${fx.body.attribution}"`);
        }
        await page.fill('#main input[name="item_value"]', '100');
        await page.click('#main button:has-text("Estimate duties")');
        const estimate = page.locator('#main section.card:has(h2:has-text("Estimate:"))');
        await estimate.waitFor();
        const text = await estimate.textContent();
        if (fx.body.attribution && !text.includes(fx.body.attribution)) throw new Error(`import estimate shows the stored rate without its attribution: ${text}`);
      });
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
        // "Sold on" = today must not be stamped at a fixed time of day (it was local noon: in the future all
        // morning, so analytics ignored the sale); the server's clock is used instead.
        const today = await page.evaluate(() => {
          const d = new Date();
          return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        });
        await page.fill('#main input[name="sold_at"]', today);
        const saleRequest = page.waitForRequest((r) => r.method() === 'POST' && /\/inventory\/\d+\/sales$/.test(new URL(r.url()).pathname));
        await page.click('#main button:has-text("Record sale")');
        const sent = (await saleRequest).postDataJSON();
        if ('sold_at' in sent) throw new Error(`a sale dated today sent sold_at=${sent.sold_at} instead of using the server clock`);
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
        await page.waitForSelector('.drawer[data-ready] .badges:has-text("bought")');
        const offered = (await page.locator('.drawer .actions button').allTextContents()).map((t) => t.trim());
        const reopening = offered.filter((t) => ['Watch', 'Stop watching', 'Dismiss', 'Restore'].includes(t));
        await page.keyboard.press('Escape');
        await page.waitForSelector('.drawer', { state: 'hidden' });
        if (reopening.length) throw new Error(`a bought opportunity still offers ${reopening.join(', ')} (undoing them reopens it as 'seen')`);
      });

      await step('tracked search create/edit/delete', async () => {
        await gotoView('sources');
        await page.fill('#main form[aria-label="Add tracked search"] input[name="query"]', 'smoke test query');
        await page.fill('#main form[aria-label="Add tracked search"] input[name="interval_minutes"]', '60');
        await page.click('#main button:has-text("Add search")');
        const row = page.locator('#main tr', { hasText: 'smoke test query' });
        await row.waitFor();
        // "Run now" changes each source's last run / status / error: the cards must be refreshed.  The run itself is
        // stubbed so the smoke test never calls an external API.
        const marker = 'SMOKE: source status after the run';
        const runApi = (u) => /\/api\/v1\/searches\/\d+\/run$/.test(new URL(u).pathname);
        const runStub = (route) => route.fulfill({ json: { runs: [], opportunities_created: 0, opportunities_updated: 0, notifications_created: 0 } });
        const sourcesApi = isApi('/sources');
        const sourcesStub = staleSourcesRoute(marker);
        await page.route(runApi, runStub);
        await page.route(sourcesApi, sourcesStub);
        try {
          await row.locator('button:has-text("Run now")').click();
          await successToast('finished');
          await page.locator('#main .source-card', { hasText: marker }).first().waitFor({ timeout: 5000 });
        } finally {
          await page.unroute(runApi, runStub);
          await page.unroute(sourcesApi, sourcesStub);
        }
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

      await step('analysis settings round-trip the engine thresholds', async () => {
        await gotoView('settings');
        const before = (await apiGet('/settings')).body;
        const f = '#main form[aria-label="Analysis settings"]';
        const save = async () => {
          await page.$eval(f, (form) => { form.dataset.smokeSaved = '1'; });
          const put = page.waitForRequest((r) => r.method() === 'PUT' && new URL(r.url()).pathname.endsWith('/api/v1/settings'));
          await page.click(`${f} button:has-text("Save settings")`);
          const body = (await put).postDataJSON();
          await successToast('Settings saved');
          await page.waitForSelector(`${f}:not([data-smoke-saved])`); // the form is re-rendered from the stored values
          return body;
        };
        await page.fill(`${f} input[name="min_roi"]`, '12.5');
        await page.fill(`${f} input[name="unknown_shipping"]`, '99.90');
        await page.selectOption(`${f} select[name="pricing_strategy"]`, before.pricing_strategy === 'market' ? 'competitive' : 'market');
        const sent = await save();
        const after = (await apiGet('/settings')).body;
        if (sent.min_roi_bp !== 1250 || after.min_roi_bp !== 1250) throw new Error(`min ROI not saved (sent ${sent.min_roi_bp}, stored ${after.min_roi_bp})`);
        if (after.unknown_shipping_assumption_cents !== 9990) throw new Error(`shipping assumption not saved: ${after.unknown_shipping_assumption_cents}`);
        if (after.pricing_strategy === before.pricing_strategy) throw new Error('pricing strategy not saved');
        if (after.target_roi_bp !== before.target_roi_bp) throw new Error('calculator target ROI changed by an unrelated edit');
        // restore (an emptied shipping assumption is stored as $0, which the engine treats like "none")
        await page.fill(`${f} input[name="min_roi"]`, bpToInput(before.min_roi_bp));
        await page.fill(`${f} input[name="unknown_shipping"]`, centsToInput(before.unknown_shipping_assumption_cents));
        await page.selectOption(`${f} select[name="pricing_strategy"]`, before.pricing_strategy);
        await save();
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
