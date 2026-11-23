// Runs the Telegram Code nodes of n8n/workflow.json and n8n/errors.json against fixtures (no n8n needed): node tests/n8n_code_nodes.js
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname, '..', 'n8n');
const wf = JSON.parse(fs.readFileSync(path.join(dir, 'workflow.json'), 'utf8'));
const er = JSON.parse(fs.readFileSync(path.join(dir, 'errors.json'), 'utf8'));
const src = (w, name) => w.nodes.find((n) => n.name === name).parameters.jsCode;
const AF = Object.getPrototypeOf(async function () {}).constructor;
async function run(w, name, { nodes = {}, input = {}, binary = null, exec = '77' }) {
  const fn = new AF('$', '$input', '$execution', 'Buffer', src(w, name));
  const ctx = { helpers: { getBinaryDataBuffer: async () => Buffer.from(binary.text), prepareBinaryData: async (b, n) => ({ size: b.length, name: n, text: b.toString() }) } };
  const $ = (n) => { if (!(n in nodes)) throw new Error("Node '" + n + "' hasn't been executed"); return { first: () => ({ json: nodes[n] }), all: () => [{ json: nodes[n] }] }; };
  const $input = { first: () => ({ json: input, binary: binary ? { data: {} } : undefined }), all: () => [{ json: input }] };
  return fn.call(ctx, $, $input, { id: exec }, Buffer);
}
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
const throws = async (p) => { try { await p; return ''; } catch (e) { return e.message; } };
const FAKE_TOKEN = 'bot' + '123456789:' + 'A'.repeat(35);        // built at run time so no token-shaped string sits in the repo
(async () => {
  const mk = (n, nf, reason) => {
    const results = [], all = [];
    for (let i = 0; i < n; i++) { const v = 'H1_' + (i + 1) + 'S_9X16'; all.push(v); results.push({ version: v, file: 'runs/77/renders/' + v + '.mp4', status: i < nf ? 'failed' : 'passed', qa: { passed: i >= nf, reasons: i < nf ? [reason + ' ' + i] : [] } }); }
    return { all, results, not_made: [], form_url: 'https://n8n.example.com/form-waiting/77?signature=ab12&x=1' };
  };
  const cfg = { text: JSON.stringify({ telegram_chat_id: '12345678' }) };
  const CT = 'Telegram: chat id and texts';
  let A = mk(18, 2, 'voice-over differs from the script (word error rate 1.00, over 0.15)');
  let r = (await run(wf, CT, { nodes: { 'Approval: build': A }, binary: cfg }))[0].json;
  ok(r.chat_id === '12345678', 'chat id read from the config file');
  ok(r.caption.startsWith('18 versions: 16 passed, 2 failed (H1_1S_9X16: voice-over') && r.caption.length <= 1024, 'caption: summary + failures, ' + r.caption.length + ' chars');
  A = mk(18, 15, 'voice-over differs from the script (word error rate 1.00, over 0.15)');
  r = (await run(wf, CT, { nodes: { 'Approval: build': A }, binary: cfg }))[0].json;
  ok(r.caption.length <= 1024 && /\+\d+ more\)$/.test(r.caption), 'many failures truncated with "+N more": ' + r.caption.length + ' chars');
  A = mk(18, 3, 'a & b < c > d &&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&');
  r = (await run(wf, CT, { nodes: { 'Approval: build': A }, binary: cfg }))[0].json;
  ok(!/&(?!amp;|lt;|gt;)/.test(r.caption), 'caption: escaped last, no split entity');
  A = mk(18, 0, '');
  r = (await run(wf, CT, { nodes: { 'Approval: build': A }, binary: cfg }))[0].json;
  ok(r.caption === '18 versions: 18 passed, 0 failed', 'no failures: ' + r.caption);
  ok(r.review_text.includes('<a href="https://n8n.example.com/form-waiting/77?signature=ab12&amp;x=1">') && !/[_*\[`]/.test(r.ask), 'review link html-escaped; ask text has no markdown characters');
  ok(/must be digits/.test(await throws(run(wf, CT, { nodes: { 'Approval: build': A }, binary: { text: '{"telegram_chat_id": "abc"}' } }))), 'bad chat id refused');
  ok((await throws(run(wf, CT, { nodes: { 'Approval: build': A }, binary: { text: '{nope' } }))).includes('not valid JSON'), 'bad JSON refused without echoing it');
  ok((await throws(run(wf, CT, { nodes: { 'Approval: build': A }, binary: null }))).includes('was not read'), 'missing file refused');
  // Approve passed
  A = mk(6, 2, 'loudness off'); A.results[2].file = null; A.results[3].file = null; A.results[3].status = 'failed'; A.not_made = [{ version: 'H2_6S_9X16', reason: 'hook H2 was dropped' }]; A.all.push('H2_6S_9X16');
  const rt = (await run(wf, 'Approval: read Telegram', { nodes: { 'Approval: build': A }, input: { data: { approved: true } } }))[0].json;
  ok(rt.approved.length === 2 && rt.expired === false && rt.channel === 'Telegram', 'Approve passed: ' + rt.approved.join(','));
  ok(rt.approver === 'reviewer (Telegram)' && rt.general_note === 'approved on Telegram' && rt.rejected.length === 5, 'approver, general note, rejected ' + rt.rejected.length);
  ok(rt.rejected[0].note === 'not approved (failed QA: loudness off 0)' && rt.rejected.some((x) => x.note.startsWith('not made: hook H2')), 'reject notes keep the QA reasons: ' + JSON.stringify(rt.rejected.map((x) => x.note)));
  ok(['expired', 'approved', 'rejected', 'ignored', 'refused', 'approved_notes', 'general_note', 'submitted_at'].every((k) => k in rt), 'same shape as the form read');
  const rx = (await run(wf, 'Approval: read Telegram', { nodes: { 'Approval: build': A }, input: {} }))[0].json;
  ok(rx.expired === true && rx.channel === 'Telegram' && rx.approved.length === 0, 'no answer = expired, channel Telegram');
  const none = mk(3, 3, 'bad');
  const rz = (await run(wf, 'Approval: read Telegram', { nodes: { 'Approval: build': none }, input: { data: { approved: true } } }))[0].json;
  ok(rz.approved.length === 0 && rz.expired === false && rz.general_note === 'nothing approved (no version passed QA)', 'Approve passed with no passed version: ' + rz.general_note);
  const ask0 = (await run(wf, CT, { nodes: { 'Approval: build': none }, binary: cfg }))[0].json.ask;
  ok(ask0.includes('nothing to approve'), 'ask text says there is nothing to approve');
  // Package: nothing approved or expired
  const NA = 'Package: nothing approved or expired';
  const pe = (await run(wf, NA, { input: { expired: true, channel: 'Telegram' } }))[0].json;
  ok(pe.package_status === 'expired' && pe.package.error.includes('Telegram approval'), 'expired text names the channel: ' + pe.package.error);
  const pf = (await run(wf, NA, { input: { expired: true, channel: 'form' } }))[0].json;
  ok(pf.package.error.includes('form approval'), 'expired text, form');
  const pn = (await run(wf, NA, { input: { expired: false, channel: 'Telegram' } }))[0].json;
  ok(pn.package_status === 'nothing_approved' && pn.package.error === 'nothing was approved: no package (no version passed QA)', 'nothing approved text: ' + pn.package.error);
  const pn2 = (await run(wf, NA, { input: { expired: false, channel: 'form' } }))[0].json;
  ok(pn2.package.error === 'nothing was approved: no package', 'nothing approved, form');
  // Telegram: failed
  const f = (await run(wf, 'Telegram: failed', { input: { error: 'Bad Request: chat not found at https://api.telegram.org/' + FAKE_TOKEN + '/sendMessage' } }))[0].json;
  ok(f.telegram_error.includes('chat not found') && !/AAAAAAAA/.test(f.telegram_error) && !/123456789:/.test(f.telegram_error), 'token scrubbed: ' + f.telegram_error);
  const f2 = (await run(wf, 'Telegram: failed', { input: { somethingElse: { big: 'x'.repeat(500) } } }))[0].json;
  ok(f2.telegram_error === 'Telegram step failed (no error text)', 'no error text: a fixed message, not the item');
  // routing after Send and Wait: the two IF nodes, evaluated as n8n would (their expressions), then the answer node
  const cond = (name) => src2(name);
  function src2(name) { const n = wf.nodes.find((x) => x.name === name); const e = n.parameters.conditions.conditions[0].leftValue.replace(/^=\{\{/, '').replace(/\}\}$/, ''); return new Function('$json', 'return (' + e + ');'); }
  const sendFailed = cond('Telegram: send failed?'), reviewPressed = cond('Telegram: Review pressed?');
  const route = async (item) => {                    // which of the workflow's paths one item from the Send and Wait takes
    if (sendFailed(item)) return 'telegram-failed -> form';
    if (reviewPressed(item)) return 'review link -> form';
    const out = (await run(wf, 'Approval: read Telegram', { nodes: { 'Approval: build': A }, input: item }))[0].json;
    return out.expired ? 'expired' : 'approved ' + out.approved.length;
  };
  ok((await route({ error: 'Bad Request: chat not found' })) === 'telegram-failed -> form', 'send failure: only the form fallback, never "expired"');
  ok((await route({})) === 'expired', 'timeout ({}): expired');
  ok((await route({ data: { approved: false } })) === 'review link -> form', 'tap on Review: link and form');
  ok((await route({ data: { approved: true } })).startsWith('approved '), 'tap on Approve passed: approved');
  // done text
  const S = { run_id: '77' };
  const built = { package_status: 'built', approved: ['a', 'b'], rejected: [1], body: { run: { mode: 'stand-in voice, stand-in b-roll; GPU and voice not costed yet' }, approved: [{ approver: 'reviewer (Telegram)' }] }, package: { folder: 'runs/77/package', report: { cost_per_asset_usd: 0.0042, turnaround_min: 36.5, machine_min: 34 } } };
  const DT = 'Telegram: done text';
  const nodesFor = (B) => ({ 'Telegram: send done?': B, 'Run: state for 12b': S });
  const d = (await run(wf, DT, { nodes: nodesFor(built), binary: cfg }))[0].json;
  ok(d.chat_id === '12345678' && d.text.includes('Approved 2, rejected 1 (reviewer (Telegram))') && d.text.includes('$0.004') && d.text.includes('total 36.5 min') && d.text.includes('runs/77/package'), 'done message');
  const dn = (await run(wf, DT, { nodes: nodesFor({ package_status: 'nothing_approved', channel: 'Telegram', rejected: [1, 2, 3] }), binary: cfg }))[0].json;
  ok(dn.text.startsWith('Run 77: nothing approved (no version passed QA), no package.') && dn.text.includes('Rejected 3'), 'no-package message: ' + dn.text.replace(/\n/g, ' | '));
  const de = (await run(wf, DT, { nodes: nodesFor({ package_status: 'expired', channel: 'Telegram', rejected: [] }), binary: cfg }))[0].json;
  ok(de.text.includes('the Telegram approval got no answer in 7 days'), 'expired message names the channel');
  const d2 = (await run(wf, DT, { nodes: nodesFor(built), binary: null }))[0].json;
  ok(d2.error && !d2.chat_id, 'done text without config does not throw: ' + d2.error);
  const DR = 'Telegram: done result';
  const dr1 = (await run(wf, DR, { nodes: { 'Telegram: send done?': built, [DT]: d2 }, input: d2 }))[0].json;
  ok(dr1.telegram_done === 'skipped: no chat id' && dr1.package.folder === 'runs/77/package', 'no chat id: skipped, package carried');
  const dbug = (await run(wf, DR, { nodes: { 'Telegram: send done?': built, [DT]: { error: "Node 'Run: state for 12b' hasn't been executed" } }, input: { error: "Node 'Run: state for 12b' hasn't been executed" } }))[0].json;
  ok(dbug.telegram_done === "skipped: Node 'Run: state for 12b' hasn't been executed", 'a text-step bug keeps its reason: ' + dbug.telegram_done);
  const dr2 = (await run(wf, DR, { nodes: { 'Telegram: send done?': built, [DT]: d }, input: { error: 'Bad Request: chat not found' } }))[0].json;
  ok(dr2.telegram_done === 'failed: Bad Request: chat not found', 'send failed: ' + dr2.telegram_done);
  const dr3 = (await run(wf, DR, { nodes: { 'Telegram: send done?': built, [DT]: d }, input: { ok: true, result: { message_id: 5 } } }))[0].json;
  ok(dr3.telegram_done === 'sent', 'sent');
  // error workflow
  const E = { run_id: '77', node: 'Render: POST /render', message: 'x'.repeat(250) + ' https://api.telegram.org/' + FAKE_TOKEN + '/sendMessage <b>' };
  const ET = 'Error: Telegram text';
  const et = (await run(er, ET, { nodes: { 'Error: build error.json': E }, binary: cfg }))[0].json;
  ok(et.chat_id === '12345678' && et.text.includes('run 77 failed') && et.text.includes('Node: Render: POST /render') && et.text.length < 700, 'error text, ' + et.text.length + ' chars');
  ok(!/AAAAAAAA/.test(et.text) && !/123456789:/.test(et.text), 'error text: token scrubbed from the message');
  const et2 = (await run(er, ET, { nodes: { 'Error: build error.json': E }, binary: null }))[0].json;
  ok(et2.error && !et2.chat_id, 'error text without config does not throw');
  const ER = 'Error: record the Telegram result';
  const rec = async (T, input) => JSON.parse((await run(er, ER, { nodes: { 'Error: build error.json': E, [ET]: T }, input }))[0].binary.data.text);
  ok((await rec(et, { ok: true })).telegram === 'sent', 'error.json: telegram sent');
  ok((await rec(et, { error: 'Bad Request: chat not found' })).telegram === 'failed: Bad Request: chat not found', 'error.json: telegram failed with the reason');
  const skipped = await rec(et2, et2);
  const bug = await rec({ error: 'x.map is not a function' }, { error: 'x.map is not a function' });
  ok(bug.telegram === 'skipped: x.map is not a function', 'error.json: a text-step bug keeps its reason');
  ok(skipped.telegram === 'skipped: no chat id' && skipped.run_id === '77' && skipped.node === 'Render: POST /render', 'error.json: skipped, original fields kept');
  console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})();
