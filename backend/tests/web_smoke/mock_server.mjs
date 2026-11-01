// Contract-shaped MOCK of the FLIP RADAR HTTP API, used only to smoke-test the web dashboard without the
// Python backend.  Every record is a clearly labelled TEST FIXTURE (is_demo: true, "FIXTURE" in titles);
// none of it is market data.  Shapes follow backend/flipradar/schemas.py.
//
// Usage: node tests/web_smoke/mock_server.mjs [port]   (token: "smoke-token")
import { readFile } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, '../../flipradar/web');
export const MOCK_TOKEN = 'smoke-token';
const CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
const XSS = '<img src=x onerror=alert(1)>';
const now = () => new Date().toISOString();
const daysAgo = (d) => new Date(Date.now() - d * 86400000).toISOString();

function listing(id, title, price, extra = {}) {
  return {
    id, source_key: 'fixture_source', external_id: `FX-${id}`, url: `https://example.invalid/item/${id}`, title,
    currency: 'MXN', price_cents: price, price_mxn_cents: price, shipping_cents: null, free_shipping: null,
    condition: 'used', brand: 'fixturebrand', model: 'fixture model', category: 'phone', gtin: null,
    seller_name: `fixture seller ${id}`, location: null, image_url: null, available_quantity: 1, sold_quantity: 0,
    status: 'active', product_id: 1, match_confidence: 0.97, match_method: 'canonical_key', is_accessory: false,
    is_bundle: false, is_suspicious: false, risk_flags: [], is_demo: true, first_seen_at: daysAgo(5), last_seen_at: daysAgo(1),
    ...extra,
  };
}

function opportunity(id, overrides = {}) {
  return {
    id, product_id: 1, product_title: `FIXTURE phone 128gb ${XSS}`, product_key: 'fixturebrand|fixture model|128gb',
    category: 'phone', buy_listing_id: 100 + id, buy_title: `FIXTURE listing ${id} ${XSS}`, buy_url: `https://example.invalid/buy/${id}`,
    buy_source_key: 'fixture_source', buy_condition: 'used', image_url: null, fee_profile_key: 'fixture_profile',
    buy_total_cents: 450000, expected_sale_cents: 620000, net_profit_cents: 61234 - id * 1000, roi_bp: 1361 - id * 10,
    margin_bp: 987, score: 72.4 - id * 5, confidence: id === 1 ? 'high' : 'medium', comparable_count: 7,
    risk_level: ['none', 'info', 'warning', 'critical'][id % 4], demand_level: id === 1 ? 'medium' : 'unknown',
    status: id === 1 ? 'new' : 'seen', is_demo: true, created_at: daysAgo(2), updated_at: daysAgo(1), ...overrides,
  };
}

