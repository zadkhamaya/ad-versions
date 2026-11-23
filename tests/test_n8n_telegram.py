"""The Telegram part of the n8n exports (task 12c): wired as designed, fails soft, and nothing private is committed."""
import json
import re
from pathlib import Path

N8N = Path(__file__).resolve().parents[1] / "n8n"
WF = json.loads((N8N / "workflow.json").read_text())
ER = json.loads((N8N / "errors.json").read_text())
BY = {n["name"]: n for n in WF["nodes"]}
EBY = {n["name"]: n for n in ER["nodes"]}


def targets(conns, name, output=0):
    outs = conns.get(name, {}).get("main", [])
    return [c["node"] for c in outs[output]] if len(outs) > output else []


def test_every_connection_points_at_a_node():
    for nodes, conns in ((BY, WF["connections"]), (EBY, ER["connections"])):
        for src, v in conns.items():
            assert src in nodes
            assert all(c["node"] in nodes for outs in v["main"] for c in outs)


def test_telegram_nodes_are_on_and_use_the_named_credential():
    tg = [n for n in WF["nodes"] + ER["nodes"] if n["type"] == "n8n-nodes-base.telegram"]
    assert len(tg) == 5
    for n in tg:
        assert not n.get("disabled"), n["name"]
        assert n["credentials"] == {"telegramApi": {"name": "telegram-demo"}}      # the name only, never an id
        assert "$env" not in json.dumps(n["parameters"]), n["name"]                  # n8n blocks env access in nodes; keep it blocked


def test_approval_buttons_and_wait_limit():
    p = BY["Telegram: approve or review"]["parameters"]
    assert p["operation"] == "sendAndWait" and p["responseType"] == "approval"
    assert p["approvalOptions"]["values"] == {"approvalType": "double", "approveLabel": "Approve passed", "disapproveLabel": "Review"}
    assert p["options"]["limitWaitTime"]["values"] == {"resumeAmount": 7, "resumeUnit": "days"}
    assert BY["Telegram: approve or review"]["alwaysOutputData"] is True               # a timed-out Send and Wait emits nothing; this turns it into "no answer" (expired)
    assert BY["Telegram: sheet photo"]["parameters"]["binaryData"] is True            # sendPhoto needs the sheet as binary


CHAT_ID_VALUE = re.compile(r"telegram_chat_id\\*\"?\s*[:=]\s*\\*\"?-?\d{5,}")      # a chat id value, also inside a JSON-escaped jsCode string
LONG_DIGITS = re.compile(r"\d{6,}")


def chat_id_leaks(node):
    """Why this node has a chat id in it (empty = clean): a literal in chatId, a long digit run in its code, or a value next to the key."""
    p = node.get("parameters", {})
    out = []
    if "chatId" in p and not (str(p["chatId"]).startswith("=") and "chat_id" in str(p["chatId"])):
        out.append("chatId is not an expression on chat_id")
    if LONG_DIGITS.search(str(p.get("chatId", ""))) or LONG_DIGITS.search(str(p.get("jsCode", ""))):
        out.append("a long digit run")
    if CHAT_ID_VALUE.search(json.dumps(node)):
        out.append("a chat id value")
    return out


def test_the_chat_id_guard_catches_what_it_should():
    bad_literal = {"name": "x", "parameters": {"chatId": "123456789"}}
    bad_code = {"name": "y", "parameters": {"jsCode": 'const cfg = {"telegram_chat_id": "123456789"};'}}
    bad_expression_digits = {"name": "z", "parameters": {"chatId": "={{ $json.chat_id + '123456789' }}"}}
    fine = {"name": "ok", "parameters": {"chatId": "={{ $json.chat_id }}", "jsCode": "const id = /^-?\\d{1,20}$/.test(x);"}}
    assert chat_id_leaks(bad_literal) and chat_id_leaks(bad_code) and chat_id_leaks(bad_expression_digits)
    assert CHAT_ID_VALUE.search(json.dumps(bad_code))                                   # the escaped form is caught too
    assert chat_id_leaks(fine) == []


def test_the_chat_id_comes_from_the_config_file_only():
    assert BY["Telegram: read config"]["parameters"]["fileSelector"] == "/data/config/telegram.json"
    assert BY["Telegram: done config"]["parameters"]["fileSelector"] == "/data/config/telegram.json"
    assert EBY["Error: read Telegram config"]["parameters"]["fileSelector"] == "/data/config/telegram.json"
    for n in WF["nodes"] + ER["nodes"]:
        if n["type"] == "n8n-nodes-base.telegram" or "Telegram" in n["name"] or "telegram" in n["parameters"].get("jsCode", ""):
            assert chat_id_leaks(n) == [], n["name"]
        assert not CHAT_ID_VALUE.search(json.dumps(n)), n["name"]
    text = (N8N / "workflow.json").read_text() + (N8N / "errors.json").read_text() + (N8N / "README.md").read_text()
    assert not CHAT_ID_VALUE.search(text)                                               # the file's shape is documented, a value never
    assert not re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}", text)                            # no bot token


