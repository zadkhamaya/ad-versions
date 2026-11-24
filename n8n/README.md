# The n8n workflow (brief to package, dry run)

Two exported workflows, credentials stripped (names kept):

| File | Workflow | What it does |
|---|---|---|
| `workflow.json` | **ad-versions** | brief form → source clip → beats → scripts → compliance → look line → b-roll (dry run) → render the 18 versions → QA → contact sheet → approval on Telegram (or the form) → package → done message. |
| `errors.json` | **ad-versions errors** | the error workflow of the one above: on any failure it writes `runs/<id>/error.json` (node, message, time), then sends a Telegram message (run id, failing node, message cut to 300 characters) and writes `error.json` again with `telegram` (`sent`, `failed: <reason>` or `skipped: no chat id`), then stops; a failure in the trigger itself has no execution id, so its folder is `trigger-<timestamp>`. A failure in the live b-roll branch also deletes the GPU pod this run deployed, in a branch of its own that runs first (see "Live b-roll"). |

Built and tested on n8n **2.46.6** (the tag pinned in `docker/.env`).

## What each part does

Every node is named for its job; the run is the execution id (`run_id`), and everything lands in `/data/runs/<run_id>/`.

1. **Brief form** (Form Trigger, Basic Auth). Fields, in order: Brand, Product, Audience, Offer, Key message 1/2/3,
   Required disclaimer (pre-filled with the rulebook's), Source clip (`.mp4`/`.mov`), Source clip link (https, for a
   file over 90 MB; used instead of the upload when both are given), Shot list, Look (optional), Landing URL
   (optional, https; goes to `Link` in manifest.csv), Call to action (default `Learn More`, our guess at Meta's button text),
   **Voice** and **B-roll** (stand-in or real, below).
2. **Run: ids and brief** → **Run: folder** (`POST /run`, because n8n's file nodes write only into an existing folder) →
   **Rulebook: get** → **Disclaimer: must match the rulebook**: the form's disclaimer must equal the rulebook's (case,
   spacing and punctuation ignored), else the run stops at once with "the brief's disclaimer differs from the approved one
   in the rulebook"; the rulebook's text is the one that goes on the end card (`disclaimer` in the run state) →
   **Source: link given?** → download (https only, redirects are not followed, so the check covers the whole fetch;
   **Source: size cap (400 MB)** refuses a larger file, one constant in that node; uploads are limited to 90 MB, links to 400 MB) or the upload → **Source: write source.mp4**.
3. **Beats: parse** (Code) turns the form's Shot list into the service's `beats` (syntax below; the old `S1 0.5-6.0 opener-H1` lines are still read); **Beats: validate** is
   `POST /reframe` on the real clip: it refuses beats outside the clip, suggests windows for `auto` beats and draws the
   sheet. **Reframe: keep** stores the suggestions, the sheet path (`runs/<id>/reframe.jpg`) and the review/split flags.
4. **Scripts**: one LLM call with structured output (`json_schema`, strict): 2 hooks, a voice-over line
   for 6, 15 and 30 s, 3 b-roll shots per hook, each shot an enum of the rulebook's `shots` → **Scripts: validate**
   (shape, shots in the rulebook, word budgets noted; the prompt asks for 10 to 11, 32 to 37 and 70 to 80 words, the measured
   rate of the VoxCPM2 voice, docs/measured.md; a hook that picks the same shot twice gets the duplicate replaced
   by an unused rulebook shot, noted in `script_notes`).
5. **Compliance**: `POST /lint` on every line and shot (the hard word list; for a line, any bracket character is a hit too, `brackets`,
   because the voice skips or speaks what is inside them) → a second LLM call judges each **line**
   against `banned_media`, the approved claims and the tone (no testimonial lines, no outcomes) and returns
   `{"<id>": {"ok", "rewrite"}}`. B-roll shots are pre-approved in the rulebook, so only the word list checks them →
   **apply 1** rewrites a failing line once (a shot with a word-list hit is dropped, not rewritten) →
   `POST /lint` again on the rewrites → **apply 2** drops a rewrite that still hits, drops a hook left without lines,
   and writes `compliance.notes` (each rewrite before → after, each drop and why). Line ids are `H1:6`, `H1:15`,
   `H1:30`; shot ids `H1:A`, `H1:B`, `H1:C`.
6. **Look line** (spec 4.3 part 1): the Look field wins when filled and clean; it goes through `/lint` too, and on a hit
   it is ignored (kept in the run data as `look_brief_rejected`) and the vision path runs. Otherwise `POST /frames` at the middles of S1, S3
   and S5 → the files are read and sent to the vision model with the spec's prompt → `POST /lint` on the line → one
   retry naming the hit words → the rulebook's `look_fallback`. The line (and where it came from: `brief`, `vision`
   or `fallback`) is kept in the run data.
7. **B-roll**, stand-in branch: copies the six stand-in clips (`A|B|C` × `1344x768|768x1344`) from
   `/data/brand/fixtures/broll/` to `runs/<id>/broll/<hook><A|B|C>_<size>.mp4`: 12 files for two hooks, fewer when the
   compliance pass dropped a shot. The **live** branch (toolkit pods and `/api/broll`) is described under "Live b-roll" below.
8. **Run data**: `runs/<id>/run.json` holds the whole run so far (brief, beats, reframe, rulebook, scripts, compliance,
   look, b-roll, LLM cost). **Run: state for 12b** hands that same object (no binary) to the second half (below);
   **Run: summary** is a short side view for reading an execution.

### Second half: render, QA, approval, package

9. **Render: plan** lists the versions in `naming.matrix()` order (H1/H2 × 6/15/30 × 9X16/1X1/16X9). A version whose
   hook or length line the compliance pass dropped is **not made**, with the reason. **Render: loop** feeds one version at
   a time through the steps below (an HTTP Request node with several items fires them all at once, and the media service
   renders one at a time, so the loop is what keeps each request inside its timeout).
10. **Render: POST /render** (timeout 20 min): `source`, `beats`, the hook's `slots` as `broll` (left out for 6 s, which has
    no cutaway), `vo_text` = the line, the rulebook's disclaimer and brand, `music` = `brand/music.m4a`, `logo` = `brand/logo.png`, and
    `voice` = `"stub"` or `"runpod"` from the form's Voice choice. An error answer (4xx, 5xx, a timeout) makes that version
    **failed** with the service's message; the loop goes on. A request that times out leaves the service still rendering that
    version, and the next request queues behind it: the 20 minutes and the loop's batch of 1, as built, are the safe pair
    (do not raise the batch).
