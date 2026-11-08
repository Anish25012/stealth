import ast
import csv
import email
import email.policy
import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stealth.core import paths
from stealth.core.errors import MailAuthError
from stealth.core.secrets import MemoryStore
from stealth.demo import DEMO_BROKERS, run_demo
from stealth.devmail.servers import LocalImap, LocalSmtp
from stealth.registry import loader
from stealth_cli import main

runner = CliRunner()

ANSWERS = "\n".join(
    [
        "Test Person",  # name
        "",  # no other names
        "test.person@example.org",
        "",
        "1 Example Road, SW1A 1AA",
        "",
        "07700 900123",
        "",
        "n",  # not for someone else
        "",  # send-from default
        "127.0.0.1",
        "1",
        "127.0.0.1",
        "1",
        "app-password",
        "",  # 60
        "",  # 90
        "0",  # no delay
        "",  # 40
        "",  # skip none
    ]
)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    secrets = MemoryStore()
    monkeypatch.setattr(main, "_secrets", lambda: secrets)
    # Setup proves the account works before saving. The fake servers in these answers don't
    # exist, so the check is replaced; the tests below that need it record what it was given.
    monkeypatch.setattr(main, "check_connection", lambda account, password: None)
    return tmp_path


@pytest.fixture
def ready(cli):
    result = runner.invoke(main.app, ["init"], input=ANSWERS + "\n")
    assert result.exit_code == 0, result.output
    return cli


def run(*args, **kwargs):
    return runner.invoke(main.app, list(args), **kwargs)


def test_init_creates_a_profile_and_shows_the_recovery_code(ready):
    assert run("status").exit_code == 0
    assert list((ready / "profiles").iterdir())


def test_init_stores_no_password_on_disk(ready):
    for path in ready.rglob("*"):
        if path.is_file():
            assert b"app-password" not in path.read_bytes()


def test_dry_run_writes_one_valid_eml_per_email_broker(ready):
    """Acceptance check: uses the real shipped broker list."""
    result = run("send")
    assert "Nothing was sent" in result.output
    outbox = next((ready / "profiles").iterdir()) / "outbox"
    files = sorted(outbox.glob("*.eml"))
    from stealth.core.jurisdictions import UK

    numbers = loader.counts(loader.load_registry(), UK)
    assert len(files) == numbers["default_email"]
    for path in files:
        parsed = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        assert parsed["To"] and parsed["From"] and parsed["Subject"] and parsed["Message-ID"]
        assert "Article 17" in parsed.get_body().get_content()


def test_live_send_requires_confirmation(ready):
    result = run("send", "--live", input="n\n")
    assert result.exit_code == 1
    assert "Send these now?" in result.output
    assert "does-not-matter" not in result.output


def test_live_send_with_bad_connection_gives_a_plain_message(ready):
    result = run("send", "--live", "--yes")
    assert result.exit_code == 1
    assert "couldn't reach" in result.output
    assert "Traceback" not in result.output


def _point_at(cli_home, smtp, imap):
    """Redirect the saved mail account at local servers."""
    from stealth.profiles.store import ProfileStore

    store = ProfileStore(main._secrets(), home=cli_home)
    with store.open() as profile:
        account = profile.mail_account()
        account.smtp_port, account.imap_port = smtp.port, imap.port
        account.smtp_security = account.imap_security = "none"
        profile.save_mail_account(account)
        profile.save_settings(send_delay_seconds=0, daily_send_limit=1000)


def test_live_send_reaches_a_local_server_for_every_email_broker(ready, monkeypatch):
    """Acceptance check: the real registry through the real SMTP code path."""
    from stealth.core.jurisdictions import UK

    numbers = loader.counts(loader.load_registry(), UK)
    with LocalSmtp() as smtp, LocalImap("app-password") as imap:
        _point_at(ready, smtp, imap)
        result = run("send", "--live", "--yes")
    assert result.exit_code == 0, result.output
    assert f"Sent {numbers['default_email']}." in result.output
    assert len(smtp.delivered) == numbers["default_email"]
    assert len({d.recipients[0] for d in smtp.delivered}) == numbers["default_email"]


