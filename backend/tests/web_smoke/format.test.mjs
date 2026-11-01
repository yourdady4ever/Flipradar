// Unit tests for the dashboard's pure money/format helpers.  Run: node --test tests/web_smoke/format.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  axisTicks,
  bpToInput,
  centsToInput,
  fmtBp,
  fmtMoney,
  fmtMoneyAxis,
  fmtValue,
  niceStep,
  parseScaled,
  percentToBp,
  pesosToCents,
  safeHttpUrl,
  scaledToDecimal,
} from '../../flipradar/web/static/app.js';

test('pesosToCents uses exact string math', () => {
  assert.equal(pesosToCents('1234.5'), 123450);
  assert.equal(pesosToCents('1234.50'), 123450);
  assert.equal(pesosToCents('1,234.56'), 123456);
  assert.equal(pesosToCents('$99'), 9900);
  assert.equal(pesosToCents(' 0.07 '), 7);
  assert.equal(pesosToCents('.5'), 50);
  assert.equal(pesosToCents('1234.'), 123400);
  assert.equal(pesosToCents('19999.99'), 1999999); // 19999.99 * 100 = 1999998.9999999998 in floats
  assert.equal(pesosToCents('1.1'), 110); // 1.1 * 100 = 110.00000000000001 in floats
  assert.equal(pesosToCents('12999 MXN'), 1299900);
  assert.equal(pesosToCents(''), null);
  assert.equal(pesosToCents('   '), null);
});

test('pesosToCents rejects junk, negatives, excess decimals and ambiguous commas', () => {
  for (const bad of ['abc', '1.234', '-5', '1,5', '12,34.00', '.', '-', '1e5', '0x10', '1.2.3']) {
    assert.throws(() => pesosToCents(bad), Error, bad);
  }
  assert.throws(() => pesosToCents('99999999999999999'), /too large/);
  assert.equal(parseScaled('-5', 2, { allowNegative: true }), -500);
});

test('percentToBp', () => {
  assert.equal(percentToBp('16'), 1600);
  assert.equal(percentToBp('2.5'), 250);
  assert.equal(percentToBp('0.01'), 1);
  assert.equal(percentToBp(''), null);
  assert.throws(() => percentToBp('1.005'));
});

test('scaledToDecimal and input round trips', () => {
  assert.equal(scaledToDecimal(123405), '1234.05');
  assert.equal(scaledToDecimal(-5), '-0.05');
  assert.equal(scaledToDecimal(0), '0.00');
  assert.equal(scaledToDecimal(Number.MAX_SAFE_INTEGER), '90071992547409.91');
  assert.equal(centsToInput(123450), '1234.50');
  assert.equal(centsToInput(null), '');
  assert.equal(bpToInput(1600), '16.00');
  for (const c of [0, 1, 99, 100, 123456789, 1999999]) assert.equal(pesosToCents(centsToInput(c)), c);
});

test('fmtMoney formats es-MX MXN from integer centavos', () => {
  assert.equal(fmtMoney(1234567), '$12,345.67');
  assert.equal(fmtMoney(-150), '-$1.50');
  assert.equal(fmtMoney(0), '$0.00');
  assert.equal(fmtMoney(5), '$0.05');
  assert.equal(fmtMoney(null), '—');
  assert.equal(fmtMoney(12.5), '—'); // never format a non-integer as money
  assert.match(fmtMoney(1999, 'USD'), /19\.99/);
  assert.equal(fmtMoneyAxis(1500000), '$15,000');
});

test('fmtBp and fmtValue', () => {
  assert.equal(fmtBp(1600), '16.00%');
  assert.equal(fmtBp(-250), '-2.50%');
  assert.equal(fmtBp(null), '—');
  assert.equal(fmtValue('net_profit_cents', 20000), '$200.00');
  assert.equal(fmtValue('roi_bp', 3520), '35.20%');
  assert.equal(fmtValue('n', 12), '12');
  assert.equal(fmtValue('flag', true), 'yes');
  assert.equal(fmtValue('obj', { a: 1 }), '{"a":1}');
});

test('axis ticks are integers, include zero and cover the range', () => {
  assert.equal(niceStep(3), 5);
  assert.equal(niceStep(23), 25);
  assert.equal(niceStep(70), 100);
  for (const [lo, hi] of [[0, 0], [-12345, 987654], [0, 3], [-50000, -100], [100, 250000000]]) {
    const ax = axisTicks(lo, hi, 5);
    assert.ok(ax.ticks.includes(0), `zero in ${lo}..${hi}`);
    assert.ok(ax.min <= Math.min(0, lo) && ax.max >= Math.max(0, hi));
    for (const t of ax.ticks) assert.ok(Number.isInteger(t));
    assert.ok(ax.ticks.length <= 12);
  }
});

test('safeHttpUrl only allows http(s)', () => {
  assert.equal(safeHttpUrl('https://articulo.mercadolibre.com.mx/MLM-1'), 'https://articulo.mercadolibre.com.mx/MLM-1');
  assert.equal(safeHttpUrl('javascript:alert(1)'), null);
  assert.equal(safeHttpUrl('data:text/html,hi'), null);
  assert.equal(safeHttpUrl('/relative'), null);
  assert.equal(safeHttpUrl(null), null);
});