11. **QA** per rendered version: `POST /check`; `POST /transcribe` with the line as `script` (WER, banned words)
    and the brief's brand name as `hotwords` (never the script: the check must stay independent). With a hotword, the service
    counts a split name as one word ("Rest Field" heard for Restfield), so a made-up brand does not fail the word check;
    `POST /frames` `at: "last"` → the vision model reads the end card's disclaimer back → compared with the
    rulebook's text ignoring case, spacing and punctuation. **QA: merge** builds `{passed, reasons}` as docs/api.md
    "How `qa` is merged": the render log's reasons, `/check`, `/transcribe`, and the disclaimer read. A QA call that fails
    to run counts as a reason (a version that could not be checked does not pass), and a failed check with no reason of
    its own gets one ("... failed with no reason given").
12. **Sheet** (`POST /sheet`, all rendered files in matrix order → `runs/<id>/sheet.jpg`, a green PASS or red FAIL on every
    cell) and **Approval: build**: the summary line (`18 versions: 17 passed, 1 failed (H2_6S_1X1: loudness ...)`), the sheet
    (shown in the form through the sheet link below) and a table of every version with its QA.
13. **Approval** has two ways in that end in the same place (see "Approval on Telegram" below). **Approval: form** is a Wait node
    resumed by a form (the form-waiting path is public by design, its URL carries a signature). One checkbox per version, the ones
    that passed pre-ticked, and a notes box (one note per line, `H2_6S_1X1: voice clipped`; other lines are a general note, which
    goes to report.md). **A version that failed QA can be approved only with a note** (`VERSION: why`): ticked without one it is
    rejected with "override needs a note". An approved version's note goes to `/package` and report.md ("approved over QA:
    reasons — note"). Wait limit 7 days; when it runs out the form returns no submission and the run is recorded as `expired`,
    nothing packaged. The form's URL is in `run.json` → `approval.form_url` (written before the wait), and a reviewer, or a
    script, posts it as multipart: `field-0` repeated per ticked version, `field-1` the notes. A tick on a version that was never
    rendered is ignored. The form lists all 18 versions as static options because n8n validates the Wait node's field list when
    the workflow is published, so it cannot be built per run; the table above it says which were not made.