def test_cycle_live_and_status_csv(ready):
    with LocalSmtp() as smtp, LocalImap("app-password") as imap:
        _point_at(ready, smtp, imap)
        assert "Requests sent" in run("cycle", "--live").output
        assert "Replies: 0" in run("cycle", "--live").output
        assert "0 replies" in run("inbox").output
    result = run("status", "--csv")
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert rows
    assert {"broker", "status", "deadline"} <= set(rows[0])
    assert any(r["status"] == "sent" for r in rows)
    assert "Broker list:" in run("status").output


def test_cycle_preview_needs_no_mail_server(ready):
    assert "Nothing was sent" in run("cycle").output


def test_confirm_and_ico_explain_problems(ready):
    with LocalSmtp() as smtp, LocalImap("app-password") as imap:
        _point_at(ready, smtp, imap)
        run("send", "--live", "--yes")
    assert "removed" in run("confirm", "experian-uk", "removed").output
    result = run("ico", "equifax-uk")
    assert result.exit_code == 1
    assert "isn't ready" in result.output
    assert run("confirm", "nobody", "removed").exit_code == 1


def test_refused_case_can_be_complained_about(ready):
    with LocalSmtp() as smtp, LocalImap("app-password") as imap:
        _point_at(ready, smtp, imap)
        run("send", "--live", "--yes")
        run("confirm", "captify", "refused")
        result = run("complain", "captify")
    assert "Complaint sent" in result.output


def test_forms_list_and_done(ready):
    output = run("forms").output
    assert "192-com" in output
    assert "192.com/c01/new-request" in output
    with LocalSmtp() as smtp, LocalImap("app-password") as imap:
        _point_at(ready, smtp, imap)
        run("send", "--live", "--yes")
    assert "clock started" in run("forms", "--done", "192-com").output
    assert "192-com:" not in run("forms").output


def test_checklist(ready):
    output = run("checklist").output
    for phrase in ("open electoral register", "Telephone Preference", "Mailing Preference"):
        assert phrase in output
    assert "Results about you" in output
    assert "[x] Join the Telephone" in run("checklist", "--done", "tps").output
    assert run("checklist", "--done", "nothing").exit_code == 1


def test_brokers_count_is_honest(cli):
    output = run("brokers").output
    numbers = loader.counts(
        loader.load_registry(), __import__("stealth.core.jurisdictions").core.jurisdictions.UK
    )
    assert str(numbers["default"]) in output
    assert run("brokers", "--country", "ZZ").exit_code == 1


def test_no_profile_is_explained(cli):
    result = run("status")
    assert result.exit_code == 1
    assert "stealth init" in result.output


def test_recover_restores_keychain(ready, monkeypatch):
    output = runner.invoke(main.app, ["init"], input=ANSWERS + "\n").output
    code = next(line.split(": ")[1] for line in output.splitlines() if "recovery code:" in line)
    profile_id = next(
        line.split(": ")[1] for line in output.splitlines() if "Profile created" in line
    )
    main._secrets().delete(f"db-key:{profile_id}")
    assert run("recover", profile_id, code).exit_code == 0
    assert run("status", "--profile-id", profile_id).exit_code == 0