function detail(o) {
  const line = (key, label, amount, formula) => ({ key, label, amount_cents: amount, formula });
  return {
    ...o,
    price_stats: {
      n: 7, median_cents: 650000, p25_cents: 620000, p75_cents: 690000, min_cents: 590000, max_cents: 720000,
      mad_cents: 30000, iqr_cents: 70000, dispersion: 0.1077, source_count: 1, seller_count: 7,
      excluded: { outlier: 1, stale: 2 }, comparable_ids: [201, 202], window_days: 30,
    },
    cost_breakdown: {
      sale_price_cents: o.expected_sale_cents,
      acquisition_lines: [line('buy_price', 'Purchase price', 430000, 'listing price'), line('buy_shipping', 'Shipping to you', 20000, 'as provided'), line('import_costs', 'Import duties / customs', 0, 'as provided')],
      selling_lines: [line('commission', 'Marketplace commission', 99200, 'sale x 16.00%'), line('iva_on_fees', 'IVA on fees', 15872, 'fees x 16.00%'), line('isr_withheld', 'ISR withheld by platform', 13897, 'sale / (1 + 16.00%) x 2.50%')],
      total_acquisition_cents: 450000, total_selling_costs_cents: 108766, net_proceeds_cents: 511234,
      net_profit_cents: o.net_profit_cents, roi_bp: o.roi_bp, margin_bp: o.margin_bp, fee_profile_key: 'fixture_profile',
      assumptions: ["Fee profile 'Fixture profile' values as of 2026-01-01; verify current rates."],
    },
    score_components: [
      { key: 'profit', label: 'Net profit', raw_value: 612.34, points: 25, max_points: 30, explanation: 'FIXTURE explanation for profit points.' },
      { key: 'roi', label: 'ROI', raw_value: 0.1361, points: 12.5, max_points: 25, explanation: 'FIXTURE explanation for ROI points.' },
      { key: 'risk', label: 'Risk penalty', raw_value: null, points: -6, max_points: 0, explanation: `FIXTURE penalty ${XSS}` },
    ],
    risk_flags: [
      { code: 'unknown_shipping', severity: 'info', message: 'FIXTURE: shipping cost unknown', evidence: {} },
      { code: 'price_far_below_market', severity: 'critical', message: `FIXTURE critical flag ${XSS}`, evidence: { robust_z: -4.2, median_cents: 650000 } },
      { code: 'new_seller', severity: 'warning', message: 'FIXTURE warning flag', evidence: { seller_transactions: 2 } },
    ],
    demand: { level: o.demand_level, confidence: o.demand_level === 'unknown' ? null : 'low', signals: o.demand_level === 'unknown' ? [] : [{ name: 'sold_delta', value: 3, unit: 'units/30d', source: 'fixture_source', note: 'FIXTURE signal' }], note: 'FIXTURE demand note' },
    explanation: `FIXTURE explanation line 1\n<script>alert(1)</script> must render as text\nline 3`,
    facts: { expected_sale_cents: o.expected_sale_cents, roi_bp: o.roi_bp, comparables: 7, strategy: 'competitive' },
    ai_summary: null,
    comparables: [listing(201, `FIXTURE comparable A ${XSS}`, 620000, { free_shipping: true }), listing(202, 'FIXTURE comparable B', 650000, { shipping_cents: 9900, is_suspicious: true, url: 'javascript:alert(1)' })],
  };
}

function createState() {
  const profile = {
    key: 'fixture_profile', name: 'Fixture profile', channel: 'custom', commission_bp: 1600,
    fixed_fee_tiers: [{ max_price_cents: 29900, fee_cents: 2500 }, { max_price_cents: null, fee_cents: 0 }],
    payment_fee_bp: 0, payment_fixed_cents: 0, iva_on_fees: true, iva_rate_bp: 1600, seller_shipping_cents: 0,
    free_shipping_threshold_cents: 29900, isr_withholding_bp: 250, iva_withholding_bp: 800,
    source_url: 'https://example.invalid/fees', as_of: '2026-01-01', notes: 'FIXTURE', is_builtin: true, updated_at: now(),
  };
  return {
    seq: 1000,
    profiles: [profile],
    sources: [
      { key: 'fixture_source', name: 'Fixture marketplace', kind: 'marketplace', country: 'MX', currency: 'MXN', enabled: true, default_fee_profile_key: 'fixture_profile', configured: false, requires_credentials: true, capabilities: ['search', 'item'], docs_url: 'https://example.invalid/docs', notes: 'FIXTURE source', last_run_at: daysAgo(1), last_status: 'error', last_error: 'FIXTURE: not configured' },
      { key: 'manual', name: 'Manual / CSV', kind: 'manual', country: 'MX', currency: 'MXN', enabled: true, default_fee_profile_key: null, configured: true, requires_credentials: false, capabilities: ['import'], docs_url: null, notes: '', last_run_at: null, last_status: null, last_error: null },
    ],
    opportunities: [opportunity(1), opportunity(2), opportunity(3, { net_profit_cents: -500, roi_bp: -11 })],
    searches: [{ id: 1, query: 'fixture phone', category_hint: null, source_keys: [], max_results: 50, interval_minutes: 180, enabled: true, last_run_at: null, created_at: daysAgo(3) }],
    runs: [{ id: 1, source_key: 'fixture_source', query: 'fixture phone', started_at: daysAgo(1), finished_at: daysAgo(1), status: 'error', items_fetched: 0, items_new: 0, items_updated: 0, error: 'FIXTURE: not configured' }],
    alerts: [],
    notifications: [
      { id: 2, rule_id: null, opportunity_id: 1, kind: 'opportunity', title: `FIXTURE notification ${XSS}`, body: 'FIXTURE body\nsecond line', created_at: daysAgo(0), read_at: null },
      { id: 1, rule_id: null, opportunity_id: null, kind: 'system', title: 'FIXTURE read notification', body: 'FIXTURE', created_at: daysAgo(2), read_at: daysAgo(1) },
    ],
    inventory: [],
    settings: { min_comparables: 3, comparable_max_age_days: 30, default_fee_profile_key: 'fixture_profile', target_roi_bp: 2000, min_profit_cents: 20000, fx_usd_mxn_manual: null, include_demo_data: true, ai_provider: 'none', scheduler_enabled: false },
  };
}

