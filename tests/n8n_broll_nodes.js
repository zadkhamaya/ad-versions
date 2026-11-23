// Runs the live b-roll Code nodes of n8n/workflow.json and the pod clean-up nodes of n8n/errors.json against fixtures (no n8n, no toolkit): node tests/n8n_broll_nodes.js
// The IF nodes are evaluated from their own expressions, and the whole loop (pod, jobs, downloads, finish) is walked with a fake toolkit at the end.
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname, '..', 'n8n');
const wf = JSON.parse(fs.readFileSync(path.join(dir, 'workflow.json'), 'utf8'));
const er = JSON.parse(fs.readFileSync(path.join(dir, 'errors.json'), 'utf8'));
const node = (w, name) => w.nodes.find((n) => n.name === name);
const AF = Object.getPrototypeOf(async function () {}).constructor;
// nodes: {name: json | [json, ...]} = what $('name') returns (first/last/all); input: one json or an array of items; binary: {text} for the first input item
async function run(w, name, { nodes = {}, input = {}, inputs = null, binaries = null, exec = '77' }) {
  const fn = new AF('$', '$input', '$execution', 'Buffer', node(w, name).parameters.jsCode);
  const items = (inputs || [input]).map((j, i) => ({ json: j, binary: binaries && binaries[i] ? { data: { __buf: Buffer.from(binaries[i]) } } : undefined }));
  const ctx = { helpers: { getBinaryDataBuffer: async (i) => items[i].binary.data.__buf, prepareBinaryData: async (b, n) => ({ size: b.length, name: n, text: b.toString() }) } };
  const $ = (n) => {
    if (!(n in nodes)) throw new Error("Node '" + n + "' hasn't been executed");
    const v = nodes[n]; const arr = (Array.isArray(v) ? v : [v]).map((j) => ({ json: j }));
    return { first: () => arr[0], last: () => arr[arr.length - 1], all: () => arr };
  };
  const $input = { first: () => items[0], all: () => items };
  return fn.call(ctx, $, $input, { id: exec }, Buffer);
}
const cond = (w, name) => { const e = node(w, name).parameters.conditions.conditions[0].leftValue.replace(/^=\{\{/, '').replace(/\}\}$/, ''); return new Function('$', '$json', 'return (' + e + ');'); };
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
const throws = async (p) => { try { await p; return ''; } catch (e) { return e.message; } };
const P = 'B-roll live: ';
const clone = (x) => JSON.parse(JSON.stringify(x));
const mp4 = (n = 2000) => Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from('ftypisom'), Buffer.alloc(n)]);
const SHOTS = ['a dim bedroom at night, curtains moving slightly, no people', 'a phone face up on a bedside table', 'soft morning light through linen curtains.'];
const Sbase = () => ({ run_id: '77', look: { line: 'warm lamp light, deep blue shadows, shallow depth of field, fine grain.', source: 'vision' }, voice_mode: 'stand-in', broll_mode: 'live',
  hooks: [{ id: 'H1', broll: [...SHOTS], lines: { '6': 'a', '15': 'b', '30': 'c' } }, { id: 'H2', broll: [...SHOTS], lines: { '6': 'a', '15': 'b', '30': 'c' } }] });
