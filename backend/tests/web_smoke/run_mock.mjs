// Runs the dashboard smoke test against the in-process MOCK API (no Python backend needed).
//   node tests/web_smoke/run_mock.mjs
import { MOCK_TOKEN, startMockServer } from './mock_server.mjs';
import { runSmoke } from './smoke.mjs';

const mock = await startMockServer(0);
console.log(`mock server: ${mock.url}`);
let problems;
try {
  problems = await runSmoke({ url: mock.url, token: MOCK_TOKEN, mutate: true });
} finally {
  await mock.close();
}
if (problems.length) {
  console.error(`\nSMOKE (mock) FAILED (${problems.length} problem(s)):`);
  for (const p of problems) console.error(` - ${p}`);
  process.exit(1);
}
console.log('\nSMOKE (mock) PASSED');