function inventoryOut(item) {
  const sold = item.sales.reduce((n, s) => n + s.quantity, 0);
  return {
    ...item,
    quantity_available: item.quantity - sold,
    total_cost_cents: item.unit_cost_cents * item.quantity + item.extra_costs_cents,
    days_held: 0,
    realized_profit_cents: item.sales.reduce((n, s) => n + s.profit_cents, 0),
    status: item.quantity - sold === 0 ? 'sold' : item.status,
  };
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks);
}

function send(res, status, data, headers = {}) {
  const body = data === undefined ? '' : JSON.stringify(data);
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...headers });
  res.end(body);
}

const notFound = (res, what = 'not found') => send(res, 404, { detail: what });

async function handleApi(st, req, res, url) {
  const p = url.pathname.replace(/^\/api\/v1/, '');
  const m = req.method;
  const auth = req.headers.authorization || '';
  if (auth !== `Bearer ${MOCK_TOKEN}`) return send(res, 401, { detail: 'Missing bearer token' }, { 'WWW-Authenticate': 'Bearer' });
  const raw = m === 'GET' || m === 'DELETE' ? Buffer.alloc(0) : await readBody(req);
  const ctype = req.headers['content-type'] || '';
  let body = {};
  if (ctype.startsWith('application/json') && raw.length) {
    try {
      body = JSON.parse(raw.toString('utf8'));
    } catch {
      return send(res, 422, { detail: 'invalid JSON' });
    }
  }
  const id = (re) => {
    const mm = re.exec(p);
    return mm ? Number(mm[1]) : null;
  };
  let mm;

  if (p === '/auth/check' && m === 'GET') return send(res, 200, { ok: true, token_name: 'smoke', auth_required: true });
  if (p === '/sources' && m === 'GET') return send(res, 200, st.sources);
  if ((mm = /^\/sources\/([a-z0-9_]+)$/.exec(p)) && m === 'PATCH') {
    const src = st.sources.find((s) => s.key === mm[1]);
    if (!src) return notFound(res);
    Object.assign(src, body);
    return send(res, 200, src);
  }
  if ((mm = /^\/sources\/([a-z0-9_]+)\/test$/.exec(p)) && m === 'POST') {
    const src = st.sources.find((s) => s.key === mm[1]);
    return send(res, 200, { key: mm[1], configured: Boolean(src && src.configured), ok: null, message: 'FIXTURE: not configured', details: { hint: 'set credentials' } });
  }
  if (p === '/searches' && m === 'GET') return send(res, 200, st.searches);
  if (p === '/searches' && m === 'POST') {
    if (!body.query || body.query.length < 2) return send(res, 422, { detail: [{ loc: ['body', 'query'], msg: 'String should have at least 2 characters' }] });
    const s = { id: ++st.seq, category_hint: null, source_keys: [], max_results: 50, interval_minutes: 180, enabled: true, ...body, last_run_at: null, created_at: now() };
    st.searches.push(s);
    return send(res, 201, s);
  }
  if ((mm = /^\/searches\/(\d+)$/.exec(p))) {
    const s = st.searches.find((x) => x.id === Number(mm[1]));
    if (!s) return notFound(res);
    if (m === 'PATCH') return send(res, 200, Object.assign(s, body));
    if (m === 'DELETE') {
      st.searches = st.searches.filter((x) => x !== s);
      return send(res, 200, { ok: true, message: 'deleted' });
    }
  }
  if ((mm = /^\/searches\/(\d+)\/run$/.exec(p)) && m === 'POST') return send(res, 200, { runs: st.runs, opportunities_created: 0, opportunities_updated: 0, notifications_created: 0 });
  if (p === '/collect' && m === 'POST') return send(res, 200, { runs: st.runs, opportunities_created: 0, opportunities_updated: 0, notifications_created: 0 });
  if (p === '/runs' && m === 'GET') return send(res, 200, st.runs);

  if (p === '/opportunities' && m === 'GET') {
    const q = (url.searchParams.get('q') || '').toLowerCase();
    let items = st.opportunities.filter((o) => !q || o.buy_title.toLowerCase().includes(q));
    const statuses = url.searchParams.get('status');
    if (statuses && statuses !== 'all') items = items.filter((o) => statuses.split(',').includes(o.status));
    else if (!statuses) items = items.filter((o) => ['new', 'seen', 'watching', 'bought'].includes(o.status));
    const limit = Number(url.searchParams.get('limit') || 50);
    const offset = Number(url.searchParams.get('offset') || 0);
    if (limit > 200) return send(res, 422, { detail: [{ loc: ['query', 'limit'], msg: 'Input should be less than or equal to 200' }] });
    return send(res, 200, { items: items.slice(offset, offset + limit), total: items.length, limit, offset });
  }
  if ((mm = /^\/opportunities\/(\d+)$/.exec(p))) {
    const o = st.opportunities.find((x) => x.id === Number(mm[1]));
    if (!o) return notFound(res, `opportunity ${mm[1]} not found`);
    if (m === 'GET') return send(res, 200, detail(o));
    if (m === 'PATCH') return send(res, 200, Object.assign(o, { status: body.status, updated_at: now() }));
  }
  if ((mm = /^\/opportunities\/(\d+)\/buy$/.exec(p)) && m === 'POST') {
    const o = st.opportunities.find((x) => x.id === Number(mm[1]));
    if (!o) return notFound(res);
    o.status = 'bought';
    const item = { id: ++st.seq, title: o.buy_title, sku: null, product_id: o.product_id, opportunity_id: o.id, condition: 'used', quantity: body.quantity || 1, unit_cost_cents: body.unit_cost_cents ?? 430000, extra_costs_cents: body.extra_costs_cents ?? 0, purchase_source: o.buy_source_key, purchase_url: o.buy_url, purchased_at: now(), status: 'purchased', target_sale_cents: o.expected_sale_cents, listed_channel: null, location: null, notes: body.notes || null, sales: [], created_at: now(), updated_at: now() };
    st.inventory.push(item);
    return send(res, 201, inventoryOut(item));
  }
  if ((mm = /^\/opportunities\/(\d+)\/ai-summary$/.exec(p)) && m === 'POST') {
    return send(res, 200, { opportunity_id: Number(mm[1]), provider: 'none', summary: null, validated: false, message: 'FIXTURE: AI narrative disabled (FLIPRADAR_LLM_PROVIDER=none).' });
  }
  if (p === '/analyze/recompute' && m === 'POST') return send(res, 200, { products_analyzed: 1, opportunities_created: 0, opportunities_updated: 3, opportunities_expired: 0, notifications_created: 0 });

  if (p === '/calculator/profit' && m === 'POST') {
    const buy = body.buy_price_cents + (body.buy_shipping_cents || 0) + (body.import_costs_cents || 0) + (body.other_buy_costs_cents || 0);
    const commission = Math.round((body.sale_price_cents * 1600) / 10000);
    const net = body.sale_price_cents - commission - (body.other_sale_costs_cents || 0);
    const profit = net - buy;
    return send(res, 200, {
      result: {
        sale_price_cents: body.sale_price_cents,
        acquisition_lines: [{ key: 'buy_price', label: 'Purchase price', amount_cents: body.buy_price_cents, formula: 'listing price' }],
        selling_lines: [{ key: 'commission', label: 'Marketplace commission (FIXTURE math)', amount_cents: commission, formula: 'sale x 16.00%' }],
        total_acquisition_cents: buy, total_selling_costs_cents: body.sale_price_cents - net, net_proceeds_cents: net, net_profit_cents: profit,
        roi_bp: buy ? Math.round((profit * 10000) / buy) : null, margin_bp: body.sale_price_cents ? Math.round((profit * 10000) / body.sale_price_cents) : null,
        fee_profile_key: body.fee_profile_key || 'fixture_profile', assumptions: ['FIXTURE: mock server arithmetic, not the real calculator.'],
      },
      break_even_sale_cents: Math.ceil((buy * 10000) / 8400), max_buy_for_target_cents: body.target_roi_bp === null ? null : 1000, target_roi_bp: body.target_roi_bp,
    });
  }
  if (p === '/calculator/import-costs' && m === 'POST') {
    return send(res, 200, { rule_key: body.rule_key, rule_name: 'FIXTURE courier rule', customs_value_usd_cents: body.item_value_usd_cents + (body.shipping_usd_cents || 0), usd_mxn_rate: body.usd_mxn_rate || '18.0000', fx_source: 'fixture', duty_rate_bp: 1700, duties_mxn_cents: 30600, customs_value_mxn_cents: 180000, notes: ['FIXTURE note'], source_url: 'https://example.invalid/rule', as_of: '2026-01-01' });
  }
  if (p === '/fee-profiles' && m === 'GET') return send(res, 200, st.profiles);
  if (p === '/fee-profiles' && m === 'POST') {
    if (st.profiles.some((x) => x.key === body.key)) return send(res, 409, { detail: 'fee profile exists' });
    const prof = { ...body, is_builtin: false, updated_at: now() };
    st.profiles.push(prof);
    return send(res, 201, prof);
  }
  if ((mm = /^\/fee-profiles\/([a-z0-9_]+)$/.exec(p))) {
    const prof = st.profiles.find((x) => x.key === mm[1]);
    if (!prof) return notFound(res);
    if (m === 'PUT') return send(res, 200, Object.assign(prof, body, { updated_at: now() }));
    if (m === 'DELETE') {
      if (prof.is_builtin) return send(res, 400, { detail: 'built-in profiles cannot be deleted' });
      st.profiles = st.profiles.filter((x) => x !== prof);
      return send(res, 200, { ok: true, message: 'deleted' });
    }
  }

  if (p === '/inventory' && m === 'GET') {
    const status = url.searchParams.get('status');
    const items = st.inventory.map(inventoryOut).filter((i) => !status || i.status === status);
    return send(res, 200, { items, total: items.length, limit: 100, offset: 0 });
  }
  if (p === '/inventory' && m === 'POST') {
    if (!Number.isInteger(body.unit_cost_cents)) return send(res, 422, { detail: [{ loc: ['body', 'unit_cost_cents'], msg: 'Field required' }] });
    const item = { id: ++st.seq, sku: null, product_id: null, opportunity_id: null, condition: 'unknown', quantity: 1, extra_costs_cents: 0, purchase_source: null, purchase_url: null, purchased_at: now(), status: 'purchased', target_sale_cents: null, listed_channel: null, location: null, notes: null, ...body, sales: [], created_at: now(), updated_at: now() };
    st.inventory.push(item);
    return send(res, 201, inventoryOut(item));
  }
  if ((mm = /^\/inventory\/(\d+)$/.exec(p))) {
    const item = st.inventory.find((x) => x.id === Number(mm[1]));
    if (!item) return notFound(res);
    if (m === 'GET') return send(res, 200, inventoryOut(item));
    if (m === 'PATCH') return send(res, 200, inventoryOut(Object.assign(item, body)));
    if (m === 'DELETE') {
      st.inventory = st.inventory.filter((x) => x !== item);
      return send(res, 200, { ok: true, message: 'deleted' });
    }
  }
  if ((mm = /^\/inventory\/(\d+)\/sales$/.exec(p)) && m === 'POST') {
    const item = st.inventory.find((x) => x.id === Number(mm[1]));
    if (!item) return notFound(res);
    const basis = item.unit_cost_cents * body.quantity;
    const fees = body.fees_cents ?? Math.round((body.unit_price_cents * body.quantity * 1600) / 10000);
    const net = body.unit_price_cents * body.quantity - fees - (body.shipping_cents || 0) - (body.other_costs_cents || 0);
    const sale = { id: ++st.seq, inventory_item_id: item.id, quantity: body.quantity, unit_price_cents: body.unit_price_cents, channel: body.channel || null, fee_profile_key: body.fee_profile_key || null, fees_cents: fees, shipping_cents: body.shipping_cents || 0, taxes_withheld_cents: body.taxes_withheld_cents || 0, other_costs_cents: body.other_costs_cents || 0, cost_basis_cents: basis, net_proceeds_cents: net, profit_cents: net - basis, sold_at: body.sold_at || now(), notes: body.notes || null };
    item.sales.push(sale);
    return send(res, 201, sale);
  }
  if ((mm = /^\/sales\/(\d+)$/.exec(p)) && m === 'DELETE') {
    for (const item of st.inventory) item.sales = item.sales.filter((s) => s.id !== Number(mm[1]));
    return send(res, 200, { ok: true, message: 'deleted' });
  }
  if (p === '/analytics/summary' && m === 'GET') {
    return send(res, 200, {
      period_days: Number(url.searchParams.get('days') || 30), revenue_cents: 1234500, realized_profit_cents: -2300, units_sold: 3, sales_count: 2,
      avg_roi_bp: -150, avg_days_to_sell: 12.5, inventory_units: 4, inventory_cost_cents: 870000, inventory_target_value_cents: null,
      aging: { '0-7': 1, '8-30': 2, '31-90': 0, '90+': 1 }, open_opportunities: 3, avg_open_opportunity_score: 62.4,
      potential_profit_open_cents: 120000, opportunities_by_source: { fixture_source: 3 },
      top_products_by_profit: [{ product_id: 1, title: `FIXTURE product ${XSS}`, profit_cents: 4500, units: 2 }],
      listings_tracked: 10, products_tracked: 2, last_collection_at: daysAgo(1),
    });
  }
  if (p === '/analytics/timeseries' && m === 'GET') {
    const bucket = url.searchParams.get('bucket') || 'week';
    const points = [];
    for (let i = 0; i < 12; i++) {
      const d = new Date(Date.UTC(2026, 7, 1 + i * 7));
      points.push({ period: d.toISOString().slice(0, 10), revenue_cents: (i % 3) * 150000 + 5000, profit_cents: i % 4 === 1 ? -42000 : (i % 3) * 30000, units: i % 3, purchases_cents: 100000 });
    }
    return send(res, 200, { bucket, points });
  }
  if (p === '/alerts' && m === 'GET') return send(res, 200, st.alerts);
  if (p === '/alerts' && m === 'POST') {
    const a = { id: ++st.seq, keywords: null, brand: null, category: null, min_profit_cents: null, min_roi_bp: null, min_score: null, max_buy_cents: null, min_confidence: null, exclude_suspicious: true, channels: ['in_app'], enabled: true, ...body, created_at: now() };
    st.alerts.push(a);
    return send(res, 201, a);
  }
  if ((mm = /^\/alerts\/(\d+)$/.exec(p))) {
    const a = st.alerts.find((x) => x.id === Number(mm[1]));
    if (!a) return notFound(res);
    if (m === 'PATCH') return send(res, 200, Object.assign(a, body));
    if (m === 'DELETE') {
      st.alerts = st.alerts.filter((x) => x !== a);
      return send(res, 200, { ok: true, message: 'deleted' });
    }
  }
  if (p === '/notifications' && m === 'GET') {
    const unread = url.searchParams.get('unread_only') === 'true';
    return send(res, 200, st.notifications.filter((n) => !unread || !n.read_at));
  }
  if ((mm = /^\/notifications\/(\d+)\/read$/.exec(p)) && m === 'POST') {
    const n = st.notifications.find((x) => x.id === Number(mm[1]));
    if (n) n.read_at = now();
    return send(res, 200, { ok: true, message: '' });
  }
  if (p === '/notifications/read-all' && m === 'POST') {
    for (const n of st.notifications) n.read_at = n.read_at || now();
    return send(res, 200, { ok: true, message: '' });
  }
  if (p === '/import/csv' && m === 'POST') {
    if (!ctype.startsWith('multipart/form-data')) return send(res, 422, { detail: 'multipart expected' });
    const lines = raw.toString('utf8').split('\n').filter((l) => l.includes(','));
    return send(res, 200, { rows_total: Math.max(0, lines.length - 1), rows_imported: Math.max(0, lines.length - 1), rows_updated: 0, rows_rejected: 0, errors: [] });
  }
  if (p === '/check-url' && m === 'POST') return send(res, 200, { url: body.url, allowed_by_robots: true, found: false, listing: null, extracted: {}, message: 'FIXTURE: no Product JSON-LD found' });
  if (p === '/settings' && m === 'GET') return send(res, 200, st.settings);
  if (p === '/settings' && m === 'PUT') {
    for (const [k, v] of Object.entries(body)) st.settings[k] = k === 'fx_usd_mxn_manual' && v === '' ? null : v;
    return send(res, 200, st.settings);
  }
  if (p === '/fx/latest' && m === 'GET') return notFound(res, 'no FX rate stored');
  return notFound(res, `mock: no route ${m} ${p}`);
}

