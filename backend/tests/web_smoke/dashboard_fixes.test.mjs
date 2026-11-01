// Regression tests for verified dashboard defects (pure helpers only, no DOM).
//   node --test tests/web_smoke/dashboard_fixes.test.mjs
// Browser-level checks for the same defects live in smoke.mjs (run_mock.mjs runs them against the mock API).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  dateInputToIso,
  fxRateRows,
  importFxAttribution,
  isAuthRejection,
  offsetAfterEmptyPage,
  oppStatusActions,
} from '../../flipradar/web/static/app.js';

const OPEN_STATUSES = new Set(['new', 'seen', 'watching']); // analytics OPEN_OPPORTUNITY_STATUSES

test('dashboard#0: a bought or expired opportunity offers no status toggle that reopens it', () => {
  for (const status of ['bought', 'expired']) {
    assert.deepEqual(oppStatusActions(status), [], status);
  }
  // unknown statuses get no actions either (never guess a transition)
  assert.deepEqual(oppStatusActions('something_else'), []);
  assert.deepEqual(oppStatusActions(undefined), []);
});

test('dashboard#0: the open statuses keep their Watch / Dismiss / Restore actions', () => {
  const targets = (status) => oppStatusActions(status).map((a) => [a.label, a.to]);
  assert.deepEqual(targets('new'), [['Watch', 'watching'], ['Dismiss', 'dismissed']]);
  assert.deepEqual(targets('seen'), [['Watch', 'watching'], ['Dismiss', 'dismissed']]);
  assert.deepEqual(targets('watching'), [['Stop watching', 'seen'], ['Dismiss', 'dismissed']]);
  assert.deepEqual(targets('dismissed'), [['Watch', 'watching'], ['Restore', 'seen']]);
  for (const status of ['new', 'seen', 'watching', 'dismissed']) {
    for (const action of oppStatusActions(status)) {
      assert.ok(action.message, `${status} -> ${action.to} needs a confirmation message`);
      assert.ok(OPEN_STATUSES.has(action.to) || action.to === 'dismissed', action.to);
    }
  }
});

test('dashboard#1: a date picked as today is never sent as a future timestamp', () => {
  // Every hour of the local day, including the mornings before local noon that used to fail.
  for (let hour = 0; hour < 24; hour++) {
    const now = new Date(2026, 10, 1, hour, 10, 0);
    const iso = dateInputToIso('2026-11-01', now);
    assert.ok(iso === null || Date.parse(iso) <= now.getTime(), `hour ${hour}: ${iso} is after ${now.toISOString()}`);
  }
  // today -> null: the field is omitted and the server stamps its own clock (immune to browser clock skew)
  assert.equal(dateInputToIso('2026-11-01', new Date(2026, 10, 1, 9, 10)), null);
  assert.equal(dateInputToIso('2026-11-01', new Date(2026, 10, 1, 23, 59)), null);
});

test('dashboard#1: past dates keep the local-noon stamp; future and malformed dates are rejected', () => {
  const now = new Date(2026, 10, 1, 9, 10);
  assert.equal(dateInputToIso('2026-10-31', now), new Date(2026, 9, 31, 12, 0, 0).toISOString());
  assert.equal(dateInputToIso('2025-02-28', now), new Date(2025, 1, 28, 12, 0, 0).toISOString());
  assert.equal(dateInputToIso('', now), null);
  assert.equal(dateInputToIso('   ', now), null);
  assert.throws(() => dateInputToIso('2026-11-02', now), /future/);
  assert.throws(() => dateInputToIso('2027-01-01', now), /future/);
  for (const bad of ['2026-13-01', '2026-02-30', '01/11/2026', '2026-1-1', 'today']) {
    assert.throws(() => dateInputToIso(bad, now), /invalid date/, bad);
  }
});