def test_unexpected_errors_never_show_a_traceback(ready, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("secret details")

    monkeypatch.setattr(main, "load_registry", boom)
    result = run("status")
    assert result.exit_code == 2
    assert "Something went wrong" in result.output
    assert "secret details" not in result.output


def test_known_errors_show_their_hint(ready, monkeypatch):
    def denied(*_a, **_k):
        raise MailAuthError("Rejected.", hint="Try an app password.")

    monkeypatch.setattr(main, "open_mail", denied)
    result = run("inbox")
    assert "Rejected." in result.output
    assert "Try an app password." in result.output


def test_version_flag(cli):
    assert run("--version").exit_code == 0


def test_demo_runs_start_to_finish(cli):
    result = run("demo")
    assert result.exit_code == 0, result.output
    assert "Demo finished" in result.output


def test_demo_outcome():
    lines = []
    result = run_demo(lines.append)
    assert result.chain_ok
    assert result.statuses == {
        "dead-address": "bounced",
        "find-a-neighbour": "sent",
        "form-only": "needs_you",
        "prompt-data": "sent",
        "refusing-corp": "ico_ready",
        "silent-lists": "ico_ready",
    }
    assert "Refusing Corp" in result.ico_draft


def test_demo_only_uses_test_addresses():
    assert all(b.email is None or b.email.endswith(".test") for b in DEMO_BROKERS.values())


def test_engine_never_imports_a_front_end():
    root = Path(main.__file__).parent.parent / "stealth"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(("stealth_cli", "stealth_app", "typer")), path


def test_engine_reads_time_only_through_the_clock():
    root = Path(main.__file__).parent.parent / "stealth"
    for path in root.rglob("*.py"):
        if path.name == "clock.py":
            continue
        text = path.read_text("utf-8")
        assert "datetime.now" not in text, path
        assert "date.today" not in text, path


def _extra_broker(home, **changes):
    entry = {
        "id": "me-test",
        "name": "My Test Inbox",
        "domain": "example.org",
        "region": "UK",
        "category": "other",
        "method": "email",
        "email": "someone@example.org",
        "form_url": None,
        "notes": "",
        "source": "me",
        "last_verified": "2026-11-01",
        "laws": ["uk-gdpr"],
        "tier": "core",
        "confidence": 1,
    }
    (home / "brokers_extra.json").write_text(
        json.dumps({"version": 1, "brokers": [{**entry, **changes}]})
    )


def test_only_sends_to_one_broker_and_touches_nothing_else(ready):
    """The manual own-inbox test depends on this: it must never reach a real company."""
    _extra_broker(ready)
    with LocalSmtp() as smtp, LocalImap("app-password") as imap:
        _point_at(ready, smtp, imap)
        result = run("send", "--live", "--yes", "--only", "me-test")
    assert result.exit_code == 0, result.output
    assert [d.recipients[0] for d in smtp.delivered] == ["someone@example.org"]
    rows = list(csv.DictReader(io.StringIO(run("status", "--csv").output)))
    assert [r["broker"] for r in rows] == ["My Test Inbox"]


def test_only_previews_one_letter(ready):
    _extra_broker(ready)
    assert "1 letters written" in run("send", "--only", "me-test").output


def test_only_explains_a_broker_that_would_send_nothing(ready):
    _extra_broker(ready, laws=["ccpa"])
    result = run("send", "--live", "--yes", "--only", "me-test")
    assert result.exit_code == 1
    assert "isn't on your list" in result.output
    assert run("send", "--only", "nobody").exit_code == 1


# Setup catches mistakes before saving -----------------------------------------------------------


def _answers(**changes):
    """ANSWERS with some lines changed: i9="x" replaces the tenth answer."""
    lines = ANSWERS.split("\n")
    for index, value in changes.items():
        lines[int(index[1:])] = value
    return "\n".join(lines) + "\n"


def test_init_refuses_a_send_from_that_isnt_an_email_address(cli):
    # The real slip that prompted this: a name typed where the address goes.
    answers = _answers(i9="Risha0407\nme@example.org")
    result = runner.invoke(main.app, ["init"], input=answers)
    assert result.exit_code == 0, result.output
    assert "'Risha0407' isn't an email address" in result.output
    with main._profile(None) as profile:
        assert profile.mail_account().address == "me@example.org"


def test_init_checks_the_password_and_lets_you_try_again(cli, monkeypatch):
    tried = []

    def check(account, password):
        tried.append(password)
        if password != "right":
            raise MailAuthError("Your email provider rejected the sign-in.", hint="App password.")

    monkeypatch.setattr(main, "check_connection", check)
    answers = _answers(i14="wrong\nn\nright")
    result = runner.invoke(main.app, ["init"], input=answers)
    assert result.exit_code == 0, result.output
    assert tried == ["wrong", "right"]
    assert "rejected the sign-in" in result.output
    assert "It works." in result.output
    with main._profile(None) as profile:
        assert main._secrets().get(profile.mail_secret_name) == "right"


def test_a_gmail_app_password_must_be_sixteen_letters(cli):
    answers = _answers(
        i10="smtp.gmail.com",
        i11="587",
        i12="imap.gmail.com",
        i13="993",
        i14="short\nabcd efgh ijkl mnop",
    )
    result = runner.invoke(main.app, ["init"], input=answers)
    assert result.exit_code == 0, result.output
    assert "That's 5 characters, but a Gmail app password is 16 letters" in result.output
    with main._profile(None) as profile:
        assert main._secrets().get(profile.mail_secret_name) == "abcdefghijklmnop"


def test_password_replaces_the_app_password_and_can_fix_the_address(ready):
    result = run("password", "--address", "new@example.org", input="new-password\n")
    assert result.exit_code == 0, result.output
    with main._profile(None) as profile:
        assert profile.mail_account().address == "new@example.org"
        assert profile.mail_account().username == "new@example.org"
        assert main._secrets().get(profile.mail_secret_name) == "new-password"
    assert run("password", "--address", "nope", input="x\n").exit_code == 1


def test_inbox_counts_replies_it_settled_by_itself(ready, monkeypatch):
    """The real Gmail test: a clear "we've removed it" closed the case, yet it said 0 replies."""
    from stealth.inbox import processor

    def fake_process(profile, mailbox, brokers):
        return processor.InboxReport(
            resolved=[("My Test Inbox", "They've deleted your data")],
            acknowledged=["Acme Data"],
            replies=["Vague Ltd"],
        )

    monkeypatch.setattr(main, "open_mail", lambda profile: (None, None))
    monkeypatch.setattr(processor, "process_inbox", fake_process)
    output = run("inbox").output
    assert "3 replies, 0 bounces." in output
    assert "My Test Inbox: They've deleted your data. Recorded." in output
    assert "Acme Data: acknowledged your request" in output
    assert "Reply from Vague Ltd: read it" in output


# Google sign-in from the command line -----------------------------------------------------------


@pytest.fixture
def google(cli, monkeypatch):
    """Google sign-in offered, with the browser round trip replaced."""
    from stealth.mail import google_oauth

    monkeypatch.setattr(google_oauth, "client", lambda: google_oauth.Client("id", "secret"))
    calls = []

    def sign_in(hint=""):
        calls.append(hint)
        return "signed.in@gmail.com", "refresh-token"

    monkeypatch.setattr(main, "_google_sign_in", sign_in)
    return calls


def test_init_can_sign_in_with_google_instead_of_an_app_password(google):
    lines = ANSWERS.split("\n")
    # Up to "not for someone else", then yes to Google, then the sending preferences.
    answers = [*lines[:9], "y", *lines[15:]]
    result = runner.invoke(main.app, ["init"], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    assert google == ["test.person@example.org"]
    assert "SMTP server" not in result.output
    with main._profile(None) as profile:
        account = profile.mail_account()
        assert account.provider == "gmail-oauth"
        assert account.address == "signed.in@gmail.com"
        assert account.smtp_host is None
        assert main._secrets().get(profile.mail_secret_name) == "refresh-token"


def test_init_without_google_still_asks_for_an_app_password(google):
    lines = ANSWERS.split("\n")
    answers = [*lines[:9], "n", *lines[9:]]
    result = runner.invoke(main.app, ["init"], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    assert google == []
    with main._profile(None) as profile:
        assert profile.mail_account().provider == "smtp"


def test_stealth_google_switches_an_app_password_profile_over(ready, google):
    result = run("google")
    assert result.exit_code == 0, result.output
    with main._profile(None) as profile:
        assert profile.mail_account().provider == "gmail-oauth"
        assert main._secrets().get(profile.mail_secret_name) == "refresh-token"
    assert google == ["test.person@example.org"]
    refused = run("password", input="x\n")
    assert refused.exit_code == 1
    assert "signs in with Google" in refused.output