const STATIC_TYPES = { '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.html': 'text/html; charset=utf-8' };

export function startMockServer(port = 0) {
  const st = createState();
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (url.pathname === '/' && req.method === 'GET') {
        const html = await readFile(path.join(WEB, 'templates', 'index.html'));
        res.writeHead(200, { 'Content-Type': STATIC_TYPES['.html'], 'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff' });
        return res.end(html);
      }
      if (url.pathname.startsWith('/static/') && req.method === 'GET') {
        const rel = path.normalize(decodeURIComponent(url.pathname.slice('/static/'.length)));
        const file = path.join(WEB, 'static', rel);
        if (!file.startsWith(path.join(WEB, 'static') + path.sep)) return notFound(res);
        try {
          const data = await readFile(file);
          res.writeHead(200, { 'Content-Type': STATIC_TYPES[path.extname(file)] || 'application/octet-stream', 'X-Content-Type-Options': 'nosniff' });
          return res.end(data);
        } catch {
          return notFound(res);
        }
      }
      if (url.pathname === '/health') return send(res, 200, { status: 'ok', version: '0.1.0-mock', api_version: '1', database: 'mock: ok', time: now() });
      if (url.pathname.startsWith('/api/v1/')) return await handleApi(st, req, res, url);
      return notFound(res);
    } catch (err) {
      return send(res, 500, { detail: String(err && err.stack ? err.stack : err) });
    }
  });
  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/`, close: () => new Promise((r) => server.close(r)) }));
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { url } = await startMockServer(Number(process.argv[2] || 8765));
  console.log(`mock FLIP RADAR API on ${url} (token: ${MOCK_TOKEN})`);
}