test('dashboard#2: the latest FX rate card includes the source attribution when the API sends one', () => {
  const attribution = 'Rates By Exchange Rate API (https://www.exchangerate-api.com)';
  const rows = fxRateRows({ base: 'USD', quote: 'MXN', rate: '18.4321', rate_date: '2026-11-01', source: 'exchangerate_api_open', attribution });
  assert.deepEqual(rows.filter(Boolean), [
    ['Pair', 'USD/MXN'], ['Rate', '18.4321'], ['Date', '2026-11-01'], ['Source', 'exchangerate_api_open'], ['Attribution', attribution],
  ]);
  const banxico = fxRateRows({ base: 'USD', quote: 'MXN', rate: '18.2', rate_date: '2026-11-01', source: 'banxico_fix', attribution: null });
  assert.equal(banxico.filter(Boolean).length, 4, 'no empty attribution row when the source needs none');
});

test('dashboard#2: the import estimate shows the attribution of the stored rate it used', () => {
  const fx = { source: 'frankfurter_ecb', rate_date: '2026-11-01', rate: '18.4', attribution: 'Estimate: ECB reference rates via Frankfurter' };
  assert.equal(importFxAttribution({ usd_mxn_rate: '18.4', fx_source: 'frankfurter_ecb (2026-11-01)' }, fx), fx.attribution);
  // a different stored rate than the one used: never attach someone else's attribution
  assert.equal(importFxAttribution({ usd_mxn_rate: '18.1', fx_source: 'exchangerate_api_open (2026-10-31)' }, fx), null);
  assert.equal(importFxAttribution({ usd_mxn_rate: '18.4', fx_source: 'provided' }, fx), null);
  assert.equal(importFxAttribution({ usd_mxn_rate: '18.4', fx_source: 'frankfurter_ecb (2026-11-01)' }, null), null);
  assert.equal(importFxAttribution({ usd_mxn_rate: '18.4', fx_source: 'manual (2026-11-01)' }, { source: 'manual', rate_date: '2026-11-01', attribution: null }), null);
});

test('dashboard#3: only a 401 makes the dashboard forget the saved token', () => {
  assert.equal(isAuthRejection({ status: 401 }), true);
  for (const status of [0, 403, 404, 429, 500, 502, 503, undefined]) {
    assert.equal(isAuthRejection({ status }), false, String(status));
  }
  assert.equal(isAuthRejection(new Error('boom')), false);
  assert.equal(isAuthRejection(null), false);
});

test('dashboard#5: an empty page past the end moves back to the last non-empty page', () => {
  // 26 rows, page 2 of 25 held only row 26, which was dismissed: 25 remain -> back to offset 0
  assert.equal(offsetAfterEmptyPage({ offset: 25, limit: 25, total: 25, count: 0 }), 0);
  assert.equal(offsetAfterEmptyPage({ offset: 100, limit: 50, total: 73, count: 0 }), 50);
  assert.equal(offsetAfterEmptyPage({ offset: 150, limit: 50, total: 100, count: 0 }), 50);
  // everything is gone -> first page, which shows the real empty state
  assert.equal(offsetAfterEmptyPage({ offset: 50, limit: 50, total: 0, count: 0 }), 0);
  // nothing to fix
  assert.equal(offsetAfterEmptyPage({ offset: 0, limit: 50, total: 0, count: 0 }), null);
  assert.equal(offsetAfterEmptyPage({ offset: 25, limit: 25, total: 26, count: 1 }), null);
  assert.equal(offsetAfterEmptyPage({ offset: 25, limit: 25, total: 30, count: 0 }), null); // inconsistent page: do not loop
  assert.equal(offsetAfterEmptyPage({ offset: 25, limit: 25, total: null, count: 0 }), null);
  // the new offset is always smaller, so reloading can never loop
  for (let total = 0; total < 130; total++) {
    for (const limit of [25, 50, 100]) {
      for (let offset = limit; offset <= 300; offset += limit) {
        const next = offsetAfterEmptyPage({ offset, limit, total, count: 0 });
        if (next === null) continue;
        assert.ok(next < offset && next >= 0 && next % limit === 0, `${offset}/${limit}/${total} -> ${next}`);
        assert.ok(total === 0 ? next === 0 : next < total, `${offset}/${limit}/${total} -> ${next}`);
      }
    }
  }
});
