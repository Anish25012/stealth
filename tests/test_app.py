"""The desktop app's local server, runtime file, agent and smoke test.

The window and tray need a desktop session, so CI covers them by running `--smoke-test` against
the installed build. These tests cover everything else.
"""

import json
import sys

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from stealth_app import agent, launcher, runtime, smoke
from stealth_app.server import COOKIE, TOKEN_HEADER, create_app, start_server

TOKEN = "t" * 43
PORT = 50123
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def shown():
    return []


@pytest.fixture
def client(shown):
    app = create_app(TOKEN, PORT, on_show=lambda: shown.append(True))
    return TestClient(app, base_url=BASE, follow_redirects=False)


def login(client):
    response = client.get("/auth", params={"t": TOKEN})
    assert response.status_code == 303
    return response


def test_other_hosts_are_refused_even_with_a_valid_session(client):
    login(client)
    assert client.get("/", headers={"Host": "attacker.test"}).status_code == 421
    assert client.get("/", headers={"Host": f"127.0.0.1:{PORT + 1}"}).status_code == 421


def test_pages_need_the_session_cookie(client):
    assert client.get("/").status_code == 401
    assert client.get("/static/app.css").status_code == 401


def test_a_wrong_token_gets_no_cookie(client):
    response = client.get("/auth", params={"t": "wrong"})
    assert response.status_code == 401
    assert COOKIE not in response.cookies


def test_the_token_becomes_a_strict_http_only_cookie(client):
    header = login(client).headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=strict" in header
    page = client.get("/")
    assert page.status_code == 200
    assert "Welcome to Stealth" in page.text


def test_pages_carry_a_strict_content_security_policy(client):
    login(client)
    headers = client.get("/").headers
    policy = headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy
    assert headers["cache-control"] == "no-store"


def test_pages_have_no_inline_styles_or_scripts(client):
    # The policy above would silently block them, which is how the first build lost its styling.
    login(client)
    page = client.get("/").text
    assert "<style" not in page
    assert "<script>" not in page


def test_health_says_nothing_without_the_token(client):
    assert client.get("/health").json() == {"ok": True}
    body = client.get("/health", headers={TOKEN_HEADER: TOKEN}).json()
    assert body["app"] == "stealth"


def test_show_needs_the_token(client, shown):
    assert client.post("/show").status_code == 403
    assert shown == []
    assert client.post("/show", headers={TOKEN_HEADER: TOKEN}).status_code == 200
    assert shown == [True]


def test_show_is_refused_when_there_is_no_window_to_show():
    app = TestClient(create_app(TOKEN, PORT), base_url=BASE)
    assert app.post("/show", headers={TOKEN_HEADER: TOKEN}).status_code == 403


def test_static_files_are_served_with_their_type(client):
    login(client)
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


@pytest.mark.parametrize("name", ["..%2F__init__.py", ".hidden.css", "app.py", "missing.css"])
def test_static_files_are_names_only(client, name):
    login(client)
    assert client.get(f"/static/{name}").status_code == 404


def test_runtime_file_round_trip(tmp_path):
    info = runtime.AgentInfo(port=1234, token="abc", pid=99)
    runtime.write(info, tmp_path)
    assert runtime.read(tmp_path) == info
    assert info.login_url == "http://127.0.0.1:1234/auth?t=abc"
    runtime.remove(info, tmp_path)
    assert runtime.read(tmp_path) is None


def test_an_old_agent_never_deletes_a_newer_agents_file(tmp_path):
    old = runtime.AgentInfo(port=1, token="old", pid=1)
    new = runtime.AgentInfo(port=2, token="new", pid=2)
    runtime.write(new, tmp_path)
    runtime.remove(old, tmp_path)
    assert runtime.read(tmp_path) == new


@pytest.mark.parametrize("content", ["", "not json", "{}", '{"port": "x", "token": 1, "pid": 2}'])
def test_a_damaged_runtime_file_reads_as_no_agent(tmp_path, content):
    runtime.runtime_file(tmp_path).write_text(content)
    assert runtime.read(tmp_path) is None


def test_a_real_server_answers_and_takes_show_requests():
    calls = []
    server = start_server(on_show=lambda: calls.append(1))
    try:
        info = runtime.AgentInfo(port=server.port, token=server.token, pid=0)
        assert runtime.is_alive(info)
        assert not runtime.is_alive(runtime.AgentInfo(port=server.port, token="wrong", pid=0))
        assert runtime.request_show(info)
        assert calls == [1]
    finally:
        server.stop()
    assert not runtime.is_alive(info, timeout=0.5)


def test_running_agent_ignores_a_stale_file(tmp_path):
    server = start_server()
    port = server.port
    server.stop()
    runtime.write(runtime.AgentInfo(port=port, token="gone", pid=0), tmp_path)
    assert runtime.running_agent(tmp_path) is None


def test_self_command_from_source():
    assert runtime.self_command("window") == [sys.executable, "-m", "stealth_app", "window"]


class FakeProcess:
    def __init__(self):
        self.running = True

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.running = False


def test_agent_keeps_one_window_and_closes_it_on_quit(monkeypatch, tmp_path):
    monkeypatch.setenv("STEALTH_HOME", str(tmp_path))
    opened = []
    monkeypatch.setattr(agent, "open_window", lambda: opened.append(FakeProcess()) or opened[-1])
    server = start_server()
    the_agent = agent.Agent(server)
    runtime.write(the_agent.info)
    the_agent.show()
    the_agent.show()
    assert len(opened) == 1
    the_agent.shutdown()
    assert not opened[0].running
    assert runtime.read() is None
    opened[0].running = False
    the_agent.show()
    assert len(opened) == 2


def test_launcher_arguments():
    assert launcher.parse([]).role == "agent"
    assert launcher.parse(["window"]).role == "window"
    args = launcher.parse(["--smoke-test", "--with-window"])
    assert args.smoke_test and args.with_window


def test_smoke_checks_pass_without_a_window(monkeypatch, tmp_path):
    monkeypatch.setenv("STEALTH_HOME", str(tmp_path))
    checks = {c.name: c for c in smoke.run_checks(with_window=False)}
    assert "window" not in checks
    # The tray and keychain depend on the desktop session, so CI's installed-build test owns them.
    for name in ("registry", "database", "demo", "server"):
        assert checks[name].ok, checks[name].detail


def test_smoke_report_is_written_and_failures_set_the_exit_code(monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    monkeypatch.setattr(smoke, "run_checks", lambda with_window: [smoke.Check("x", True)])
    assert smoke.main(with_window=False, report=report) == 0
    assert json.loads(report.read_text())["passed"] is True
    monkeypatch.setattr(
        smoke,
        "run_checks",
        lambda with_window: [smoke.Check("x", True), smoke.Check("y", False, "broke")],
    )
    assert smoke.main(with_window=False, report=report) == 1
    optional = [smoke.Check("y", False, required=False)]
    monkeypatch.setattr(smoke, "run_checks", lambda with_window: optional)
    assert smoke.main(with_window=False, report=report) == 0


def test_a_failing_check_reports_the_error_rather_than_raising():
    def broken():
        raise ValueError("no luck")

    check = smoke._run("broken", broken)
    assert not check.ok
    assert "no luck" in check.detail