14. **Package: body** → **POST /package**: approved items (`file`, `version`, `hook_text` = the hook's 6 s line,
    `script`, `length_s`, `fmt`, `synthetic_media`, `qa`, approver `reviewer`, approval time = the form's submission time),
    rejected items with the reviewer's note and why (QA reasons, not made), and `run` = brand (the rulebook's),
    campaign (the brief's product), `started_at` (the brief's submission), `finished_at` (now), GPU 0, voice 0 and
    `llm_usd` = the first half's plus this half's vision calls, `mode` (what was stand-in), `review_ready_at` (when the form
    was published: report.md gives "machine time to review-ready" and the total, in Asia/Jakarta time), the landing URL and
    CTA (`link`, `cta`) and the reviewer's general note. `upload` is false with stand-in b-roll: the media service has no R2
    environment yet; live b-roll sends `true`, and `/package` then fails clearly if R2 is not configured. Nothing approved
    or an expired form = no package (a normal end). The answer (folder, files, report numbers) is written to `run.json` →
    `package`; a **failed** package (a 4xx or 5xx from `/package`) ends the run in error after run.json is written, so the
    error workflow fires; otherwise **Run: done**.

### The contact-sheet link

The sheet is shown in the approval form through a second trigger in the same workflow:
`GET /webhook/ad-versions-sheet?run=<id>&token=<32 hex>`. The `/webhook` path is public through the tunnel by design; the token is
the protection: 128 random bits made in **Render: plan** (`crypto.randomBytes`, so the container needs
`NODE_FUNCTION_ALLOW_BUILTIN=crypto`) and stored in `run.json` (`sheet_token`, `sheet_url`). A wrong or missing token, a run
that does not exist or a malformed id answers 404, and the only file ever served is `runs/<id>/sheet.jpg`.

### Approval on Telegram

Telegram is the first contact for the approval; the form stays behind it. After the sheet and the run.json write:

