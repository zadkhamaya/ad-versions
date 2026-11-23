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
  ok(L.pod_deadline_ms - L.started_ms === 30 * 60000, 'plan: 30-minute pod limit');
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
  const dec = async (resp, { prev = null, late = false } = {}) => {
    const l = clone(L); if (late) l.pod_deadline_ms = now - 1;
    const nodes = { [P + 'plan']: { live: l } }; if (prev) nodes[P + 'wait for the pod'] = prev;
    return (await run(wf, P + 'pod decision', { nodes, input: resp }))[0].json;
  };
  const okr = (body) => ({ statusCode: 200, body: { configured: true, missing: [], ...body } });
  let d = await dec(okr({ vm: null }));
  ok(d.next === 'deploy' && !d.deploy_sent, 'decision: no pod -> deploy');
  ok(/gone from the Pods page/.test(await throws(dec(okr({ vm: null }), { prev: { deploy_sent: true } }))), 'decision: after a deploy, a vanished pod is an error (no redeploy loop)');
  for (const st of ['launching', 'booting', 'setting_up', 'starting', 'deleting']) { d = await dec(okr({ vm: VM({ state: st }) })); ok(d.next === 'wait', 'decision: ' + st + ' -> wait'); }
  d = await dec(okr({ vm: VM({ state: 'launching' }) }), { prev: { deploy_sent: true } });
  ok(d.next === 'wait' && d.deploy_sent === true, 'decision: the deploy flag rides along through the waits');
  d = await dec(okr({ vm: VM() }));
  ok(d.next === 'ready' && d.live.pod.name === 'swarm-pod-test' && d.live.pod.rate_usd_h === 0.43 && d.live.pod.by_this_run === false, 'decision: a ready pod with its bundles is reused (not by this run): ' + JSON.stringify(d.live.pod));
  d = await dec(okr({ vm: VM({ final_price_hr: 0.39 }) }), { prev: { deploy_sent: true } });
  ok(d.next === 'ready' && d.live.pod.rate_usd_h === 0.39 && d.live.pod.by_this_run === true, 'decision: the final price wins; a pod deployed here is marked');
  d = await dec(okr({ vm: VM({ launched_at: Math.floor(now / 1000) + 5 }) }));
  ok(d.live.pod.by_this_run === true, 'decision: launched after this run began = ours');
  ok((await dec(okr({ vm: VM({ add_queue: [{ kind: 'bundles', names: ['h3-t2v'] }], bundles_started: [] }) }))).next === 'wait', 'decision: ready but bundles queued -> wait');
  ok((await dec(okr({ vm: VM({ snapshot: { picker: { download: 'running' } } }) }))).next === 'wait', 'decision: download running -> wait');
  ok((await dec(okr({ vm: VM({ snapshot: { picker: { download: 'idle' } } }) }))).next === 'wait', 'decision: a started bundle with no "done" yet -> wait');
  ok(/bundle download failed/.test(await throws(dec(okr({ vm: VM({ snapshot: { picker: { download: 'failed: disk full' } } }) })))), 'decision: a failed download stops the run');
  ok(/h3-t2v/.test(await throws(dec(okr({ vm: VM({ bundles: ['h3-swap'], bundles_started: [] }) })))), 'decision: a pod without h3-t2v stops the run');
  ok(/build host/.test(await throws(dec(okr({ vm: VM({ kind: 'build' }) })))), 'decision: a build host is never used');
  ok(/failed its setup/.test(await throws(dec(okr({ vm: VM({ state: 'setup_failed', step: 'pull', setup_error: 'no disk' }) })))), 'decision: setup_failed stops the run with the reason');
  ok(/not configured/.test(await throws(dec({ statusCode: 200, body: { configured: false, missing: ['MASSED_API_KEY'], vm: null } }))), 'decision: unconfigured toolkit');
  ok(/refused the machine token/.test(await throws(dec({ statusCode: 401, body: { error: 'Sign in again.' } }))), 'decision: 401');
  ok((await dec({ error: { message: 'ECONNRESET' } })).next === 'wait' && (await dec({ statusCode: 502, body: {} })).next === 'wait', 'decision: a hiccup waits and asks again');
  ok(/not ready after 30 minutes \(the pod swarm-pod-test is booting/.test(await throws(dec(okr({ vm: VM({ state: 'booting' }) }), { late: true }))), 'decision: 30 minutes is the limit');
  ok(cond(wf, P + 'pod ready?')({}, { next: 'ready' }) && !cond(wf, P + 'pod ready?')({}, { next: 'wait' }) && cond(wf, P + 'deploy needed?')({}, { next: 'deploy' }) && !cond(wf, P + 'deploy needed?')({}, { next: 'wait' }), 'decision: the two IFs read next');

  // ---- catalog and deploy
  const decOut = { live: L, next: 'deploy', deploy_sent: false };
  const cat = (r) => run(wf, P + 'catalog check', { nodes: { [P + 'pod decision']: decOut }, input: r });
  ok((await cat({ statusCode: 200, body: { bundles: [{ name: 'h3-swap' }, { name: 'h3-t2v' }] } }))[0].json.next === 'deploy', 'catalog: h3-t2v listed -> carry on');
  ok(/does not list the h3-t2v bundle \(1 bundles listed; lists error: old/.test(await throws(cat({ statusCode: 200, body: { bundles: [{ name: 'h3-swap' }], lists: { error: 'old' } } }))), 'catalog: not listed -> stop');
  ok(/refresh failed \(500\)/.test(await throws(cat({ statusCode: 500, body: { error: 'x' } }))) && /refresh failed/.test(await throws(cat({ statusCode: 200, body: { bundles: null } }))) === false, 'catalog: errors stop (a null bundle list is "not listed")');
  const dep = (r) => run(wf, P + 'deploy result', { nodes: { [P + 'pod decision']: decOut }, input: r });
  const dd = (await dep({ statusCode: 202, body: { vm: { name: 'swarm-pod-x' } } }))[0].json;
  ok(dd.deploy_sent === true && dd.next === 'wait' && dd.live.clips.length === 12, 'deploy: 202 -> wait, flag set');
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

  // ---- finish and done
  const allDone = clone(L); allDone.clips.forEach((c, i) => { c.status = 'done'; c.file = 'runs/77/broll/' + c.clip + '_' + c.size + '.mp4'; c.wall_s = 20 + i; c.tries = 1; });
  allDone.pod = { name: 'swarm-pod-test', product: 'gpu_1x_a6000_high_ram', rate_usd_h: 0.4275, launched_at: Math.floor((Date.now() - 25 * 60000) / 1000), by_this_run: true };
  const fn = async (live, S = Sbase()) => (await run(wf, P + 'finish', { nodes: { 'Look: ready': S }, input: { live } }))[0].json;
  let f = await fn(allDone);
  ok(f.result.files.length === 12 && Object.keys(f.result.slots.H1).join() === 'slot1,slot2,slot3' && f.result.slots.H1.slot2.wide === 'runs/77/broll/H1B_1344x768.mp4' && f.result.slots.H2.slot3.tall === 'runs/77/broll/H2C_768x1344.mp4' && f.result.notes.length === 0 && f.result.clips.H1A.wide_wall_s === 20, 'finish: 12 files, A->slot1 B->slot2 C->slot3, per-clip times');
  const part = clone(allDone); part.clips.find((c) => c.clip === 'H1B' && c.size === '768x1344').status = 'dropped'; part.clips.find((c) => c.clip === 'H1B' && c.size === '768x1344').reason = 'clip H1B 768x1344: oom'; delete part.clips.find((c) => c.clip === 'H1B' && c.size === '768x1344').file;
  f = await fn(part);
  ok(!('slot2' in f.result.slots.H1) && 'slot3' in f.result.slots.H1 && f.result.notes.length === 1 && /H1B: no cutaway \(clip H1B 768x1344: oom\)/.test(f.result.notes[0]) && f.result.files.length === 11, 'finish: one size missing -> the slot is left out and the note says why');
  const none = clone(allDone); none.clips.forEach((c) => { c.status = 'dropped'; c.reason = 'x'; });
  ok(/no b-roll clip could be made/.test(await throws(fn(none))), 'finish: no clip at all fails the run');
  const S4 = Sbase(); S4.hooks[1].broll = [null, null, null]; const l4 = clone(allDone); l4.clips = l4.clips.filter((c) => c.clip.startsWith('H1'));
  f = await fn(l4, S4);
  ok(Object.keys(f.result.slots.H2).length === 0 && Object.keys(f.result.slots.H1).length === 3 && f.result.notes.length === 0, 'finish: a hook with no shots has no slots and no note');
  const dn = (r, live = allDone) => run(wf, P + 'done', { nodes: { [P + 'finish']: { live, result: f.result }, 'Look: ready': Sbase() }, input: r });
  f = await fn(allDone);
  const dn1 = (await run(wf, P + 'done', { nodes: { [P + 'finish']: f, 'Look: ready': Sbase() }, input: { statusCode: 200, body: { vm: { uptime_s: 1620, cost: 0.855 } } } }))[0].json;
  ok(dn1.broll.mode === 'live' && dn1.broll.gpu_minutes === 27 && dn1.broll.gpu_rate_usd_h === 0.4275 && dn1.broll.pod_delete === 'deleted' && dn1.broll.gpu_cost_per_toolkit_usd === 0.855 && dn1.broll.files.length === 12 && dn1.slots.H1.slot1.wide.endsWith('H1A_1344x768.mp4') && dn1.run_id === '77' && dn1.broll.pod.name === 'swarm-pod-test', 'done: GPU minutes from the pod\'s uptime, rate from the pod, same state shape as the stand-in');
  const reused = clone(allDone); reused.pod.by_this_run = false; reused.started_ms = Date.now() - 6 * 60000;
  const dn2 = (await run(wf, P + 'done', { nodes: { [P + 'finish']: { live: reused, result: f.result }, 'Look: ready': Sbase() }, input: { statusCode: 200, body: { vm: { uptime_s: 99999 } } } }))[0].json;
  ok(dn2.broll.gpu_minutes >= 5.9 && dn2.broll.gpu_minutes <= 6.2, 'done: a reused pod counts only this run\'s time: ' + dn2.broll.gpu_minutes);
  const dn3 = (await run(wf, P + 'done', { nodes: { [P + 'finish']: f, 'Look: ready': Sbase() }, input: { statusCode: 409, body: { error: 'There is no pod.' } } }))[0].json;
  ok(/already gone/.test(dn3.broll.pod_delete), 'done: a pod already gone is fine');
  ok(/could not be deleted \(500: boom\)/.test(await throws(run(wf, P + 'done', { nodes: { [P + 'finish']: f, 'Look: ready': Sbase() }, input: { statusCode: 500, body: { error: 'boom' } } }))) && /could not be deleted \(409: Not now: the pod is setting_up/.test(await throws(run(wf, P + 'done', { nodes: { [P + 'finish']: f, 'Look: ready': Sbase() }, input: { statusCode: 409, body: { error: 'Not now: the pod is setting_up.' } } }))), 'done: a delete that failed stops the run (the error workflow tries again and tells Geoff)');

  // ---- Package: body takes the GPU numbers
  const PB = 'Package: body';
  const A = { results: [{ version: 'H1_6S_9X16', hook: 'H1', file: 'f', line: 'l', length_s: 6, fmt: '9X16', qa: { passed: true, reasons: [] } }], llm_usd_12b: 0.01, waiting_since: '2026-11-24T01:00:00Z' };
  const pbIn = { approved: ['H1_6S_9X16'], approved_notes: {}, rejected: [], submitted_at: 'x', approver: 'reviewer (form)' };
  const runPB = async (S) => (await run(wf, PB, { nodes: { 'Approval: build': A, 'Run: state for 12b': S, 'Brief form': { submittedAt: '2026-11-24T00:00:00Z' } }, input: pbIn }))[0].json.body;
  const SP = { ...Sbase(), brief: { product: 'Sleep', brand: 'B' }, rulebook: { brand: 'Restfield' }, disclaimer: 'd', llm_usd: 0.02, broll: dn1.broll };
  let pb = await runPB(SP);
  ok(pb.run.gpu_minutes === 27 && pb.run.gpu_rate_usd_h === 0.4275 && pb.upload === true && /GPU costed from the pod rate/.test(pb.run.mode), 'package: live b-roll sends the pod minutes and rate');
  pb = await runPB({ ...SP, broll_mode: 'stand-in', broll: { mode: 'fixtures', clips: {}, files: [] } });
  ok(pb.run.gpu_minutes === 0 && pb.run.gpu_rate_usd_h === 0 && pb.run.mode === 'stand-in voice, stand-in b-roll; GPU and voice not costed yet' && pb.upload === false, 'package: stand-in b-roll stays at zero, same text as before');
  pb = await runPB({ ...SP, broll: { ...dn1.broll, gpu_rate_usd_h: 0 } });
  ok(pb.run.gpu_rate_usd_h === 0 && /GPU and voice not costed yet/.test(pb.run.mode), 'package: no rate from the toolkit leaves the zero and says so');

  // ---- error workflow: pod clean-up
  const EN = 'Error: ';
  const live = cond(er, EN + 'failed in the live b-roll?');
  const nodeOf = (n) => ({ [EN + 'build error.json']: { node: n } });
  ok(live((x) => ({ first: () => ({ json: nodeOf('B-roll live: POST /api/broll/jobs')[EN + 'build error.json'] }) }), {}) && !live((x) => ({ first: () => ({ json: { node: 'Render: POST /render' } }) }), {}) && !live((x) => ({ first: () => ({ json: { node: 'B-roll: write clip' } }) }), {}), 'clean-up: only a failure in a "B-roll live: ..." node starts it');
  const addr = async (text) => (await run(er, EN + 'toolkit address', { input: text === null ? {} : { ok: 1 }, binaries: text === null ? null : [text] }))[0].json;
  ok((await addr(CFG)).toolkit_url === 'https://toolkit.example.com' && /not read/.test((await addr(null)).cleanup) && /not valid JSON/.test((await addr('{x')).cleanup) && /no usable toolkit_url/.test((await addr('{"toolkit_url":"ftp://x"}')).cleanup), 'clean-up: address from the config, never throws');
  const podQ = async (r) => (await run(er, EN + 'is there a pod to delete?', { input: r }))[0].json;
  ok((await podQ({ statusCode: 200, body: { vm: null } })).cleanup === 'no pod to delete' && (await podQ({ statusCode: 200, body: { vm: null } })).delete === false, 'clean-up: no pod, nothing to do');
  const del = await podQ({ statusCode: 200, body: { vm: { name: 'swarm-pod-x', kind: 'pod', state: 'booting', actions: { delete: true } } } });
  ok(del.delete === true && del.pod === 'swarm-pod-x' && cond(er, EN + 'delete the pod?')({}, del), 'clean-up: a deletable pod is deleted');
  ok(/left alone: swarm-build-x is a build host/.test((await podQ({ statusCode: 200, body: { vm: { name: 'swarm-build-x', kind: 'build', state: 'ready', actions: { delete: true } } } })).cleanup), 'clean-up: a build host is left alone');
  ok(/already being deleted/.test((await podQ({ statusCode: 200, body: { vm: { name: 'p', kind: 'pod', state: 'deleting', actions: { delete: false } } } })).cleanup) && /cannot be deleted now/.test((await podQ({ statusCode: 200, body: { vm: { name: 'p', kind: 'pod', state: 'launching', actions: { delete: false } } } })).cleanup) && /could not read the Pods page \(401\)/.test((await podQ({ statusCode: 401, body: {} })).cleanup) && /no answer/.test((await podQ({ error: { message: 'x' } })).cleanup), 'clean-up: every other case is a line, not an exception');
  const rec = async (input, pod) => (await run(er, EN + 'record the pod clean-up', { nodes: { [EN + 'is there a pod to delete?']: { pod } }, input }))[0].json.pod_cleanup;
  ok((await rec({ statusCode: 200, body: { vm: {} } }, 'swarm-pod-x')) === 'deleted pod swarm-pod-x' && /failed: the delete answered 409 \(Not now/.test(await rec({ statusCode: 409, body: { error: 'Not now: the pod is deleting.' } }, 'p')) && (await rec({ cleanup: 'no pod to delete' })) === 'no pod to delete', 'clean-up: the outcome line');
  const E = { run_id: '77', node: 'B-roll live: POST /api/broll/jobs', message: 'the toolkit refused the b-roll job (503): B-roll workflow: workflow_api.json is not in x' };
  const cfgT = '{"telegram_chat_id": "12345678"}';
  const et = (await run(er, EN + 'Telegram text', { nodes: { [EN + 'build error.json']: E, [EN + 'record the pod clean-up']: { pod_cleanup: 'deleted pod swarm-pod-x' } }, binaries: [cfgT] }))[0].json;
  ok(/\nPod: deleted pod swarm-pod-x\n/.test(et.text) && et.text.includes('503'), 'error text: the clean-up outcome is a line of the message');
  const et0 = (await run(er, EN + 'Telegram text', { nodes: { [EN + 'build error.json']: E }, binaries: [cfgT] }))[0].json;
  ok(!/Pod:/.test(et0.text), 'error text: no clean-up, no line');
  const rt = JSON.parse((await run(er, EN + 'record the Telegram result', { nodes: { [EN + 'build error.json']: E, [EN + 'Telegram text']: et, [EN + 'record the pod clean-up']: { pod_cleanup: 'deleted pod swarm-pod-x' } }, input: { ok: true } }))[0].binary.data.text);
  ok(rt.telegram === 'sent' && rt.pod_cleanup === 'deleted pod swarm-pod-x' && rt.run_id === '77', 'error.json: carries pod_cleanup');
  const rt0 = JSON.parse((await run(er, EN + 'record the Telegram result', { nodes: { [EN + 'build error.json']: E, [EN + 'Telegram text']: et0 }, input: { ok: true } }))[0].binary.data.text);
  ok(!('pod_cleanup' in rt0), 'error.json: no pod_cleanup when it did not run');
  console.log(fails ? 'FAILED ' + fails : 'ALL PASS');
  process.exit(fails ? 1 : 0);
})();
