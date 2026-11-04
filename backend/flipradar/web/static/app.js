/* FLIP RADAR web dashboard - vanilla ES2020 module, zero dependencies.
 *
 * Security rules followed throughout this file:
 *  - every server-provided string is rendered with textContent / text nodes (never innerHTML);
 *  - external links accept only http(s) URLs and open with rel="noopener noreferrer";
 *  - no inline scripts, inline event handlers or style attributes (strict CSP: default-src 'self').
 *    Bar widths use the CSSOM (element.style.width), which CSP does not restrict.
 *
 * Money rules: amounts are integer centavos end to end.  Parsing ("1234.5" -> 123450) and formatting
 * (123450 -> "$1,234.50") use integer/BigInt string arithmetic, never float multiplication.
 * Percentages are integer basis points (1600 bp = 16.00 %).
 */

const API_PREFIX = 'api/v1';
const TOKEN_KEY = 'flipradar.token';
const POLL_MS = 60_000;
const UNREAD_LIMIT = 50;

/* ============================================================================================
 * Pure helpers (exported so they can be unit-tested with Node; no DOM access)
 * ========================================================================================== */

const NF = {
  mxn: new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' }),
  mxn0: new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN', minimumFractionDigits: 0, maximumFractionDigits: 0 }),
  mxnCompact: new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN', notation: 'compact', maximumFractionDigits: 1 }),
  dec2: new Intl.NumberFormat('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  dec1: new Intl.NumberFormat('es-MX', { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
  dec4: new Intl.NumberFormat('es-MX', { maximumFractionDigits: 4 }),
  int: new Intl.NumberFormat('es-MX', { maximumFractionDigits: 0 }),
};
const currencyFormatters = new Map([['MXN', NF.mxn]]);

export function isInt(v) {
  return typeof v === 'number' && Number.isInteger(v);
}

/** Integer scaled value -> exact decimal string, e.g. (123405, 2) -> "1234.05", (-5, 2) -> "-0.05". */
export function scaledToDecimal(value, decimals = 2) {
  const n = BigInt(value);
  const neg = n < 0n;
  const abs = neg ? -n : n;
  const base = 10n ** BigInt(decimals);
  const intPart = (abs / base).toString();
  if (decimals === 0) return `${neg ? '-' : ''}${intPart}`;
  const frac = (abs % base).toString().padStart(decimals, '0');
  return `${neg ? '-' : ''}${intPart}.${frac}`;
}

/**
 * Parse a human decimal string into an integer scaled by 10^decimals using string math only.
 * "1234.5" -> 123450, "1,234.50" -> 123450, "$99" -> 9900, "" -> null.  Throws Error on bad input.
 */
export function parseScaled(raw, decimals = 2, { allowNegative = false } = {}) {
  let text = String(raw ?? '').trim().replace(/\s+/g, '');
  text = text.replace(/^(-?)\$/, '$1').replace(/(mxn|usd)$/i, '');
  if (text === '') return null;
  if (text.includes(',')) {
    if (!/^-?\d{1,3}(,\d{3})+(\.\d*)?$/.test(text)) {
      throw new Error('use "." for decimals and "," only as a thousands separator');
    }
    text = text.replace(/,/g, '');
  }
  const m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(text);
  if (!m || (m[2] === '' && !m[3])) throw new Error('not a valid number');
  if (m[1] && !allowNegative) throw new Error('must not be negative');
  const frac = m[3] || '';
  if (frac.length > decimals) throw new Error(`use at most ${decimals} decimal places`);
  let v = BigInt((m[2] || '0') + frac.padEnd(decimals, '0'));
  if (m[1]) v = -v;
  const max = BigInt(Number.MAX_SAFE_INTEGER);
  if (v > max || v < -max) throw new Error('number too large');
  return Number(v);
}

/** Pesos string -> integer centavos (exact). */
export const pesosToCents = (raw) => parseScaled(raw, 2);
/** Percent string -> integer basis points (exact): "16" -> 1600, "2.5" -> 250. */
export const percentToBp = (raw) => parseScaled(raw, 2);
/** Integer centavos -> plain input value "1234.50". */
export const centsToInput = (cents) => (isInt(cents) ? scaledToDecimal(cents, 2) : '');
/** Integer bp -> plain input value "16.00". */
export const bpToInput = (bp) => (isInt(bp) ? scaledToDecimal(bp, 2) : '');

function currencyFormatter(currency) {
  const code = String(currency || 'MXN').toUpperCase();
  if (!currencyFormatters.has(code)) {
    let nf = null;
    try {
      nf = new Intl.NumberFormat('es-MX', { style: 'currency', currency: code });
    } catch {
      nf = null;
    }
    currencyFormatters.set(code, nf);
  }
  return currencyFormatters.get(code);
}

/** Integer centavos -> "$12,345.67" (es-MX).  Non-integers render as an em dash, never a guess. */
export function fmtMoney(cents, currency = 'MXN') {
  if (!isInt(cents)) return '—';
  const dec = scaledToDecimal(cents, 2);
  const nf = currencyFormatter(currency);
  return nf ? nf.format(dec) : `${dec} ${currency}`;
}

/** Integer centavos -> whole-peso label (used on chart axes whose ticks are whole pesos). */
export function fmtMoneyAxis(cents, compact = false) {
  if (!isInt(cents)) return '';
  const dec = scaledToDecimal(cents, 2);
  return compact ? NF.mxnCompact.format(dec) : NF.mxn0.format(dec);
}

/** Integer basis points -> "16.00%". */
export function fmtBp(bp) {
  if (!isInt(bp)) return '—';
  return `${NF.dec2.format(scaledToDecimal(bp, 2))}%`;
}

export function fmtScore(score) {
  return typeof score === 'number' && Number.isFinite(score) ? NF.dec1.format(score) : '—';
}

export function fmtInt(n) {
  return isInt(n) ? NF.int.format(n) : '—';
}

function parseDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(iso) {
  const d = parseDate(iso);
  return d ? d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
}

export function fmtDateTime(iso) {
  const d = parseDate(iso);
  return d ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—';
}

/** "YYYY-MM-DD" bucket start -> local short date (parsed as a calendar date, no UTC shift). */
export function fmtPeriod(period, withYear = false) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(period || ''));
  if (!m) return String(period || '');
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString(undefined, withYear ? { year: 'numeric', month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric' });
}

export function fmtMinutes(min) {
  if (!isInt(min)) return '—';
  if (min % 1440 === 0) return `${min / 1440} d`;
  if (min % 60 === 0) return `${min / 60} h`;
  return `${min} min`;
}

/** Generic value formatter for evidence/facts dictionaries (key suffix decides money/bp). */
export function fmtValue(key, v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    if (Number.isInteger(v)) {
      if (/_cents$/.test(key)) return fmtMoney(v);
      if (/_bp$/.test(key)) return fmtBp(v);
      return NF.int.format(v);
    }
    return Number.isFinite(v) ? NF.dec4.format(v) : String(v);
  }
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export function humanizeKey(key) {
  return String(key)
    .replace(/_cents$/, ' (MXN)')
    .replace(/_bp$/, ' (%)')
    .replace(/_/g, ' ')
    .replace(/^./, (c) => c.toUpperCase());
}

/** Smallest "nice" integer step (1, 2, 2.5, 5 x 10^k; integers only) that is >= rawStep. */
export function niceStep(rawStep) {
  if (!(rawStep > 1)) return 1;
  let pow = 1;
  while (pow * 10 <= rawStep) pow *= 10;
  for (const mult of [1, 2, 2.5, 5, 10]) {
    const step = mult * pow;
    if (Number.isInteger(step) && step >= rawStep) return step;
  }
  return pow * 10;
}

/** Integer-only axis ticks over centavo values; always includes 0.  Steps are whole pesos when the range allows. */
export function axisTicks(minCents, maxCents, count = 5) {
  const lo = Math.min(0, minCents);
  let hi = Math.max(0, maxCents);
  if (lo === hi) hi = lo + 10000;
  const range = hi - lo;
  let step;
  if (range >= 500) step = niceStep(Math.ceil(range / 100 / count)) * 100;
  else step = niceStep(Math.ceil(range / count));
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end; v += step) ticks.push(v);
  return { min: start, max: end, step, ticks };
}

export function safeHttpUrl(u) {
  if (typeof u !== 'string' || !u) return null;
  try {
    const url = new URL(u);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

/**
 * <input type=date> value -> ISO datetime to send, or null to omit the field so the server stamps its own clock.
 * Today -> null: any fixed time of day would lie in the future until then, and analytics only counts rows dated
 * <= now.  Earlier days -> local noon (no day shift in any time zone).  Future days are rejected.
 */
export function dateInputToIso(raw, now = new Date()) {
  const text = String(raw ?? '').trim();
  if (!text) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  const [y, mo, d] = m ? [Number(m[1]), Number(m[2]) - 1, Number(m[3])] : [];
  const day = m ? new Date(y, mo, d, 12, 0, 0) : null;
  if (!day || day.getFullYear() !== y || day.getMonth() !== mo || day.getDate() !== d) throw new Error('invalid date');
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12, 0, 0);
  if (day.getTime() === today.getTime()) return null;
  if (day > today) throw new Error('must not be in the future');
  return day.toISOString();
}

/** Only a 401 means the token itself was rejected; network errors, 429 and 5xx are transient. */
export function isAuthRejection(err) {
  return Boolean(err) && err.status === 401;
}

/**
 * Status changes the opportunity drawer offers.  An expired one gets none (a later recompute reopens it).  A bought
 * one gets only "Restore to new", to undo a "Mark bought" made by mistake: the server refuses it (409) while an
 * inventory item still references the opportunity, so a real purchase never counts as open potential profit again.
 * Watch / Dismiss stay hidden for it: undoing them would also leave it 'seen'.
 */
export function oppStatusActions(status) {
  const watch = { label: 'Watch', to: 'watching', message: 'Watching this opportunity.', primary: true };
  const dismiss = { label: 'Dismiss', to: 'dismissed', message: 'Opportunity dismissed.' };
  switch (status) {
    case 'new':
    case 'seen':
      return [watch, dismiss];
    case 'watching':
      return [{ label: 'Stop watching', to: 'seen', message: 'No longer watching.' }, dismiss];
    case 'dismissed':
      return [watch, { label: 'Restore', to: 'seen', message: 'Opportunity restored.' }];
    case 'bought':
      return [{ label: 'Restore to new', to: 'new', message: 'Opportunity restored to new (no longer marked bought).' }];
    default:
      return [];
  }
}

/** Error text for a failed status change.  A 409 on a bought opportunity means its purchase is still in Inventory. */
export function oppStatusErrorText(err, fromStatus) {
  const msg = err && err.message ? err.message : String(err);
  if (!err || err.status !== 409 || fromStatus !== 'bought') return msg;
  return `Not restored: ${msg}. If it was marked bought by mistake, delete its inventory item first, then restore it.`;
}

/**
 * A reload returned an empty page past the end (rows were dismissed, sold or deleted meanwhile): the offset of the
 * last page that still has rows (0 when nothing is left), else null.  The result is always below `offset`.
 */
export function offsetAfterEmptyPage({ offset, limit, total, count }) {
  if (count > 0 || !(offset > 0) || !(limit > 0) || !isInt(total) || offset < total) return null;
  return total > 0 ? Math.floor((total - 1) / limit) * limit : 0;
}

/** Rows describing a stored FX rate.  The source's attribution must be shown with the rate (its terms of use). */
export function fxRateRows(fx) {
  return [
    ['Pair', `${fx.base}/${fx.quote}`],
    ['Rate', fx.rate],
    ['Date', fx.rate_date],
    ['Source', fx.source],
    fx.attribution ? ['Attribution', fx.attribution] : null,
  ];
}

/** Attribution of the stored rate an import estimate used (its fx_source is "<source> (<date>)"), else null. */
export function importFxAttribution(out, fx) {
  if (!out || !fx || !fx.attribution) return null;
  return out.fx_source === `${fx.source} (${fx.rate_date})` ? fx.attribution : null;
}

/** An import estimate whose duties can be used as a cost.  A formal import (customs broker) is never estimated:
 *  the server then sends formal_import_required and null duties. */
export function importDutiesEstimated(out) {
  return Boolean(out) && out.formal_import_required !== true && isInt(out.duties_mxn_cents);
}

/** FLIP RADAR product categories (backend normalize/vocab.CATEGORIES, same order). */
export const CATEGORIES = ['phone', 'console', 'handheld_pc', 'laptop', 'tablet', 'audio', 'smartwatch', 'camera', 'drone', 'gpu', 'tool', 'toy', 'sneakers', 'ereader', 'other'];
const CATEGORY_LABELS = { handheld_pc: 'Handheld PC', gpu: 'GPU', ereader: 'E-reader' };

export function categoryLabel(category) {
  const key = String(category ?? '');
  if (CATEGORY_LABELS[key]) return CATEGORY_LABELS[key];
  const text = key.replace(/_/g, ' ');
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
}

/** Stored settings.category_fee_profiles -> editor rows, known categories first in CATEGORIES order. */
export function categoryFeeRows(mapping) {
  if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) return [];
  const rank = (c) => {
    const i = CATEGORIES.indexOf(c);
    return i === -1 ? CATEGORIES.length : i;
  };
  return Object.keys(mapping)
    .sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0))
    .map((category) => {
      const m = mapping[category] && typeof mapping[category] === 'object' ? mapping[category] : {};
      return { category, new: typeof m.new === 'string' ? m.new : '', used: typeof m.used === 'string' ? m.used : '' };
    });
}

/**
 * Editor rows -> settings.category_fee_profiles, e.g. {console: {new: 'ml_clasica_1_2kg'}}.  A condition left empty
 * is omitted (the default profile for that condition applies); a completely empty row is ignored.  Throws an Error
 * whose `row` is the 0-based index of the offending row and `field` the input to fix ('category' or 'new').
 */
export function buildCategoryFeeProfiles(rows) {
  const out = {};
  (rows || []).forEach((r, i) => {
    const category = String((r && r.category) || '').trim();
    const neu = String((r && r.new) || '').trim();
    const used = String((r && r.used) || '').trim();
    const fail = (message, fieldName) => Object.assign(new Error(`Category fee profiles, row ${i + 1}: ${message}`), { row: i, field: fieldName });
    if (!category) {
      if (!neu && !used) return;
      throw fail('choose a category', 'category');
    }
    if (Object.prototype.hasOwnProperty.call(out, category)) throw fail(`${categoryLabel(category)} is already mapped in an earlier row`, 'category');
    if (!neu && !used) throw fail(`choose a fee profile for new or used ${categoryLabel(category)} units, or remove the row`, 'new');
    out[category] = {};
    if (neu) out[category].new = neu;
    if (used) out[category].used = used;
  });
  return out;
}

/** Normalization notes of a listing detail (attributes.normalization_notes): an array (possibly empty), or null
 *  when the object carries no attributes at all (a list row: the detail must be fetched). */
export function listingNotes(listing) {
  if (!listing || !listing.attributes || typeof listing.attributes !== 'object') return null;
  const notes = listing.attributes.normalization_notes;
  if (!Array.isArray(notes)) return [];
  return notes.filter((n) => typeof n === 'string' && n.trim()).map((n) => n.trim());
}

/** 'unassigned' (no product), 'manual' (assigned from the review queue; can be undone) or 'automatic'. */
export function assignmentState(listing) {
  if (!listing || !isInt(listing.product_id)) return 'unassigned';
  return listing.match_method === 'manual' ? 'manual' : 'automatic';
}

/** Initial product search for a listing: its extracted brand + model, else nothing (the user types). */
export function suggestedProductQuery(listing) {
  if (!listing) return '';
  return [listing.brand, listing.model].filter((v) => typeof v === 'string' && v.trim()).map((v) => v.trim()).join(' ').slice(0, 200);
}

// Opportunity facts worth surfacing next to the cost breakdown: which commission was used and whether your own
// sales were used as evidence.  Matched by key prefix so new keys of the same family render without a client change.
// (The fee profile itself is already shown with the buy listing.)
const FACT_GROUPS = [
  { title: 'Marketplace commission', test: (k) => /^(commission|category_|marketplace_(fee|commission)|listing_type)/.test(k) || /(^|_)category_(fee|commission)/.test(k) },
  { title: 'Your own sales as evidence', test: (k) => /(^|_)own_sales?(_|$)/.test(k) },
];

/** Facts grouped for display: [{title, rows: [[label, formatted value]]}]; groups without facts are left out. */
export function factHighlights(facts) {
  if (!facts || typeof facts !== 'object') return [];
  const entries = Object.entries(facts).filter(([, v]) => v !== null && v !== undefined && v !== '');
  return FACT_GROUPS.map((g) => ({
    title: g.title,
    rows: entries.filter(([k]) => g.test(k)).map(([k, v]) => [humanizeKey(k), fmtValue(k, v)]),
  })).filter((g) => g.rows.length);
}

/* ============================================================================================
 * DOM helpers
 * ========================================================================================== */

const SVG_NS = 'http://www.w3.org/2000/svg';
const PROP_KEYS = new Set(['value', 'checked', 'disabled', 'selected', 'hidden', 'required', 'multiple', 'readOnly', 'open', 'defaultChecked']);
let uidCounter = 0;
const nextId = (prefix = 'fr') => `${prefix}-${++uidCounter}`;
const $ = (sel, root = document) => root.querySelector(sel);

function appendAll(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === '') continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Replace an element's children; null/false/'' entries are skipped (Element.replaceChildren would print "null"). */
function setChildren(el, ...children) {
  el.replaceChildren();
  return appendAll(el, children);
}

function applyProps(el, props) {
  if (!props) return;
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null) continue;
    if (k === 'class') {
      const cls = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
      if (cls) el.setAttribute('class', cls);
    } else if (k === 'text') {
      el.textContent = String(v);
    } else if (k === 'on') {
      for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    } else if (k === 'dataset') {
      for (const [dk, dv] of Object.entries(v)) if (dv !== null && dv !== undefined) el.dataset[dk] = String(dv);
    } else if (k === 'style' || /^on/i.test(k)) {
      throw new Error(`refusing to set "${k}" (CSP / XSS policy)`);
    } else if (PROP_KEYS.has(k)) {
      el[k] = v;
    } else if (v === false) {
      continue;
    } else {
      el.setAttribute(k, v === true ? '' : String(v));
    }
  }
}

/** Create an HTML element.  Children: strings (text nodes), Nodes, arrays, null/false (skipped). */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  applyProps(el, props);
  return appendAll(el, children);
}

/** Create an SVG element (attributes only, never style). */
function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'on') for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === 'style' || /^on/i.test(k)) throw new Error(`refusing to set "${k}"`);
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function extLink(url, text) {
  const href = safeHttpUrl(url);
  const label = text || (href ? hostOf(href) : '—');
  if (!href) return h('span', { class: 'muted', title: 'No usable http(s) link' }, label);
  return h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, label, h('span', { class: 'sr-only' }, ' (opens in a new tab)'));
}

function badge(text, kind = '', title) {
  return h('span', { class: ['badge', kind && `badge-${kind}`], title }, text);
}

const demoBadge = () => badge('DEMO', 'demo', 'Synthetic demo data - not real market data');

const RISK_META = {
  none: ['✓', 'No risk flags', 'good'],
  info: ['ℹ', 'Info', 'info'],
  warning: ['▲', 'Warning', 'warn'],
  critical: ['✖', 'Critical', 'crit'],
};

function riskBadge(level) {
  const [icon, label, kind] = RISK_META[level] || ['?', String(level || 'unknown'), ''];
  return badge(`${icon} ${label}`, kind, `Risk level: ${label}`);
}

function severityBadge(sev) {
  return riskBadge(sev === 'info' || sev === 'warning' || sev === 'critical' ? sev : 'none');
}

function confidenceBadge(c, short = false) {
  const kind = { high: 'good', medium: 'info', low: 'warn' }[c] || '';
  if (short) return badge(c || 'n/a', kind, `Confidence: ${c || 'n/a'}`);
  return badge(c ? `${c} confidence` : 'confidence n/a', kind);
}

