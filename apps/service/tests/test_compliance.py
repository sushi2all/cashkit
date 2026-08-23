"""The SPEC §9 documents, checked against the code they describe.

A privacy policy is a set of claims about a running system. Left alone, the
claims and the system drift, and the drift is invisible — the page still reads
correctly, and it is simply no longer true. So:

* the retention periods the policy states are compared against the settings
  the service actually enforces;
* the subprocessor list is compared against **every outbound hostname in the
  source**, because a subprocessor list is a claim about the code;
* the deletion paragraph is compared against the tables `DELETE /me` clears.

None of these can be satisfied by editing prose alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cashkit_service.config import Settings

ROOT = Path(__file__).resolve().parents[3]
COMPLIANCE = ROOT / "compliance"
POLICY = (COMPLIANCE / "privacy-policy.md").read_text()
SUBPROCESSORS = (COMPLIANCE / "subprocessors.md").read_text()
TERMS = (COMPLIANCE / "terms-of-service.md").read_text()
CHECKLIST = (COMPLIANCE / "SPEC9-checklist.md").read_text()
DPA = (COMPLIANCE / "dpa-template.md").read_text()

SETTINGS = Settings(database_url="unused://")


# --- the documents exist and say the things §9 enumerates ----------------- #


@pytest.mark.parametrize(
    "name",
    [
        "privacy-policy.md",
        "terms-of-service.md",
        "dpa-template.md",
        "subprocessors.md",
        "SPEC9-checklist.md",
    ],
)
def test_the_document_exists_and_is_not_a_stub(name: str):
    text = (COMPLIANCE / name).read_text()
    assert len(text) > 1500, f"{name} is too short to be the thing §9 asks for"
    assert "TODO" not in text and "TBD" not in text, f"{name} carries a placeholder"


def test_the_policy_names_the_things_a_reader_would_look_for():
    for phrase in ("Deleting your account", "Taking your data with you", "European Union"):
        assert phrase in POLICY, f"the privacy policy has no {phrase!r} section"
    assert "Garante" in POLICY, "the Italian supervisory authority is not named"


# --- retention: the prose and the settings are one number ----------------- #


def _stated_days(label: str) -> int:
    """Pull `**30 days**` out of the retention table row containing `label`."""
    for line in POLICY.splitlines():
        if label.lower() in line.lower():
            match = re.search(r"\*\*(\d+)\s*days?\*\*", line)
            if match:
                return int(match.group(1))
    raise AssertionError(f"no bolded retention period on the policy row for {label!r}")


def test_the_policy_states_the_retention_the_service_enforces():
    """§9: *log retention stated in the privacy policy*.

    Stated **and true**. Widening a window in the settings without editing the
    page fails here, and so does the reverse.
    """
    assert _stated_days("Raw model requests and replies") == SETTINGS.llm_payload_retention_days
    assert _stated_days("Request logs") == SETTINGS.request_log_retention_days
    assert _stated_days("Backups") == SETTINGS.backup_retention_days


def test_the_thirty_day_backup_window_is_the_same_number_everywhere():
    """One number in three places: the policy, the DPA, and the code."""
    assert SETTINGS.backup_retention_days == 30
    assert "backups within 30 days" in POLICY
    assert "within 30 days" in DPA


def test_the_policy_admits_the_raw_model_payloads_exist():
    """The easiest thing to leave out, and the most sensitive thing held."""
    assert "raw model" in POLICY.lower()
    assert "purge" in POLICY.lower() or "blanked" in POLICY.lower()


# --- the subprocessor list is a claim about the code ---------------------- #

#: Hosts that appear in the source and are not a subprocessor: local
#: development, the drill containers, and the repository itself.
NOT_A_SUBPROCESSOR = {
    "localhost", "127.0.0.1", "0.0.0.0",
    "service", "minio", "sink", "origin", "fixtures", "prometheus", "alertmanager",
    "github.com", "example.com", "example.invalid", "test",
}

SOURCE_DIRS = [
    ROOT / "apps" / "service" / "cashkit_service",
    ROOT / "apps" / "client" / "src",
    ROOT / "apps" / "client" / "app",
]


def outbound_hosts() -> set[str]:
    found: set[str] = set()
    for directory in SOURCE_DIRS:
        for path in directory.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "__pycache__" in path.parts:
                continue
            for url in re.findall(r"https?://([A-Za-z0-9.\-]+)", path.read_text()):
                found.add(url)
    return {h for h in found if h not in NOT_A_SUBPROCESSOR}


def test_every_external_host_the_code_talks_to_is_on_the_list():
    """A subprocessor list is a claim about the code, so it is checked against it.

    A new vendor reached from the service or the client fails this until the
    page names it — which is exactly the moment the page should change.
    """
    #: Host → the name it must appear under on the page.
    KNOWN = {"openrouter.ai": "OpenRouter"}
    for host in outbound_hosts():
        assert host in KNOWN, (
            f"the code talks to {host!r} and this test does not know who that is; "
            "add it to compliance/subprocessors.md and to KNOWN here"
        )
        assert KNOWN[host] in SUBPROCESSORS, f"{host} is not on the subprocessor page"


def test_every_vendor_spec_9_enumerates_is_on_the_page():
    """SPEC §9's own list, item by item."""
    for vendor in ("Hetzner", "OpenRouter", "Google", "Sentry", "Grafana"):
        assert vendor in SUBPROCESSORS, f"§9 names {vendor} and the page does not"