1. **Telegram: read config** reads `data/config/telegram.json` (inside the containers `/data/config/telegram.json`), the shape
   `{"telegram_chat_id": "<digits>"}`. The chat id is in that file only: not in the repo, not in an environment variable (the
   container keeps n8n's environment access in nodes blocked, so `$env` cannot read it), not in the credential. The same file
   serves the error workflow. To start, open the bot in Telegram, send it any message, and write your chat id into the file.
2. **Telegram: sheet photo**: `runs/<id>/sheet.jpg` (read by **Telegram: read sheet**, sent as binary) with the summary line as the
   caption: `18 versions: 16 passed, 2 failed (H1_30S_9X16: voice-over differs ...; ...)`, failures listed while they fit in
   800 characters, then `+N more`.
3. **Telegram: approve or review** (regular output, `alwaysOutputData`: a send failure comes out as `{error}` and goes to **Telegram: failed**; a timeout
   comes out as one empty item = no answer = expired), a Send and Wait with two link buttons (a button opens the signed `webhook-waiting` URL in the phone's
   browser, which is public through the access layer by design, like the form's):
   - **Approve passed**: every passed version is approved, every other one rejected with the note `not approved (failed QA: <reasons>)` (or
     `not made: <reason>`), general note `approved on Telegram`, approver `reviewer (Telegram)`. No form.
   - **Review**: **Telegram: review link** sends a message with the link of the approval form and the run continues into the form
     unchanged (ticks, override notes), approver `reviewer (form)`.
   - No version passed QA: Approve passed approves nothing (general note `nothing approved (no version passed QA)`), the normal nothing-packaged path.
   - No tap in 7 days: the same `expired` path as the form, nothing packaged; `run.json` → `approval.channel` says which channel (Telegram or form) ran out.
4. Both answers leave in the same shape (`approved`, `rejected`, `approved_notes`, `general_note`, `submitted_at`, plus `approver`) and join
   at **Package: body**; `run.json` → `approval.via` says which.

**If Telegram fails**, the run does not: any of those steps (the config missing or without digits, a wrong credential, no network, a chat
that never started the bot, a missing sheet) ends in **Telegram: failed**, which records the message (token scrubbed, 300
characters) as `telegram_error` in `run.json` straight away and goes on to the form as before. The final run.json keeps `telegram_error`.

**The done message**: after `/package` builds, **Telegram: done text** / **done message** send the run id, approved and rejected counts and
who approved, cost per asset, machine time to review-ready and total turnaround, the package folder (and the R2 link on a live run). Nothing
approved or an expired approval sends one line saying so (and which channel expired). A message that cannot be sent is logged in run.json as
`telegram_done`: `sent`, `failed: <reason>`, `skipped: no chat id` (the config could not be read, so no call is made) or `skipped: <reason>` (the text
could not be built); it never fails the run.
Nothing is sent when the package failed (the error workflow tells you).

**The failure message** (error workflow): after `error.json` is written, run id, failing node and the message (300 characters, bot-token patterns
scrubbed). Its Telegram steps continue on error and the workflow has no error workflow of its own, so a failed send ends there; with no
readable chat id no call is made. The outcome is written into `error.json` (rewritten after the send) as `telegram`: `sent`,
`failed: <reason>` or `skipped: no chat id`.

Telegram texts are sent in HTML mode with `&`, `<`, `>` escaped; the Send and Wait text is plain on purpose (n8n sends that one as Markdown).

### The dry-run voice and the music placeholder

`/render` takes `voice: "stub"`: ffmpeg's `flite` voice reads the script (no RunPod, no R2). Whisper transcribes it
well but not perfectly, so a short line with an unusual word can miss the 0.15 WER gate: that is the QA doing its job on
a stand-in voice, not a workflow fault.

There is no music bed yet. `n8n/fixtures/make-music.sh <out.m4a>` makes a quiet 35 s placeholder pad; put it at
`brand/music.m4a` under the data root (on the box `~/.ad-versions/data/brand/music.m4a`) until the real bed exists.

**The brand is Restfield** (made up; `brand/rulebook.yaml`). Its wordmark, `brand/logo.png` (Lora, off-white on transparent, 1600 px wide,
made by `brand/make-logo.sh` from the OFL font in `brand/fonts/`), goes to `brand/logo.png` under the data root too (on the box
`~/.ad-versions/data/brand/logo.png`): `/render` is sent `logo: "brand/logo.png"`. The logo is optional for the service (a request with no `logo` gets the brand name drawn as text),
but the workflow always sends the path, and a path that does not exist is a 404 for that version: copy the file before a real run.

### What the run state holds (what 12b receives)

`run_id`, `dry_run` (stand-in b-roll), `voice_mode`, `broll_mode`, `source` (`runs/<id>/source.mp4`), `beats` (the service's shape), `reframe` (`beats`, `sheet`,
`flags`), `rulebook`, `disclaimer` (the rulebook's), `brief`, `hooks` (`id`, `lines` by length, `broll` = 3 shot texts,
`null` where dropped), `compliance` (`items`, `notes`), `look` (`line`, `source`), `broll` (`mode`, `clips`, `files`),
`llm_usd`, and **`slots`**: per hook, `/render`'s `broll` argument ready to send, with the shot letters mapped
**A → `slot1`, B → `slot2`, C → `slot3`**:

```json
{"H1": {"slot1": {"wide": "runs/<id>/broll/H1A_1344x768.mp4", "tall": "runs/<id>/broll/H1A_768x1344.mp4"},
        "slot2": {...}, "slot3": {...}},
 "H2": {...}}
```

A dropped shot leaves its slot out (the service then drops that cutaway). A hook dropped by compliance is not in `hooks`
and not in `slots`.

## Credentials it expects, by name

| Name | Type | Used by |
|---|---|---|
| `Nous` | OpenAI API, Base URL `https://inference-api.nousresearch.com/v1` | every LLM and vision node (HTTP Request nodes using the credential's key; the URL is in the node). Model `google/gemini-4-argon`. |
| `brief-form` | Basic Auth (user `brief`) | the Brief form's login. Without it the form answers 401. |
| `telegram-demo` | Telegram (the bot's token) | every Telegram node: the approval photo and buttons, the review link, the done message, the failure message |
| `toolkit-machine` | Header Auth: name `Authorization`, value `Bearer <machine token>` | every call to the toolkit (live b-roll and the error workflow's pod clean-up). Set its allowed domain to the toolkit host. |
| R2 | | not in n8n: it is in the media service's env |

The LLM nodes are HTTP Request nodes, not the OpenAI nodes: with a custom base URL they let the body carry
`response_format` (structured outputs) and image parts as the API defines them, and the cost the API reports
(`usage.cost`) is summed into `llm_usd` in the run data.

## Stand-in or real: Voice and B-roll

Two separate dropdowns on the form, both defaulting to the stand-in (a post that leaves them out is a stand-in run).
- **Voice**: *Stand-in (free, flite)* sends `voice: "stub"` to `/render`; *Real (RunPod)* sends `"runpod"`: a speak job on the
  voice endpoint, which runs **VoxCPM2** (`model: "voxcpm2"`, params `seed 42, cfg_value 1.6, inference_timesteps 10`; brackets in a script are
  turned into commas first, see docs/api.md). Without the RunPod
  and R2 environment in the media service every version fails with the service's message (for example `missing environment:
  VOICE_SAMPLE_KEY, ...`); the run records those as failed versions, and when none can be rendered it ends with that reason in
  `error.json`.
- **B-roll**: *Stand-in clips (no GPU)* copies the fixture clips; *Live (pods)* runs the live branch (below). The package is uploaded to R2 only for live b-roll.
- The old single "Dry run" checkbox is still understood when a script posts it (`[]` = live b-roll and real voice).
- Voice cost is not wired yet (0 in `/package`). The GPU cost is the live b-roll's pod time at the pod's rate (stand-in b-roll: 0); report.md's Mode line says which.

## Shot list syntax

The form's Shot list is six lines, one shot per line, in order; the n-th line is S<n>. Blank lines and `# comments` are ignored.

```
0:00.5-0:06    opening, hook 1
0:06.6-0:09.3  tension
0:09.6-0:15    routine, follow the action
0:15.5-0:21.5  opening, hook 2, tall crop 21%, square crop 30%
0:21.9-0:26.8  calm, tall crop 35%
0:27.5-0:31.5  close, tall crop 37%, square crop 42%
```

- **Times** `START-END`: `m:ss` or `m:ss.s` (`0:09.6`, `1:05`) or plain seconds (`9.6-15`); `-`, an en dash or an em dash between; spaces around it are fine.
- **Role** (first word after the times): `opening, hook 1` or `opening, hook 2` (also `opening hook 1`, or just `hook 1`) become `opener-H1` / `opener-H2`; `tension`, `routine`, `calm`, `close` (`closing` too) keep their names.
- **Position** (after the role, comma-separated, all optional): `left`, `centre` (or `center`), `right`; `follow the action` becomes `auto`; `tall crop 21%` is the 9:16 window's centre (`x9=0.21`), `square crop 30%` the 1:1 window's (`x1=0.30`). No position means `centre`.
- A line that cannot be read stops the run with its number and what was expected, for example `Shot 3: no time range like 0:09.6-0:15`; every unreadable line is listed at once. Fewer or more than six lines is refused too.

The translator sits in front of the old syntax and its output is the same `beats` shape, so nothing downstream changes. A line written in the old syntax is still read as it was, also mixed with plain lines (the old line keeps its own S name; a plain line is named by its position among all the lines):

```
S1 0.5-6.0 opener-H1 centre
S2 6.6-9.3 tension
S3 9.6-15.0 routine auto
S4 15.5-21.5 opener-H2 x9=0.21,x1=0.30
S5 21.9-26.8 calm x9=0.35
S6 27.5-31.5 close 0.7 x1=0.42
```

Old syntax is `NAME START-END ROLE [POSITION ...]` (ROLE a free label). `POSITION` is where the 9:16 and 1:1 crop windows sit; it may be left out (= `centre`):

| Token | Becomes (media service) |
|---|---|
| `left`, `centre` (or `center`), `right`, `auto` | `face` |
| a number `0` to `1` | `face`: the window's centre as a share of the source width |
| `x9=0.21`, `x1=0.30` (also `9x16=`, `1x1=`), alone or comma-joined | `x`: `{"9X16": 0.21, "1X1": 0.30}`, which wins over `face` for that format |

`auto` follows movement and faces, not meaning: set `x` for a beat whose point is something still.

## Setup on the box

- The n8n container needs `N8N_RESTRICT_FILE_ACCESS_TO=/data` (n8n 2.x refuses file nodes outside its own folder
  otherwise). The compose file's env file is where it goes, next to `N8N_DEFAULT_BINARY_DATA_MODE=filesystem`,
  `N8N_PAYLOAD_SIZE_MAX=100` and `N8N_FORMDATA_FILE_SIZE_MAX=100`.
- Execution history is pruned: `EXECUTIONS_DATA_PRUNE=true`, `EXECUTIONS_DATA_MAX_AGE=336` (hours, 14 days). A run keeps
  the brief and three base64 frames in its execution data.
- The n8n container also needs `NODE_FUNCTION_ALLOW_BUILTIN=crypto` (the sheet link's token).
- `data/config/telegram.json` (`{"telegram_chat_id": "<digits>"}`, see above) in the data folder for the Telegram messages; without it the run uses the form.
- The media service must have `POST /run` and `/render`'s `voice` (see `docs/api.md`).
- The dry-run clips live in the data folder: `n8n/fixtures/make-broll.sh` makes them (`n8n/fixtures/broll/` ships in
  the repo, six files of about 85 KB); copy that folder to `/data/brand/fixtures/broll/` once.

## Import

1. In n8n create the credentials above (`Nous`, `brief-form`, `telegram-demo`, and `toolkit-machine` for live b-roll).
2. Import `errors.json`, then `workflow.json` (Workflows → Import from file, or `POST /api/v1/workflows` with the
   file). Credentials are matched by name.
3. In **ad-versions** → Settings → Error workflow, pick **ad-versions errors**, and publish both (an error workflow
   must be active to run).
4. Publish **ad-versions**; the form is at `/form/brief`.

## Trust

The brief's text goes into the script prompt unescaped. That is fine for one user behind Basic Auth; the hard word list
and the transcript check are deterministic, so a brief cannot talk its way past them.

## How the form looks

The form keeps its field order (field-0 to field-15, nothing added) and only its page is dressed: title, one-line description, button label ("Make the versions") and the field examples are node parameters; the look is the node's Custom Form Styling option, whose source is `brief-form.css` (the test checks the two are equal). It uses brand kit v2: Canvas ground, one white card, Ultra Blue, capsule controls, Google Fonts for Unbounded, Plus Jakarta Sans and Martian Mono (the browser needs internet for them; without it the fallbacks show). The four group headings (The brand, The message, The footage, The output), the optional marks and the hints under a field are CSS-generated text on fields 1, 5, 9, 13 and so on; Form Trigger 2.2 has no grouping or field-note feature, and a Custom HTML field would take a field number. If a field is added or moved, update those `nth-child` numbers.

A field can be pre-filled from the address, with the field label as the query name: `/form/brief?Brand=Restfield&Offer=First%20visit%20free`, a dropdown by its option text (`&Voice=Real%20(RunPod)`).

## Posting the form from a script

Fields are `field-0` to `field-15` in the order above (Brand is 0, Offer 3, the file 8, the link 9, Shot list 10 (it was Beats), Look 11,
Landing URL 12, Call to action 13, Voice 14, B-roll 15). Dropdowns take the option text:

```
curl -u brief:$PASSWORD http://127.0.0.1:5678/form/brief \
  --form-string field-0=BRAND ... -F "field-8=@clip.mp4;type=video/mp4" \
  --form-string "field-10=$BEATS" --form-string 'field-14=Stand-in (free, flite)' --form-string 'field-15=Stand-in clips (no GPU)'
```

The approval form is posted the same way (multipart): `field-0` once per ticked version, `field-1` the notes.

## Live b-roll (B-roll = Live)

Built against the toolkit's `/api/pods` and `/api/broll` (contract: the toolkit's docs/broll-api.md). Node names all start with `B-roll live: `.

**Settings.** The toolkit's address is the one thing kept out of the repo: `data/config/toolkit.json` in the data folder
(`/data/config/toolkit.json` in the container), `{"toolkit_url": "https://<toolkit host>"}`: https, a host, no path. The importer writes it, like
`telegram.json`. Every call carries the `toolkit-machine` credential (a Header Auth `Authorization: Bearer <machine token>`; give it the toolkit host as its
only allowed domain, so the token cannot go anywhere else). The error workflow reads the same file.

**The pod.** The branch never uses a pod it did not deploy. `GET /api/pods` first: if the page shows any pod (a ready one, one in use, a build host, one being deleted), the run stops at once with
`A GPU pod is already running (<name>, <state>). Delete it on the Pods page or choose B-roll: stand-in.` and deletes nothing.
- No pod: `POST /api/pods/catalog` (a refresh), stop with a clear error unless `h3-t2v` is listed, write `runs/<id>/pod.json` (`requested_at`), then `POST /api/pods/deploy` with what the Pods page sends
  for a pod with one bundle: `{"cap_hours": 8, "bundles": ["h3-t2v"], "addons": []}` (the page's defaults: the premium A6000, the default image, an 8 h cap), and on the 202 add the pod's `name` to pod.json.
  A pod.json that cannot be written stops the run before the deploy. A refusal (a 409 such as "the premium A6000 is out of stock, use the plain one?", the balance rail) stops the run with the toolkit's words:
  the plain-A6000 and tunnel questions are the owner's.
- Then `GET /api/pods` every 30 s. A different pod, our pod gone or being deleted: the run stops. Ready is not enough: the bundles download after it, so the branch waits for the add queue to empty and the
  Downloader to say `done` (the check `/api/broll` makes itself).
- **Not ready in 45 minutes** (spec section 12), or a failed setup or bundle download: no second GPU type. The pod is deleted and the run **goes on with no b-roll**: every slot is empty, `/render` leaves the
  cutaways out, `run.json` → `broll.status` is `no pod: <reason>`, and report.md's Mode line says `no b-roll (no pod: <reason>)` (the report has no other free-text line for a run, so the Mode line carries it).
  The pod's minutes still go into the report's cost.

**The clips.** Per hook and shot, two sizes: `<hook><A|B|C>_1344x768.mp4` and `_768x1344.mp4`, the same names the stand-in branch writes, in `runs/<id>/broll/`
(12 files for two hooks; fewer when compliance dropped a shot). The prompt is the shot, the look line, and `, 24 fps, no people, no text` (spec 4.3); `variant` `turbo8`, 3 seconds,
a fixed seed per shot. One `POST /api/broll/jobs` carries every clip still to make (up to 12); a 409 with `retry: true` (one job at a time, still setting up) waits 20 s and asks again
for up to 30 minutes; any other refusal, a 503 for the missing workflow export included, stops the run with the toolkit's message. The job is polled every 15 s; each finished clip
is downloaded (the body must be an mp4) and written.
- A job stops at its first failed clip; the clips already made are kept. That clip is asked for once more with a new seed (the next job holds it and everything not yet made);
  a second failure, or a clip that cannot be downloaded twice, drops it. A step failure (no clip named) is retried once, then stops the run.
- A slot needs both sizes: a shot with one size missing leaves its cutaway out (the media service drops a slot that is not sent), and `broll.notes` in run.json says why. If no
  clip at all was made the run fails.
- `run.json` → `broll`: `clips.<id>` has `wide`, `tall` (paths), `wide_wall_s`, `tall_wall_s`, `wide_tries`, `tall_tries` and `<size>_dropped` (the reason); `jobs` lists each job
  (clips, state, wall seconds, error); `status` (`ok` or `no pod: <reason>`), `gpu_minutes`, `gpu_rate_usd_h` (the pod's price, which `/package` and report.md use), `gpu_cost_estimate_usd` (an estimate: minutes x hourly rate / 60, as report.md computes it),
  `gpu_cost_per_toolkit_usd` (the toolkit's own figure, rounded up to whole hours; which of the two Massed bills on is not confirmed), `pod_delete`. **The API reports no generation time**: a clip's `wall_s` is the time from the job start (or the clip before) to the poll that first listed it, so it
  includes setup for the first clip and is good to about 15 s; clips first listed in one poll share the interval.

**The clean-up.** After the last clip (or a give-up) the branch reads `GET /api/pods` and calls `POST /api/pods/delete` only when the page shows the pod this run deployed (the name in pod.json); any other pod is left
alone and noted in `broll.pod_delete`. A delete that fails stops the run, so the error workflow tries again and tells you.
Any failure in a `B-roll live: ...` node ends in the error workflow. Its pod clean-up is its own branch from the first node (it does not wait for the media service or the disk, and a down media service cannot stop the
Telegram message): it reads `runs/<id>/pod.json`; with no pod.json it deletes nothing. Otherwise it reads the toolkit address, asks `GET /api/pods`, and deletes the pod only if its name is the one recorded
(or, only when the deploy got no answer at all and pod.json holds just `requested_at`, a pod launched within 125 s after the request, the deploy's 120 s timeout plus slack). A deploy the toolkit answered with a refusal (a 409
such as "A VM is already on this page", for instance because the owner started a pod by hand at the same moment) writes `refused: <status>` into pod.json first, and then nothing is ever deleted for that run. A pod still `launching`
cannot be deleted yet, so the clean-up asks again every 30 s for up to 10 minutes and then logs `left for the idle timer`; the Telegram alert can therefore come up to 10 minutes late when it is still retrying. A pod it did not deploy, and any build host, is left alone. The outcome is `pod_cleanup` in error.json and a line in the Telegram message. Pods that outlive both are caught by the
toolkit's 30-minute idle delete and the cap.

## Not built yet

The visual check per clip (spec 4.3: safety and match, one regeneration) and the voice cost in the report.
