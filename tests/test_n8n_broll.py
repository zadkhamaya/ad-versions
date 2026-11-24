"""The live b-roll branch and the pod clean-up, as wired in the n8n exports (task 13b): only the toolkit calls it should make, the credential by name,
no host or token in the repo, every loop closed, and the pod deleted at the end and on a failure."""
import json
import re
from pathlib import Path

N8N = Path(__file__).resolve().parents[1] / "n8n"
WF = json.loads((N8N / "workflow.json").read_text())
ER = json.loads((N8N / "errors.json").read_text())
BY = {n["name"]: n for n in WF["nodes"]}
EBY = {n["name"]: n for n in ER["nodes"]}
LIVE = {k: v for k, v in BY.items() if k.startswith("B-roll live: ")}
P = "B-roll live: "


def targets(conns, name, output=0):
    outs = conns.get(name, {}).get("main", [])
    return [c["node"] for c in outs[output]] if len(outs) > output else []


def sources(conns, name):
    return sorted({s for s, v in conns.items() for outs in v["main"] for c in outs if c["node"] == name})


def test_the_stub_is_gone_and_the_live_branch_hangs_off_the_b_roll_switch():
    assert not any(n.get("disabled") for n in WF["nodes"])
    assert "B-roll live: not deployed" not in BY and not any("stub" in k for k in BY)
    assert targets(WF["connections"], "B-roll: dry run?", 0) == ["B-roll: read stand-in clips"]
    assert targets(WF["connections"], "B-roll: dry run?", 1) == [P + "read config"]
    assert len(LIVE) == 38


def test_every_toolkit_call_uses_the_credential_by_name_and_the_configured_address():
    calls = {}
    for name, n in list(LIVE.items()) + [(k, v) for k, v in EBY.items() if k.startswith("Error: ") and v["type"] == "n8n-nodes-base.httpRequest" and "toolkit" in json.dumps(v)]:
        if n["type"] != "n8n-nodes-base.httpRequest":
            continue
        p = n["parameters"]
        assert n["credentials"] == {"httpHeaderAuth": {"name": "toolkit-machine"}}, name         # the name only, never an id
        assert p["authentication"] == "genericCredentialType" and p["genericAuthType"] == "httpHeaderAuth", name
        assert p["url"].startswith("={{") and "toolkit_url" in p["url"] or p["url"] == "={{ $json.url }}", name
        assert "http://" not in p["url"] and "https://" not in p["url"], name
        assert p["options"]["response"]["response"]["neverError"] is True and p["options"]["response"]["response"]["fullResponse"] is True, name
        assert n.get("onError") == "continueRegularOutput", name                                  # the Code node after it says what went wrong
        calls[name] = (p["method"], re.sub(r"\{\{.*?\}\}", "<x>", p["url"].split("}}", 1)[-1]) or "<x>")
    assert sorted(set(calls.values())) == sorted({("GET", "/api/pods"), ("POST", "/api/pods/catalog"), ("POST", "/api/pods/deploy"), ("POST", "/api/pods/delete"),
                                                  ("POST", "/api/broll/jobs"), ("GET", "/api/broll/jobs/<x>"), ("GET", "<x>")})
    assert not [n for n in LIVE.values() if n["type"] == "n8n-nodes-base.httpRequest" and "/api/pods/" in n["parameters"]["url"]
                and not re.search(r"/api/pods/(catalog|deploy|delete)$", n["parameters"]["url"])]   # no keep, extend, add, retry, fake


def test_the_deploy_asks_for_the_pods_page_defaults_for_h3_t2v_after_a_catalog_check():
    assert BY[P + "POST /api/pods/deploy"]["parameters"]["jsonBody"] == "={{ { cap_hours: 8, bundles: ['h3-t2v'], addons: [] } }}"
    c = WF["connections"]
    assert targets(c, P + "deploy needed?", 0) == [P + "POST /api/pods/catalog"] and targets(c, P + "POST /api/pods/catalog") == [P + "catalog check"]
    assert targets(c, P + "catalog check") == [P + "pod request"] and targets(c, P + "pod request") == [P + "write pod.json (request)"]
    assert targets(c, P + "write pod.json (request)") == [P + "POST /api/pods/deploy"]
    assert sources(c, P + "POST /api/pods/deploy") == [P + "write pod.json (request)"]      # nothing deploys without the catalog check and a pod.json on disk
    assert BY[P + "write pod.json (request)"].get("onError") is None                       # a pod.json that cannot be written stops the run before any deploy
    assert targets(c, P + "POST /api/pods/deploy") == [P + "deploy result"] and targets(c, P + "deploy result") == [P + "write pod.json (name)"]
    assert targets(c, P + "write pod.json (name)") == [P + "deploy outcome"] and targets(c, P + "deploy outcome") == [P + "wait for the pod"]
    assert "throw" not in BY[P + "deploy result"]["parameters"]["jsCode"]                  # the refusal is thrown only after pod.json says `refused`
    assert "refused: r.statusCode" in BY[P + "deploy result"]["parameters"]["jsCode"] and "throw new Error(R.error_message)" in BY[P + "deploy outcome"]["parameters"]["jsCode"]
    for n in ("write pod.json (request)", "write pod.json (name)"):
        assert BY[P + n]["parameters"]["fileName"] == "=/data/runs/{{ $execution.id }}/pod.json"
    assert "h3-t2v" in BY[P + "catalog check"]["parameters"]["jsCode"]
    assert BY[P + "POST /api/pods/catalog"]["parameters"]["jsonBody"] == "{}"               # the toolkit answers 415 to a POST with no JSON