def test_a_telegram_failure_falls_through_to_the_form():
    steps = ["Telegram: read config", "Telegram: chat id and texts", "Telegram: read sheet", "Telegram: sheet photo", "Telegram: review link"]
    for s in steps:
        assert BY[s]["onError"] == "continueErrorOutput"
        assert targets(WF["connections"], s, 1) == ["Telegram: failed"], s
    assert targets(WF["connections"], "Telegram: failed") == ["Telegram: record error in run.json"]
    assert targets(WF["connections"], "Telegram: write run.json (error)") == ["Approval: form (wait up to 7 days)"]
    assert "telegram_error" in BY["Telegram: record error in run.json"]["parameters"]["jsCode"]


def test_a_failed_send_and_wait_takes_the_form_path_only():
    """alwaysOutputData turns a timeout (no items) into one empty item = no answer. It must not also fire when the send itself failed:
    with an error output the error item moves to output 1 and the empty output 0 gets {} (both paths run). So the node uses the regular
    output (a failure is {error} on output 0, never empty) and an IF sends errors to the form fallback before any answer is read."""
    node = BY["Telegram: approve or review"]
    assert node["onError"] == "continueRegularOutput" and node["alwaysOutputData"] is True
    assert targets(WF["connections"], "Telegram: approve or review") == ["Telegram: send failed?"]
    assert len(WF["connections"]["Telegram: approve or review"]["main"]) == 1                    # no error output that could double-fire
    assert "$json.error" in BY["Telegram: send failed?"]["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert targets(WF["connections"], "Telegram: send failed?", 0) == ["Telegram: failed"]
    assert targets(WF["connections"], "Telegram: send failed?", 1) == ["Telegram: Review pressed?"]


def test_both_approvals_join_before_package():
    assert targets(WF["connections"], "Approval: read Telegram") == ["Package: body"]
    assert targets(WF["connections"], "Approval: read the form") == ["Package: body"]
    assert targets(WF["connections"], "Telegram: Review pressed?", 0) == ["Telegram: review link"]      # true: Review
    assert targets(WF["connections"], "Telegram: Review pressed?", 1) == ["Approval: read Telegram"]    # false: Approve passed, or no answer (expired)
    assert targets(WF["connections"], "Telegram: review link", 0) == ["Approval: form (wait up to 7 days)"]
    assert "reviewer (Telegram)" in BY["Approval: read Telegram"]["parameters"]["jsCode"]
    assert "reviewer (form)" in BY["Approval: read the form"]["parameters"]["jsCode"]
    assert "P.approver" in BY["Package: body"]["parameters"]["jsCode"]


def test_the_done_message_never_fails_the_run():
    assert targets(WF["connections"], "Package: result") == ["Telegram: send done?"]
    assert targets(WF["connections"], "Package: nothing approved or expired") == ["Telegram: send done?"]       # nothing approved / expired say so too
    assert targets(WF["connections"], "Telegram: send done?", 1) == ["Run: final run.json"]                    # a failed package: the error workflow tells
    assert targets(WF["connections"], "Telegram: done chat id?", 0) == ["Telegram: done message"]
    assert targets(WF["connections"], "Telegram: done chat id?", 1) == ["Telegram: done result"]               # no chat id: no call
    assert targets(WF["connections"], "Telegram: done result") == ["Run: final run.json"]
    assert BY["Telegram: done message"]["onError"] == "continueRegularOutput" and BY["Telegram: done config"]["onError"] == "continueRegularOutput"
    assert "telegram_done" in BY["Run: final run.json"]["parameters"]["jsCode"]


def test_the_error_workflow_sends_a_message_logs_the_outcome_and_cannot_loop():
    c = ER["connections"]
    assert targets(c, "Error: write error.json") == ["Error: failed in the live b-roll?"]                      # the pod clean-up first (test_n8n_broll.py), then the message
    assert targets(c, "Error: failed in the live b-roll?", 1) == ["Error: read Telegram config"]
    assert targets(c, "Error: record the pod clean-up") == ["Error: read Telegram config"]
    assert targets(c, "Error: Telegram text") == ["Error: chat id read?"]
    assert targets(c, "Error: chat id read?", 0) == ["Error: Telegram message"]
    assert targets(c, "Error: chat id read?", 1) == ["Error: record the Telegram result"]                      # no chat id: no call, logged
    assert targets(c, "Error: Telegram message") == ["Error: record the Telegram result"]
    assert targets(c, "Error: record the Telegram result") == ["Error: write error.json (with Telegram)"]
    assert targets(c, "Error: write error.json (with Telegram)") == ["Error: stop"]
    for name in ("Error: read Telegram config", "Error: Telegram message", "Error: write error.json (with Telegram)"):
        assert EBY[name]["onError"] == "continueRegularOutput", name
    assert "telegram" in EBY["Error: record the Telegram result"]["parameters"]["jsCode"]
    assert "errorWorkflow" not in ER.get("settings", {})