function demandBadge(level, short = false) {
  const kind = { high: 'good', medium: 'info', low: 'warn' }[level] || '';
  const unknown = level === 'unknown' || !level;
  const title = unknown ? 'Not enough observed evidence to estimate demand' : `Demand: ${level}`;
  if (short) return badge(unknown ? 'unknown' : level, kind, title);
  return badge(unknown ? 'demand unknown' : `${level} demand`, kind, title);
}

function statusBadge(status) {
  const kind = { new: 'accent', watching: 'info', bought: 'good', dismissed: '', expired: '', sold: 'good', listed: 'info', in_stock: 'info', written_off: 'crit', returned: 'warn', ok: 'good', error: 'crit', running: 'info', skipped: 'warn' }[status] || '';
  return badge(String(status || '—').replace(/_/g, ' '), kind);
}

function scorePill(score) {
  const cls = score >= 70 ? 'score-high' : score >= 45 ? 'score-mid' : 'score-low';
  return h('span', { class: ['score-pill', cls], title: 'Score 0-100' }, fmtScore(score));
}

function moneySigned(cents) {
  return h('span', { class: isInt(cents) ? (cents > 0 ? 'pos' : cents < 0 ? 'neg' : null) : null }, fmtMoney(cents));
}

function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).map(([k, v]) => [h('dt', null, k), h('dd', null, v === null || v === undefined || v === '' ? '—' : v)]));
}

function tiles(items) {
  return h('div', { class: 'tiles' }, items.filter(Boolean).map(([label, value, note]) =>
    h('div', { class: 'tile' }, h('div', { class: 'tile-label' }, label), h('div', { class: 'tile-value' }, value), note ? h('div', { class: 'tile-note' }, note) : null)));
}

/** columns: [{label, cell(row) -> node|string, num?, cls?}] */
function table(columns, rows, { caption, rowProps } = {}) {
  const head = h('thead', null, h('tr', null, columns.map((c) => h('th', { scope: 'col', class: c.num ? 'num' : null }, c.label))));
  const body = h('tbody', null, rows.map((r) => h('tr', rowProps ? rowProps(r) : null, columns.map((c) => h('td', { class: [c.num && 'num', c.cls] }, c.cell(r))))));
  return h('div', { class: 'table-wrap', tabindex: '0', role: 'region', 'aria-label': caption || 'Table' },
    h('table', { class: 'table' }, caption ? h('caption', { class: 'sr-only' }, caption) : null, head, body));
}

function hbars(entries, formatValue = fmtInt) {
  const max = Math.max(1, ...entries.map(([, v]) => Math.abs(Number(v) || 0)));
  return h('ul', { class: 'hbars' }, entries.map(([label, value]) => {
    const fill = h('span', { class: ['bar-fill', value < 0 && 'neg'] });
    fill.style.width = `${((Math.abs(Number(value) || 0) / max) * 100).toFixed(1)}%`;
    return h('li', null, h('span', { class: 'hbar-label' }, label), h('span', { class: 'bar', 'aria-hidden': 'true' }, fill), h('span', { class: 'hbar-value' }, formatValue(value)));
  }));
}

function viewHeader(title, intro, actions = []) {
  return h('header', { class: 'view-head' },
    h('div', null, h('h1', { tabindex: '-1' }, title), intro ? h('p', { class: 'muted intro' }, intro) : null),
    actions.length ? h('div', { class: 'view-actions' }, actions) : null);
}

function card(title, ...body) {
  return h('section', { class: 'card' }, title ? h('h2', null, title) : null, ...body);
}

function section(title, ...body) {
  return h('section', { class: 'section' }, h('h3', null, title), ...body);
}

function errorBox(err) {
  return h('div', { class: 'error-box', role: 'alert' }, err && err.message ? err.message : String(err));
}

function field(label, input, { hint, wide } = {}) {
  if (!input.id) input.id = nextId('in');
  const wrap = h('div', { class: ['field', wide && 'field-wide'] }, h('label', { for: input.id }, label), input);
  if (hint) {
    const hid = nextId('hint');
    input.setAttribute('aria-describedby', hid);
    wrap.append(h('small', { id: hid, class: 'hint' }, hint));
  }
  return wrap;
}

function checkField(label, input, hint) {
  if (!input.id) input.id = nextId('cb');
  const wrap = h('div', { class: 'field field-check' }, input, h('label', { for: input.id }, label));
  if (hint) {
    const hid = nextId('hint');
    input.setAttribute('aria-describedby', hid);
    wrap.append(h('small', { id: hid, class: 'hint' }, hint));
  }
  return wrap;
}

const textInput = (name, props = {}) => h('input', { type: 'text', name, autocomplete: 'off', ...props });
const moneyInput = (name, props = {}) => h('input', { type: 'text', name, inputmode: 'decimal', autocomplete: 'off', placeholder: '0.00', ...props });
const pctInput = (name, props = {}) => h('input', { type: 'text', name, inputmode: 'decimal', autocomplete: 'off', placeholder: '0', ...props });
const numInput = (name, props = {}) => h('input', { type: 'number', name, inputmode: 'numeric', ...props });
const checkbox = (name, checked = false, props = {}) => h('input', { type: 'checkbox', name, checked, ...props });

function select(name, options, value, props = {}) {
  return h('select', { name, ...props }, options.map(([v, label, extra = {}]) => h('option', { value: v, selected: String(v) === String(value ?? ''), ...extra }, label)));
}

function feeProfileOptions(profiles, emptyLabel) {
  const opts = emptyLabel ? [['', emptyLabel]] : [];
  for (const p of profiles || []) opts.push([p.key, `${p.name} (${p.key})`]);
  return opts;
}

/* ============================================================================================
 * Toasts, busy state, form reading
 * ========================================================================================== */

function toast(content, kind = 'info', { timeout } = {}) {
  const region = $('#toasts');
  if (!region) return;
  const el = h('div', { class: ['toast', `toast-${kind}`] },
    h('span', { class: 'toast-msg' }, kind === 'error' ? h('span', { class: 'sr-only' }, 'Error: ') : null, content),
    h('button', { type: 'button', class: 'toast-close', 'aria-label': 'Dismiss message', on: { click: () => el.remove() } }, '×'));
  region.append(el);
  while (region.children.length > 5) region.firstElementChild.remove();
  setTimeout(() => el.remove(), timeout ?? (kind === 'error' ? 12000 : 6000));
}

class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

class InputError extends Error {
  constructor(message, fieldName) {
    super(message);
    this.name = 'InputError';
    this.field = fieldName;
  }
}

function reportError(err, context) {
  if (err instanceof ApiError && err.status === 401) return; // handled by the session logic
  const msg = err && err.message ? err.message : String(err);
  toast(context ? `${context}: ${msg}` : msg, 'error');
}

/** Run fn while marking btn busy.  Uses aria-disabled (not the disabled property) so keyboard focus stays
 *  on the button, and ignores re-entrant clicks/submits while the request is in flight. */
async function busy(btn, fn) {
  if (btn) {
    if (btn.dataset.busy) return undefined;
    btn.dataset.busy = '1';
    btn.setAttribute('aria-busy', 'true');
    btn.setAttribute('aria-disabled', 'true');
  }
  try {
    return await fn();
  } finally {
    if (btn) {
      delete btn.dataset.busy;
      btn.removeAttribute('aria-busy');
      btn.removeAttribute('aria-disabled');
    }
  }
}

function button(label, onClick, { kind = '', small = false, type = 'button', title, attrs } = {}) {
  const b = h('button', { type, class: ['btn', kind && `btn-${kind}`, small && 'btn-sm'], title, ...(attrs || {}) }, label);
  if (onClick) {
    b.addEventListener('click', (ev) => {
      busy(b, async () => {
        try {
          await onClick(ev);
        } catch (err) {
          reportError(err);
        }
      });
    });
  }
  return b;
}

function onSubmit(form, handler) {
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (form.dataset.busy) return;
    for (const el of form.querySelectorAll('[aria-invalid]')) el.removeAttribute('aria-invalid');
    const btn = ev.submitter || form.querySelector('[type="submit"]');
    form.dataset.busy = '1';
    busy(btn, async () => {
      try {
        await handler(ev);
      } catch (err) {
        if (err instanceof InputError) toast(err.message, 'error');
        else reportError(err);
      }
    }).finally(() => {
      delete form.dataset.busy;
    });
  });
}

function inputOf(form, name) {
  const el = form.elements.namedItem(name);
  if (!el) throw new Error(`form field ${name} missing`);
  return el;
}

function invalid(form, name, message) {
  const el = form.elements.namedItem(name);
  if (el && el.setAttribute) {
    el.setAttribute('aria-invalid', 'true');
    el.focus();
  }
  return new InputError(message, name);
}

function labelOf(form, name) {
  const el = form.elements.namedItem(name);
  const lbl = el && el.id ? form.querySelector(`label[for="${el.id}"]`) : null;
  return lbl ? lbl.textContent.trim() : name;
}

/** Read a pesos field -> integer centavos (or null when empty and not required). */
function readMoney(form, name, { required = false } = {}) {
  const raw = inputOf(form, name).value;
  try {
    const v = pesosToCents(raw);
    if (v === null && required) throw new Error('required');
    return v;
  } catch (e) {
    throw invalid(form, name, `${labelOf(form, name)}: ${e.message}`);
  }
}

function readPct(form, name, { required = false, max } = {}) {
  const raw = inputOf(form, name).value;
  try {
    const v = percentToBp(raw);
    if (v === null && required) throw new Error('required');
    if (v !== null && max !== undefined && v > max) throw new Error(`must be at most ${scaledToDecimal(max, 2)}%`);
    return v;
  } catch (e) {
    throw invalid(form, name, `${labelOf(form, name)}: ${e.message}`);
  }
}

function readInt(form, name, { required = false, min, max } = {}) {
  const raw = String(inputOf(form, name).value).trim();
  if (raw === '') {
    if (required) throw invalid(form, name, `${labelOf(form, name)}: required`);
    return null;
  }
  if (!/^-?\d+$/.test(raw)) throw invalid(form, name, `${labelOf(form, name)}: whole number expected`);
  const v = Number(raw);
  if ((min !== undefined && v < min) || (max !== undefined && v > max)) {
    throw invalid(form, name, `${labelOf(form, name)}: must be between ${min ?? '-∞'} and ${max ?? '∞'}`);
  }
  return v;
}

function readFloat(form, name, { min, max } = {}) {
  const raw = String(inputOf(form, name).value).trim();
  if (raw === '') return null;
  if (!/^\d+(\.\d+)?$/.test(raw)) throw invalid(form, name, `${labelOf(form, name)}: number expected`);
  const v = Number(raw);
  if ((min !== undefined && v < min) || (max !== undefined && v > max)) {
    throw invalid(form, name, `${labelOf(form, name)}: must be between ${min} and ${max}`);
  }
  return v;
}

function readStr(form, name, { required = false } = {}) {
  const v = String(inputOf(form, name).value).trim();
  if (!v && required) throw invalid(form, name, `${labelOf(form, name)}: required`);
  return v || null;
}

const readBool = (form, name) => Boolean(inputOf(form, name).checked);

/** <input type=date> value -> ISO datetime, or null (empty or today: the server uses its clock).  See dateInputToIso. */
function readDate(form, name) {
  try {
    return dateInputToIso(inputOf(form, name).value);
  } catch (e) {
    throw invalid(form, name, `${labelOf(form, name)}: ${e.message}`);
  }
}

/* ============================================================================================
 * Session storage + API client
 * ========================================================================================== */

const store = {
  get(k) {
    try {
      return window.sessionStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k, v) {
    try {
      window.sessionStorage.setItem(k, v);
    } catch {
      /* storage blocked: the token then lives in memory for this page only */
    }
  },
  del(k) {
    try {
      window.sessionStorage.removeItem(k);
    } catch {
      /* ignore */
    }
  },
};

const state = {
  token: null,
  authed: false,
  authRequired: true,
  tokenName: null,
  health: null,
  feeProfiles: null,
  sources: null,
  maxNotifId: null,
  pollTimer: null,
};

function formatDetail(detail, fallback) {
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      const loc = Array.isArray(d && d.loc) ? d.loc.filter((p) => p !== 'body' && p !== 'query' && p !== 'path').join('.') : '';
      return `${loc ? `${loc}: ` : ''}${d && d.msg ? d.msg : JSON.stringify(d)}`;
    }).join('; ');
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return fallback;
}

async function api(path, { method = 'GET', query, json, form, on401 = 'logout' } = {}) {
  const url = new URL(`${API_PREFIX}${path}`, document.baseURI);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
    }
  }
  const headers = { Accept: 'application/json' };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body;
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(json);
  } else if (form) {
    body = form; // multipart boundary is set by the browser
  }
  let res;
  try {
    res = await fetch(url, { method, headers, body, cache: 'no-store', credentials: 'same-origin' });
  } catch {
    throw new ApiError(0, 'Cannot reach the FLIP RADAR server (network error).');
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (res.status === 401 && on401 === 'logout') {
    sessionExpired();
    throw new ApiError(401, 'Not authorized.');
  }
  if (!res.ok) {
    throw new ApiError(res.status, formatDetail(data && data.detail, `HTTP ${res.status} ${res.statusText || ''}`.trim()), data && data.detail);
  }
  return data;
}

async function getFeeProfiles(force = false) {
  if (!state.feeProfiles || force) state.feeProfiles = await api('/fee-profiles');
  return state.feeProfiles;
}

async function getSources(force = false) {
  if (!state.sources || force) state.sources = await api('/sources');
  return state.sources;
}

/* ============================================================================================
 * Shell: login, logout, nav, health, unread badge
 * ========================================================================================== */

function setLoginMessage(message) {
  const msg = $('#login-message');
  msg.hidden = !message;
  msg.textContent = message || '';
}

/** canRetry: a token is still saved in this tab (the last attempt failed for a transient reason). */
function showLogin(message, { canRetry = false } = {}) {
  state.authed = false;
  stopPolling();
  toggleNav(false);
  if (current && current.view && current.view.destroy) current.view.destroy();
  current = null;
  $('#main').replaceChildren();
  $('#app').hidden = true;
  $('#boot').hidden = true;
  $('#login-view').hidden = false;
  setLoginMessage(message);
  document.title = 'Sign in · FLIP RADAR';
  const retry = $('#login-retry');
  retry.hidden = !canRetry;
  const input = $('#login-token');
  input.value = '';
  (canRetry ? retry : input).focus();
}

function showApp(check) {
  state.authed = true;
  state.authRequired = Boolean(check.auth_required);
  state.tokenName = check.token_name || null;
  state.maxNotifId = null;
  $('#login-view').hidden = true;
  $('#boot').hidden = true;
  $('#app').hidden = false;
  renderServerInfo();
  startPolling();
  if (!location.hash.startsWith('#/')) history.replaceState(null, '', '#/opportunities');
  route();
}

function sessionExpired() {
  if (!state.authed) return;
  store.del(TOKEN_KEY);
  state.token = null;
  showLogin('The server rejected the token (expired, revoked or wrong). Please sign in again.');
}

function logout() {
  store.del(TOKEN_KEY);
  state.token = null;
  state.feeProfiles = null;
  state.sources = null;
  showLogin('Signed out. The token was removed from this tab.');
}

async function loadHealth() {
  const el = $('#login-server');
  try {
    const res = await fetch(new URL('health', document.baseURI), { headers: { Accept: 'application/json' }, cache: 'no-store' });
    const data = await res.json();
    state.health = data;
    el.textContent = `Server v${data.version} · API v${data.api_version} · database: ${data.database} · status: ${data.status}`;
  } catch {
    state.health = null;
    el.textContent = 'Server health endpoint (/health) is unreachable.';
  }
  renderServerInfo();
}

function renderServerInfo() {
  const box = $('#server-info');
  if (!box) return;
  const hl = state.health;
  setChildren(box, 
    hl ? h('span', { title: `API v${hl.api_version} · database: ${hl.database}` }, `v${hl.version}`) : h('span', null, 'server ?'),
    hl && hl.status !== 'ok' ? badge('degraded', 'warn', 'The server reports a degraded status') : null,
    state.tokenName ? h('span', { class: 'token-name' }, `· ${state.tokenName}`) : null,
    state.authed && !state.authRequired ? h('span', { class: 'token-name' }, '· auth disabled') : null);
}

function toggleNav(force) {
  const nav = $('#sidenav');
  if (!nav) return;
  const open = force === undefined ? !nav.classList.contains('open') : Boolean(force);
  nav.classList.toggle('open', open);
  $('#nav-toggle').setAttribute('aria-expanded', String(open));
}

function setUnread(n, capped) {
  const el = $('#unread-badge');
  if (!el) return;
  if (!n) {
    el.hidden = true;
    el.replaceChildren();
    return;
  }
  el.hidden = false;
  setChildren(el, `${n}${capped ? '+' : ''}`, h('span', { class: 'sr-only' }, ' unread'));
}

async function refreshUnread() {
  if (!state.authed) return;
  try {
    const list = await api('/notifications', { query: { unread_only: 'true', limit: UNREAD_LIMIT } });
    if (!Array.isArray(list)) return;
    const maxId = list.reduce((m, x) => Math.max(m, x.id || 0), 0);
    if (state.maxNotifId !== null && maxId > state.maxNotifId) {
      const fresh = list.filter((x) => x.id > state.maxNotifId).length;
      if (fresh) toast([`${fresh} new notification${fresh > 1 ? 's' : ''}. `, h('a', { href: '#/notifications' }, 'Open notifications')], 'info');
    }
    state.maxNotifId = Math.max(state.maxNotifId ?? 0, maxId);
    setUnread(list.length, list.length >= UNREAD_LIMIT);
  } catch {
    /* polling is best effort; 401 is handled by api() */
  }
}

