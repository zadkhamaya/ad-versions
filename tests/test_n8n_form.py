"""The Brief form: dressed in the brand look, and the field list the scripts rely on is unchanged (field-0 to field-15)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = json.loads((ROOT / "n8n" / "workflow.json").read_text())
FORM = next(n for n in WF["nodes"] if n["name"] == "Brief form")["parameters"]
FIELDS = FORM["formFields"]["values"]

# label, type, required: the order is the field-N number the README's curl example posts to
EXPECTED = [
    ("Brand", "text", True), ("Product", "text", True), ("Audience", "text", False), ("Offer", "text", True),
    ("Key message 1", "text", True), ("Key message 2", "text", True), ("Key message 3", "text", True),
    ("Required disclaimer", "text", True), ("Source clip", "file", False), ("Source clip link", "text", False),
    ("Shot list", "textarea", True), ("Look", "text", False), ("Landing URL", "text", False),
    ("Call to action", "text", False), ("Voice", "dropdown", False), ("B-roll", "dropdown", False),
]


def test_fields_keep_their_order_types_and_required_flags():
    assert [(f["fieldLabel"], f["fieldType"], f["requiredField"]) for f in FIELDS] == EXPECTED
    assert [f["fieldLabel"] for f in FIELDS].index("Shot list") == 10          # was Beats: field-10 in the curl example


def test_no_extra_field_shifts_the_numbering():
    assert len(FIELDS) == 16 and all(f["fieldType"] != "html" for f in FIELDS)


def test_dropdown_texts_are_matched_in_code_and_kept():
    opts = {f["fieldLabel"]: [o["option"] for o in f["fieldOptions"]["values"]] for f in FIELDS if f["fieldType"] == "dropdown"}
    assert opts == {"Voice": ["Stand-in (free, flite)", "Real (RunPod)"], "B-roll": ["Stand-in clips (no GPU)", "Live (pods)"]}


def test_the_plain_form_is_empty():
    """Geoff 11-24: no placeholder, no default; a filled form is opened by the prefill link. The code supplies the defaults (harness)."""
    for f in FIELDS:
        assert not f.get("placeholder") and "defaultValue" not in f, f["fieldLabel"]


def test_title_description_and_button():
    assert FORM["formTitle"] == "New ad brief"
    assert FORM["options"]["buttonLabel"] == "Make the versions"
    desc = FORM["formDescription"]
    assert "<" not in desc and "START-END" not in desc and "ROLE" not in desc          # plain words, no syntax lecture
    assert FORM["options"]["appendAttribution"] is False and FORM["options"]["path"] == "brief"
    assert FORM["authentication"] == "basicAuth"


def test_the_css_file_is_what_the_node_holds():
    css = (ROOT / "n8n" / "brief-form.css").read_text().strip()
    assert FORM["options"]["customCss"] == css


def test_the_css_survives_n8n_sanitising():
    """n8n strips tags and parses the CSS as HTML text: a quote or an angle bracket would be changed or cut."""
    css = FORM["options"]["customCss"]
    assert '"' not in css and "<" not in css
    assert re.search(r"[A-Za-z]'[A-Za-z]", css) is None                       # no apostrophe inside a word
    for m in re.finditer(r"content: '([^']*)'", css):
        assert "&" not in m.group(1) and "\\" not in m.group(1), m.group(1)
    assert css.count("{") == css.count("}")


def test_the_css_follows_the_kit():
    css = FORM["options"]["customCss"]
    for token in ("#0033FF", "#F3EEE8", "#19123D", "#FF2E9A", "Unbounded", "Plus Jakarta Sans", "Martian Mono"):
        assert token in css, token
    assert css.count("#FF2E9A") == 1 and "var(--pink)" in css                    # the one highlight, set once
    assert "box-shadow: 0" not in css and "drop-shadow" not in css               # flat: no shadows outside inset edges
    # pink is never a text or label colour
    assert not re.search(r"(?<!background)\s*color:\s*var\(--pink\)", css.replace("background: var(--pink)", ""))


def test_groups_sit_on_fields_one_five_nine_and_thirteen():
    css = FORM["options"]["customCss"]
    heads = re.findall(r":nth-child\((\d+)\)::before \{ content: '([^']+)'", css)
    assert heads == [("1", "The brand"), ("5", "The message"), ("9", "The footage"), ("13", "The output")]
    labels = [f["fieldLabel"] for f in FIELDS]
    assert labels[0] == "Brand" and labels[4] == "Key message 1" and labels[8] == "Source clip" and labels[12] == "Landing URL"
    optional = {int(n) for n in re.findall(r":nth-child\((\d+)\) label::after", css)}
    assert {labels[i - 1] for i in optional} == {"Audience", "Look", "Landing URL", "Call to action"}
    assert not any(f["requiredField"] for f in FIELDS if f["fieldLabel"] in {labels[i - 1] for i in optional})


def test_the_page_answers_at_once_by_css():
    """The review form later in the workflow makes n8n treat this page as multi-page (no Form Trigger option changes it: the page keeps
    polling until the run reaches the review form). So from the click on (button disabled) the card shows the received text."""
    css = FORM["options"]["customCss"]
    assert ".inputs-wrapper," in css and "form.card:has(#submit-btn:disabled)" in css
    assert "content: 'Brief received'" in css
    assert "content: 'The run has started. The contact sheet comes to Telegram when the versions are checked.'" in css
    waits = [n for n in WF["nodes"] if n["type"] == "n8n-nodes-base.wait" and n["parameters"].get("resume") == "form"]
    assert waits, "the review form is a Wait node resumed by a form: that is what holds the page open"