const CFG = JSON.stringify({ toolkit_url: 'https://toolkit.example.com/' });
const planOut = async (S = Sbase(), cfg = CFG) => (await run(wf, P + 'plan', { nodes: { 'Look: ready': S }, binaries: [cfg] }))[0].json;
(async () => {
  // ---- plan
  let plan = await planOut();
  let L = plan.live;
  ok(L.toolkit_url === 'https://toolkit.example.com' && L.clips.length === 12 && L.variant === 'turbo8' && L.seconds === 3, 'plan: 12 clips, address from the config without the trailing slash');
  ok(L.clips.map((c) => c.clip + '_' + c.size).join() === 'H1A_1344x768,H1A_768x1344,H1B_1344x768,H1B_768x1344,H1C_1344x768,H1C_768x1344,H2A_1344x768,H2A_768x1344,H2B_1344x768,H2B_768x1344,H2C_1344x768,H2C_768x1344', 'plan: the names the stand-in branch writes');
  const c0 = L.clips[0];
  ok(c0.prompt === 'a dim bedroom at night, curtains moving slightly, no people, warm lamp light, deep blue shadows, shallow depth of field, fine grain, 24 fps, no people, no text', 'plan: prompt = shot + look line + suffix: ' + c0.prompt);
  ok(L.clips.every((c) => /^[A-Za-z0-9]{1,16}$/.test(c.clip) && c.prompt.length <= 2000 && Number.isInteger(c.seed) && c.seed >= 0 && c.seed < 2 ** 31 && ((c.w === 1344 && c.h === 768) || (c.w === 768 && c.h === 1344))), 'plan: ids, prompts, seeds and sizes fit /api/broll');
  const S2 = Sbase(); S2.hooks[0].broll[1] = null; S2.look = { line: '', source: 'fallback' };
  const p2 = (await planOut(S2)).live;
  ok(p2.clips.length === 10 && !p2.clips.some((c) => c.clip === 'H1B') && p2.clips[0].prompt.endsWith('no people, no text') && !p2.clips[0].prompt.includes(',,'), 'plan: a dropped shot is not asked for; an empty look line leaves no gap');
  ok(/not valid JSON/.test(await throws(planOut(Sbase(), '{nope'))) && /https:\/\/<host>/.test(await throws(planOut(Sbase(), '{"toolkit_url": "http://toolkit.example.com"}'))) && /https:\/\/<host>/.test(await throws(planOut(Sbase(), '{"toolkit_url": "https://toolkit.example.com/api"}'))), 'plan: a bad config is refused (http, a path, not JSON)');
  ok(/was not read/.test(await throws(run(wf, P + 'plan', { nodes: { 'Look: ready': Sbase() }, input: { error: 'No file(s) found' } }))), 'plan: a missing config file is a clear error');
  const S3 = Sbase(); S3.hooks.forEach((h) => { h.broll = [null, null, null]; });
  ok(/no shot left/.test(await throws(planOut(S3))), 'plan: nothing to make is an error');

  // ---- pod decision
  const now = Date.now();
  const VM = (o = {}) => ({ name: 'swarm-pod-test', kind: 'pod', state: 'ready', bundles: ['h3-t2v'], bundles_started: ['h3-t2v'], add_queue: [], snapshot: { picker: { download: 'done' } }, rate: 0.43, final_price_hr: null, launched_at: Math.floor(now / 1000) - 600, ...o });
  // sent: this run's deploy result exists (the pod it deployed is named in it)
  const dec = async (resp, { sent = false, late = false } = {}) => {
    const l = clone(L); if (late) l.pod_deadline_ms = now - 1;
    const nodes = { [P + 'plan']: { live: l } }; if (sent) nodes[P + 'deploy result'] = { live: l, next: 'wait', pod_name: 'swarm-pod-test' };
    return (await run(wf, P + 'pod decision', { nodes, input: resp }))[0].json;
  };
  const okr = (body) => ({ statusCode: 200, body: { configured: true, missing: [], ...body } });
  ok(L.pod_deadline_ms - L.started_ms === 45 * 60000, 'plan: 45-minute pod limit (spec section 12 wins over the brief)');
  let d = await dec(okr({ vm: null }));
  ok(d.next === 'deploy', 'decision: no pod -> deploy');
  for (const vm of [VM(), VM({ state: 'booting' }), VM({ state: 'deleting' }), VM({ kind: 'build', name: 'swarm-build-x', state: 'ready' }), VM({ state: 'ready', held: { holder: 'Swap' } }), VM({ bundles: ['h3-swap'] })]) {
    const m = await throws(dec(okr({ vm })));
    ok(m === 'A GPU pod is already running (' + vm.name + ', ' + vm.state + '). Delete it on the Pods page or choose B-roll: stand-in.', 'decision: a pod found before our deploy (' + vm.name + ', ' + vm.state + ') is never used: ' + m.slice(0, 40));
  }
  for (const st of ['launching', 'booting', 'setting_up', 'starting']) { d = await dec(okr({ vm: VM({ state: st }) }), { sent: true }); ok(d.next === 'wait' && d.live.pod.name === 'swarm-pod-test', 'decision: our pod ' + st + ' -> wait'); }
  d = await dec(okr({ vm: VM({ final_price_hr: 0.39 }) }), { sent: true });
  ok(d.next === 'ready' && d.live.pod.rate_usd_h === 0.39 && !('by_this_run' in d.live.pod), 'decision: our ready pod, the final price wins, no ownership guess in the state');
  d = await dec(okr({ vm: VM() }), { sent: true });
  ok(d.next === 'ready' && d.live.pod.rate_usd_h === 0.43, 'decision: ready with its bundles done -> ready');
  ok((await dec(okr({ vm: VM({ add_queue: [{ kind: 'bundles', names: ['h3-t2v'] }], bundles_started: [] }) }), { sent: true })).next === 'wait', 'decision: ready but bundles queued -> wait');
  ok((await dec(okr({ vm: VM({ snapshot: { picker: { download: 'running' } } }) }), { sent: true })).next === 'wait', 'decision: download running -> wait');
  ok((await dec(okr({ vm: VM({ snapshot: { picker: { download: 'idle' } } }) }), { sent: true })).next === 'wait', 'decision: a started bundle with no "done" yet -> wait');
  d = await dec(okr({ vm: VM({ snapshot: { picker: { download: 'failed: disk full' } } }) }), { sent: true });
  ok(d.next === 'give_up' && /^no pod: a bundle download failed/.test(d.reason), 'decision: a failed download gives up (no b-roll): ' + d.reason);
  d = await dec(okr({ vm: VM({ state: 'setup_failed', step: 'pull', setup_error: 'no disk' }) }), { sent: true });
  ok(d.next === 'give_up' && /^no pod: the pod swarm-pod-test failed its setup \(pull: no disk\)/.test(d.reason), 'decision: setup_failed gives up');
  d = await dec(okr({ vm: VM({ state: 'booting' }) }), { sent: true, late: true });
  ok(d.next === 'give_up' && d.reason === 'no pod: the pod was not ready after 45 minutes (the pod swarm-pod-test is booting)' && d.live.pod.name === 'swarm-pod-test', 'decision: not ready in 45 minutes -> give up, with the pod named for the delete');
  d = await dec(okr({ vm: VM({ snapshot: { picker: { download: 'running' } } }) }), { sent: true, late: true });
  ok(d.next === 'give_up' && /bundles are downloading/.test(d.reason), 'decision: bundles still downloading at 45 minutes -> give up');
  ok(/gave no GPU pod in 45 minutes/.test(await throws(dec({ error: { message: 'x' } }, { late: true }))), 'decision: nothing deployed and the toolkit silent for 45 minutes is an error, not a give-up');
  ok(/is being deleted/.test(await throws(dec(okr({ vm: VM({ state: 'deleting' }) }), { sent: true }))), 'decision: our pod deleting stops at once');
  ok(/is gone from the Pods page/.test(await throws(dec(okr({ vm: null }), { sent: true }))), 'decision: our pod vanished stops at once (no redeploy)');
  ok(/is not the one this run deployed \(swarm-pod-test\)/.test(await throws(dec(okr({ vm: VM({ name: 'swarm-pod-other' }) }), { sent: true }))), 'decision: a different pod after our deploy stops');
  ok(/not configured/.test(await throws(dec({ statusCode: 200, body: { configured: false, missing: ['MASSED_API_KEY'], vm: null } }))), 'decision: unconfigured toolkit');
  ok(/refused the machine token/.test(await throws(dec({ statusCode: 401, body: { error: 'Sign in again.' } }))), 'decision: 401');
  ok((await dec({ error: { message: 'ECONNRESET' } })).next === 'wait' && (await dec({ statusCode: 502, body: {} }, { sent: true })).next === 'wait', 'decision: a hiccup waits and asks again');
  const gu = cond(wf, P + 'give up?'), pr0 = cond(wf, P + 'pod ready?'), dn0 = cond(wf, P + 'deploy needed?');
  ok(gu({}, { next: 'give_up' }) && !gu({}, { next: 'wait' }) && pr0({}, { next: 'ready' }) && !pr0({}, { next: 'wait' }) && dn0({}, { next: 'deploy' }) && !dn0({}, { next: 'wait' }), 'decision: the three IFs read next');

  // ---- catalog, pod.json and deploy
  const decOut = { live: L, next: 'deploy' };
  const cat = (r) => run(wf, P + 'catalog check', { nodes: { [P + 'pod decision']: decOut }, input: r });
  ok((await cat({ statusCode: 200, body: { bundles: [{ name: 'h3-swap' }, { name: 'h3-t2v' }] } }))[0].json.next === 'deploy', 'catalog: h3-t2v listed -> carry on');
  ok(/does not list the h3-t2v bundle \(1 bundles listed; lists error: old/.test(await throws(cat({ statusCode: 200, body: { bundles: [{ name: 'h3-swap' }], lists: { error: 'old' } } }))), 'catalog: not listed -> stop');
  ok(/refresh failed \(500\)/.test(await throws(cat({ statusCode: 500, body: { error: 'x' } }))), 'catalog: errors stop');
  const pq = (await run(wf, P + 'pod request', { input: decOut }))[0];
  const pqd = JSON.parse(pq.binary.data.text);
  ok(pq.binary.data.name === 'pod.json' && /^\d{4}-\d\d-\d\dT/.test(pqd.requested_at) && pqd.requested_ms > 0 && !('name' in pqd) && pq.json.pod_request.requested_ms === pqd.requested_ms, 'pod.json (before the deploy): requested_at only');
  const dep = (r) => run(wf, P + 'deploy result', { nodes: { [P + 'pod decision']: decOut, [P + 'pod request']: pq.json }, input: r });
  const dd = (await dep({ statusCode: 202, body: { vm: { name: 'swarm-pod-x' } } }))[0];
  const ddj = JSON.parse(dd.binary.data.text);
  ok(dd.json.pod_name === 'swarm-pod-x' && dd.json.next === 'wait' && dd.json.live.clips.length === 12 && ddj.name === 'swarm-pod-x' && ddj.requested_ms === pqd.requested_ms, 'deploy: 202 -> pod.json now names the pod and keeps requested_at');
  ok(/refused to deploy.*409.*premium A6000 is out of stock: ask the owner/.test(await throws(dep({ statusCode: 409, body: { error: 'No Premium A6000 is free', confirm: 'plain' } }))), 'deploy: the plain-A6000 question is the owner\'s');
  ok(/pod address could not be probed/.test(await throws(dep({ statusCode: 409, body: { error: 'Can\'t tell', confirm: 'tunnel' } }))) && /refused to deploy the GPU pod \(409\): The Massed balance/.test(await throws(dep({ statusCode: 409, body: { error: 'The Massed balance $1.00 is under 2 h' } }))), 'deploy: other refusals carry the toolkit\'s words');
  const depNode = node(wf, P + 'POST /api/pods/deploy');
  ok(depNode.parameters.jsonBody === "={{ { cap_hours: 8, bundles: ['h3-t2v'], addons: [] } }}", 'deploy: the Pods page\'s body for a pod with one bundle (cap 8 h, no image, product default)');

  // ---- jobs
  const ready = { live: L, next: 'ready' };
  let nb = (await run(wf, P + 'next batch', { input: ready }))[0].json;
  ok(nb.batch.length === 12 && nb.body.clips.length === 12 && nb.body.variant === 'turbo8' && nb.body.seconds === 3 && nb.live.clips.every((c) => c.tries === 0) && nb.live.submit_until_ms > Date.now(), 'batch: all 12 in one job');
  ok(JSON.stringify(Object.keys(nb.body.clips[0])) === '["id","prompt","seed","w","h"]', 'batch: only the fields the API takes');
  const fin = clone(L); fin.clips.forEach((c) => { c.status = 'done'; });
  ok((await run(wf, P + 'next batch', { input: { live: fin } }))[0].json.batch.length === 0 && !cond(wf, P + 'anything to submit?')({}, { batch: [] }) && cond(wf, P + 'anything to submit?')({}, { batch: ['x'] }), 'batch: nothing pending -> finish');
  const sub = (r, n = nb) => run(wf, P + 'submit result', { nodes: { [P + 'next batch']: n }, input: r });
  let sr = (await sub({ statusCode: 202, body: { id: '0123456789ab' } }))[0].json;
  ok(sr.accepted && sr.live.job.id === '0123456789ab' && sr.live.jobs.length === 1 && cond(wf, P + 'job accepted?')({}, sr), 'submit: 202 -> job recorded');
  ok(sr.live.submit_until_ms === null, 'submit: a 202 resets the resubmit window (the next job gets a fresh 30 minutes)');
  const stale = clone(nb); stale.live.submit_until_ms = Date.now() - 1;           // the first job took over 30 minutes
  const afterAccept = (await sub({ statusCode: 202, body: { id: '0123456789ab' } }, stale))[0].json;
  const nb2 = (await run(wf, P + 'next batch', { input: { live: afterAccept.live } }))[0].json;
  ok(nb2.live.submit_until_ms > Date.now() + 29 * 60000, 'submit: a retry job after a long first job gets a fresh window, so one transient 409 does not throw the run away');
  sr = (await sub({ statusCode: 409, body: { error: 'The pod is still setting up its bundles.', retry: true } }))[0].json;
  ok(!sr.accepted && sr.live.submit_waits === 1 && sr.live.clips.every((c) => c.tries === 0) && !cond(wf, P + 'job accepted?')({}, sr), 'submit: 409 retry:true -> wait and ask again, nothing counted');
  const lateNb = clone(nb); lateNb.live.submit_until_ms = Date.now() - 1;
  ok(/would not take the b-roll job in 30 minutes/.test(await throws(sub({ statusCode: 409, body: { error: 'busy', retry: true } }, lateNb))), 'submit: retry has a 30-minute end');
  ok(/refused the b-roll job \(409\): The pod is held by Swap/.test(await throws(sub({ statusCode: 409, body: { error: 'The pod is held by Swap.', retry: false } }))), 'submit: 409 retry:false stops');
  ok(/refused the b-roll job \(503\): B-roll workflow: workflow_api.json is not in/.test(await throws(sub({ statusCode: 503, body: { error: 'B-roll workflow: workflow_api.json is not in /x' } }))), 'submit: the 503 message reaches the error workflow');
  ok(/refused the b-roll job \(400\)/.test(await throws(sub({ statusCode: 400, body: { error: 'clips: bad' } }))) && /no answer/.test(await throws(sub({ error: { message: 'x' } }))), 'submit: 400 and no answer stop');

  // ---- polling
  const accepted = (await sub({ statusCode: 202, body: { id: '0123456789ab' } }))[0].json;
  const clipsOf = (keys) => keys.map((k) => { const m = /^(\w+)_(\d+)x(\d+)$/.exec(k); return { id: m[1], w: +m[2], h: +m[3], file: k + '.mp4', seconds: 3 }; });
  const poll = (resp, wait) => run(wf, P + 'poll result', { nodes: { [P + 'wait for the clips']: wait, 'Look: ready': { run_id: '77' } }, input: resp });
  const T0 = Date.now();
  const w0 = clone(accepted); w0.live.job.submitted_ms = T0 - 40000;
  let pr = (await poll({ statusCode: 200, body: { state: 'running', clips: clipsOf(['H1A_1344x768']) } }, w0))[0].json;
  ok(pr.running === true && pr.live.job.seen['H1A_1344x768'].wall_s >= 39 && cond(wf, P + 'job running?')({}, pr), 'poll: running; a clip first seen is timed from the job start');
  const w1 = { live: pr.live };
  const both = (await poll({ statusCode: 200, body: { state: 'running', clips: clipsOf(['H1A_1344x768', 'H1A_768x1344', 'H1B_1344x768']) } }, w0))[0].json;
  ok(['H1A_1344x768', 'H1A_768x1344', 'H1B_1344x768'].every((k) => Math.abs(both.live.job.seen[k].wall_s - 13) <= 1), 'poll: clips first listed in one poll share the time since the job start (40 s / 3)');
  pr = (await poll({ statusCode: 200, body: { state: 'running', clips: clipsOf(['H1A_1344x768', 'H1A_768x1344']) } }, w1))[0].json;
  ok(pr.live.job.seen['H1A_1344x768'].ms === w1.live.job.seen['H1A_1344x768'].ms && pr.live.job.seen['H1A_768x1344'], 'poll: a clip seen before keeps its time');
  const allKeys = L.clips.map((c) => c.clip + '_' + c.size);
  pr = (await poll({ statusCode: 200, body: { state: 'done', clips: clipsOf(allKeys) } }, { live: pr.live }))[0].json;
  ok(pr.running === false && pr.fetch.length === 12 && pr.fetch[0].path === '/data/runs/77/broll/H1A_1344x768.mp4' && pr.fetch[0].url === 'https://toolkit.example.com/api/broll/jobs/0123456789ab/files/H1A_1344x768.mp4' && pr.live.jobs[0].state === 'done', 'poll: done -> 12 downloads, run-folder paths, API file URLs');
  ok(cond(wf, P + 'files to fetch?')({}, pr), 'poll: files to fetch? true');
  // a clip fails: the finished ones are kept, the failing one is retried with a new seed
  const w2 = clone(accepted);
  const fk = allKeys.slice(0, 2);
  let pf = (await poll({ statusCode: 200, body: { state: 'failed', error: 'clip H1B 1344x768: ComfyUI error', clips: clipsOf(fk) } }, w2))[0].json;
  const byk = (o, k) => o.live.clips.find((c) => c.clip + '_' + c.size === k);
  ok(byk(pf, 'H1A_1344x768').tries === 1 && byk(pf, 'H1B_1344x768').tries === 1 && byk(pf, 'H1B_768x1344').tries === 0, 'poll: tries count the clips that ran, not the ones the job never reached');
  ok(pf.fetch.length === 2 && byk(pf, 'H1A_1344x768').status === 'fetch' && byk(pf, 'H1B_1344x768').status === 'pending' && byk(pf, 'H1B_1344x768').attempts === 1 && byk(pf, 'H1B_1344x768').seed !== L.clips[2].seed, 'poll: failed clip -> kept finished, retry with a new seed');
  ok(byk(pf, 'H1B_768x1344').attempts === 0 && byk(pf, 'H1B_768x1344').status === 'pending', 'poll: clips after the failure are untouched');
  // second failure of the same clip drops it
  const w3 = clone(pf); w3.live.job = { id: 'aaaaaaaaaaaa', submitted_ms: T0, batch: ['H1B_1344x768'], seen: {}, poll_errors: 0 }; w3.live.jobs.push({ id: 'aaaaaaaaaaaa', clips: 1 });
  pf = (await poll({ statusCode: 200, body: { state: 'failed', error: 'clip H1B 1344x768: again', clips: [] } }, w3))[0].json;
  ok(byk(pf, 'H1B_1344x768').status === 'dropped' && /again/.test(byk(pf, 'H1B_1344x768').reason) && pf.fetch.length === 0 && !cond(wf, P + 'files to fetch?')({}, pf), 'poll: second failure -> dropped, nothing to fetch');
  // a step failure (no clip named) once is retried, twice stops
  const w4 = clone(accepted);
  const ps = (await poll({ statusCode: 200, body: { state: 'failed', error: 'tunnel: ssh timed out', clips: [] } }, w4))[0].json;
  ok(ps.live.job_failures === 1 && ps.live.clips.every((c) => c.status === 'pending' && c.attempts === 0), 'poll: a step failure is retried once with every clip still pending');
  ok(/failed twice before it could make a clip: tunnel/.test(await throws(poll({ statusCode: 200, body: { state: 'failed', error: 'tunnel: ssh timed out', clips: [] } }, { live: { ...ps.live, job: w4.live.job } }))), 'poll: a second step failure stops the run');
  const wd = clone(accepted);
  const pdn = (await poll({ statusCode: 200, body: { state: 'done', clips: clipsOf(allKeys.slice(0, 11)) } }, wd))[0].json;
  ok(pdn.fetch.length === 11 && byk(pdn, 'H2C_768x1344').attempts === 1, 'poll: done with a clip missing counts as a failed attempt');
  // network trouble while polling
  let pe = (await poll({ error: { message: 'ECONNRESET' } }, w0))[0].json;
  ok(pe.running && pe.live.job.poll_errors === 1, 'poll: one failed poll does not lose the job');
  const wmany = clone(w0); wmany.live.job.poll_errors = 8;
  ok(/lost the toolkit/.test(await throws(poll({ error: { message: 'ECONNRESET' } }, wmany))) && /has no b-roll job/.test(await throws(poll({ statusCode: 404, body: {} }, w0))), 'poll: nine failures in a row, or a 404, stop the run');
  const wold = clone(w0); wold.live.job.submitted_ms = T0 - (12 * 15 + 11) * 60000;
  ok(/still running after/.test(await throws(poll({ statusCode: 200, body: { state: 'running', clips: [] } }, wold))), 'poll: a job that never ends stops the run');

  // ---- downloads
  const ftf = await run(wf, P + 'files to fetch', { input: pr });
  ok(ftf.length === 12 && ftf[0].json.key === 'H1A_1344x768', 'fetch: one item per clip');
  const metas = ftf.map((i) => i.json);
  const chk = async (resp, bins) => (await run(wf, P + 'check download', { nodes: { [P + 'files to fetch']: metas.slice(0, resp.length) }, inputs: resp, binaries: bins }));
  const good = mp4();
  let cd = await chk([{ statusCode: 200 }, { statusCode: 404 }, { statusCode: 200 }, { error: { message: 'timeout' } }, { statusCode: 200 }], [good, null, 'not a video, just text '.repeat(100), null, Buffer.alloc(10)]);
  ok(cd.map((i) => i.json.ok).join() === 'true,false,false,false,false' && cd[0].binary && !cd[1].binary && /answered 404/.test(cd[1].json.reason) && /not an mp4/.test(cd[2].json.reason) && /timeout/.test(cd[3].json.reason), 'download check: only a 200 with an mp4 body goes on: ' + cd.map((i) => i.json.reason || 'ok').join(' | '));
  // after the writes
  const afterIn = (n, wroteOk) => ({ nodes: { [P + 'poll result']: pr, [P + 'check download']: metas.slice(0, n).map((m, i) => ({ ...m, ok: wroteOk[i], reason: wroteOk[i] ? undefined : 'download answered 404', bytes: 2000 })) }, inputs: wroteOk.map((w) => (w ? { fileName: 'x.mp4' } : { error: { message: 'no binary' } })) });
  const prFew = clone(pr); prFew.fetch = pr.fetch.slice(0, 3);
  const ad = async (okList, prev = prFew) => (await run(wf, P + 'after downloads', { ...afterIn(okList.length, okList), nodes: { [P + 'poll result']: prev, [P + 'check download']: metas.slice(0, okList.length).map((m, i) => ({ ...m, ok: okList[i], reason: okList[i] ? undefined : 'download answered 404', bytes: 2000 })) } }))[0].json;
  let af = await ad([true, true, false]);
  ok(byk(af, 'H1A_1344x768').status === 'done' && byk(af, 'H1A_1344x768').file === 'runs/77/broll/H1A_1344x768.mp4' && byk(af, 'H1B_1344x768').status === 'pending' && byk(af, 'H1B_1344x768').attempts === 1 && af.live.job === null, 'after: written = done, a missing file = a failed attempt (pending again)');
  const prAgain = clone(prFew); prAgain.live.clips.find((c) => c.clip === 'H1B' && c.size === '1344x768').attempts = 1;
  af = await ad([true, true, false], prAgain);
  ok(byk(af, 'H1B_1344x768').status === 'dropped', 'after: the second failure drops the clip');
  const nothing = (await run(wf, P + 'after downloads', { nodes: { [P + 'poll result']: { live: clone(L), running: false, fetch: [] } }, input: { live: clone(L), running: false, fetch: [] } }))[0].json;
  ok(nothing.live.job === null && nothing.live.clips.every((c) => c.status === 'pending'), 'after: nothing to fetch passes the state on');

  // ---- finish, the own-pod check and done
  const allDone = clone(L); allDone.clips.forEach((c, i) => { c.status = 'done'; c.file = 'runs/77/broll/' + c.clip + '_' + c.size + '.mp4'; c.wall_s = 20 + i; c.tries = 1; });
  allDone.pod = { name: 'swarm-pod-test', product: 'gpu_1x_a6000_high_ram', rate_usd_h: 0.4275, launched_at: Math.floor((Date.now() - 25 * 60000) / 1000) };
  const fn = async (live, S = Sbase()) => (await run(wf, P + 'finish', { nodes: { 'Look: ready': S }, input: { live } }))[0].json;
  let f = await fn(allDone);
  ok(f.result.files.length === 12 && Object.keys(f.result.slots.H1).join() === 'slot1,slot2,slot3' && f.result.slots.H1.slot2.wide === 'runs/77/broll/H1B_1344x768.mp4' && f.result.slots.H2.slot3.tall === 'runs/77/broll/H2C_768x1344.mp4' && f.result.notes.length === 0 && f.result.clips.H1A.wide_wall_s === 20, 'finish: 12 files, A->slot1 B->slot2 C->slot3, per-clip times');
  const part = clone(allDone); const pc = part.clips.find((c) => c.clip === 'H1B' && c.size === '768x1344'); pc.status = 'dropped'; pc.reason = 'clip H1B 768x1344: oom'; delete pc.file;
  f = await fn(part);
  ok(!('slot2' in f.result.slots.H1) && 'slot3' in f.result.slots.H1 && f.result.notes.length === 1 && /H1B: no cutaway \(clip H1B 768x1344: oom\)/.test(f.result.notes[0]) && f.result.files.length === 11, 'finish: one size missing -> the slot is left out and the note says why');
  const none = clone(allDone); none.clips.forEach((c) => { c.status = 'dropped'; c.reason = 'x'; });
  ok(/no b-roll clip could be made/.test(await throws(fn(none))), 'finish: no clip at all fails the run');
  const S4 = Sbase(); S4.hooks[1].broll = [null, null, null]; const l4 = clone(allDone); l4.clips = l4.clips.filter((c) => c.clip.startsWith('H1'));
  f = await fn(l4, S4);
  ok(Object.keys(f.result.slots.H2).length === 0 && Object.keys(f.result.slots.H1).length === 3 && f.result.notes.length === 0, 'finish: a hook with no shots has no slots and no note');
  const gl = clone(L); gl.pod = { name: 'swarm-pod-test', rate_usd_h: 0.43, launched_at: Math.floor(Date.now() / 1000) - 2700 };
  const gv = (await run(wf, P + 'finish', { nodes: { 'Look: ready': Sbase() }, input: { live: gl, next: 'give_up', reason: 'no pod: the pod was not ready after 45 minutes (x)' } }))[0].json;
  ok(gv.live.status.startsWith('no pod: ') && gv.result.files.length === 0 && Object.keys(gv.result.slots.H1).length === 0 && Object.keys(gv.result.slots.H2).length === 0 && /^no b-roll: no pod/.test(gv.result.notes[0]), 'finish: give up = every slot empty, the status and a note');
  // the own-pod check before the delete
  f = await fn(allDone);
  const opc = async (resp, fin = f) => (await run(wf, P + 'own pod check', { nodes: { [P + 'finish']: fin }, input: resp }))[0].json;
  const podView = (o = {}) => ({ name: 'swarm-pod-test', kind: 'pod', state: 'ready', uptime_s: 1620, cost: 0.4275, actions: { delete: true }, ...o });
  let oc = await opc({ statusCode: 200, body: { vm: podView() } });
  ok(oc.delete === true && cond(wf, P + 'delete the pod?')({}, oc), 'own pod check: the pod this run deployed is deleted');
  oc = await opc({ statusCode: 200, body: { vm: podView({ name: 'swarm-pod-owner' }) } });
  ok(oc.delete === false && /^left alone: swarm-pod-owner is not the pod this run deployed/.test(oc.pod_delete) && !cond(wf, P + 'delete the pod?')({}, oc), 'own pod check: any other pod is left alone');
  oc = await opc({ statusCode: 200, body: { vm: null } });
  ok(oc.delete === false && oc.pod_delete === 'already gone', 'own pod check: no pod = already gone');
  ok((await opc({ statusCode: 200, body: { vm: podView({ state: 'deleting', actions: { delete: false } }) } })).pod_delete === 'already being deleted', 'own pod check: already being deleted');
  ok(/cannot be deleted now/.test(await throws(opc({ statusCode: 200, body: { vm: podView({ state: 'launching', actions: { delete: false } }) } }))) && /could not read the Pods page/.test(await throws(opc({ error: { message: 'x' } }))), 'own pod check: a pod it cannot delete now, or a Pods page it cannot read, stops the run (the error workflow keeps trying)');
  const noName = clone(f); delete noName.live.pod.name;
  ok((await opc({ statusCode: 200, body: { vm: podView() } }, noName)).delete === false, 'own pod check: with no pod name nothing is deleted');
  const dnx = (r, fin = f, chk = { delete: true, uptime_s: 1620, cost: 0.855 }) => run(wf, P + 'done', { nodes: { [P + 'finish']: fin, [P + 'own pod check']: chk, 'Look: ready': Sbase() }, input: r });
  const dn1 = (await dnx({ statusCode: 200, body: { vm: { uptime_s: 1620, cost: 0.855 } } }))[0].json;
  ok(dn1.broll.mode === 'live' && dn1.broll.status === 'ok' && dn1.broll.gpu_minutes === 27 && dn1.broll.gpu_rate_usd_h === 0.4275 && dn1.broll.pod_delete === 'deleted' && dn1.broll.gpu_cost_per_toolkit_usd === 0.855 && dn1.broll.files.length === 12 && dn1.slots.H1.slot1.wide.endsWith('H1A_1344x768.mp4') && dn1.run_id === '77' && dn1.broll.pod.name === 'swarm-pod-test' && !('by_this_run' in dn1.broll.pod), 'done: GPU minutes from the pod\'s uptime, rate from the pod, same state shape as the stand-in');
  ok(dn1.broll.gpu_cost_estimate_usd === 0.1924 && /an estimate: gpu_minutes x gpu_rate_usd_h \/ 60/.test(dn1.broll.gpu_cost_note), 'done: the GPU cost is labelled minutes x hourly rate, an estimate (' + dn1.broll.gpu_cost_estimate_usd + ')');
  const dn3 = (await dnx({ delete: false }, f, { delete: false, pod_delete: 'already gone' }))[0].json;
  ok(dn3.broll.pod_delete === 'already gone' && dn3.broll.gpu_minutes > 24 && dn3.broll.gpu_minutes < 26, 'done: a pod already gone is fine; minutes from its launch time');
  const dn4 = (await dnx({ statusCode: 409, body: { error: 'There is no pod.' } }))[0].json;
  ok(/already gone/.test(dn4.broll.pod_delete), 'done: a delete that finds no pod is fine');
  ok(/could not be deleted \(500: boom\)/.test(await throws(dnx({ statusCode: 500, body: { error: 'boom' } }))) && /could not be deleted \(409: Not now: the pod is setting_up/.test(await throws(dnx({ statusCode: 409, body: { error: 'Not now: the pod is setting_up.' } }))), 'done: a delete that failed stops the run (the error workflow tries again and tells Geoff)');
  const dgv = (await run(wf, P + 'done', { nodes: { [P + 'finish']: gv, [P + 'own pod check']: { delete: true, uptime_s: 2760, cost: 0.4275 }, 'Look: ready': Sbase() }, input: { statusCode: 200, body: { vm: { uptime_s: 2760, cost: 0.4275 } } } }))[0].json;
  ok(dgv.broll.status.startsWith('no pod: ') && dgv.broll.files.length === 0 && dgv.broll.gpu_minutes === 46 && dgv.slots.H1 && Object.keys(dgv.slots.H1).length === 0, 'done: a give-up still records the pod minutes it cost and sends no slots');

  // ---- Package: body takes the GPU numbers
  const PB = 'Package: body';
  const A = { results: [{ version: 'H1_6S_9X16', hook: 'H1', file: 'f', line: 'l', length_s: 6, fmt: '9X16', qa: { passed: true, reasons: [] } }], llm_usd_12b: 0.01, waiting_since: '2026-11-24T01:00:00Z' };
  const pbIn = { approved: ['H1_6S_9X16'], approved_notes: {}, rejected: [], submitted_at: 'x', approver: 'reviewer (form)' };
  const runPB = async (S) => (await run(wf, PB, { nodes: { 'Approval: build': A, 'Run: state for 12b': S, 'Brief form': { submittedAt: '2026-11-24T00:00:00Z' } }, input: pbIn }))[0].json.body;
  const SP = { ...Sbase(), brief: { product: 'Sleep', brand: 'B' }, rulebook: { brand: 'Restfield' }, disclaimer: 'd', llm_usd: 0.02, broll: dn1.broll };
  let pb = await runPB(SP);
  ok(pb.run.gpu_minutes === 27 && pb.run.gpu_rate_usd_h === 0.4275 && pb.upload === true && /GPU costed from the pod rate \(an estimate\)/.test(pb.run.mode), 'package: live b-roll sends the pod minutes and rate');
  pb = await runPB({ ...SP, broll: dgv.broll });
  ok(pb.run.mode.includes('no b-roll (no pod: the pod was not ready after 45 minutes (x))') && pb.run.mode.length <= 200 && pb.run.gpu_minutes === 46, 'package: no pod = report.md\'s Mode line says the run had no b-roll and why');
  pb = await runPB({ ...SP, broll_mode: 'stand-in', broll: { mode: 'fixtures', clips: {}, files: [] } });
  ok(pb.run.gpu_minutes === 0 && pb.run.gpu_rate_usd_h === 0 && pb.run.mode === 'stand-in voice, stand-in b-roll; GPU and voice not costed yet' && pb.upload === false, 'package: stand-in b-roll stays at zero, same text as before');
  pb = await runPB({ ...SP, broll: { ...dn1.broll, gpu_rate_usd_h: 0 } });
  ok(pb.run.gpu_rate_usd_h === 0 && /GPU and voice not costed yet/.test(pb.run.mode), 'package: no rate from the toolkit leaves the zero and says so');

  // ---- error workflow: pod clean-up
  const EN = 'Error: ';
  const live = cond(er, EN + 'failed in the live b-roll?');
  const lv = (n) => live({}, { execution: n === undefined ? undefined : { lastNodeExecuted: n } });
  ok(lv('B-roll live: POST /api/broll/jobs') && !lv('Render: POST /render') && !lv('B-roll: write clip') && !lv(undefined), 'clean-up: only a failure in a "B-roll live: ..." node starts it (read from Error: ids, not from the media folder step)');
  const rec0 = async (text) => (await run(er, EN + 'pod record', { input: text === null ? { error: 'No file(s) found' } : { ok: 1 }, binaries: text === null ? null : [text] }))[0].json;
  const askOwn = cond(er, EN + 'pod deployed by this run?');
  let pr1 = await rec0('{"requested_at":"x","requested_ms":' + (Date.now() - 600000) + ',"name":"swarm-pod-test"}');
  ok(pr1.pod_name === 'swarm-pod-test' && askOwn({}, pr1), 'clean-up: pod.json with a name = a pod this run deployed');
  pr1 = await rec0('{"requested_at":"x","requested_ms":' + (Date.now() - 600000) + '}');
  ok(!pr1.pod_name && pr1.requested_ms > 0 && askOwn({}, pr1), 'clean-up: pod.json with only requested_at (the deploy never answered) still counts');
  for (const bad of [null, '{x', '{}', '{"name": ""}']) { const x = await rec0(bad); ok(/^(no pod to delete|left alone)/.test(x.cleanup) && !askOwn({}, x), 'clean-up: pod.json ' + (bad === null ? 'missing' : bad) + ' -> nothing is deleted: ' + x.cleanup); }
  const addr = async (text) => (await run(er, EN + 'toolkit address', { input: text === null ? {} : { ok: 1 }, binaries: text === null ? null : [text] }))[0].json;
  ok((await addr(CFG)).toolkit_url === 'https://toolkit.example.com' && /not read/.test((await addr(null)).cleanup) && /not valid JSON/.test((await addr('{x')).cleanup) && /no usable toolkit_url/.test((await addr('{"toolkit_url":"ftp://x"}')).cleanup), 'clean-up: address from the config, never throws');
  const TQ = Date.now() - 600000;
  const podQ = async (r, rec, wait = null) => { const nodes = { [EN + 'pod record']: rec }; if (wait) nodes[EN + 'wait to retry'] = wait; return (await run(er, EN + 'is there a pod to delete?', { nodes, input: r }))[0].json; };
  const NAMED = { pod_name: 'swarm-pod-test', requested_ms: TQ }, UNNAMED = { pod_name: '', requested_ms: TQ };
  const vmv = (o = {}) => ({ statusCode: 200, body: { vm: { name: 'swarm-pod-test', kind: 'pod', state: 'ready', launched_at: TQ / 1000 + 20, actions: { delete: true }, ...o } } });
  ok((await podQ({ statusCode: 200, body: { vm: null } }, NAMED)).cleanup === 'no pod on the Pods page, nothing to delete', 'clean-up: no pod, nothing to do');
  let del = await podQ(vmv(), NAMED);
  ok(del.delete === true && del.pod === 'swarm-pod-test' && cond(er, EN + 'delete the pod?')({}, del), 'clean-up: the pod pod.json names is deleted');
  const other = await podQ(vmv({ name: 'swarm-pod-owner', launched_at: TQ / 1000 + 20 }), NAMED);
  ok(other.delete === false && !other.retry && /left alone: swarm-pod-owner was not deployed by this run/.test(other.cleanup), 'clean-up: a pod with another name is left alone, even one launched after the request');
  del = await podQ(vmv({ name: 'swarm-pod-any' }), UNNAMED);
  ok(del.delete === true, 'clean-up: no name recorded, a pod launched after the request is ours');
  ok((await podQ(vmv({ launched_at: TQ / 1000 - 3600 }), UNNAMED)).delete === false && /not deployed by this run/.test((await podQ(vmv({ launched_at: TQ / 1000 - 3600 }), UNNAMED)).cleanup), 'clean-up: no name recorded, a pod launched before the request is left alone');
  ok(/left alone: swarm-build-x/.test((await podQ(vmv({ name: 'swarm-build-x', kind: 'build', launched_at: TQ / 1000 + 5 }), UNNAMED)).cleanup), 'clean-up: a build host is never ours');
  ok(/already being deleted/.test((await podQ(vmv({ state: 'deleting', actions: { delete: false } }), NAMED)).cleanup), 'clean-up: already being deleted');
  const lau = await podQ(vmv({ state: 'launching', actions: { delete: false } }), NAMED);
  ok(lau.delete === false && lau.retry === true && lau.attempt === 1 && cond(er, EN + 'try again?')({}, lau), 'clean-up: a pod still launching cannot be deleted: try again in 30 s');
  const lau2 = await podQ(vmv({ state: 'launching', actions: { delete: false } }), NAMED, { ...lau });
  ok(lau2.attempt === 2, 'clean-up: the attempt count rides through the wait');
  const lau20 = await podQ(vmv({ state: 'launching', actions: { delete: false } }), NAMED, { attempt: 20 });
  ok(lau20.retry === false && /^left for the idle timer: pod swarm-pod-test was still launching after 10 minutes/.test(lau20.cleanup), 'clean-up: 20 tries (10 minutes) then "left for the idle timer"');
  ok((await podQ({ statusCode: 200, body: { vm: null } }, NAMED, { attempt: 3 })).cleanup === 'pod gone', 'clean-up: the pod went away while waiting');
  ok(/could not read the Pods page \(401\)/.test((await podQ({ statusCode: 401, body: {} }, NAMED)).cleanup) && /no answer/.test((await podQ({ error: { message: 'x' } }, NAMED)).cleanup), 'clean-up: every other case is a line, not an exception');
  const rec = async (input, pod) => (await run(er, EN + 'record the pod clean-up', { nodes: { [EN + 'is there a pod to delete?']: { pod }, }, input }))[0].json.pod_cleanup;
  ok((await rec({ statusCode: 200, body: { vm: {} } }, 'swarm-pod-x')) === 'deleted pod swarm-pod-x' && /failed: the delete answered 409 \(Not now/.test(await rec({ statusCode: 409, body: { error: 'Not now: the pod is deleting.' } }, 'p')) && (await rec({ cleanup: 'no pod to delete' })) === 'no pod to delete', 'clean-up: the outcome line');
  const E = { run_id: '77', node: 'B-roll live: POST /api/broll/jobs', message: 'the toolkit refused the b-roll job (503): B-roll workflow: workflow_api.json is not in x' };
  const cfgT = '{"telegram_chat_id": "12345678"}';
  const ids = { run_id: '77', execution: { id: 77, lastNodeExecuted: 'B-roll live: POST /api/broll/jobs', error: { message: E.message } }, workflow: { name: 'ad-versions' } };
  const be = async (nodes) => (await run(er, EN + 'build error.json', { nodes: { [EN + 'ids']: ids, ...nodes } }))[0];
  const b1 = await be({ [EN + 'record the pod clean-up']: { pod_cleanup: 'deleted pod swarm-pod-x' } });
  ok(JSON.parse(b1.binary.data.text).pod_cleanup === 'deleted pod swarm-pod-x' && b1.json.node === 'B-roll live: POST /api/broll/jobs', 'error.json: the first write already has the clean-up outcome (no later rewrite needed)');
  ok(!('pod_cleanup' in (await be({})).json), 'error.json: no outcome when the clean-up did not run');
  const et = (await run(er, EN + 'Telegram text', { nodes: { [EN + 'build error.json']: { ...E, pod_cleanup: 'deleted pod swarm-pod-x' } }, binaries: [cfgT] }))[0].json;
  ok(/\nPod: deleted pod swarm-pod-x\n/.test(et.text) && et.text.includes('503'), 'error text: the clean-up outcome is a line of the message');
  const et0 = (await run(er, EN + 'Telegram text', { nodes: { [EN + 'build error.json']: E }, binaries: [cfgT] }))[0].json;
  ok(!/Pod:/.test(et0.text), 'error text: no clean-up, no line');
  const rt = JSON.parse((await run(er, EN + 'record the Telegram result', { nodes: { [EN + 'build error.json']: { ...E, pod_cleanup: 'deleted pod swarm-pod-x' }, [EN + 'Telegram text']: et }, input: { ok: true } }))[0].binary.data.text);
  ok(rt.telegram === 'sent' && rt.pod_cleanup === 'deleted pod swarm-pod-x' && rt.run_id === '77', 'error.json (second write): keeps pod_cleanup');
  console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})();