function startPolling() {
  stopPolling();
  refreshUnread();
  state.pollTimer = setInterval(refreshUnread, POLL_MS);
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

/* ============================================================================================
 * Router
 * ========================================================================================== */

const VIEWS = {
  opportunities: { title: 'Opportunities', create: (m, r) => OpportunitiesView(m, r) },
  review: { title: 'Review queue', create: (m, r) => ReviewView(m, r) },
  calculator: { title: 'Calculator', create: (m) => CalculatorView(m) },
  inventory: { title: 'Inventory', create: (m, r) => InventoryView(m, r) },
  analytics: { title: 'Analytics', create: (m) => AnalyticsView(m) },
  sources: { title: 'Searches & Sources', create: (m) => SourcesView(m) },
  alerts: { title: 'Alerts', create: (m) => AlertsView(m) },
  notifications: { title: 'Notifications', create: (m) => NotificationsView(m) },
  settings: { title: 'Settings', create: (m) => SettingsView(m) },
};

let current = null;
let mountSeq = 0;
let routedOnce = false;

function parseRoute() {
  const raw = location.hash.startsWith('#/') ? location.hash.slice(2) : '';
  const [name = '', id = ''] = raw.split('?')[0].split('/');
  return { name: Object.prototype.hasOwnProperty.call(VIEWS, name) ? name : 'opportunities', id: /^\d+$/.test(id) ? Number(id) : null };
}

function mountGuard() {
  const id = ++mountSeq;
  return () => id === mountSeq && state.authed;
}

function markReady(main, name) {
  main.dataset.ready = name;
}

function route() {
  if (!state.authed) return;
  if (location.hash && !location.hash.startsWith('#/')) return; // in-page anchors
  const r = parseRoute();
  for (const a of document.querySelectorAll('#sidenav a[data-route]')) {
    if (a.dataset.route === r.name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
  if (current && current.name === r.name && current.view && current.view.update) {
    current.view.update(r);
    return;
  }
  if (current && current.view && current.view.destroy) current.view.destroy();
  const main = $('#main');
  main.replaceChildren();
  delete main.dataset.ready;
  const def = VIEWS[r.name];
  document.title = `${def.title} · FLIP RADAR`;
  const view = def.create(main, r) || {};
  current = { name: r.name, view };
  if (routedOnce) {
    const heading = main.querySelector('h1');
    if (heading) heading.focus();
  }
  routedOnce = true;
}

/* ============================================================================================
 * Shared renderers (opportunity detail + calculator)
 * ========================================================================================== */

function renderBreakdown(r) {
  if (!r || !Array.isArray(r.acquisition_lines) || !Array.isArray(r.selling_lines)) {
    return h('p', { class: 'muted' }, 'No cost breakdown stored.');
  }
  const line = (l) => h('tr', { class: l.amount_cents === 0 ? 'zero' : null },
    h('td', null, l.label), h('td', { class: 'formula' }, h('code', null, l.formula || '')), h('td', { class: 'num' }, fmtMoney(l.amount_cents)));
  const group = (title) => h('tr', { class: 'group' }, h('th', { colspan: '3', scope: 'colgroup' }, title));
  const total = (label, formula, value) => h('tr', { class: 'total' },
    h('th', { scope: 'row' }, label), h('td', { class: 'formula' }, formula ? h('code', null, formula) : ''), h('td', { class: 'num' }, value));
  const tbl = h('div', { class: 'table-wrap', tabindex: '0', role: 'region', 'aria-label': 'Cost breakdown' },
    h('table', { class: 'table breakdown' },
      h('caption', { class: 'sr-only' }, 'Cost breakdown per unit'),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Concept'), h('th', { scope: 'col' }, 'Formula'), h('th', { scope: 'col', class: 'num' }, 'Amount'))),
      h('tbody', null,
        group('Acquisition (per unit)'),
        r.acquisition_lines.map(line),
        total('Total acquisition', 'sum of acquisition lines', fmtMoney(r.total_acquisition_cents)),
        group(`Selling costs (fee profile: ${r.fee_profile_key})`),
        r.selling_lines.map(line),
        total('Total selling costs', 'sum of selling lines', fmtMoney(r.total_selling_costs_cents)),
        group('Result'),
        total('Sale price', 'expected / entered sale price', fmtMoney(r.sale_price_cents)),
        total('Net proceeds', 'sale price − selling costs', fmtMoney(r.net_proceeds_cents)),
        total('Net profit', 'net proceeds − acquisition', moneySigned(r.net_profit_cents)),
        total('ROI', 'net profit ÷ acquisition', r.roi_bp === null || r.roi_bp === undefined ? 'n/a' : fmtBp(r.roi_bp)),
        total('Margin', 'net profit ÷ sale price', r.margin_bp === null || r.margin_bp === undefined ? 'n/a' : fmtBp(r.margin_bp)))));
  const assumptions = Array.isArray(r.assumptions) && r.assumptions.length
    ? h('div', null, h('h4', { class: 'small' }, 'Assumptions'), h('ul', { class: 'small' }, r.assumptions.map((a) => h('li', null, a))))
    : null;
  return h('div', { class: 'stack' }, tbl, assumptions);
}

const STAT_ROWS = [
  ['min_cents', 'Minimum'],
  ['p25_cents', '25th percentile (p25)'],
  ['median_cents', 'Median'],
  ['p75_cents', '75th percentile (p75)'],
  ['max_cents', 'Maximum'],
  ['mad_cents', 'Median absolute deviation (MAD)'],
  ['iqr_cents', 'Interquartile range (IQR)'],
];

function renderDerivation(d) {
  const ps = d.price_stats || {};
  const match = STAT_ROWS.slice(0, 5).find(([k]) => isInt(ps[k]) && ps[k] === d.expected_sale_cents);
  const windowText = isInt(ps.window_days) ? ` observed within the last ${ps.window_days} days` : '';
  const sentence = match
    ? `Expected sale price ${fmtMoney(d.expected_sale_cents)} = the ${match[1].toLowerCase()} of ${fmtInt(ps.n)} comparable listing prices${windowText}, from ${fmtInt(ps.source_count)} source(s) and ${fmtInt(ps.seller_count)} seller(s).`
    : `Expected sale price ${fmtMoney(d.expected_sale_cents)} was derived from ${fmtInt(ps.n)} comparable listings${windowText} (${fmtInt(ps.source_count)} source(s), ${fmtInt(ps.seller_count)} seller(s)); see the explanation and facts below.`;
  const rows = [
    h('tr', null, h('th', { scope: 'row' }, 'Comparables used (n)'), h('td', { class: 'num' }, fmtInt(ps.n))),
    STAT_ROWS.map(([k, label]) => h('tr', { class: match && match[0] === k ? 'highlight' : null },
      h('th', { scope: 'row' }, label, match && match[0] === k ? ' ← expected sale' : ''), h('td', { class: 'num' }, fmtMoney(ps[k])))),
    h('tr', null, h('th', { scope: 'row' }, 'Dispersion (IQR ÷ median)'), h('td', { class: 'num' }, typeof ps.dispersion === 'number' ? `${NF.dec1.format(ps.dispersion * 100)}%` : '—')),
  ];
  const excluded = ps.excluded && typeof ps.excluded === 'object' ? Object.entries(ps.excluded).filter(([, n]) => n) : [];
  return h('div', { class: 'stack' },
    h('p', null, sentence),
    h('p', { class: 'muted small' }, 'Prices are observed totals (price + known shipping) of comparable listings for the same product and condition. Nothing is estimated without observed data.'),
    h('div', { class: 'table-wrap', tabindex: '0', role: 'region', 'aria-label': 'Comparable price statistics' },
      h('table', { class: 'table' }, h('caption', { class: 'sr-only' }, 'Comparable price statistics'), h('tbody', null, rows))),
    excluded.length
      ? h('div', null, h('h4', { class: 'small' }, 'Listings excluded from the comparables'), h('ul', { class: 'small' }, excluded.map(([reason, n]) => h('li', null, `${reason.replace(/_/g, ' ')}: ${fmtInt(n)}`))))
      : null);
}

function renderScore(components, score) {
  if (!Array.isArray(components) || !components.length) return h('p', { class: 'muted' }, 'No score components stored.');
  const list = h('ul', { class: 'score-list' }, components.map((c) => {
    const max = Number(c.max_points) || 0;
    const pts = Number(c.points) || 0;
    const ratio = max > 0 ? Math.min(1, Math.abs(pts) / max) : (pts !== 0 ? 1 : 0);
    const fill = h('span', { class: ['bar-fill', pts < 0 && 'neg'] });
    fill.style.width = `${(ratio * 100).toFixed(1)}%`;
    // raw_value is shown only via the explanation text: its unit (centavos, fraction, count) differs per component.
    return h('li', null,
      h('div', { class: 'score-row' }, h('span', null, c.label), h('span', { class: ['score-pts', pts < 0 && 'neg'] }, `${pts > 0 ? '+' : ''}${fmtScore(pts)} / ${fmtScore(max)}`)),
      h('span', { class: 'bar', 'aria-hidden': 'true' }, fill),
      h('p', { class: 'muted small' }, c.explanation || ''));
  }));
  return h('div', null, h('p', null, `Total score ${fmtScore(score)} / 100 is the sum of these deterministic components:`), list);
}

function evidenceList(evidence) {
  if (!evidence || typeof evidence !== 'object') return null;
  const entries = Object.entries(evidence);
  if (!entries.length) return null;
  return kv(entries.map(([k, v]) => [humanizeKey(k), fmtValue(k, v)]));
}

function renderRiskFlags(flags) {
  if (!Array.isArray(flags) || !flags.length) return h('p', { class: 'muted' }, 'No risk flags were raised by the deterministic rules.');
  const order = { critical: 0, warning: 1, info: 2 };
  const sorted = [...flags].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
  return h('ul', { class: 'flags' }, sorted.map((f) => h('li', { class: ['flag', `sev-${f.severity}`] },
    h('div', null, severityBadge(f.severity), ' ', h('code', null, f.code)),
    h('p', null, f.message),
    evidenceList(f.evidence))));
}

function genericTable(rows, caption, preferred = []) {
  const keys = [];
  for (const k of preferred) if (rows.some((r) => r && Object.prototype.hasOwnProperty.call(r, k))) keys.push(k);
  for (const r of rows) for (const k of Object.keys(r || {})) if (!keys.includes(k) && keys.length < 10) keys.push(k);
  return table(keys.map((k) => ({
    label: humanizeKey(k),
    num: rows.some((r) => r && typeof r[k] === 'number'),
    cell: (r) => (r && k === 'url' ? extLink(r[k]) : fmtValue(k, r ? r[k] : null)),
  })), rows, { caption });
}

function renderDemand(dm) {
  if (!dm) return h('p', { class: 'muted' }, 'No demand data.');
  const signals = Array.isArray(dm.signals) ? dm.signals : [];
  return h('div', { class: 'stack' },
    kv([['Level', demandBadge(dm.level)], ['Confidence', dm.confidence || '—']]),
    dm.note ? h('p', null, dm.note) : null,
    dm.level === 'unknown' ? h('p', { class: 'muted small' }, 'Not enough observed evidence (sold-quantity changes, ended listings) to estimate demand. FLIP RADAR does not guess.') : null,
    signals.length ? genericTable(signals, 'Demand signals', ['name', 'value', 'unit', 'source', 'note']) : null);
}

function renderListings(listings, caption = 'Listings') {
  if (!Array.isArray(listings) || !listings.length) return h('p', { class: 'muted' }, 'No listings.');
  return table([
    { label: 'Title', cls: 'title-cell', cell: (l) => [
      extLink(l.url, l.title), l.is_demo ? [' ', demoBadge()] : null, l.is_suspicious ? [' ', badge('suspicious', 'crit')] : null,
      l.match_method === 'manual' ? [' ', badge('assigned manually', 'accent', 'Assigned by hand from the review queue')] : null,
      isInt(l.id) ? h('div', { class: 'sub' }, h('a', { href: `#/review/${l.id}` }, 'Listing details', h('span', { class: 'sr-only' }, ` for listing #${l.id}`))) : null] },
    { label: 'Source', cell: (l) => l.source_key },
    { label: 'Price (MXN)', num: true, cell: (l) => [fmtMoney(l.price_mxn_cents), l.currency && l.currency !== 'MXN' ? h('div', { class: 'sub muted' }, fmtMoney(l.price_cents, l.currency)) : null] },
    { label: 'Shipping', num: true, cell: (l) => (isInt(l.shipping_cents) ? fmtMoney(l.shipping_cents) : l.free_shipping ? 'Free' : 'unknown') },
    { label: 'Condition', cell: (l) => l.condition },
    { label: 'Seller', cell: (l) => l.seller_name || '—' },
    { label: 'Sold qty', num: true, cell: (l) => (isInt(l.sold_quantity) ? fmtInt(l.sold_quantity) : '—') },
    { label: 'Last seen', cell: (l) => fmtDate(l.last_seen_at) },
  ], listings, { caption });
}

/* ============================================================================================
 * Opportunities view (+ detail drawer)
 * ========================================================================================== */

const oppState = { q: '', min_score: '', min_profit: '', min_roi: '', status: '', sort: 'score', include_suspicious: false, include_demo: '', limit: 50, offset: 0 };

function oppEmptyState(filtered) {
  if (filtered) {
    return h('div', { class: 'empty' },
      h('h2', null, 'No opportunities match these filters'),
      h('p', null, 'Relax the minimum score, profit or ROI, change the status filter, or include suspicious listings.'));
  }
  return h('div', { class: 'empty' },
    h('h2', null, 'No opportunities yet'),
    h('p', null, 'FLIP RADAR never invents prices, demand or market statistics. An opportunity appears only after real listings have been collected and enough comparable listings of the same product exist to compute an expected resale price.'),
    h('ol', null,
      h('li', null, 'Add a tracked search (for example "iphone 13 128gb") on ', h('a', { href: '#/sources' }, 'Searches & Sources'), ' and press "Run now". Mercado Libre needs free API credentials configured on the server.'),
      h('li', null, 'Or import a CSV of prices you collected yourself, or check a single product URL, on the same page.'),
      h('li', null, 'Then press "Recompute opportunities". New opportunities can also trigger ', h('a', { href: '#/alerts' }, 'alerts'), '.')),
    h('p', { class: 'muted small' }, 'Demo data (always badged DEMO) is shown only when enabled in Settings or with the "Demo data" filter.'));
}

function OpportunitiesView(main, initialRoute) {
  const alive = mountGuard();
  const results = h('div', { class: 'results' });
  const pager = h('nav', { class: 'pager', 'aria-label': 'Opportunity pages' });
  const form = h('form', { class: 'card filters form-grid', novalidate: true, role: 'search', 'aria-label': 'Filter opportunities' },
    field('Search', h('input', { type: 'search', name: 'q', value: oppState.q, placeholder: 'title, brand, model…', autocomplete: 'off' })),
    field('Min score (0–100)', numInput('min_score', { min: '0', max: '100', step: '1', value: oppState.min_score })),
    field('Min net profit (MXN)', moneyInput('min_profit', { value: oppState.min_profit })),
    field('Min ROI (%)', pctInput('min_roi', { value: oppState.min_roi, placeholder: 'e.g. 15' })),
    field('Status', select('status', [['', 'Open (new, seen, watching, bought)'], ['all', 'All statuses'], ['new', 'New'], ['seen', 'Seen'], ['watching', 'Watching'], ['bought', 'Bought'], ['dismissed', 'Dismissed'], ['expired', 'Expired']], oppState.status)),
    field('Sort by', select('sort', [['score', 'Score'], ['profit', 'Net profit'], ['roi', 'ROI'], ['recent', 'Most recent']], oppState.sort)),
    field('Demo data', select('include_demo', [['', 'Per settings'], ['true', 'Include'], ['false', 'Exclude']], oppState.include_demo)),
    field('Rows per page', select('limit', [['25', '25'], ['50', '50'], ['100', '100']], String(oppState.limit))),
    checkField('Include suspicious listings', checkbox('include_suspicious', oppState.include_suspicious)),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn btn-primary' }, 'Apply filters'),
      button('Reset', () => {
        Object.assign(oppState, { q: '', min_score: '', min_profit: '', min_roi: '', status: '', sort: 'score', include_suspicious: false, include_demo: '', limit: 50, offset: 0 });
        form.reset();
        for (const [name, value] of [['q', ''], ['min_score', ''], ['min_profit', ''], ['min_roi', ''], ['status', ''], ['sort', 'score'], ['include_demo', ''], ['limit', '50']]) inputOf(form, name).value = value;
        inputOf(form, 'include_suspicious').checked = false;
        return load();
      })));

  onSubmit(form, async () => {
    const minProfit = readMoney(form, 'min_profit');
    const minRoi = readPct(form, 'min_roi');
    const minScore = readFloat(form, 'min_score', { min: 0, max: 100 });
    Object.assign(oppState, {
      q: inputOf(form, 'q').value.trim(),
      min_score: minScore === null ? '' : String(minScore),
      min_profit: minProfit === null ? '' : centsToInput(minProfit),
      min_roi: minRoi === null ? '' : bpToInput(minRoi),
      status: inputOf(form, 'status').value,
      sort: inputOf(form, 'sort').value,
      include_demo: inputOf(form, 'include_demo').value,
      limit: Number(inputOf(form, 'limit').value) || 50,
      include_suspicious: readBool(form, 'include_suspicious'),
      offset: 0,
    });
    await load();
  });

  const drawer = createOppDrawer({ onChanged: () => load() });
  main.append(
    viewHeader('Opportunities', 'Buy candidates whose expected resale price (from observed comparable listings) exceeds the full cost of buying and selling. Click a row for the full, auditable breakdown.',
      [button('Refresh', () => load(), { small: true })]),
    form, results, pager, drawer.backdrop, drawer.panel);

  function isFiltered() {
    return Boolean(oppState.q || oppState.min_score || oppState.min_profit || oppState.min_roi || oppState.status || oppState.include_suspicious || oppState.include_demo);
  }

  function query() {
    return {
      q: oppState.q,
      min_score: oppState.min_score,
      min_profit_cents: oppState.min_profit ? pesosToCents(oppState.min_profit) : '',
      min_roi_bp: oppState.min_roi ? percentToBp(oppState.min_roi) : '',
      status: oppState.status,
      sort: oppState.sort,
      include_suspicious: oppState.include_suspicious ? 'true' : 'false',
      include_demo: oppState.include_demo,
      limit: oppState.limit,
      offset: oppState.offset,
    };
  }

  function renderPage(page) {
    const items = Array.isArray(page.items) ? page.items : [];
    if (!items.length) {
      setChildren(results, oppEmptyState(isFiltered() || oppState.offset > 0));
      if (!oppState.include_suspicious && oppState.offset === 0) showHiddenSuspicious();
    } else {
      setChildren(results, table([
        { label: 'Title', cls: 'title-cell', cell: (o) => [h('a', { href: `#/opportunities/${o.id}`, class: 'row-link' }, o.buy_title), o.is_demo ? [' ', demoBadge()] : null, h('div', { class: 'sub muted' }, o.product_title)] },
        { label: 'Source', cell: (o) => o.buy_source_key },
        { label: 'Buy total', num: true, cell: (o) => fmtMoney(o.buy_total_cents) },
        { label: 'Expected sale', num: true, cell: (o) => fmtMoney(o.expected_sale_cents) },
        { label: 'Net profit', num: true, cell: (o) => moneySigned(o.net_profit_cents) },
        { label: 'ROI', num: true, cell: (o) => fmtBp(o.roi_bp) },
        { label: 'Score', num: true, cell: (o) => scorePill(o.score) },
        { label: 'Confidence', cell: (o) => confidenceBadge(o.confidence, true) },
        { label: 'Risk', cell: (o) => riskBadge(o.risk_level) },
        { label: 'Demand', cell: (o) => demandBadge(o.demand_level, true) },
        { label: 'Status', cell: (o) => statusBadge(o.status) },
      ], items, {
        caption: 'Opportunities',
        rowProps: (o) => ({
          class: ['clickable', o.is_demo && 'is-demo', o.status === 'new' && 'is-new'],
          dataset: { id: o.id },
          on: {
            click: (ev) => {
              if (ev.target.closest('a, button, input, select')) return;
              location.hash = `#/opportunities/${o.id}`;
            },
          },
        }),
      }));
    }
    const total = isInt(page.total) ? page.total : items.length;
    const from = items.length ? oppState.offset + 1 : 0;
    const to = oppState.offset + items.length;
    setChildren(pager, 
      h('span', { class: 'muted small', 'aria-live': 'polite' }, `Showing ${fmtInt(from)}–${fmtInt(items.length ? to : 0)} of ${fmtInt(total)}`),
      h('span', { class: 'pager-buttons' },
        button('← Previous', () => { oppState.offset = Math.max(0, oppState.offset - oppState.limit); return load(); }, { small: true, attrs: { disabled: oppState.offset <= 0 } }),
        button('Next →', () => { oppState.offset += oppState.limit; return load(); }, { small: true, attrs: { disabled: to >= total } })));
  }

  // An empty list can still hide opportunities whose buy listing was flagged suspicious (they are excluded by
  // default).  Say so explicitly instead of claiming there is nothing.
  async function showHiddenSuspicious() {
    try {
      const probe = await api('/opportunities', { query: { ...query(), include_suspicious: 'true', limit: 1, offset: 0 } });
      if (!alive() || !probe || !probe.total || oppState.include_suspicious) return;
      const box = results.querySelector('.empty');
      if (!box) return;
      const heading = box.querySelector('h2');
      if (heading) heading.textContent = 'Only suspicious opportunities found';
      box.prepend(h('div', { class: 'notice notice-warn hidden-suspicious', role: 'status' },
        h('p', null, `${fmtInt(probe.total)} opportunit${probe.total === 1 ? 'y is' : 'ies are'} hidden because the buy listing was flagged as suspicious (for example a price far below the market, replica wording or off-platform payment). Review the risk flags before trusting them.`),
        button('Show suspicious opportunities', () => {
          oppState.include_suspicious = true;
          inputOf(form, 'include_suspicious').checked = true;
          oppState.offset = 0;
          return load();
        }, { small: true, kind: 'primary' })));
    } catch {
      /* the main list already rendered; this hint is optional */
    }
  }

  async function load() {
    results.classList.add('loading');
    try {
      const page = await api('/opportunities', { query: query() });
      if (!alive()) return;
      // e.g. the only row of the last page was dismissed or bought: show the new last page, not an empty one
      const back = offsetAfterEmptyPage({ offset: oppState.offset, limit: oppState.limit, total: page.total, count: (page.items || []).length });
      if (back !== null) {
        oppState.offset = back;
        await load();
        return;
      }
      renderPage(page);
    } catch (err) {
      if (!alive()) return;
      setChildren(results, errorBox(err));
      pager.replaceChildren();
    } finally {
      results.classList.remove('loading');
    }
  }

  load().finally(() => {
    if (alive()) markReady(main, 'opportunities');
  });
  if (initialRoute.id) drawer.open(initialRoute.id);

  return {
    update(r) {
      if (r.id) drawer.open(r.id);
      else drawer.close();
    },
    destroy() {
      drawer.close();
    },
  };
}

// Why a bought or expired opportunity offers no Watch / Dismiss (see oppStatusActions).
const OPP_STATUS_NOTES = new Map([
  ['bought', 'Bought: the purchase is tracked in Inventory and no longer counts as open potential profit in Analytics, so Watch and Dismiss are not offered. Marked bought by mistake? Delete its inventory item, then use “Restore to new”. Use “Mark bought…” only to record another purchase.'],
  ['expired', 'Expired: at the last recompute this listing no longer qualified as an opportunity. It reopens automatically if a later recompute finds it again.'],
]);

function createOppDrawer({ onChanged }) {
  const titleId = nextId('opp-title');
  const backdrop = h('div', { class: 'backdrop', hidden: true, on: { click: () => { location.hash = '#/opportunities'; } } });
  const panel = h('aside', { class: 'drawer', role: 'dialog', 'aria-modal': 'false', 'aria-labelledby': titleId, hidden: true, tabindex: '-1' });
  const onKey = (ev) => {
    if (ev.key === 'Escape' && !panel.hidden) {
      ev.preventDefault();
      location.hash = '#/opportunities';
    }
  };
  let seq = 0;
  let lastFocus = null;
  let openId = null;

  const closeButton = () => h('a', { class: 'btn btn-sm', href: '#/opportunities', 'aria-label': 'Close detail' }, '✕ Close');

  async function open(id, { keepScroll = false } = {}) {
    const my = ++seq;
    if (panel.hidden) {
      lastFocus = document.activeElement;
      document.addEventListener('keydown', onKey);
    }
    openId = id;
    panel.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add('drawer-open');
    delete panel.dataset.ready;
    const scroll = panel.scrollTop;
    if (!keepScroll) setChildren(panel, h('div', { class: 'drawer-head' }, h('h2', { id: titleId, tabindex: '-1' }, `Opportunity #${id}`), closeButton()), h('p', { class: 'muted' }, 'Loading…'));
    if (!keepScroll) $(`#${titleId}`, panel).focus();
    try {
      const d = await api(`/opportunities/${id}`);
      if (my !== seq) return;
      render(d);
      if (keepScroll) panel.scrollTop = scroll;
      else $(`#${titleId}`, panel).focus();
      if (d.status === 'new') markSeen(d);
    } catch (err) {
      if (my !== seq) return;
      setChildren(panel, h('div', { class: 'drawer-head' }, h('h2', { id: titleId, tabindex: '-1' }, `Opportunity #${id}`), closeButton()), errorBox(err));
    } finally {
      if (my === seq) panel.dataset.ready = String(id);
    }
  }

  async function markSeen(d) {
    try {
      await api(`/opportunities/${d.id}`, { method: 'PATCH', json: { status: 'seen' } });
      onChanged();
    } catch {
      /* non-essential */
    }
  }

  function close() {
    seq++;
    document.removeEventListener('keydown', onKey);
    if (panel.hidden) return;
    openId = null;
    panel.hidden = true;
    backdrop.hidden = true;
    document.body.classList.remove('drawer-open');
    panel.replaceChildren();
    if (lastFocus && lastFocus.isConnected && lastFocus.focus) lastFocus.focus();
    lastFocus = null;
  }

  async function setStatus(d, status, message) {
    try {
      await api(`/opportunities/${d.id}`, { method: 'PATCH', json: { status } });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) throw new ApiError(409, oppStatusErrorText(err, d.status), err.detail);
      throw err;
    }
    toast(message, 'success');
    onChanged();
    if (openId === d.id) await open(d.id, { keepScroll: true });
  }

  function buyForm(d, setOpen) {
    const f = h('form', { class: 'inline-form form-grid', novalidate: true, hidden: true, 'aria-label': 'Mark as bought' },
      field('Quantity bought', numInput('quantity', { min: '1', max: '1000', step: '1', value: '1', required: true })),
      field('Unit cost (MXN)', moneyInput('unit_cost', { placeholder: 'listing price' }), { hint: 'Empty = the listing price.' }),
      field('Extra costs (MXN, total)', moneyInput('extra_costs', { placeholder: 'listing shipping' }), { hint: 'Empty = listing shipping if known.' }),
      field('Notes', textInput('notes', { maxlength: '2000' }), { wide: true }),
      h('div', { class: 'form-actions' },
        h('button', { type: 'submit', class: 'btn btn-primary' }, 'Confirm purchase'),
        h('button', { type: 'button', class: 'btn', on: { click: () => setOpen(false) } }, 'Cancel')));
    onSubmit(f, async () => {
      const body = { quantity: readInt(f, 'quantity', { required: true, min: 1, max: 1000 }) };
      const unit = readMoney(f, 'unit_cost');
      const extra = readMoney(f, 'extra_costs');
      const notes = readStr(f, 'notes');
      if (unit !== null) body.unit_cost_cents = unit;
      if (extra !== null) body.extra_costs_cents = extra;
      if (notes) body.notes = notes;
      const item = await api(`/opportunities/${d.id}/buy`, { method: 'POST', json: body });
      toast(['Added to inventory: ', h('a', { href: `#/inventory/${item.id}` }, item.title)], 'success');
      onChanged();
      await open(d.id, { keepScroll: true });
    });
    return f;
  }

  function aiBox(d) {
    const out = h('div', { class: 'stack', 'aria-live': 'polite' },
      d.ai_summary
        ? [h('pre', { class: 'explanation' }, d.ai_summary), h('p', { class: 'muted small' }, 'Previously generated summary.')]
        : h('p', { class: 'muted small' }, 'Optional narrative from a local LLM (Ollama) or a compatible server, checked against the deterministic facts. The numbers above remain the source of truth.'));
    const btn = button('Generate AI summary', async () => {
      const res = await api(`/opportunities/${d.id}/ai-summary`, { method: 'POST' });
      setChildren(out, 
        h('div', { class: 'badges' },
          res.validated ? badge('✓ validated against facts', 'good') : badge('⚠ not validated', 'warn', 'The summary cites numbers that are not in the deterministic facts, or no summary was produced'),
          badge(`provider: ${res.provider}`)),
        res.message ? h('p', { class: res.validated ? 'muted small' : 'notice notice-warn' }, res.message) : null,
        res.summary ? h('pre', { class: 'explanation' }, res.summary) : h('p', { class: 'muted' }, 'No summary was produced.'));
    });
    return h('div', { class: 'stack' }, h('div', { class: 'actions' }, btn), out);
  }

  function render(d) {
    const setOpen = (isOpen) => {
      bf.hidden = !isOpen;
      buyToggle.setAttribute('aria-expanded', String(isOpen));
      if (isOpen) inputOf(bf, 'quantity').focus();
      else buyToggle.focus();
    };
    const bf = buyForm(d, setOpen);
    const buyToggle = button('Mark bought…', () => setOpen(bf.hidden), { attrs: { 'aria-expanded': 'false' } });
    const statusActions = oppStatusActions(d.status).map((a) => button(a.label, () => setStatus(d, a.to, a.message), { kind: a.primary ? 'primary' : '' }));
    const facts = d.facts && typeof d.facts === 'object' ? Object.entries(d.facts) : [];
    const highlights = factHighlights(d.facts);
    setChildren(panel,
      h('div', { class: 'drawer-head' }, h('h2', { id: titleId, tabindex: '-1' }, d.product_title), closeButton()),
      h('div', { class: 'badges' }, d.is_demo ? demoBadge() : null, statusBadge(d.status), riskBadge(d.risk_level), confidenceBadge(d.confidence), demandBadge(d.demand_level), d.category ? badge(d.category) : null),
      h('div', { class: 'actions' }, statusActions, buyToggle),
      OPP_STATUS_NOTES.has(d.status) ? h('p', { class: 'muted small' }, OPP_STATUS_NOTES.get(d.status)) : null,
      bf,
      tiles([
        ['Net profit', moneySigned(d.net_profit_cents), 'after all fees, shipping and withholdings'],
        ['ROI', fmtBp(d.roi_bp), 'net profit ÷ acquisition'],
        ['Margin', fmtBp(d.margin_bp), 'net profit ÷ sale price'],
        ['Score', `${fmtScore(d.score)} / 100`],
        ['Buy total', fmtMoney(d.buy_total_cents)],
        ['Expected sale', fmtMoney(d.expected_sale_cents), `${fmtInt(d.comparable_count)} comparables`],
      ]),
      section('Buy listing', kv([
        ['Listing', extLink(d.buy_url, d.buy_title)],
        ['Source', d.buy_source_key],
        ['Condition', d.buy_condition],
        ['Product key', h('code', null, d.product_key)],
        ['Resale fee profile', d.fee_profile_key],
        ['First detected', fmtDateTime(d.created_at)],
        ['Last updated', fmtDateTime(d.updated_at)],
      ])),
      section('Expected sale price: derivation', renderDerivation(d)),
      section('Cost breakdown', renderBreakdown(d.cost_breakdown)),
      highlights.length
        ? section('Commission and evidence sources', h('div', { class: 'stack fact-groups' }, highlights.map((g) => h('div', null, h('h4', { class: 'small' }, g.title), kv(g.rows)))),
          h('p', { class: 'muted small' }, 'Recorded with this opportunity at the last recompute. Every value is also listed under Facts.'))
        : null,
      section('Score components', renderScore(d.score_components, d.score)),
      section('Risk flags', renderRiskFlags(d.risk_flags)),
      section('Demand', renderDemand(d.demand)),
      section('Explanation', h('pre', { class: 'explanation' }, d.explanation || 'No explanation stored.')),
      section('AI summary (optional)', aiBox(d)),
      section(`Comparables (${(d.comparables || []).length})`, renderListings(d.comparables || [], 'Comparable listings')),
      section('Facts', h('details', null,
        h('summary', null, `Every number the explanation may cite (${facts.length})`),
        facts.length ? kv(facts.map(([k, v]) => [k, fmtValue(k, v)])) : h('p', { class: 'muted' }, 'No facts stored.'))));
  }

  return { panel, backdrop, open, close };
}

/* ============================================================================================
 * Calculator view
 * ========================================================================================== */

function feeProfileSummary(p) {
  if (!p) return h('p', { class: 'muted small' }, 'The server default fee profile (Settings) will be used.');
  const tiers = Array.isArray(p.fixed_fee_tiers) && p.fixed_fee_tiers.length
    ? p.fixed_fee_tiers.map((t) => `${isInt(t.max_price_cents) ? `≤ ${fmtMoney(t.max_price_cents)}` : 'above'}: ${fmtMoney(t.fee_cents)}`).join('; ')
    : 'none';
  return h('div', { class: 'small' },
    h('p', null, `Commission ${fmtBp(p.commission_bp)} · fixed fee tiers: ${tiers} · payment ${fmtBp(p.payment_fee_bp)} + ${fmtMoney(p.payment_fixed_cents)}${p.iva_on_fees ? ` · IVA ${fmtBp(p.iva_rate_bp)} on fees` : ''} · seller shipping ${fmtMoney(p.seller_shipping_cents)}${isInt(p.free_shipping_threshold_cents) ? ` (mandatory free shipping ≥ ${fmtMoney(p.free_shipping_threshold_cents)})` : ''} · withholdings ISR ${fmtBp(p.isr_withholding_bp)}, IVA ${fmtBp(p.iva_withholding_bp)}.`),
    h('p', { class: 'muted' }, p.as_of ? `Values as of ${p.as_of}. ` : 'No "as of" date recorded. ', p.source_url ? extLink(p.source_url, 'Fee source') : 'No source URL recorded.', ' Verify current rates before relying on them.'));
}

function CalculatorView(main) {
  const alive = mountGuard();
  const profileSel = select('fee_profile_key', [['', 'Default profile (from Settings)']], '');
  const profileInfo = h('div', { class: 'profile-info' }, feeProfileSummary(null));
  let profiles = [];

  const form = h('form', { class: 'card form-grid', novalidate: true, 'aria-labelledby': 'calc-title' },
    h('h2', { id: 'calc-title', class: 'field-wide' }, 'Resale profit calculator'),
    field('Buy price (MXN)', moneyInput('buy_price', { required: true })),
    field('Expected sale price (MXN)', moneyInput('sale_price', { required: true })),
    field('Fee profile (where you resell)', profileSel),
    field('Shipping to you (MXN)', moneyInput('buy_shipping')),
    field('Import costs (MXN)', moneyInput('import_costs'), { hint: 'Use the import estimator below for cross-border buys.' }),
    field('Other acquisition costs (MXN)', moneyInput('other_buy_costs'), { hint: 'Repairs, cleaning, travel…' }),
    field('Packaging / other selling costs (MXN)', moneyInput('other_sale_costs')),
    field('Seller shipping override (MXN)', moneyInput('seller_shipping_override', { placeholder: 'profile rule' }), { hint: 'Empty = apply the fee profile shipping rule.' }),
    field('Target ROI (%)', pctInput('target_roi', { placeholder: 'e.g. 25' }), { hint: 'Optional: computes the maximum buy price.' }),
    checkField('Sale price includes IVA', checkbox('includes_iva', true), 'Withholdings are computed on the price before IVA.'),
    checkField('Count platform ISR/IVA withholdings as cost', checkbox('withholdings_cost', true), 'Untick if you credit them in your tax return.'),
    h('div', { class: 'field-wide' }, profileInfo),
    h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Calculate')));
  const result = h('div', { class: 'calc-result', 'aria-live': 'polite' });

  profileSel.addEventListener('change', () => {
    setChildren(profileInfo, feeProfileSummary(profiles.find((p) => p.key === profileSel.value) || null));
  });

  onSubmit(form, async () => {
    const body = {
      buy_price_cents: readMoney(form, 'buy_price', { required: true }),
      sale_price_cents: readMoney(form, 'sale_price', { required: true }),
      fee_profile_key: profileSel.value || null,
      buy_shipping_cents: readMoney(form, 'buy_shipping') ?? 0,
      import_costs_cents: readMoney(form, 'import_costs') ?? 0,
      other_buy_costs_cents: readMoney(form, 'other_buy_costs') ?? 0,
      other_sale_costs_cents: readMoney(form, 'other_sale_costs') ?? 0,
      seller_shipping_override_cents: readMoney(form, 'seller_shipping_override'),
      sale_price_includes_iva: readBool(form, 'includes_iva'),
      treat_withholdings_as_cost: readBool(form, 'withholdings_cost'),
      target_roi_bp: readPct(form, 'target_roi', { max: 100000 }),
    };
    const out = await api('/calculator/profit', { method: 'POST', json: body });
    if (!alive()) return;
    const r = out.result;
    setChildren(result, card('Result',
      tiles([
        ['Net profit', moneySigned(r.net_profit_cents)],
        ['ROI', r.roi_bp === null ? 'n/a' : fmtBp(r.roi_bp)],
        ['Margin', r.margin_bp === null ? 'n/a' : fmtBp(r.margin_bp)],
        ['Net proceeds', fmtMoney(r.net_proceeds_cents), 'sale − selling costs'],
        ['Break-even sale price', isInt(out.break_even_sale_cents) ? fmtMoney(out.break_even_sale_cents) : 'not reachable', 'lowest sale price with profit ≥ $0'],
        isInt(out.target_roi_bp)
          ? ['Max buy price', isInt(out.max_buy_for_target_cents) ? fmtMoney(out.max_buy_for_target_cents) : 'not reachable', `for ${fmtBp(out.target_roi_bp)} ROI at this sale price`]
          : null,
      ]),
      renderBreakdown(r)));
    result.dataset.ready = 'calc';
  });

  // ---- import cost estimator
  const impForm = h('form', { class: 'card form-grid', novalidate: true, 'aria-labelledby': 'imp-title' },
    h('h2', { id: 'imp-title', class: 'field-wide' }, 'Import cost estimator (USD purchases)'),
    field('Item value (USD)', moneyInput('item_value', { required: true })),
    field('Shipping (USD)', moneyInput('shipping')),
    field('Import rule key', textInput('rule_key', { value: 'courier_usmca' }), { hint: 'Rule defined on the server (default courier_usmca).' }),
    field('USD → MXN rate', textInput('rate', { inputmode: 'decimal', placeholder: 'latest stored rate' }), { hint: 'Empty = latest stored FX rate (Banxico or manual).' }),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn btn-primary' }, 'Estimate duties'),
      button('Show latest FX rate', async () => {
        try {
          const fx = await api('/fx/latest');
          setChildren(impResult, card('Latest stored FX rate', kv(fxRateRows(fx))));
        } catch (err) {
          if (err.status === 404) setChildren(impResult, h('p', { class: 'notice notice-warn' }, 'No FX rate stored yet. Configure a Banxico token or a manual USD/MXN rate in Settings.'));
          else throw err;
        }
      })));
  const impResult = h('div', { 'aria-live': 'polite' });

  onSubmit(impForm, async () => {
    const rate = readStr(impForm, 'rate');
    if (rate && !/^\d{1,4}(\.\d{1,8})?$/.test(rate)) throw invalid(impForm, 'rate', 'USD → MXN rate: decimal number expected (e.g. 18.25)');
    const body = {
      item_value_usd_cents: readMoney(impForm, 'item_value', { required: true }),
      shipping_usd_cents: readMoney(impForm, 'shipping') ?? 0,
      rule_key: readStr(impForm, 'rule_key') || 'courier_usmca',
      usd_mxn_rate: rate,
    };
    const out = await api('/calculator/import-costs', { method: 'POST', json: body });
    // A stored rate from a public source must be shown with its attribution, which only /fx/latest carries.
    const fx = rate ? null : await api('/fx/latest').catch(() => null);
    const fxAttribution = importFxAttribution(out, fx);
    if (!alive()) return;
    // A formal import (customs broker) is not estimated: the server sends null duties and formal_import_required.
    const estimated = importDutiesEstimated(out);
    const notEstimated = (text) => h('span', { class: 'tile-text' }, text);
    const formalId = nextId('formal');
    const useDuties = button('Use duties as import costs above', () => {
      if (!estimated) return;
      inputOf(form, 'import_costs').value = centsToInput(out.duties_mxn_cents);
      inputOf(form, 'import_costs').focus();
      toast('Import costs filled in the calculator.', 'success');
    }, { attrs: { disabled: !estimated, 'aria-describedby': estimated ? null : formalId } });
    setChildren(impResult, card(`Estimate: ${out.rule_name}`,
      tiles([
        ['Duties & taxes (MXN)', estimated ? fmtMoney(out.duties_mxn_cents) : notEstimated('not estimated (formal import required)')],
        ['Customs value (MXN)', fmtMoney(out.customs_value_mxn_cents)],
        ['Customs value (USD)', fmtMoney(out.customs_value_usd_cents, 'USD')],
        ['Duty rate', estimated && isInt(out.duty_rate_bp) ? fmtBp(out.duty_rate_bp) : notEstimated('not estimated')],
      ]),
      estimated ? null : h('p', { id: formalId, class: 'notice notice-warn formal-import' },
        'This customs value needs a formal import through a customs broker (agente aduanal). FLIP RADAR does not estimate those duties: ask a broker for a quote and enter it as import costs yourself.'),
      kv([
        ['Rule', h('code', null, out.rule_key)],
        ['USD → MXN', `${out.usd_mxn_rate} (${out.fx_source})`],
        fxAttribution ? ['FX attribution', fxAttribution] : null,
        ['Source', out.source_url ? extLink(out.source_url, 'official reference') : 'not recorded'],
        ['As of', out.as_of || '—'],
      ]),
      Array.isArray(out.notes) && out.notes.length ? h('ul', { class: 'small' }, out.notes.map((n) => h('li', null, n))) : null,
      h('div', { class: 'actions' }, useDuties)));
  });

  main.append(
    viewHeader('Calculator', 'Deterministic profit maths with every fee line, formula and assumption shown. Money is entered in pesos and sent to the server as exact integer centavos.'),
    form, result, impForm, impResult);

  getFeeProfiles()
    .then((list) => {
      if (!alive()) return;
      profiles = list || [];
      const value = profileSel.value;
      setChildren(profileSel, ...feeProfileOptions(profiles, 'Default profile (from Settings)').map(([v, label]) => h('option', { value: v, selected: v === value }, label)));
    })
    .catch((err) => reportError(err, 'Fee profiles'))
    .finally(() => {
      if (alive()) markReady(main, 'calculator');
    });
}

/* ============================================================================================
 * Inventory view
 * ========================================================================================== */

const INV_STATUSES = ['purchased', 'in_transit', 'in_stock', 'listed', 'sold', 'returned', 'written_off'];
const INV_EDITABLE_STATUSES = ['purchased', 'in_transit', 'in_stock', 'listed', 'returned', 'written_off'];
const CONDITIONS = ['new', 'used', 'refurbished', 'for_parts', 'unknown'];
const invState = { status: '', q: '', offset: 0, limit: 100 };
const label = (value) => String(value).replace(/_/g, ' ');

function InventoryView(main, initialRoute) {
  const alive = mountGuard();
  const box = h('div');
  main.append(box);
  let seq = 0;

  function show(r) {
    const my = ++seq;
    const isCurrent = () => alive() && my === seq;
    const p = r.id ? renderDetail(r.id, isCurrent) : renderList(isCurrent);
    p.finally(() => {
      if (isCurrent()) markReady(main, r.id ? `inventory-${r.id}` : 'inventory');
    });
  }

  async function renderList(isCurrent) {
    delete main.dataset.ready;
    const results = h('div', { class: 'results' });
    const pager = h('nav', { class: 'pager', 'aria-label': 'Inventory pages' });
    const filter = h('form', { class: 'card form-grid', novalidate: true, role: 'search', 'aria-label': 'Filter inventory' },
      field('Status', select('status', [['', 'All'], ...INV_STATUSES.map((st) => [st, label(st)])], invState.status)),
      field('Search', h('input', { type: 'search', name: 'q', value: invState.q, placeholder: 'title, SKU…' })),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Filter')));
    onSubmit(filter, async () => {
      invState.status = inputOf(filter, 'status').value;
      invState.q = inputOf(filter, 'q').value.trim();
      invState.offset = 0;
      await load();
    });
    inputOf(filter, 'status').addEventListener('change', () => filter.requestSubmit());

    const add = h('form', { class: 'form-grid', novalidate: true, 'aria-label': 'Add inventory item' },
      field('Title', textInput('title', { required: true, maxlength: '300' }), { wide: true }),
      field('SKU', textInput('sku', { maxlength: '64' })),
      field('Condition', select('condition', CONDITIONS.map((c) => [c, label(c)]), 'unknown')),
      field('Quantity', numInput('quantity', { min: '1', max: '100000', step: '1', value: '1', required: true })),
      field('Unit cost (MXN)', moneyInput('unit_cost', { required: true })),
      field('Extra costs (MXN, total)', moneyInput('extra_costs'), { hint: 'Shipping, import, repairs…' }),
      field('Purchase source', textInput('purchase_source', { maxlength: '120' })),
      field('Purchase URL', h('input', { type: 'url', name: 'purchase_url', maxlength: '1000', autocomplete: 'off' })),
      field('Purchased on', h('input', { type: 'date', name: 'purchased_at' }), { hint: 'Empty = today.' }),
      field('Status', select('status', [['purchased', 'purchased'], ['in_transit', 'in transit'], ['in_stock', 'in stock'], ['listed', 'listed']], 'purchased')),
      field('Target sale price (MXN)', moneyInput('target_sale')),
      field('Listed on (channel)', textInput('listed_channel', { maxlength: '64' })),
      field('Location', textInput('location', { maxlength: '120' })),
      field('Notes', h('textarea', { name: 'notes', maxlength: '4000', rows: '2' }), { wide: true }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Add item')));
    onSubmit(add, async () => {
      const url = readStr(add, 'purchase_url');
      if (url && !safeHttpUrl(url)) throw invalid(add, 'purchase_url', 'Purchase URL: must start with http:// or https://');
      const body = {
        title: readStr(add, 'title', { required: true }),
        sku: readStr(add, 'sku'),
        condition: inputOf(add, 'condition').value,
        quantity: readInt(add, 'quantity', { required: true, min: 1, max: 100000 }),
        unit_cost_cents: readMoney(add, 'unit_cost', { required: true }),
        extra_costs_cents: readMoney(add, 'extra_costs') ?? 0,
        purchase_source: readStr(add, 'purchase_source'),
        purchase_url: url,
        purchased_at: readDate(add, 'purchased_at'),
        status: inputOf(add, 'status').value,
        target_sale_cents: readMoney(add, 'target_sale'),
        listed_channel: readStr(add, 'listed_channel'),
        location: readStr(add, 'location'),
        notes: readStr(add, 'notes'),
      };
      for (const k of Object.keys(body)) if (body[k] === null) delete body[k];
      const item = await api('/inventory', { method: 'POST', json: body });
      toast(`Added "${item.title}" to inventory.`, 'success');
      location.hash = `#/inventory/${item.id}`;
    });

    setChildren(box, 
      viewHeader('Inventory', 'Items you bought, their cost basis, status and realized profit. Profit is computed from what you actually paid and sold for.'),
      filter,
      h('details', { class: 'card' }, h('summary', null, 'Add item'), add),
      results, pager);

    async function load() {
      results.classList.add('loading');
      try {
        const page = await api('/inventory', { query: { status: invState.status, q: invState.q, limit: invState.limit, offset: invState.offset } });
        if (!isCurrent()) return;
        const items = page.items || [];
        const back = offsetAfterEmptyPage({ offset: invState.offset, limit: invState.limit, total: page.total, count: items.length });
        if (back !== null) {
          invState.offset = back;
          await load();
          return;
        }
        setChildren(results, items.length
          ? table([
            { label: 'Title', cls: 'title-cell', cell: (i) => [h('a', { href: `#/inventory/${i.id}` }, i.title), i.sku ? h('div', { class: 'sub muted' }, `SKU ${i.sku}`) : null] },
            { label: 'Condition', cell: (i) => label(i.condition) },
            { label: 'Available / bought', num: true, cell: (i) => `${fmtInt(i.quantity_available)} / ${fmtInt(i.quantity)}` },
            { label: 'Unit cost', num: true, cell: (i) => fmtMoney(i.unit_cost_cents) },
            { label: 'Total cost', num: true, cell: (i) => fmtMoney(i.total_cost_cents) },
            { label: 'Target sale', num: true, cell: (i) => fmtMoney(i.target_sale_cents) },
            { label: 'Status', cell: (i) => statusBadge(i.status) },
            { label: 'Days held', num: true, cell: (i) => fmtInt(i.days_held) },
            { label: 'Realized profit', num: true, cell: (i) => moneySigned(i.realized_profit_cents) },
          ], items, {
            caption: 'Inventory items',
            rowProps: (i) => ({ class: 'clickable', on: { click: (ev) => { if (!ev.target.closest('a, button')) location.hash = `#/inventory/${i.id}`; } } }),
          })
          : h('div', { class: 'empty' }, h('h2', null, 'No inventory items'), h('p', null, invState.status || invState.q ? 'Nothing matches this filter.' : 'Add an item above, or use "Mark bought" on an opportunity.')));
        const total = isInt(page.total) ? page.total : items.length;
        const to = invState.offset + items.length;
        setChildren(pager, 
          h('span', { class: 'muted small' }, `Showing ${fmtInt(items.length ? invState.offset + 1 : 0)}–${fmtInt(items.length ? to : 0)} of ${fmtInt(total)}`),
          h('span', { class: 'pager-buttons' },
            button('← Previous', () => { invState.offset = Math.max(0, invState.offset - invState.limit); return load(); }, { small: true, attrs: { disabled: invState.offset <= 0 } }),
            button('Next →', () => { invState.offset += invState.limit; return load(); }, { small: true, attrs: { disabled: to >= total } })));
      } catch (err) {
        if (isCurrent()) setChildren(results, errorBox(err));
      } finally {
        results.classList.remove('loading');
      }
    }
    await load();
  }

  async function renderDetail(id, isCurrent) {
    delete main.dataset.ready;
    setChildren(box, h('p', null, h('a', { href: '#/inventory' }, '← Back to inventory')), h('p', { class: 'muted' }, 'Loading item…'));
    let item;
    let profiles = [];
    try {
      [item, profiles] = await Promise.all([api(`/inventory/${id}`), getFeeProfiles().catch(() => [])]);
    } catch (err) {
      if (isCurrent()) setChildren(box, h('p', null, h('a', { href: '#/inventory' }, '← Back to inventory')), errorBox(err));
      return;
    }
    if (!isCurrent()) return;
    const reload = () => {
      if (isCurrent()) show({ name: 'inventory', id });
    };

    // quick status change
    const statusOpts = INV_EDITABLE_STATUSES.map((st) => [st, label(st)]);
    if (!INV_EDITABLE_STATUSES.includes(item.status)) statusOpts.unshift([item.status, `${label(item.status)} (current)`, { disabled: true }]);
    const statusSel = select('status', statusOpts, item.status);
    const statusForm = h('form', { class: 'actions', novalidate: true, 'aria-label': 'Change status' },
      field('Status', statusSel), h('button', { type: 'submit', class: 'btn' }, 'Update status'));
    onSubmit(statusForm, async () => {
      if (statusSel.value === item.status) return toast('Status unchanged.');
      await api(`/inventory/${id}`, { method: 'PATCH', json: { status: statusSel.value } });
      toast('Status updated.', 'success');
      reload();
    });

    // edit form (sends only changed fields)
    const edit = h('form', { class: 'form-grid', novalidate: true, 'aria-label': 'Edit item' },
      field('Title', textInput('title', { value: item.title, required: true, maxlength: '300' }), { wide: true }),
      field('SKU', textInput('sku', { value: item.sku || '', maxlength: '64' })),
      field('Condition', select('condition', CONDITIONS.map((c) => [c, label(c)]), item.condition)),
      field('Unit cost (MXN)', moneyInput('unit_cost', { value: centsToInput(item.unit_cost_cents), required: true })),
      field('Extra costs (MXN, total)', moneyInput('extra_costs', { value: centsToInput(item.extra_costs_cents) })),
      field('Target sale price (MXN)', moneyInput('target_sale', { value: centsToInput(item.target_sale_cents) })),
      field('Listed on (channel)', textInput('listed_channel', { value: item.listed_channel || '', maxlength: '64' })),
      field('Location', textInput('location', { value: item.location || '', maxlength: '120' })),
      field('Notes', h('textarea', { name: 'notes', maxlength: '4000', rows: '2' }, item.notes || ''), { wide: true }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Save changes')));
    onSubmit(edit, async () => {
      const next = {
        title: readStr(edit, 'title', { required: true }),
        sku: readStr(edit, 'sku'),
        condition: inputOf(edit, 'condition').value,
        unit_cost_cents: readMoney(edit, 'unit_cost', { required: true }),
        extra_costs_cents: readMoney(edit, 'extra_costs') ?? 0,
        target_sale_cents: readMoney(edit, 'target_sale'),
        listed_channel: readStr(edit, 'listed_channel'),
        location: readStr(edit, 'location'),
        notes: readStr(edit, 'notes'),
      };
      const patch = {};
      for (const [k, v] of Object.entries(next)) if ((item[k] ?? null) !== v) patch[k] = v;
      if (!Object.keys(patch).length) return toast('Nothing changed.');
      await api(`/inventory/${id}`, { method: 'PATCH', json: patch });
      toast('Item updated.', 'success');
      reload();
    });

    // record sale
    const saleForm = h('form', { class: 'form-grid', novalidate: true, 'aria-label': 'Record sale' },
      field('Quantity', numInput('quantity', { min: '1', max: String(Math.max(1, item.quantity_available)), step: '1', value: '1', required: true })),
      field('Unit sale price (MXN)', moneyInput('unit_price', { required: true, value: centsToInput(item.target_sale_cents) })),
      field('Channel', textInput('channel', { maxlength: '64', value: item.listed_channel || '' })),
      field('Fee profile', select('fee_profile_key', feeProfileOptions(profiles, 'None (enter fees manually)'), ''), { hint: 'Computes fees with the calculator.' }),
      field('Fees actually charged (MXN)', moneyInput('fees', { placeholder: 'computed' }), { hint: 'Overrides the computed fees.' }),
      field('Shipping you paid (MXN, total)', moneyInput('shipping'), { hint: 'On Mercado Libre profiles this replaces the profile shipping estimate; leave empty to use the estimate.' }),
      field('Taxes withheld (MXN)', moneyInput('taxes', { placeholder: 'computed' }), { hint: 'Overrides computed withholdings.' }),
      field('Other costs (MXN)', moneyInput('other_costs')),
      field('Sold on', h('input', { type: 'date', name: 'sold_at' }), { hint: 'Empty = today.' }),
      field('Notes', textInput('notes', { maxlength: '2000' }), { wide: true }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Record sale')));
    onSubmit(saleForm, async () => {
      const body = {
        quantity: readInt(saleForm, 'quantity', { required: true, min: 1, max: item.quantity_available }),
        unit_price_cents: readMoney(saleForm, 'unit_price', { required: true }),
        channel: readStr(saleForm, 'channel'),
        fee_profile_key: inputOf(saleForm, 'fee_profile_key').value || null,
        fees_cents: readMoney(saleForm, 'fees'),
        shipping_cents: readMoney(saleForm, 'shipping') ?? 0,
        taxes_withheld_cents: readMoney(saleForm, 'taxes'),
        other_costs_cents: readMoney(saleForm, 'other_costs') ?? 0,
        sold_at: readDate(saleForm, 'sold_at'),
        notes: readStr(saleForm, 'notes'),
      };
      for (const k of Object.keys(body)) if (body[k] === null) delete body[k];
      const sale = await api(`/inventory/${id}/sales`, { method: 'POST', json: body });
      toast(['Sale recorded. Profit: ', moneySigned(sale.profit_cents)], 'success');
      reload();
    });

    const sales = Array.isArray(item.sales) ? item.sales : [];
    const salesTable = sales.length
      ? table([
        { label: 'Sold', cell: (sl) => fmtDate(sl.sold_at) },
        { label: 'Qty', num: true, cell: (sl) => fmtInt(sl.quantity) },
        { label: 'Unit price', num: true, cell: (sl) => fmtMoney(sl.unit_price_cents) },
        { label: 'Channel', cell: (sl) => [sl.channel || '—', sl.fee_profile_key ? h('div', { class: 'sub muted' }, sl.fee_profile_key) : null] },
        { label: 'Fees', num: true, cell: (sl) => fmtMoney(sl.fees_cents) },
        { label: 'Shipping', num: true, cell: (sl) => fmtMoney(sl.shipping_cents) },
        { label: 'Withheld', num: true, cell: (sl) => fmtMoney(sl.taxes_withheld_cents) },
        { label: 'Other', num: true, cell: (sl) => fmtMoney(sl.other_costs_cents) },
        { label: 'Cost basis', num: true, cell: (sl) => fmtMoney(sl.cost_basis_cents) },
        { label: 'Net proceeds', num: true, cell: (sl) => fmtMoney(sl.net_proceeds_cents) },
        { label: 'Profit', num: true, cell: (sl) => moneySigned(sl.profit_cents) },
        { label: 'Actions', cell: (sl) => button('Delete', async () => {
          if (!window.confirm('Delete this sale? The sold quantity is returned to the item.')) return;
          await api(`/sales/${sl.id}`, { method: 'DELETE' });
          toast('Sale deleted and quantity restored.', 'success');
          reload();
        }, { small: true, kind: 'danger', attrs: { 'aria-label': `Delete sale of ${fmtDate(sl.sold_at)}` } }) },
      ], sales, { caption: 'Sales of this item' })
      : h('p', { class: 'muted' }, 'No sales recorded yet.');

    setChildren(box, 
      h('p', null, h('a', { href: '#/inventory' }, '← Back to inventory')),
      viewHeader(item.title, null, [button('Delete item', async () => {
        if (!window.confirm(`Delete "${item.title}" and all its sales? This cannot be undone.`)) return;
        await api(`/inventory/${id}`, { method: 'DELETE' });
        toast('Item deleted.', 'success');
        location.hash = '#/inventory';
      }, { kind: 'danger', small: true })]),
      h('div', { class: 'badges' }, statusBadge(item.status), badge(label(item.condition))),
      tiles([
        ['Available / bought', `${fmtInt(item.quantity_available)} / ${fmtInt(item.quantity)}`],
        ['Total cost', fmtMoney(item.total_cost_cents), 'units × unit cost + extra costs'],
        ['Target sale', fmtMoney(item.target_sale_cents)],
        ['Realized profit', moneySigned(item.realized_profit_cents)],
        ['Days held', fmtInt(item.days_held)],
      ]),
      h('div', { class: 'grid-2' },
        card('Details', kv([
          ['SKU', item.sku],
          ['Unit cost', fmtMoney(item.unit_cost_cents)],
          ['Extra costs', fmtMoney(item.extra_costs_cents)],
          ['Purchased', fmtDate(item.purchased_at)],
          ['Purchase source', item.purchase_source],
          ['Purchase URL', item.purchase_url ? extLink(item.purchase_url) : null],
          ['Listed on', item.listed_channel],
          ['Location', item.location],
          ['Opportunity', isInt(item.opportunity_id) ? h('a', { href: `#/opportunities/${item.opportunity_id}` }, `#${item.opportunity_id}`) : null],
          ['Notes', item.notes],
        ])),
        card('Status', statusForm)),
      card(`Sales (${sales.length})`, salesTable),
      item.quantity_available > 0
        ? card('Record a sale', saleForm)
        : card('Record a sale', h('p', { class: 'muted' }, 'All units are sold or returned. Delete a sale to restore quantity.')),
      h('details', { class: 'card' }, h('summary', null, 'Edit item'), edit));
  }

  show(initialRoute);
  return { update: show };
}

/* ============================================================================================
 * Analytics view (KPI tiles + SVG bar chart built with createElementNS)
 * ========================================================================================== */

const anState = { days: 30, bucket: '' };

function autoBucket(days) {
  if (days <= 31) return 'day';
  if (days <= 120) return 'week';
  return 'month';
}

function buildBarChart(points, width) {
  const W = Math.max(280, Math.floor(width));
  const H = 280;
  const margin = { top: 14, right: 12, bottom: 34, left: 0 };
  const values = points.flatMap((p) => [p.revenue_cents, p.profit_cents]).filter(isInt);
  const ax = axisTicks(values.length ? Math.min(...values) : 0, values.length ? Math.max(...values) : 0, 5);
  const compact = Math.max(Math.abs(ax.min), Math.abs(ax.max)) >= 1_000_000; // >= $10,000
  const fmtTick = (v) => (ax.step >= 100 ? fmtMoneyAxis(v, compact) : fmtMoney(v));
  margin.left = Math.min(110, 14 + Math.max(...ax.ticks.map((t) => fmtTick(t).length)) * 6.6);
  const plotW = W - margin.left - margin.right;
  const plotH = H - margin.top - margin.bottom;
  const y = (v) => margin.top + ((ax.max - v) / (ax.max - ax.min)) * plotH;
  const n = points.length;
  const band = plotW / n;
  const gap = 2;
  const barW = Math.max(2, Math.min(24, (band - 8) / 2 - gap / 2));
  const y0 = y(0);

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': `Revenue and profit per ${anState.bucket || 'period'}; see the data table below` });
  const grid = s('g', { 'aria-hidden': 'true' });
  for (const t of ax.ticks) {
    const ty = Math.round(y(t)) + 0.5;
    grid.append(
      s('line', { class: t === 0 ? 'baseline' : 'gridline', x1: margin.left, x2: W - margin.right, y1: ty, y2: ty }),
      s('text', { x: margin.left - 6, y: ty, 'text-anchor': 'end', 'dominant-baseline': 'middle' }, fmtTick(t)));
  }
  svg.append(grid);

  // A bar with a 4px rounded data end (top for positive, bottom for negative), square at the baseline.
  const barPath = (x, v) => {
    const yv = y(v);
    const hgt = Math.abs(yv - y0);
    if (hgt < 0.5) return `M${x},${y0}h${barW}v0.5h${-barW}Z`;
    const r = Math.min(4, hgt, barW / 2);
    if (v >= 0) return `M${x},${y0}V${yv + r}Q${x},${yv} ${x + r},${yv}H${x + barW - r}Q${x + barW},${yv} ${x + barW},${yv + r}V${y0}Z`;
    return `M${x},${y0}V${yv - r}Q${x},${yv} ${x + r},${yv}H${x + barW - r}Q${x + barW},${yv} ${x + barW},${yv - r}V${y0}Z`;
  };

  const bars = s('g', { 'aria-hidden': 'true' });
  const labels = s('g', { 'aria-hidden': 'true' });
  const maxLabels = Math.max(1, Math.floor(plotW / 70));
  const every = Math.ceil(n / maxLabels);
  points.forEach((p, i) => {
    const cx = margin.left + band * i + band / 2;
    const x1 = cx - barW - gap / 2;
    const x2 = cx + gap / 2;
    if (isInt(p.revenue_cents)) bars.append(s('path', { class: 'bar-revenue', d: barPath(x1, p.revenue_cents) }));
    if (isInt(p.profit_cents)) bars.append(s('path', { class: 'bar-profit', d: barPath(x2, p.profit_cents) }));
    if (i % every === 0) labels.append(s('text', { x: cx, y: H - margin.bottom + 18, 'text-anchor': 'middle' }, fmtPeriod(p.period)));
  });
  svg.append(bars, labels);
  return { svg, band, margin, plotH, W };
}

function chartBlock(points) {
  const wrap = h('div', { class: 'chart-wrap' });
  const tip = h('div', { class: 'chart-tip', hidden: true, role: 'presentation' });
  let lastWidth = 0;

  function draw() {
    const width = wrap.clientWidth || 640;
    if (Math.abs(width - lastWidth) < 4 && wrap.firstChild) return;
    lastWidth = width;
    const { svg, band, margin, plotH } = buildBarChart(points, width);
    const hits = s('g');
    points.forEach((p, i) => {
      const rect = s('rect', {
        class: 'hit', x: margin.left + band * i, y: margin.top, width: band, height: plotH, tabindex: '0',
        'aria-label': `${fmtPeriod(p.period, true)}: revenue ${fmtMoney(p.revenue_cents)}, profit ${fmtMoney(p.profit_cents)}`,
      });
      const showTip = () => {
        for (const r of hits.querySelectorAll('.active')) r.classList.remove('active');
        rect.classList.add('active');
        setChildren(tip, 
          h('div', { class: 'muted' }, fmtPeriod(p.period, true)),
          h('div', { class: 'tip-row' }, h('span', { class: 'tip-key revenue' }), h('strong', null, fmtMoney(p.revenue_cents)), ' revenue'),
          h('div', { class: 'tip-row' }, h('span', { class: 'tip-key profit' }), h('strong', null, fmtMoney(p.profit_cents)), ' profit'),
          h('div', { class: 'muted' }, `${fmtInt(p.units)} units · purchases ${fmtMoney(p.purchases_cents)}`));
        tip.hidden = false;
        const scale = wrap.clientWidth / Number(svg.getAttribute('width'));
        const center = (margin.left + band * i + band / 2) * scale;
        const tipW = tip.offsetWidth;
        tip.style.left = `${Math.max(0, Math.min(wrap.clientWidth - tipW, center - tipW / 2))}px`;
        tip.style.top = '0px';
      };
      const hideTip = () => {
        rect.classList.remove('active');
        tip.hidden = true;
      };
      rect.addEventListener('pointerenter', showTip);
      rect.addEventListener('pointerleave', hideTip);
      rect.addEventListener('focus', showTip);
      rect.addEventListener('blur', hideTip);
      hits.append(rect);
    });
    svg.append(hits);
    setChildren(wrap, svg, tip);
  }

  let raf = 0;
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(draw);
  }) : null;
  if (ro) ro.observe(wrap);
  requestAnimationFrame(draw);

  const legend = h('div', { class: 'chart-legend' },
    h('span', null, h('span', { class: 'swatch swatch-revenue', 'aria-hidden': 'true' }), 'Revenue'),
    h('span', null, h('span', { class: 'swatch swatch-profit', 'aria-hidden': 'true' }), 'Profit (negative bars = losses)'));
  const dataTable = h('details', null, h('summary', null, 'Show data table'), table([
    { label: 'Period start', cell: (p) => fmtPeriod(p.period, true) },
    { label: 'Revenue', num: true, cell: (p) => fmtMoney(p.revenue_cents) },
    { label: 'Profit', num: true, cell: (p) => moneySigned(p.profit_cents) },
    { label: 'Units', num: true, cell: (p) => fmtInt(p.units) },
    { label: 'Purchases', num: true, cell: (p) => fmtMoney(p.purchases_cents) },
  ], points, { caption: 'Revenue and profit per period' }));
  return { el: h('div', { class: 'stack' }, legend, wrap, dataTable), destroy: () => ro && ro.disconnect() };
}

function AnalyticsView(main) {
  const alive = mountGuard();
  let chart = null;
  const controls = h('form', { class: 'card form-grid', novalidate: true, 'aria-label': 'Analytics period' },
    field('Period', select('days', [['7', 'Last 7 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days'], ['365', 'Last 365 days']], String(anState.days))),
    field('Chart bucket', select('bucket', [['', 'Automatic'], ['day', 'Day'], ['week', 'Week'], ['month', 'Month']], anState.bucket)));
  const body = h('div', { class: 'results' });
  for (const name of ['days', 'bucket']) {
    inputOf(controls, name).addEventListener('change', () => {
      anState.days = Number(inputOf(controls, 'days').value) || 30;
      anState.bucket = inputOf(controls, 'bucket').value;
      load();
    });
  }
  controls.addEventListener('submit', (ev) => ev.preventDefault());

  main.append(viewHeader('Analytics', 'Realized results from your recorded sales and inventory, plus what FLIP RADAR is tracking. All figures are computed from stored records.'), controls, body);

  async function load() {
    body.classList.add('loading');
    const bucket = anState.bucket || autoBucket(anState.days);
    try {
      const [sum, ts] = await Promise.all([
        api('/analytics/summary', { query: { days: anState.days } }),
        api('/analytics/timeseries', { query: { days: anState.days, bucket } }),
      ]);
      if (!alive()) return;
      if (chart) chart.destroy();
      const points = ts && Array.isArray(ts.points) ? ts.points : [];
      chart = points.length ? chartBlock(points) : null;
      const aging = sum.aging && typeof sum.aging === 'object' ? sum.aging : {};
      const agingOrder = ['0-7', '8-30', '31-90', '90+'];
      const agingEntries = [...agingOrder.filter((k) => k in aging), ...Object.keys(aging).filter((k) => !agingOrder.includes(k))].map((k) => [`${k} days`, aging[k]]);
      const bySource = Object.entries(sum.opportunities_by_source || {}).sort((a, b) => b[1] - a[1]);
      const top = Array.isArray(sum.top_products_by_profit) ? sum.top_products_by_profit : [];
      setChildren(body, 
        tiles([
          ['Revenue', fmtMoney(sum.revenue_cents), `last ${sum.period_days} days`],
          ['Realized profit', moneySigned(sum.realized_profit_cents)],
          ['Average ROI', fmtBp(sum.avg_roi_bp), 'profit ÷ cost basis of sales'],
          ['Units sold', fmtInt(sum.units_sold), `${fmtInt(sum.sales_count)} sales`],
          ['Avg days to sell', typeof sum.avg_days_to_sell === 'number' ? NF.dec1.format(sum.avg_days_to_sell) : '—'],
          ['Units in inventory', fmtInt(sum.inventory_units)],
          ['Capital tied up', fmtMoney(sum.inventory_cost_cents), 'cost basis of unsold units'],
          ['Inventory target value', fmtMoney(sum.inventory_target_value_cents)],
          ['Open opportunities', fmtInt(sum.open_opportunities), `avg score ${fmtScore(sum.avg_open_opportunity_score)}`],
          ['Potential profit (open)', fmtMoney(sum.potential_profit_open_cents)],
          ['Listings / products tracked', `${fmtInt(sum.listings_tracked)} / ${fmtInt(sum.products_tracked)}`],
          ['Last collection', fmtDateTime(sum.last_collection_at)],
        ]),
        card(`Revenue and profit per ${ts && ts.bucket ? ts.bucket : bucket}`, chart ? chart.el : h('p', { class: 'muted' }, 'No sales or purchases recorded in this period.')),
        h('div', { class: 'grid-2' },
          card('Inventory aging (units)', agingEntries.length ? hbars(agingEntries) : h('p', { class: 'muted' }, 'No unsold inventory.')),
          card('Open opportunities by source', bySource.length ? hbars(bySource) : h('p', { class: 'muted' }, 'No open opportunities.'))),
        card('Top products by realized profit', top.length ? genericTable(top, 'Top products by profit', ['title', 'product_title', 'profit_cents', 'revenue_cents', 'units']) : h('p', { class: 'muted' }, 'No sales recorded in this period.')));
    } catch (err) {
      if (alive()) setChildren(body, errorBox(err));
    } finally {
      body.classList.remove('loading');
    }
  }

  load().finally(() => {
    if (alive()) markReady(main, 'analytics');
  });
  return {
    destroy() {
      if (chart) chart.destroy();
    },
  };
}

/* ============================================================================================
 * Searches & Sources view
 * ========================================================================================== */

function sourceChecks(sources, name, selected = [], filterFn = () => true) {
  const list = (sources || []).filter(filterFn);
  if (!list.length) return h('p', { class: 'muted small' }, 'No sources available.');
  return h('div', { class: 'checks' }, list.map((src) => {
    const id = nextId('src');
    return h('label', { for: id }, h('input', { type: 'checkbox', id, name, value: src.key, checked: selected.includes(src.key) }), `${src.name}${src.configured ? '' : ' (not configured)'}`);
  }));
}

const checkedValues = (form, name) => [...form.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);

function renderCollectResult(res) {
  const runs = Array.isArray(res.runs) ? res.runs : [];
  return h('div', { class: 'stack' },
    h('p', null, `Opportunities created: ${fmtInt(res.opportunities_created)}, updated: ${fmtInt(res.opportunities_updated)}; notifications: ${fmtInt(res.notifications_created)}.`),
    runs.length ? runsTable(runs) : h('p', { class: 'muted' }, 'No source was run (none enabled and configured with search capability?).'));
}

function runsTable(runs) {
  return table([
    { label: 'Run', cell: (r) => `#${r.id}` },
    { label: 'Source', cell: (r) => r.source_key || '—' },
    { label: 'Query', cell: (r) => r.query || '—' },
    { label: 'Started', cell: (r) => fmtDateTime(r.started_at) },
    { label: 'Status', cell: (r) => statusBadge(r.status) },
    { label: 'Fetched', num: true, cell: (r) => fmtInt(r.items_fetched) },
    { label: 'New', num: true, cell: (r) => fmtInt(r.items_new) },
    { label: 'Updated', num: true, cell: (r) => fmtInt(r.items_updated) },
    { label: 'Error', cell: (r) => (r.error ? h('span', { class: 'neg small' }, r.error) : '—') },
  ], runs, { caption: 'Collection runs' });
}

function SourcesView(main) {
  const alive = mountGuard();
  let sources = [];
  let profiles = [];
  let editing = null; // tracked search being edited

  const searchesBox = h('div');
  const searchFormBox = h('div');
  const collectBox = h('div');
  const sourcesBox = h('div', { class: 'source-grid' });
  const runsBox = h('div');
  const recomputeOut = h('div', { 'aria-live': 'polite' });
  const importOut = h('div', { 'aria-live': 'polite' });
  const urlOut = h('div', { 'aria-live': 'polite' });

  main.append(
    viewHeader('Searches & Sources', 'Where data comes from. Only official APIs, feeds you are allowed to read, your own CSV files and single URLs you check (robots.txt respected) are used.',
      [button('Recompute opportunities', async () => {
        const res = await api('/analyze/recompute', { method: 'POST' });
        setChildren(recomputeOut, card('Recompute result', kv([
          ['Products analyzed', fmtInt(res.products_analyzed)],
          ['Opportunities created', fmtInt(res.opportunities_created)],
          ['Opportunities updated', fmtInt(res.opportunities_updated)],
          ['Opportunities expired', fmtInt(res.opportunities_expired)],
          ['Notifications created', fmtInt(res.notifications_created)],
        ])));
        toast('Opportunities recomputed.', 'success');
      }, { kind: 'primary' })]),
    recomputeOut,
    card('Tracked searches', searchesBox, searchFormBox),
    card('Collect now (one-off search)', collectBox),
    h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, 'Sources'), button('Refresh', () => loadSources(true), { small: true })), sourcesBox),
    h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, 'Recent collection runs'), button('Refresh', () => loadRuns(), { small: true })), runsBox),
    h('div', { class: 'grid-2' }, importCard(), urlCard()));

  // ---- tracked searches
  async function loadSearches() {
    try {
      const list = await api('/searches');
      if (!alive()) return;
      setChildren(searchesBox, list.length
        ? table([
          { label: 'Query', cell: (t) => [t.query, t.category_hint ? h('div', { class: 'sub muted' }, `category: ${t.category_hint}`) : null] },
          { label: 'Sources', cell: (t) => (t.source_keys && t.source_keys.length ? t.source_keys.join(', ') : 'all enabled') },
          { label: 'Max results', num: true, cell: (t) => fmtInt(t.max_results) },
          { label: 'Every', num: true, cell: (t) => fmtMinutes(t.interval_minutes) },
          { label: 'Enabled', cell: (t) => {
            const cb = h('input', { type: 'checkbox', checked: t.enabled, 'aria-label': `Enable search ${t.query}` });
            cb.addEventListener('change', async () => {
              cb.disabled = true;
              try {
                await api(`/searches/${t.id}`, { method: 'PATCH', json: { enabled: cb.checked } });
                toast(`Search "${t.query}" ${cb.checked ? 'enabled' : 'disabled'}.`, 'success');
              } catch (err) {
                cb.checked = !cb.checked;
                reportError(err);
              } finally {
                cb.disabled = false;
              }
            });
            return cb;
          } },
          { label: 'Last run', cell: (t) => fmtDateTime(t.last_run_at) },
          { label: 'Actions', cell: (t) => h('div', { class: 'row-actions' },
            button('Run now', async () => {
              try {
                const res = await api(`/searches/${t.id}/run`, { method: 'POST' });
                setChildren(collectBox.querySelector('.collect-out'), card(`Run result: "${t.query}"`, renderCollectResult(res)));
                toast(`Search "${t.query}" finished.`, 'success');
              } finally {
                // a run (even a failed one) changes the search's and each source's last run / status / error
                loadSearches();
                loadRuns();
                loadSources(true);
              }
            }, { small: true, kind: 'primary' }),
            button('Edit', () => {
              editing = t;
              renderSearchForm();
              searchFormBox.querySelector('input[name="query"]').focus();
            }, { small: true }),
            button('Delete', async () => {
              if (!window.confirm(`Delete tracked search "${t.query}"?`)) return;
              await api(`/searches/${t.id}`, { method: 'DELETE' });
              toast('Search deleted.', 'success');
              if (editing && editing.id === t.id) {
                editing = null;
                renderSearchForm();
              }
              loadSearches();
            }, { small: true, kind: 'danger' })) },
        ], list, { caption: 'Tracked searches' })
        : h('p', { class: 'muted' }, 'No tracked searches yet. Add one below; the scheduler runs enabled searches at their interval.'));
    } catch (err) {
      if (alive()) setChildren(searchesBox, errorBox(err));
    }
  }

  function renderSearchForm() {
    const t = editing;
    const f = h('form', { class: 'form-grid', novalidate: true, 'aria-label': t ? 'Edit tracked search' : 'Add tracked search' },
      h('h3', { class: 'field-wide' }, t ? `Edit search #${t.id}` : 'Add tracked search'),
      field('Query', textInput('query', { required: true, minlength: '2', maxlength: '200', value: t ? t.query : '', placeholder: 'e.g. nintendo switch oled' })),
      field('Category hint', textInput('category_hint', { maxlength: '64', value: t ? t.category_hint || '' : '', placeholder: 'optional, e.g. console' })),
      field('Max results', numInput('max_results', { min: '1', max: '200', value: String(t ? t.max_results : 50) })),
      field('Interval (minutes)', numInput('interval_minutes', { min: '15', max: '10080', value: String(t ? t.interval_minutes : 180) }), { hint: '15 min – 7 days' }),
      checkField('Enabled', checkbox('enabled', t ? t.enabled : true)),
      h('fieldset', { class: 'field-wide' }, h('legend', null, 'Sources (none checked = all enabled sources with search)'),
        sourceChecks(sources, 'source_keys', t ? t.source_keys || [] : [], (src) => (src.capabilities || []).includes('search'))),
      h('div', { class: 'form-actions' },
        h('button', { type: 'submit', class: 'btn btn-primary' }, t ? 'Save search' : 'Add search'),
        t ? h('button', { type: 'button', class: 'btn', on: { click: () => { editing = null; renderSearchForm(); } } }, 'Cancel edit') : null));
    onSubmit(f, async () => {
      const body = {
        query: readStr(f, 'query', { required: true }),
        category_hint: readStr(f, 'category_hint'),
        source_keys: checkedValues(f, 'source_keys'),
        max_results: readInt(f, 'max_results', { required: true, min: 1, max: 200 }),
        interval_minutes: readInt(f, 'interval_minutes', { required: true, min: 15, max: 10080 }),
        enabled: readBool(f, 'enabled'),
      };
      if (body.query.length < 2) throw invalid(f, 'query', 'Query: at least 2 characters');
      if (editing) {
        await api(`/searches/${editing.id}`, { method: 'PATCH', json: body });
        toast('Search updated.', 'success');
      } else {
        await api('/searches', { method: 'POST', json: body });
        toast('Search added.', 'success');
      }
      editing = null;
      renderSearchForm();
      await loadSearches();
    });
    setChildren(searchFormBox, h('div', { class: 'inline-form' }, f));
  }

  // ---- ad-hoc collect
  function renderCollectForm() {
    const f = h('form', { class: 'form-grid', novalidate: true, 'aria-label': 'Collect now' },
      field('Query', textInput('query', { required: true, minlength: '2', maxlength: '200', placeholder: 'e.g. ps5 slim' })),
      field('Max results per source', numInput('limit', { min: '1', max: '200', value: '50' })),
      checkField('Analyze after collecting', checkbox('analyze', true)),
      h('fieldset', { class: 'field-wide' }, h('legend', null, 'Sources (none checked = all enabled & configured)'),
        sourceChecks(sources, 'source_keys', [], (src) => (src.capabilities || []).includes('search'))),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Collect now')));
    const out = h('div', { class: 'collect-out', 'aria-live': 'polite' });
    onSubmit(f, async () => {
      const body = {
        query: readStr(f, 'query', { required: true }),
        source_keys: checkedValues(f, 'source_keys'),
        limit: readInt(f, 'limit', { required: true, min: 1, max: 200 }),
        analyze: readBool(f, 'analyze'),
      };
      if (body.query.length < 2) throw invalid(f, 'query', 'Query: at least 2 characters');
      setChildren(out, h('p', { class: 'muted' }, 'Collecting… (sources are rate limited; this can take a while)'));
      try {
        const res = await api('/collect', { method: 'POST', json: body });
        setChildren(out, renderCollectResult(res));
        toast('Collection finished.', 'success');
      } catch (err) {
        setChildren(out, errorBox(err));
        throw err;
      } finally {
        loadRuns();
        loadSources(true);
      }
    });
    setChildren(collectBox, f, out);
  }

  // ---- sources
  async function loadSources(force = false) {
    try {
      sources = await getSources(force);
      if (!alive()) return;
      setChildren(sourcesBox, ...(sources.length ? sources.map(sourceCard) : [h('p', { class: 'muted' }, 'No sources registered.')]));
    } catch (err) {
      if (alive()) setChildren(sourcesBox, errorBox(err));
    }
  }

  function sourceCard(src) {
    const testOut = h('div', { class: 'test-result', 'aria-live': 'polite' });
    const enabled = h('input', { type: 'checkbox', checked: src.enabled });
    enabled.addEventListener('change', async () => {
      enabled.disabled = true;
      try {
        await api(`/sources/${encodeURIComponent(src.key)}`, { method: 'PATCH', json: { enabled: enabled.checked } });
        src.enabled = enabled.checked;
        toast(`${src.name} ${enabled.checked ? 'enabled' : 'disabled'}.`, 'success');
      } catch (err) {
        enabled.checked = !enabled.checked;
        reportError(err);
      } finally {
        enabled.disabled = false;
      }
    });
    const feeSel = select('fee', feeProfileOptions(profiles, 'No resale fee profile'), src.default_fee_profile_key || '');
    feeSel.addEventListener('change', async () => {
      try {
        await api(`/sources/${encodeURIComponent(src.key)}`, { method: 'PATCH', json: { default_fee_profile_key: feeSel.value || null } });
        src.default_fee_profile_key = feeSel.value || null;
        toast(`Resale fee profile of ${src.name} updated.`, 'success');
      } catch (err) {
        feeSel.value = src.default_fee_profile_key || '';
        reportError(err);
      }
    });
    const enabledId = nextId('srcen');
    enabled.id = enabledId;
    const feeId = nextId('srcfee');
    feeSel.id = feeId;
    return h('article', { class: 'source-card' },
      h('div', null, h('h3', null, src.name), h('code', null, src.key), ' ', badge(src.kind), ' ', badge(`${src.country} · ${src.currency}`)),
      h('div', { class: 'badges' },
        src.configured ? badge('✓ configured', 'good') : badge(src.requires_credentials ? '✖ needs credentials' : '✖ not configured', 'warn'),
        src.last_status ? statusBadge(src.last_status) : null),
      src.notes ? h('p', { class: 'small' }, src.notes) : null,
      kv([
        ['Capabilities', (src.capabilities || []).join(', ') || '—'],
        ['Last run', fmtDateTime(src.last_run_at)],
        src.last_error ? ['Last error', h('span', { class: 'neg' }, src.last_error)] : null,
        ['Documentation', src.docs_url ? extLink(src.docs_url, 'official docs') : '—'],
      ]),
      h('div', { class: 'source-controls' },
        h('label', { for: enabledId }, enabled, 'Enabled'),
        h('label', { for: feeId }, 'Resale fees:'), feeSel,
        button('Test', async () => {
          setChildren(testOut, h('span', { class: 'muted' }, 'Testing…'));
          const res = await api(`/sources/${encodeURIComponent(src.key)}/test`, { method: 'POST' });
          const kind = res.ok === true ? 'good' : res.ok === false ? 'crit' : 'warn';
          setChildren(testOut, 
            h('div', { class: 'badges' }, badge(res.ok === true ? '✓ OK' : res.ok === false ? '✖ failed' : 'not checked', kind), res.configured ? null : badge('not configured', 'warn')),
            h('p', null, res.message),
            res.details && Object.keys(res.details).length ? h('details', null, h('summary', null, 'Details'), kv(Object.entries(res.details).map(([k, v]) => [humanizeKey(k), fmtValue(k, v)]))) : null);
        }, { small: true })),
      testOut);
  }

  async function loadRuns() {
    try {
      const runs = await api('/runs', { query: { limit: 50 } });
      if (!alive()) return;
      setChildren(runsBox, runs.length ? runsTable(runs) : h('p', { class: 'muted' }, 'No collection runs yet.'));
    } catch (err) {
      if (alive()) setChildren(runsBox, errorBox(err));
    }
  }

  function importCard() {
    const f = h('form', { class: 'form-grid', novalidate: true, 'aria-label': 'Import CSV' },
      field('CSV file (max 5 MB)', h('input', { type: 'file', name: 'file', accept: '.csv,text/csv', required: true }), { wide: true }),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Upload & import')));
    onSubmit(f, async () => {
      const input = inputOf(f, 'file');
      const file = input.files && input.files[0];
      if (!file) throw invalid(f, 'file', 'Choose a CSV file first.');
      if (file.size > 5 * 1024 * 1024) throw invalid(f, 'file', 'The file is larger than 5 MB.');
      const fd = new FormData();
      fd.append('file', file, file.name);
      const res = await api('/import/csv', { method: 'POST', form: fd });
      setChildren(importOut, 
        kv([['Rows', fmtInt(res.rows_total)], ['Imported', fmtInt(res.rows_imported)], ['Updated', fmtInt(res.rows_updated)], ['Rejected', fmtInt(res.rows_rejected)]]),
        Array.isArray(res.errors) && res.errors.length
          ? h('details', { open: true }, h('summary', null, `Errors (${res.errors.length})`), h('ul', { class: 'small' }, res.errors.slice(0, 200).map((e) => h('li', null, e))))
          : null);
      toast(`CSV imported: ${fmtInt(res.rows_imported)} new, ${fmtInt(res.rows_updated)} updated.`, 'success');
    });
    return card('Import CSV (your own price data)', f,
      h('details', null, h('summary', null, 'CSV format'),
        h('p', { class: 'small' }, 'Header row required. Required columns: ', h('code', null, 'title'), ', ', h('code', null, 'price'), ' (pesos, e.g. 12999.00). Optional: currency, url, condition, shipping, source (default manual), external_id, brand, model, gtin, seller, location, sold_quantity, available_quantity, observed_at (ISO date). Max 20 000 rows.')),
      importOut);
  }

  function urlCard() {
    const f = h('form', { class: 'form-grid', novalidate: true, 'aria-label': 'Check a product URL' },
      field('Product page URL', h('input', { type: 'url', name: 'url', required: true, placeholder: 'https://…', autocomplete: 'off' }), { wide: true }),
      checkField('Save as a listing', checkbox('save', true)),
      h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Check URL')));
    onSubmit(f, async () => {
      const url = readStr(f, 'url', { required: true });
      if (!safeHttpUrl(url)) throw invalid(f, 'url', 'URL: must start with http:// or https://');
      const res = await api('/check-url', { method: 'POST', json: { url, save: readBool(f, 'save') } });
      const l = res.listing;
      setChildren(urlOut, 
        h('div', { class: 'badges' },
          res.allowed_by_robots ? badge('✓ allowed by robots.txt', 'good') : badge('✖ blocked by robots.txt', 'crit'),
          res.found ? badge('✓ product data found', 'good') : badge('no product data found', 'warn')),
        res.message ? h('p', null, res.message) : null,
        l ? kv([['Listing', extLink(l.url, l.title)], ['Price', fmtMoney(l.price_cents, l.currency)], ['Condition', l.condition], ['Saved as listing', `#${l.id}`]]) : null,
        res.extracted && Object.keys(res.extracted).length
          ? h('details', null, h('summary', null, 'Extracted fields'), kv(Object.entries(res.extracted).map(([k, v]) => [humanizeKey(k), fmtValue(k, v)])))
          : null);
    });
    return card('Check a single product URL', h('p', { class: 'muted small' }, 'Fetches one page you choose (robots.txt respected) and reads schema.org Product/Offer data. No crawling.'), f, urlOut);
  }

  (async () => {
    try {
      profiles = await getFeeProfiles().catch(() => []);
      await loadSources(true); // never a cached list: runs (also scheduled ones) change status and errors
      if (!alive()) return;
      renderSearchForm();
      renderCollectForm();
      await Promise.all([loadSearches(), loadRuns()]);
    } finally {
      if (alive()) markReady(main, 'sources');
    }
  })();
}

/* ============================================================================================
 * Alerts view
 * ========================================================================================== */

const ALERT_CHANNELS = [['in_app', 'In-app'], ['ntfy', 'ntfy push'], ['telegram', 'Telegram'], ['webhook', 'Webhook']];

function AlertsView(main) {
  const alive = mountGuard();
  let editing = null;
  const listBox = h('div');
  const formBox = h('div');
  main.append(
    viewHeader('Alerts', 'Rules evaluated whenever opportunities are created or updated. Matching opportunities create notifications on the channels you choose (external channels must be configured on the server).'),
    card('Alert rules', listBox),
    card(null, formBox));

  function renderForm() {
    const r = editing;
    const f = h('form', { class: 'form-grid', novalidate: true, 'aria-label': r ? 'Edit alert rule' : 'New alert rule' },
      h('h2', { class: 'field-wide' }, r ? `Edit rule "${r.name}"` : 'New alert rule'),
      field('Name', textInput('name', { required: true, maxlength: '120', value: r ? r.name : '' })),
      field('Keywords (all must match)', textInput('keywords', { maxlength: '200', value: r ? r.keywords || '' : '' })),
      field('Brand', textInput('brand', { maxlength: '80', value: r ? r.brand || '' : '' })),
      field('Category', textInput('category', { maxlength: '80', value: r ? r.category || '' : '' })),
      field('Min net profit (MXN)', moneyInput('min_profit', { value: r ? centsToInput(r.min_profit_cents) : '' })),
      field('Min ROI (%)', pctInput('min_roi', { value: r ? bpToInput(r.min_roi_bp) : '' })),
      field('Min score (0–100)', numInput('min_score', { min: '0', max: '100', step: '0.1', value: r && r.min_score !== null ? String(r.min_score) : '' })),
      field('Max buy price (MXN)', moneyInput('max_buy', { value: r ? centsToInput(r.max_buy_cents) : '' })),
      field('Min confidence', select('min_confidence', [['', 'Any'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High']], r ? r.min_confidence || '' : '')),
      checkField('Exclude suspicious listings', checkbox('exclude_suspicious', r ? r.exclude_suspicious : true)),
      checkField('Enabled', checkbox('enabled', r ? r.enabled : true)),
      h('fieldset', { class: 'field-wide' }, h('legend', null, 'Channels'),
        h('div', { class: 'checks' }, ALERT_CHANNELS.map(([value, text]) => {
          const id = nextId('ch');
          return h('label', { for: id }, h('input', { type: 'checkbox', id, name: 'channels', value, checked: r ? (r.channels || []).includes(value) : value === 'in_app' }), text);
        }))),
      h('div', { class: 'form-actions' },
        h('button', { type: 'submit', class: 'btn btn-primary' }, r ? 'Save rule' : 'Create rule'),
        r ? h('button', { type: 'button', class: 'btn', on: { click: () => { editing = null; renderForm(); } } }, 'Cancel edit') : null));
    onSubmit(f, async () => {
      const body = {
        name: readStr(f, 'name', { required: true }),
        keywords: readStr(f, 'keywords'),
        brand: readStr(f, 'brand'),
        category: readStr(f, 'category'),
        min_profit_cents: readMoney(f, 'min_profit'),
        min_roi_bp: readPct(f, 'min_roi'),
        min_score: readFloat(f, 'min_score', { min: 0, max: 100 }),
        max_buy_cents: readMoney(f, 'max_buy'),
        min_confidence: inputOf(f, 'min_confidence').value || null,
        exclude_suspicious: readBool(f, 'exclude_suspicious'),
        channels: checkedValues(f, 'channels'),
        enabled: readBool(f, 'enabled'),
      };
      if (!body.channels.length) throw new InputError('Choose at least one channel.', 'channels');
      if (editing) {
        await api(`/alerts/${editing.id}`, { method: 'PATCH', json: body });
        toast('Alert rule updated.', 'success');
      } else {
        await api('/alerts', { method: 'POST', json: body });
        toast('Alert rule created.', 'success');
      }
      editing = null;
      renderForm();
      await load();
    });
    setChildren(formBox, f);
  }

  async function load() {
    try {
      const rules = await api('/alerts');
      if (!alive()) return;
      setChildren(listBox, rules.length
        ? table([
          { label: 'Name', cell: (r) => r.name },
          { label: 'Matches', cell: (r) => [r.keywords ? `“${r.keywords}”` : null, r.brand ? ` brand: ${r.brand}` : null, r.category ? ` category: ${r.category}` : null].filter(Boolean).join(' ') || 'any product' },
          { label: 'Min profit', num: true, cell: (r) => fmtMoney(r.min_profit_cents) },
          { label: 'Min ROI', num: true, cell: (r) => fmtBp(r.min_roi_bp) },
          { label: 'Min score', num: true, cell: (r) => fmtScore(r.min_score) },
          { label: 'Max buy', num: true, cell: (r) => fmtMoney(r.max_buy_cents) },
          { label: 'Confidence', cell: (r) => r.min_confidence || 'any' },
          { label: 'Suspicious', cell: (r) => (r.exclude_suspicious ? 'excluded' : 'included') },
          { label: 'Channels', cell: (r) => (r.channels || []).join(', ') },
          { label: 'Enabled', cell: (r) => {
            const cb = h('input', { type: 'checkbox', checked: r.enabled, 'aria-label': `Enable rule ${r.name}` });
            cb.addEventListener('change', async () => {
              cb.disabled = true;
              try {
                await api(`/alerts/${r.id}`, { method: 'PATCH', json: { enabled: cb.checked } });
                toast(`Rule "${r.name}" ${cb.checked ? 'enabled' : 'disabled'}.`, 'success');
              } catch (err) {
                cb.checked = !cb.checked;
                reportError(err);
              } finally {
                cb.disabled = false;
              }
            });
            return cb;
          } },
          { label: 'Actions', cell: (r) => h('div', { class: 'row-actions' },
            button('Edit', () => {
              editing = r;
              renderForm();
              formBox.querySelector('input[name="name"]').focus();
            }, { small: true }),
            button('Delete', async () => {
              if (!window.confirm(`Delete alert rule "${r.name}"?`)) return;
              await api(`/alerts/${r.id}`, { method: 'DELETE' });
              toast('Alert rule deleted.', 'success');
              if (editing && editing.id === r.id) {
                editing = null;
                renderForm();
              }
              await load();
            }, { small: true, kind: 'danger' })) },
        ], rules, { caption: 'Alert rules' })
        : h('p', { class: 'muted' }, 'No alert rules yet. Create one below to get notified about matching opportunities.'));
    } catch (err) {
      if (alive()) setChildren(listBox, errorBox(err));
    }
  }

  renderForm();
  load().finally(() => {
    if (alive()) markReady(main, 'alerts');
  });
}

/* ============================================================================================
 * Notifications view
 * ========================================================================================== */

const notifState = { unreadOnly: false, limit: 50 };

function NotificationsView(main) {
  const alive = mountGuard();
  const listBox = h('div', { class: 'results' });
  const unreadCb = checkbox('unread_only', notifState.unreadOnly);
  unreadCb.addEventListener('change', () => {
    notifState.unreadOnly = unreadCb.checked;
    load();
  });
  main.append(
    viewHeader('Notifications', 'Created by your alert rules (and system events). Newest first.', [
      button('Mark all read', async () => {
        await api('/notifications/read-all', { method: 'POST' });
        toast('All notifications marked as read.', 'success');
        await load();
        refreshUnread();
      }),
      button('Refresh', () => load()),
    ]),
    h('div', { class: 'card' }, checkField('Show unread only', unreadCb)),
    listBox);

  async function load() {
    listBox.classList.add('loading');
    try {
      const list = await api('/notifications', { query: { unread_only: notifState.unreadOnly ? 'true' : 'false', limit: notifState.limit } });
      if (!alive()) return;
      setChildren(listBox, list.length
        ? h('ul', { class: 'notif-list' }, list.map((n) => {
          const unread = !n.read_at;
          return h('li', { class: ['notif', unread && 'unread'] },
            h('div', { class: 'notif-head' },
              h('span', { class: 'notif-title' }, unread ? h('span', { class: 'sr-only' }, 'Unread: ') : null, n.title),
              h('span', { class: 'muted small' }, badge(n.kind), ' ', fmtDateTime(n.created_at))),
            h('p', { class: 'notif-body' }, n.body),
            h('div', { class: 'row-actions' },
              isInt(n.opportunity_id) ? h('a', { class: 'btn btn-sm', href: `#/opportunities/${n.opportunity_id}` }, 'Open opportunity') : null,
              unread ? button('Mark read', async () => {
                await api(`/notifications/${n.id}/read`, { method: 'POST' });
                await load();
                refreshUnread();
              }, { small: true }) : h('span', { class: 'muted small' }, `read ${fmtDateTime(n.read_at)}`)));
        }))
        : h('div', { class: 'empty' }, h('h2', null, notifState.unreadOnly ? 'No unread notifications' : 'No notifications yet'),
          h('p', null, 'Notifications are created when an opportunity matches one of your ', h('a', { href: '#/alerts' }, 'alert rules'), '.')));
    } catch (err) {
      if (alive()) setChildren(listBox, errorBox(err));
    } finally {
      listBox.classList.remove('loading');
    }
  }

  load().finally(() => {
    if (alive()) markReady(main, 'notifications');
  });
}

/* ============================================================================================
 * Settings view (app settings + fee profiles)
 * ========================================================================================== */

function tiersText(tiers) {
  if (!Array.isArray(tiers) || !tiers.length) return 'none';
  return tiers.map((t) => `${isInt(t.max_price_cents) ? `≤ ${fmtMoney(t.max_price_cents)}` : 'above'}: ${fmtMoney(t.fee_cents)}`).join('; ');
}

/** Editor for settings.category_fee_profiles: one row per category with a resale fee profile for new and for used
 *  units.  Stored values the lists do not know (a deleted profile, a category set through the API) stay selectable. */
function categoryFeeEditor(mapping, profiles) {
  const rowsBox = h('div', { class: 'catfee-rows' });
  const profileOpts = (current) => {
    const opts = feeProfileOptions(profiles, 'Default profile');
    if (current && !opts.some(([v]) => v === current)) opts.push([current, `${current} (missing profile)`]);
    return opts;
  };
  const categoryOpts = (current) => {
    const opts = [['', 'Choose a category'], ...CATEGORIES.map((c) => [c, categoryLabel(c)])];
    if (current && !CATEGORIES.includes(current)) opts.push([current, `${current} (not a FLIP RADAR category)`]);
    return opts;
  };
  function row(r = { category: '', new: '', used: '' }) {
    const cat = select('catfee_category', categoryOpts(r.category), r.category);
    const remove = h('button', { type: 'button', class: 'btn btn-sm btn-danger', on: { click: () => el.remove() } }, 'Remove');
    const describe = () => remove.setAttribute('aria-label', `Remove the ${cat.value ? categoryLabel(cat.value) : 'empty'} category row`);
    cat.addEventListener('change', describe);
    describe();
    const el = h('div', { class: 'catfee-row' },
      field('Category', cat),
      field('Profile for new / refurbished units', select('catfee_new', profileOpts(r.new), r.new)),
      field('Profile for used units', select('catfee_used', profileOpts(r.used), r.used)),
      remove);
    return el;
  }
  for (const r of categoryFeeRows(mapping)) rowsBox.append(row(r));
  const el = h('fieldset', { class: 'field-wide catfee' },
    h('legend', null, 'Resale fee profile by category'),
    h('p', { class: 'hint' }, 'Heavier items ship in a more expensive tier: map their categories to a matching fee profile. Categories without a row, and conditions left on “Default profile”, use the default profiles above.'),
    rowsBox,
    h('button', { type: 'button', class: 'btn btn-sm', on: { click: () => {
      const r = row();
      rowsBox.append(r);
      r.querySelector('select').focus();
    } } }, '+ Add category'));
  function read() {
    const rows = [...rowsBox.querySelectorAll('.catfee-row')];
    for (const s of rowsBox.querySelectorAll('[aria-invalid]')) s.removeAttribute('aria-invalid');
    try {
      return buildCategoryFeeProfiles(rows.map((r) => ({
        category: r.querySelector('select[name="catfee_category"]').value,
        new: r.querySelector('select[name="catfee_new"]').value,
        used: r.querySelector('select[name="catfee_used"]').value,
      })));
    } catch (err) {
      const bad = rows[err.row] && rows[err.row].querySelector(`select[name="catfee_${err.field || 'category'}"]`);
      if (bad) {
        bad.setAttribute('aria-invalid', 'true');
        bad.focus();
      }
      throw new InputError(err.message, 'category_fee_profiles');
    }
  }
  return { el, read };
}

function SettingsView(main) {
  const alive = mountGuard();
  const settingsBox = h('div');
  const profilesBox = h('div');
  const profileFormBox = h('div');
  let profiles = [];
  let editing = null;

  main.append(
    viewHeader('Settings', 'Opportunity thresholds, pricing and the fee profiles used to compute resale costs. Fee values must be verified against the official fee pages; each profile records its source and date.'),
    card('Analysis settings', settingsBox),
    card('Fee profiles', profilesBox),
    card(null, profileFormBox));

  async function loadSettings() {
    try {
      const st = await api('/settings');
      if (!alive()) return;
      const catFees = categoryFeeEditor(st.category_fee_profiles, profiles);
      const f = h('form', { class: 'form-grid', novalidate: true, 'aria-label': 'Analysis settings' },
        h('p', { class: 'field-wide muted small' }, 'Changes to thresholds, pricing and resale fee profiles apply when opportunities are recomputed: press "Recompute opportunities" on Searches & Sources (a collection run only recomputes the products it touched).'),
        field('Min comparables', numInput('min_comparables', { min: '2', max: '100', value: String(st.min_comparables) }), { hint: 'Listings needed before an expected price is computed.' }),
        field('Comparable max age (days)', numInput('comparable_max_age_days', { min: '1', max: '365', value: String(st.comparable_max_age_days) })),
        field('Buy listing max age (days)', numInput('buy_max_age_days', { min: '1', max: '90', value: String(st.buy_max_age_days ?? 3) }), { hint: 'A collected listing not seen within this many days is never an opportunity (it may be gone).' }),
        field('Manual / CSV listing max age (days)', numInput('manual_buy_max_age_days', { min: '1', max: '365', value: String(st.manual_buy_max_age_days ?? 30) }), { hint: 'Listings you entered yourself (manual entry or CSV import) are not re-collected, so they get their own, longer window.' }),
        field('Pricing strategy', select('pricing_strategy', [['competitive', 'Competitive: 25th percentile of comparable prices'], ['market', 'Market: median of comparable prices']], st.pricing_strategy), { hint: 'Used while fewer than "Min comparables" sold prices are known; otherwise their median is used.' }),
        field('Min ROI for opportunities (%)', pctInput('min_roi', { value: bpToInput(st.min_roi_bp) }), { hint: 'Listings below this ROI do not become opportunities.' }),
        field('Min net profit (MXN)', moneyInput('min_profit', { value: centsToInput(st.min_profit_cents) }), { hint: 'Listings below this net profit do not become opportunities.' }),
        field('Resale fee profile: new / refurbished units', select('default_fee_profile_key', feeProfileOptions(profiles), st.default_fee_profile_key)),
        field('Resale fee profile: used / unknown-condition units', select('default_fee_profile_key_used', feeProfileOptions(profiles), st.default_fee_profile_key_used)),
        field('Shipping assumed when unknown (MXN)', moneyInput('unknown_shipping', { value: centsToInput(st.unknown_shipping_assumption_cents) }), { hint: 'Added to the buy cost when a listing does not report shipping (it is flagged "shipping unknown"). Empty = $0.' }),
        field('Calculator target ROI (%)', pctInput('target_roi', { value: bpToInput(st.target_roi_bp) }), { hint: 'Default target for the maximum buy price in the Calculator. It does not filter opportunities.' }),
        field('Manual USD → MXN rate', textInput('fx', { inputmode: 'decimal', value: st.fx_usd_mxn_manual || '', placeholder: 'only without Banxico' }), { hint: 'Used only when no automatic FX source is configured.' }),
        checkField('Use exact Mercado Libre category commissions', checkbox('use_marketplace_category_fees', st.use_marketplace_category_fees ?? true),
          'When the per-category commission has been fetched from Mercado Libre and cached, it replaces the fee profile’s generic commission. The opportunity detail shows which commission was used.'),
        checkField('Use your own sales as sold-price evidence', checkbox('use_own_sales_as_evidence', st.use_own_sales_as_evidence ?? true),
          'Sales you recorded in Inventory count as sold prices for the same product.'),
        checkField('Include demo data in analysis and lists', checkbox('include_demo', st.include_demo_data), 'Demo rows are always badged DEMO.'),
        catFees.el,
        h('div', { class: 'field-wide' }, kv([
          ['AI provider', st.ai_provider],
          ['Background scheduler', st.scheduler_enabled ? 'enabled' : 'disabled'],
        ]), h('p', { class: 'muted small' }, 'AI provider and scheduler are configured with FLIPRADAR_* environment variables on the server.')),
        h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Save settings')));
      onSubmit(f, async () => {
        const fx = readStr(f, 'fx');
        if (fx && !/^\d{1,4}(\.\d{1,8})?$/.test(fx)) throw invalid(f, 'fx', 'Manual USD → MXN rate: decimal number expected (e.g. 18.25)');
        const unknownShipping = readMoney(f, 'unknown_shipping');
        const body = {
          min_comparables: readInt(f, 'min_comparables', { required: true, min: 2, max: 100 }),
          comparable_max_age_days: readInt(f, 'comparable_max_age_days', { required: true, min: 1, max: 365 }),
          buy_max_age_days: readInt(f, 'buy_max_age_days', { required: true, min: 1, max: 90 }),
          manual_buy_max_age_days: readInt(f, 'manual_buy_max_age_days', { required: true, min: 1, max: 365 }),
          category_fee_profiles: catFees.read(),
          use_marketplace_category_fees: readBool(f, 'use_marketplace_category_fees'),
          use_own_sales_as_evidence: readBool(f, 'use_own_sales_as_evidence'),
          pricing_strategy: inputOf(f, 'pricing_strategy').value,
          min_roi_bp: readPct(f, 'min_roi', { required: true, max: 100000 }),
          min_profit_cents: readMoney(f, 'min_profit', { required: true }),
          default_fee_profile_key: inputOf(f, 'default_fee_profile_key').value || null,
          default_fee_profile_key_used: inputOf(f, 'default_fee_profile_key_used').value || null,
          // The API cannot store null back; $0 is what the engine assumes for "no assumption" anyway.
          unknown_shipping_assumption_cents: unknownShipping ?? (isInt(st.unknown_shipping_assumption_cents) ? 0 : null),
          target_roi_bp: readPct(f, 'target_roi', { required: true, max: 100000 }),
          // "" explicitly clears a stored manual rate; omit the key when it was and stays empty.
          fx_usd_mxn_manual: fx || (st.fx_usd_mxn_manual ? '' : null),
          include_demo_data: readBool(f, 'include_demo'),
        };
        for (const k of Object.keys(body)) if (body[k] === null) delete body[k];
        await api('/settings', { method: 'PUT', json: body });
        toast('Settings saved.', 'success');
        await loadSettings();
      });
      setChildren(settingsBox, f);
    } catch (err) {
      if (alive()) setChildren(settingsBox, errorBox(err));
    }
  }

  async function loadProfiles() {
    try {
      profiles = await getFeeProfiles(true);
      if (!alive()) return;
      setChildren(profilesBox, profiles.length
        ? table([
          { label: 'Profile', cell: (p) => [h('strong', null, p.name), h('div', { class: 'sub' }, h('code', null, p.key), p.is_builtin ? [' ', badge('built-in')] : null)] },
          { label: 'Channel', cell: (p) => p.channel },
          { label: 'Commission', num: true, cell: (p) => fmtBp(p.commission_bp) },
          { label: 'Fixed fee tiers', cell: (p) => h('span', { class: 'small' }, tiersText(p.fixed_fee_tiers)) },
          { label: 'Payment fee', num: true, cell: (p) => `${fmtBp(p.payment_fee_bp)} + ${fmtMoney(p.payment_fixed_cents)}` },
          { label: 'IVA on fees', cell: (p) => (p.iva_on_fees ? `yes (${fmtBp(p.iva_rate_bp)})` : 'no') },
          { label: 'Seller shipping', num: true, cell: (p) => [fmtMoney(p.seller_shipping_cents), isInt(p.free_shipping_threshold_cents) ? h('div', { class: 'sub muted' }, `free ≥ ${fmtMoney(p.free_shipping_threshold_cents)}`) : null] },
          { label: 'Withholdings', cell: (p) => `ISR ${fmtBp(p.isr_withholding_bp)} · IVA ${fmtBp(p.iva_withholding_bp)}` },
          { label: 'Source / as of', cell: (p) => [p.source_url ? extLink(p.source_url, 'source') : 'no source', h('div', { class: 'sub muted' }, p.as_of || 'date unknown')] },
          { label: 'Actions', cell: (p) => h('div', { class: 'row-actions' },
            button('Edit', () => {
              editing = p;
              renderProfileForm();
              profileFormBox.querySelector('input[name="name"]').focus();
            }, { small: true }),
            p.is_builtin ? null : button('Delete', async () => {
              if (!window.confirm(`Delete fee profile "${p.name}"?`)) return;
              await api(`/fee-profiles/${encodeURIComponent(p.key)}`, { method: 'DELETE' });
              toast('Fee profile deleted.', 'success');
              state.feeProfiles = null;
              if (editing && editing.key === p.key) {
                editing = null;
                renderProfileForm();
              }
              await loadProfiles();
            }, { small: true, kind: 'danger' })) },
        ], profiles, { caption: 'Fee profiles' })
        : h('p', { class: 'muted' }, 'No fee profiles.'));
    } catch (err) {
      if (alive()) setChildren(profilesBox, errorBox(err));
    }
  }

  function tierRow(t = {}) {
    const row = h('div', { class: 'tier-row' },
      field('Applies up to sale price (MXN)', moneyInput('tier_max', { value: t.max_price_cents !== undefined && t.max_price_cents !== null ? centsToInput(t.max_price_cents) : '', placeholder: 'no limit' })),
      field('Fixed fee (MXN)', moneyInput('tier_fee', { value: t.fee_cents !== undefined ? centsToInput(t.fee_cents) : '' })),
      h('button', { type: 'button', class: 'btn btn-sm btn-danger', 'aria-label': 'Remove tier', on: { click: () => row.remove() } }, 'Remove'));
    return row;
  }

  function readTiers(f) {
    const tiers = [];
    for (const row of f.querySelectorAll('.tier-row')) {
      const maxIn = row.querySelector('input[name="tier_max"]');
      const feeIn = row.querySelector('input[name="tier_fee"]');
      if (!maxIn.value.trim() && !feeIn.value.trim()) continue;
      let max;
      let fee;
      try {
        max = pesosToCents(maxIn.value);
      } catch (e) {
        maxIn.setAttribute('aria-invalid', 'true');
        maxIn.focus();
        throw new InputError(`Fee tier limit: ${e.message}`);
      }
      try {
        fee = pesosToCents(feeIn.value);
        if (fee === null) throw new Error('required');
      } catch (e) {
        feeIn.setAttribute('aria-invalid', 'true');
        feeIn.focus();
        throw new InputError(`Fee tier amount: ${e.message}`);
      }
      tiers.push({ max_price_cents: max, fee_cents: fee });
    }
    const bounded = tiers.filter((t) => t.max_price_cents !== null).map((t) => t.max_price_cents);
    if (bounded.some((v, i) => i > 0 && v <= bounded[i - 1])) throw new InputError('Fee tiers must be in ascending order of their price limit.');
    const unboundedIdx = tiers.findIndex((t) => t.max_price_cents === null);
    if (unboundedIdx !== -1 && unboundedIdx !== tiers.length - 1) throw new InputError('The tier without a price limit must be the last one.');
    return tiers;
  }

  function renderProfileForm() {
    const p = editing;
    const tiersBox = h('div', null, (p ? p.fixed_fee_tiers || [] : []).map((t) => tierRow(t)));
    const f = h('form', { class: 'form-grid', novalidate: true, 'aria-label': p ? 'Edit fee profile' : 'New fee profile' },
      h('h2', { class: 'field-wide' }, p ? `Edit fee profile "${p.name}"` : 'New fee profile'),
      field('Key', textInput('key', { required: true, value: p ? p.key : '', readOnly: Boolean(p), pattern: '[a-z0-9_]{2,64}', maxlength: '64' }), { hint: 'a-z, 0-9 and _ (2–64 chars); cannot be changed later.' }),
      field('Name', textInput('name', { required: true, maxlength: '128', value: p ? p.name : '' })),
      field('Channel', textInput('channel', { maxlength: '64', value: p ? p.channel : 'custom' })),
      field('Commission (%)', pctInput('commission', { value: p ? bpToInput(p.commission_bp) : '' })),
      field('Payment fee (%)', pctInput('payment_fee', { value: p ? bpToInput(p.payment_fee_bp) : '' })),
      field('Payment fixed fee (MXN)', moneyInput('payment_fixed', { value: p ? centsToInput(p.payment_fixed_cents) : '' })),
      checkField('Channel adds IVA on its fees', checkbox('iva_on_fees', p ? p.iva_on_fees : false)),
      field('IVA rate (%)', pctInput('iva_rate', { value: p ? bpToInput(p.iva_rate_bp) : '16.00' })),
      field('Seller shipping per unit (MXN)', moneyInput('seller_shipping', { value: p ? centsToInput(p.seller_shipping_cents) : '' })),
      field('Mandatory free shipping from (MXN)', moneyInput('free_shipping_threshold', { value: p ? centsToInput(p.free_shipping_threshold_cents) : '', placeholder: 'never' })),
      field('ISR withholding (%)', pctInput('isr_withholding', { value: p ? bpToInput(p.isr_withholding_bp) : '' })),
      field('IVA withholding (%)', pctInput('iva_withholding', { value: p ? bpToInput(p.iva_withholding_bp) : '' })),
      field('Source URL', h('input', { type: 'url', name: 'source_url', maxlength: '1000', value: p ? p.source_url || '' : '', autocomplete: 'off' })),
      field('Values as of', textInput('as_of', { maxlength: '32', value: p ? p.as_of || '' : '', placeholder: 'YYYY-MM-DD' })),
      field('Notes', h('textarea', { name: 'notes', maxlength: '4000', rows: '2' }, p ? p.notes || '' : ''), { wide: true }),
      h('fieldset', { class: 'field-wide' }, h('legend', null, 'Fixed fee tiers (first tier whose limit ≥ sale price applies)'), tiersBox,
        h('button', { type: 'button', class: 'btn btn-sm', on: { click: () => tiersBox.append(tierRow()) } }, '+ Add tier')),
      h('div', { class: 'form-actions' },
        h('button', { type: 'submit', class: 'btn btn-primary' }, p ? 'Save profile' : 'Create profile'),
        p ? h('button', { type: 'button', class: 'btn', on: { click: () => { editing = null; renderProfileForm(); } } }, 'Cancel edit') : null));
    onSubmit(f, async () => {
      const key = readStr(f, 'key', { required: true });
      if (!/^[a-z0-9_]{2,64}$/.test(key)) throw invalid(f, 'key', 'Key: use 2–64 characters a-z, 0-9 or _');
      const sourceUrl = readStr(f, 'source_url');
      if (sourceUrl && !safeHttpUrl(sourceUrl)) throw invalid(f, 'source_url', 'Source URL: must start with http:// or https://');
      const body = {
        key,
        name: readStr(f, 'name', { required: true }),
        channel: readStr(f, 'channel') || 'custom',
        commission_bp: readPct(f, 'commission', { max: 10000 }) ?? 0,
        fixed_fee_tiers: readTiers(f),
        payment_fee_bp: readPct(f, 'payment_fee', { max: 10000 }) ?? 0,
        payment_fixed_cents: readMoney(f, 'payment_fixed') ?? 0,
        iva_on_fees: readBool(f, 'iva_on_fees'),
        iva_rate_bp: readPct(f, 'iva_rate', { max: 10000 }) ?? 1600,
        seller_shipping_cents: readMoney(f, 'seller_shipping') ?? 0,
        free_shipping_threshold_cents: readMoney(f, 'free_shipping_threshold'),
        isr_withholding_bp: readPct(f, 'isr_withholding', { max: 10000 }) ?? 0,
        iva_withholding_bp: readPct(f, 'iva_withholding', { max: 10000 }) ?? 0,
        source_url: sourceUrl,
        as_of: readStr(f, 'as_of'),
        notes: readStr(f, 'notes'),
      };
      if (editing) {
        await api(`/fee-profiles/${encodeURIComponent(editing.key)}`, { method: 'PUT', json: body });
        toast('Fee profile saved.', 'success');
      } else {
        await api('/fee-profiles', { method: 'POST', json: body });
        toast('Fee profile created.', 'success');
      }
      state.feeProfiles = null;
      editing = null;
      renderProfileForm();
      await loadProfiles();
    });
    setChildren(profileFormBox, f);
  }

  (async () => {
    try {
      await loadProfiles();
      if (!alive()) return;
      renderProfileForm();
      await loadSettings();
    } finally {
      if (alive()) markReady(main, 'settings');
    }
  })();
}

/* ============================================================================================
 * Boot
 * ========================================================================================== */

async function signIn(token) {
  state.token = token || null;
  try {
    const check = await api('/auth/check', { on401: 'throw' });
    if (!check || !check.ok) throw new ApiError(401, 'Token not accepted.');
    if (token) store.set(TOKEN_KEY, token);
    showApp(check);
  } catch (err) {
    state.token = null;
    if (err.status === 401) {
      setLoginMessage(token ? 'Invalid or revoked token.' : 'This server requires an API token.');
    } else {
      setLoginMessage(err.message || String(err));
    }
    throw err;
  }
}

function wireShell() {
  const form = $('#login-form');
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const input = $('#login-token');
    const token = input.value.trim();
    if (!token) {
      input.setAttribute('aria-invalid', 'true');
      setLoginMessage('Enter your API token.');
      input.focus();
      return;
    }
    input.removeAttribute('aria-invalid');
    busy($('#login-submit'), () => signIn(token).catch(() => input.focus()));
  });
  const noAuth = $('#login-noauth');
  noAuth.addEventListener('click', () => busy(noAuth, () => signIn(null).catch(() => {})));
  const retry = $('#login-retry');
  retry.addEventListener('click', () => busy(retry, () => {
    loadHealth();
    const saved = store.get(TOKEN_KEY);
    return saved ? resumeSavedSession(saved) : showLogin();
  }));
  $('#logout-btn').addEventListener('click', logout);
  $('#nav-toggle').addEventListener('click', () => toggleNav());
  for (const a of document.querySelectorAll('#sidenav a')) a.addEventListener('click', () => toggleNav(false));
  $('#skip-link').addEventListener('click', (ev) => {
    ev.preventDefault();
    const target = state.authed ? $('#main') : $('#login-token');
    if (target) target.focus();
  });
  window.addEventListener('hashchange', route);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && state.authed) refreshUnread();
  });
}

/** Sign in with the token saved in this tab.  Only a 401 discards it: after a network error, 429 or 5xx it is kept,
 *  so a retry (or a reload) works without pasting it again (create-token prints it only once). */
async function resumeSavedSession(saved) {
  try {
    await signIn(saved);
  } catch (err) {
    if (isAuthRejection(err)) {
      store.del(TOKEN_KEY);
      showLogin('The saved token is no longer valid. Please sign in again.');
    } else {
      const reason = String((err && err.message) || err).replace(/\.$/, '');
      showLogin(`Could not sign in with the saved token: ${reason}. The token is still saved in this tab; retry when the server is reachable.`, { canRetry: true });
    }
  }
}

async function boot() {
  wireShell();
  loadHealth();
  const saved = store.get(TOKEN_KEY);
  if (saved) await resumeSavedSession(saved);
  else showLogin();
}

if (typeof document !== 'undefined' && document.getElementById('login-form')) {
  boot();
}
