// Regression tests for second-round review findings (pure helpers only, no DOM).
//   node --test tests/web_smoke/round2.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { oppStatusActions, oppStatusErrorText } from '../../flipradar/web/static/app.js';

test('contracts#1: a bought opportunity can be restored to new (the server allows it once no inventory item references it)', () => {
  const actions = oppStatusActions('bought');
  assert.deepEqual(actions.map((a) => [a.label, a.to]), [['Restore to new', 'new']]);
  assert.ok(actions[0].message, 'the restore needs a confirmation message');
  // Watch / Dismiss stay hidden: undoing them used to leave a real purchase 'seen' (counted as open potential profit).
  assert.ok(!actions.some((a) => a.to === 'watching' || a.to === 'dismissed'));
  // expired and unknown statuses still get nothing
  assert.deepEqual(oppStatusActions('expired'), []);
  assert.deepEqual(oppStatusActions(undefined), []);
});

test('contracts#1: the 409 for a bought opportunity still in inventory says what to do', () => {
  const serverMessage = 'this opportunity was bought (an inventory item references it); manage it from Inventory instead';
  const err = Object.assign(new Error(serverMessage), { status: 409 });
  const text = oppStatusErrorText(err, 'bought');
  assert.ok(text.includes(serverMessage), 'the server reason is kept');
  assert.match(text, /^Not restored/);
  assert.match(text, /delete (its|the) inventory item/i);
  // any other failure keeps the plain message
  assert.equal(oppStatusErrorText(Object.assign(new Error('boom'), { status: 500 }), 'bought'), 'boom');
  assert.equal(oppStatusErrorText(Object.assign(new Error('conflict'), { status: 409 }), 'seen'), 'conflict');
  assert.equal(oppStatusErrorText('plain', 'bought'), 'plain');
});