def test_the_loops_close_and_the_waits_stay_short():
    c = WF["connections"]
    assert targets(c, P + "wait for the pod") == [P + "GET /api/pods"] and targets(c, P + "pod ready?", 0) == [P + "next batch"]
    assert targets(c, P + "deploy needed?", 1) == [P + "give up?"] and targets(c, P + "give up?", 0) == [P + "finish"] and targets(c, P + "give up?", 1) == [P + "wait for the pod"]
    assert targets(c, P + "job running?", 0) == [P + "wait for the clips"] and targets(c, P + "wait for the clips") == [P + "GET /api/broll/jobs/<id>"]
    assert targets(c, P + "job accepted?", 1) == [P + "wait to resubmit"] and targets(c, P + "wait to resubmit") == [P + "next batch"]
    assert targets(c, P + "after downloads") == [P + "next batch"]
    assert targets(c, P + "files to fetch?", 1) == [P + "after downloads"]                   # a poll that finished nothing still goes on
    waits = {k: v["parameters"] for k, v in LIVE.items() if v["type"] == "n8n-nodes-base.wait"}
    assert {k: (w["amount"], w["unit"]) for k, w in waits.items()} == {P + "wait for the pod": (30, "seconds"), P + "wait for the clips": (15, "seconds"), P + "wait to resubmit": (20, "seconds")}
    assert all(w["resume"] == "timeInterval" and w["amount"] < 65 for w in waits.values())     # under 65 s n8n keeps the wait in memory


def test_the_clips_are_written_under_the_stand_in_names_and_the_state_has_the_same_shape():
    assert BY[P + "write clip"]["parameters"]["fileName"] == "={{ $json.path }}" and BY[P + "write clip"]["onError"] == "continueRegularOutput"
    assert "'/data/runs/' + run_id + '/broll/' + key + '.mp4'" in BY[P + "poll result"]["parameters"]["jsCode"]
    assert BY[P + "GET clip file"]["parameters"]["options"]["response"]["response"]["responseFormat"] == "file"
    assert targets(WF["connections"], P + "done") == ["Run data: build run.json"] and sources(WF["connections"], "Run data: build run.json") == [P + "done", "B-roll: done"]
    for k in ("A -> slot1", "B -> slot2", "C -> slot3"):
        assert k in BY[P + "finish"]["parameters"]["jsCode"]


def test_a_found_pod_is_never_used_and_only_the_runs_own_pod_is_deleted_at_the_end():
    c = WF["connections"]
    dec = BY[P + "pod decision"]["parameters"]["jsCode"]
    assert "A GPU pod is already running (" in dec and "Delete it on the Pods page or choose B-roll: stand-in." in dec
    assert "by_this_run" not in json.dumps(WF)                                                 # no ownership guessing from launch times
    assert targets(c, P + "anything to submit?", 1) == [P + "finish"] and targets(c, P + "finish") == [P + "GET /api/pods before the delete"]
    assert targets(c, P + "own pod check") == [P + "delete the pod?"] and targets(c, P + "delete the pod?", 0) == [P + "POST /api/pods/delete"]
    assert sources(c, P + "POST /api/pods/delete") == [P + "delete the pod?"] and targets(c, P + "delete the pod?", 1) == [P + "done"]
    assert targets(c, P + "POST /api/pods/delete") == [P + "done"]
    assert "vm.name !== name" in BY[P + "own pod check"]["parameters"]["jsCode"]                # the page must show the pod this run deployed
    assert "pod_delete" in BY[P + "done"]["parameters"]["jsCode"]


