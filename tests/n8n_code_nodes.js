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
  // compliance: brackets are a hit on a voice line only (the /lint call says which texts are spoken)
  const H = { id: 'H1', lines: { '6': 'Sleep (really) better.', '15': 'Sleep better.', '30': 'Sleep well.' }, broll: ['a (x) shot', 'b', 'c'] };
  const col = (await run(wf, 'Compliance: collect', { input: { hooks: [H] } }))[0].json;
  ok(JSON.stringify(col.body.voice) === JSON.stringify([true, true, true, false, false, false]) && col.body.texts.length === 6, 'lint 1 marks the three lines as spoken and the shots not');
  const comp = col.comp_items.map((i, k) => ({ ...i, hits: k === 0 ? ['brackets'] : [] }));
  const judge = { choices: [{ message: { content: JSON.stringify({ 'H1:6': { ok: false, rewrite: 'Sleep, really, better.' }, 'H1:15': { ok: true, rewrite: '' }, 'H1:30': { ok: true, rewrite: '' } }) } }] };
  const a1 = (await run(wf, 'Compliance: apply 1', { nodes: { 'Compliance: judge prompt': { comp_items: comp, hooks: [H], llm_usd: 0 } }, input: judge }))[0].json;
  ok(a1.comp_items[0].status === 'rewritten' && JSON.stringify(a1.body.voice) === '[true]' && a1.body.texts[0] === 'Sleep, really, better.', 'a bracket hit goes to the judge for one rewrite, which is linted as a spoken line');
  // Shot list: a translator in front of the old beats syntax (Beats: parse); the output is the same `beats` shape
  const BP = 'Beats: parse';
  const parse = async (text) => (await run(wf, BP, { input: { run_id: '77', source: { path: 'runs/77/source.mp4' }, brief: { beats: text } } }))[0].json;
  const parseErr = async (text) => await throws(parse(text));
  const OLD = 'S1 0.5-6.0 opener-H1 centre\nS2 6.6-9.3 tension\nS3 9.6-15.0 routine auto\nS4 15.5-21.5 opener-H2 x9=0.21,x1=0.30\nS5 21.9-26.8 calm x9=0.35\nS6 27.5-31.5 close x9=0.37,x1=0.42';
  const PLAIN = '0:00.5-0:06    opening, hook 1\n0:06.6-0:09.3  tension\n0:09.6-0:15    routine, follow the action\n0:15.5-0:21.5  opening, hook 2, tall crop 21%, square crop 30%\n0:21.9-0:26.8  calm, tall crop 35%\n0:27.5-0:31.5  close, tall crop 37%, square crop 42%';
  const old = await parse(OLD);
  ok(old.beats.length === 6 && old.beats[3].role === 'opener-H2' && JSON.stringify(old.beats[3].x) === '{"9X16":0.21,"1X1":0.3}' && old.beats[2].face === 'auto', 'old syntax still parses unchanged');
  ok(JSON.stringify(old.body) === JSON.stringify({ run_id: '77', source: { path: 'runs/77/source.mp4' }, beats: old.beats, formats: ['9X16', '1X1'] }), 'old syntax: the /reframe body shape');
  const plain = await parse(PLAIN);
  ok(JSON.stringify(plain.beats) === JSON.stringify(old.beats) && JSON.stringify(plain.body) === JSON.stringify(old.body), 'the plain-words block gives exactly the old syntax beats and body');
  const formPh = wf.nodes.find((n) => n.name === 'Brief form').parameters.formFields.values.find((x) => x.fieldLabel === 'Shot list').placeholder;
  ok(formPh === PLAIN && JSON.stringify((await parse(formPh)).beats) === JSON.stringify(old.beats), 'the form placeholder is the plain block, and it parses');
  ok(plain.beats.map((b) => b.name).join() === 'S1,S2,S3,S4,S5,S6' && plain.beats.map((b) => b.role).join() === 'opener-H1,tension,routine,opener-H2,calm,close', 'shots are named by position, roles mapped');
  const ROLES6 = ['opening, hook 1', 'tension', 'routine', 'opening, hook 2', 'calm', 'close'];
  const seconds = await parse(['0.5-6', '6.6-9.3', '9.6 - 15', '15.5–21.5', '21.9-26.8', '27.5-31.5'].map((l, i) => l + '  ' + ROLES6[i]).join('\n'));
  ok(seconds.beats[0].start === 0.5 && seconds.beats[0].end === 6 && seconds.beats[2].start === 9.6 && seconds.beats[2].end === 15 && seconds.beats[3].start === 15.5 && seconds.beats[3].end === 21.5, 'plain seconds, spaces around the dash, an en dash');
  const mix = await parse('1:05.5-1:20  opening, hook 1\n1:20.5–1:30 tension, left\n0:09.6-0:15 routine, centre\n0:15.5—0:21.5 opening hook 2, right\n0:21.9-0:26.8  calm\n0:27.5-0:31.5  closing');
  ok(mix.beats[0].start === 65.5 && mix.beats[0].end === 80 && mix.beats[1].start === 80.5 && mix.beats[1].face === 'left' && mix.beats[3].face === 'right' && mix.beats[3].role === 'opener-H2' && mix.beats[5].role === 'close', 'minutes, an em dash, left/right, "opening hook 2" without a comma, "closing" = close');
  const centre = await parse(PLAIN.replace('routine, follow the action', 'routine, center'));
  ok(centre.beats[2].face === 'centre' && centre.beats[1].face === 'centre' && !('x' in centre.beats[1]), 'centre/center, and no position means centre');
  const odd = await parse('0:00.5-0:06 Opening, Hook 1,   TALL CROP 5.5 %\n' + PLAIN.split('\n').slice(1).join('\n'));
  ok(JSON.stringify(odd.beats[0].x) === '{"9X16":0.055}', 'case, spaces and a decimal percent are read: ' + JSON.stringify(odd.beats[0].x));
  const mixed = await parse(['S1 0.5-6.0 opener-H1 centre', '', '# the second shot', '0:06.6-0:09.3  tension  # trailing note', '0:09.6-0:15    routine, follow the action', 'S4 15.5-21.5 opener-H2 x9=0.21,x1=0.30', '21.9-26.8 calm, tall crop 35%', 'S6 27.5-31.5 close x9=0.37,x1=0.42'].join('\n'));
  ok(JSON.stringify(mixed.beats) === JSON.stringify(old.beats), 'old and plain lines mixed, blank lines and # comments ignored');
  const crlf = await parse(PLAIN.replace(/\n/g, '\r\n') + '\r\n\r\n');
  ok(JSON.stringify(crlf.beats) === JSON.stringify(old.beats), 'Windows line ends and trailing blank lines');
  // errors, in plain words, with the shot number
  const EP = (t, re, m) => parseErr(t).then((e) => ok(re.test(e), m + ': ' + e));
  const lineN = (n, text) => PLAIN.split('\n').map((l, i) => (i === n - 1 ? text : l)).join('\n');
  await EP(lineN(3, 'routine, follow the action'), /^Shot 3: no time range like 0:09\.6-0:15$/, 'no times');
  await EP(lineN(3, '0:09.6 routine'), /^Shot 3: no time range like 0:09\.6-0:15$/, 'one time only');
  await EP(lineN(2, '0:09.3-0:06.6  tension'), /^Shot 2: the end must be after the start$/, 'end before start');
  await EP(lineN(2, '0:06.6-0:75  tension'), /^Shot 2: seconds after the colon must be under 60/, 'seconds over 59');
  await EP(lineN(5, '0:21.9-0:26.8'), /^Shot 5: after the times, say what the shot is: opening, hook 1 \| opening, hook 2 \| tension \| routine \| calm \| close$/, 'no role');
  await EP(lineN(5, '0:21.9-0:26.8  dramatic'), /^Shot 5: "dramatic" is not a shot role; use opening, hook 1 \| opening, hook 2 \| tension \| routine \| calm \| close$/, 'unknown role');
  await EP(lineN(1, '0:00.5-0:06  opening'), /^Shot 1: say which hook the opening is: opening, hook 1 or opening, hook 2$/, 'opening without a hook');
  await EP(lineN(1, '0:00.5-0:06  opening, hook 3'), /^Shot 1: say which hook the opening is/, 'hook 3');
  await EP(lineN(6, '0:27.5-0:31.5  close, zoomed in'), /^Shot 6: "zoomed in" is not a position; use left, centre, right, follow the action, tall crop 20%, square crop 30%$/, 'unknown position word');
  await EP(lineN(6, '0:27.5-0:31.5  close, tall crop 140%'), /^Shot 6: a crop is a percentage from 0 to 100, not 140%$/, 'crop over 100');
  await EP(PLAIN.split('\n').slice(0, 5).join('\n'), /^the shot list needs six shots, one per line; found 5$/, 'five shots');
  await EP(PLAIN + '\n0:32-0:35  close', /^Shot 7: only six shots are needed, one per line$/, 'seven shots');
  await EP('S1 0.5-6.0 opener-H1\nS1 6.6-9.3 tension\n' + PLAIN.split('\n').slice(2).join('\n'), /beat S1 must appear exactly once/, 'a repeated old name still caught');
  await EP('S9 0.5-6.0 opener-H1\n' + PLAIN.split('\n').slice(1).join('\n'), /the name must be one of S1, S2, S3, S4, S5, S6/, 'old syntax errors keep their words');
  await EP(lineN(3, 'routine 0:09.6-0:15'), /^Shot 3: no time range/, 'times not first');
  const two = await parseErr(lineN(2, 'tension').replace('0:15.5-0:21.5  opening, hook 2', '0:15.5-0:21.5  opening'));
  ok(/^Shot 2: no time range/.test(two) && /; Shot 4: say which hook/.test(two), 'every unreadable line is reported at once: ' + two);
  // the form field is "Shot list" now, the old label still read (a script that posts "Beats")
  const RB = 'Run: ids and brief';
  const formIn = { Brand: 'Restfield', Product: 'Sleep care', Offer: 'First visit free', 'Key message 1': 'a', 'Key message 2': 'b', 'Key message 3': 'c', 'Required disclaimer': 'd', 'Source clip link': 'https://example.com/a.mp4' };
  const rb1 = (await run(wf, RB, { input: { ...formIn, 'Shot list': PLAIN } }))[0].json;
  ok(rb1.brief.beats === PLAIN, 'Run: ids and brief reads the Shot list field');
  const rb2 = (await run(wf, RB, { input: { ...formIn, Beats: OLD } }))[0].json;
  ok(rb2.brief.beats === OLD, 'and still the old Beats label');
  ok(/needs a shot list/.test(await throws(run(wf, RB, { input: { ...formIn, 'Shot list': '  ' } }))), 'an empty shot list is refused in plain words');
  console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})();