def test_the_speech_path_is_addressed_rather_than_omitted():
    """SPEC §9 requires the speech path on the list — or a stated reason it is not.

    D-MLP-45: mobile requires on-device recognition with no cloud fallback, and
    web is on-device or disabled, so as configured the MLP adds **no** speech
    subprocessor. That is a claim, so it is stated on the page rather than left
    as an absence a reader has to interpret.
    """
    assert "speech" in SUBPROCESSORS.lower()
    assert "on-device" in SUBPROCESSORS or "on your own device" in SUBPROCESSORS


def test_the_cloud_dictation_flag_is_still_off_by_default():
    """The other half of D-MLP-45, checked in the client rather than believed.

    Enabling `EXPO_PUBLIC_ALLOW_CLOUD_DICTATION` without naming the browser
    speech vendor on the privacy page would make the subprocessor list wrong.
    """
    dictation = (ROOT / "apps" / "client" / "src" / "voice" / "dictation.ts").read_text()
    assert "EXPO_PUBLIC_ALLOW_CLOUD_DICTATION" in dictation
    env_files = list((ROOT / "apps" / "client").glob(".env*"))
    for path in env_files:
        assert "EXPO_PUBLIC_ALLOW_CLOUD_DICTATION=1" not in path.read_text(), (
            f"{path} enables cloud dictation; the browser speech vendor must be "
            "on compliance/subprocessors.md first"
        )


def test_the_list_records_what_is_deliberately_absent():
    """An absence a reader has to infer is not a disclosure.

    Four vendors are deliberately not used, and each is a decision recorded
    elsewhere: no managed database, no speech vendor (D-MLP-45), no
    LLM-observability platform (SPEC §11's stated non-adoption), no bank
    aggregator (ADR-0026).
    """
    lowered = SUBPROCESSORS.lower()
    for phrase in ("no database vendor", "no speech-recognition vendor",
                   "no llm-observability platform", "no bank aggregator"):
        assert phrase in lowered, f"the page does not record {phrase!r}"


# --- deletion: the paragraph and the cascade ------------------------------ #


def test_the_policy_deletion_paragraph_names_what_the_code_deletes():
    """`DELETE /me` clears these tables; the policy says so in the user's words."""
    lowered = POLICY.lower()
    for thing in ("session", "book", "turns", "raw model requests", "sign-in link"):
        assert thing in lowered, f"the deletion paragraph does not mention {thing!r}"
    assert "not reversible" in lowered or "irreversible" in lowered


def test_the_terms_do_not_claim_the_assistant_advises():
    """ADR-0015: a command interpreter, not a financial adviser.

    The whole product argument is that a number on the screen came from the
    engine. Terms that promised advice would be promising the one thing the
    architecture refuses to do.
    """
    assert "not financial advice" in TERMS.lower()
    for word in ("we recommend", "our advice", "advisory service"):
        assert word not in TERMS.lower(), f"the terms promise {word!r}"


# --- the checklist is honest --------------------------------------------- #


def test_the_checklist_states_its_verdict_and_its_blockers():
    """A checklist that only lists green items is a checklist nobody can act on.

    S3's rule for this track: a gate clause that could not run is a finding,
    not a rounding error. This asserts the page keeps saying so.
    """
    assert "Verdict:" in CHECKLIST
    assert "## Blockers" in CHECKLIST
    assert "Owner" in CHECKLIST


def test_the_checklist_names_the_email_provider_as_open():
    """It is the item that blocks a first external user, so it must not go quiet.

    Delete this test on the day a provider is chosen — and update the
    subprocessor page in the same commit.
    """
    from cashkit_service.mail import ConsoleMailer

    assert ConsoleMailer is not None, "the placeholder mailer is still the default"
    assert "OPEN" in CHECKLIST and "mail" in CHECKLIST.lower()