def test_a_pod_that_is_not_ready_in_45_minutes_is_deleted_and_the_run_goes_on_without_b_roll():
    assert "45 * 60000" in BY[P + "plan"]["parameters"]["jsCode"] and "30 * 60000" in BY[P + "next batch"]["parameters"]["jsCode"]
    assert "give_up" in BY[P + "pod decision"]["parameters"]["jsCode"] and "no pod: " in BY[P + "pod decision"]["parameters"]["jsCode"]
    assert "no b-roll (" in BY["Package: body"]["parameters"]["jsCode"]                        # report.md's Mode line says so
    assert targets(WF["connections"], P + "done") == ["Run data: build run.json"]              # not an error: the run goes on to render, with empty slots


def test_the_error_workflow_deletes_only_the_pod_this_run_deployed_and_does_not_depend_on_the_media_service():
    c = ER["connections"]
    E = "Error: "
    # the clean-up is its own branch from Error: ids, ahead of the run folder (a higher node: n8n runs the top branch first)
    assert targets(c, E + "ids") == [E + "failed in the live b-roll?", E + "run folder"]
    assert EBY[E + "failed in the live b-roll?"]["position"][1] < EBY[E + "run folder"]["position"][1]
    cond = EBY[E + "failed in the live b-roll?"]["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert "execution" in cond and "^B-roll live" in cond
    for n in (E + "run folder", E + "write error.json"):
        assert EBY[n]["onError"] == "continueRegularOutput", n                                  # media down or a full disk must not stop the alert
    chain = [E + "failed in the live b-roll?", E + "read pod.json", E + "pod record", E + "pod deployed by this run?", E + "read toolkit config", E + "toolkit address",
             E + "toolkit address ok?", E + "GET /api/pods", E + "is there a pod to delete?", E + "delete the pod?"]
    for a, b in zip(chain, chain[1:]):
        assert b in targets(c, a, 0), (a, b)
    assert EBY[E + "read pod.json"]["parameters"]["fileSelector"] == "=/data/runs/{{ $json.run_id }}/pod.json"
    assert targets(c, E + "pod deployed by this run?", 1) == [E + "record the pod clean-up"]    # no pod.json: nothing is deleted
    assert sources(c, E + "POST /api/pods/delete") == [E + "delete the pod?"] and targets(c, E + "delete the pod?", 0) == [E + "POST /api/pods/delete"]
    assert targets(c, E + "delete the pod?", 1) == [E + "try again?"] and targets(c, E + "try again?", 0) == [E + "wait to retry"] and targets(c, E + "wait to retry") == [E + "GET /api/pods"]
    assert EBY[E + "wait to retry"]["parameters"] == {"resume": "timeInterval", "amount": 30, "unit": "seconds"}
    clean = [n for k, n in EBY.items() if k in chain + [E + "record the pod clean-up", E + "POST /api/pods/delete", E + "try again?", E + "wait to retry"]]
    for n in clean:
        assert "build error.json" not in json.dumps(n) and "Error: run folder" not in json.dumps(n), n["name"]     # no dependency on the folder or error.json steps
        if n["type"] in ("n8n-nodes-base.httpRequest", "n8n-nodes-base.readWriteFile"):
            assert n.get("onError") == "continueRegularOutput", n["name"]                       # a failed clean-up must not stop the failure message
    own = EBY[E + "is there a pod to delete?"]["parameters"]["jsCode"]
    assert "vm.name === R.pod_name" in own and "was not deployed by this run" in own and "launched_at" in own
    assert "R.requested_ms + 125000" in own                                                      # the launch-time rule is bounded to the deploy's 120 s window
    assert "d.refused" in EBY[E + "pod record"]["parameters"]["jsCode"]                          # a refused deploy: nothing is deleted
    assert "pod_cleanup" in EBY[E + "build error.json"]["parameters"]["jsCode"] and "d.pod_cleanup" in EBY[E + "Telegram text"]["parameters"]["jsCode"]
    assert targets(c, E + "write error.json") == [E + "read Telegram config"]                    # the Telegram path is the one it was


def test_the_package_costs_the_pod_when_there_was_one():
    js = BY["Package: body"]["parameters"]["jsCode"]
    assert "gpu_minutes: gpuMin, gpu_rate_usd_h: gpuRate" in js and "gpu_minutes: 0" not in js


def test_no_host_or_token_ships_and_the_readme_names_the_setting_and_the_credential():
    text = "".join((N8N / f).read_text() for f in ("workflow.json", "errors.json", "README.md"))
    assert not re.search(r"zadkhamaya|\.xyz|machine\.token|Bearer [A-Za-z0-9]{8,}", text)
    readme = (N8N / "README.md").read_text()
    assert "toolkit-machine" in readme and "config/toolkit.json" in readme and "https://<toolkit host>" in readme
    assert "toolkit.example.com" not in json.dumps(WF)                                        # the old stub's address is gone
