from __future__ import annotations

import asyncio

import pytest

from app.enums import AuthState
from app.execution.process import ProcessResult
from app.providers import login as login_module
from app.providers.agy_login import (
    AgyLoginTerminal, AuthorizationCodePrompt, GoogleLoginMenu,
    model_rows, validate_authorization_code,
)
from app.providers.base import ProbeResult
from app.providers.login import LoginError, ProviderLoginManager
from app.providers.resolver import ExecutableKind, ResolvedExecutable


def test_google_menu_handles_split_ansi_and_never_answers_twice():
    menu = GoogleLoginMenu()
    assert not menu.feed("Select login method:\r\n\x1b[32m> 1. Goo")
    assert menu.feed("gle OAuth\x1b[0m\r\n2. Use a Google Cloud project")
    assert not menu.feed("Select login method: > 1. Google OAuth")
    assert not menu.feed("Accept terms? Press Enter")


CODE_PROMPT = (
    "After authenticating, copy the code displayed in the browser and paste it below:\r\n"
    "authorization code..."
)
FAKE_CODE = "4/prism-fake-code-for-tests"


def test_authorization_code_prompt_detects_split_ansi_output():
    prompt = AuthorizationCodePrompt()
    assert not prompt.feed("Open the URL below in your browser.")
    assert not prompt.feed(CODE_PROMPT[:-8])
    assert prompt.feed("\x1b[32m" + CODE_PROMPT[-8:] + "\x1b[0m")
    assert not prompt.feed("Signing in...")


@pytest.mark.parametrize("value", [None, {}, "", "/logout", "4/abc\r/logout", "4/abc\x1b[A", "4/" + "a" * 2050])
def test_code_validation_rejects_commands_and_terminal_controls(value):
    with pytest.raises(ValueError):
        validate_authorization_code(value)


def test_code_is_sent_as_one_bracketed_paste_then_enter():
    writes = []
    terminal = AgyLoginTerminal.__new__(AgyLoginTerminal)
    terminal._pty = type("PTY", (), {"write": lambda self, value: writes.append(value)})()
    terminal._winpty_error = OSError
    terminal.submit_authorization_code(FAKE_CODE)
    assert writes == ["\x1b[200~" + FAKE_CODE + "\x1b[201~", "\r"]


@pytest.mark.parametrize("output", [
    "Select login method: 1. Google OAuth > 2. Use a Google Cloud project",
    "Google OAuth succeeded. Press Enter to accept terms.",
    "Choose theme: > 1. Dark",
    "Select login method: > 1. Enterprise login",
])
def test_unrecognized_screens_never_receive_enter(output):
    assert not GoogleLoginMenu().feed(output)


def test_console_model_rows_exclude_progress_and_errors():
    assert model_rows(
        "\rFetching available models...\r\n"
        "\x1b[32mgemini-3.8-flash-high     Gemini 3.8 Flash (High)\x1b[0m\r\r\n"
        "claude-sonnet-4-6         Claude Sonnet 4.6 (Thinking)\r\n"
        "Error: You are not logged in."
    ) == (
        "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)"
    )
    assert model_rows("\rFetching available models...\rNo models available") == ""


@pytest.mark.parametrize("mode", ["pipe", "legacy", "empty", "cancel"])
async def test_models_fallback_requires_real_rows_and_cleans_up(mode, monkeypatch, tmp_path):
    from app.providers import agy_login
    created = asyncio.Event()
    terminal = None

    async def capture(*args, **kwargs):
        return ProcessResult(exit_code=0, stdout="gemini\tGemini" if mode == "pipe" else "")

    class Terminal:
        pid = 111111
        returncode = None

        def __init__(self, resolved, cwd, env, args):
            nonlocal terminal
            assert mode != "pipe"
            assert args == ("models",)
            terminal = self
            created.set()

        def read(self):
            if mode == "cancel":
                return ""
            self.returncode = 0
            return "gemini-3.8-flash-high  Gemini 3.8 Flash (High)" if mode == "legacy" else "Fetching available models..."

    def kill(pid):
        assert pid == Terminal.pid
        terminal.returncode = 1

    monkeypatch.setattr(agy_login.sys, "platform", "win32")
    monkeypatch.setattr(agy_login.proc, "run_capture", capture)
    monkeypatch.setattr(agy_login.proc, "kill_process_tree", kill)
    monkeypatch.setattr(agy_login, "AgyLoginTerminal", Terminal)
    task = asyncio.create_task(agy_login.run_agy_models(
        ResolvedExecutable("agy.exe", ExecutableKind.NATIVE_EXE), cwd=tmp_path, env={},
    ))
    if mode == "cancel":
        await created.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert terminal.returncode == 1
    else:
        result = await task
        assert bool(result.stdout) == (mode != "empty")


@pytest.mark.parametrize("outcome", ["success", "code", "code_rejected", "cancel", "timeout", "exit", "error"])
async def test_browser_login_lifecycle(outcome, monkeypatch, tmp_path):
    terminal_created = asyncio.Event()
    selected = asyncio.Event()
    submitted = asyncio.Event()
    calls = []

    class Terminal:
        pid = 123456
        returncode = None

        def __init__(self, resolved, cwd, env):
            assert resolved.path == str(tmp_path / "agy.exe")
            assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
            assert env["TERM"] == "xterm-256color"
            terminal_created.set()

        def read(self):
            if outcome == "code_rejected" and submitted.is_set():
                return "OAuth error: invalid_grant (test)"
            if outcome in {"code", "code_rejected"} and selected.is_set():
                return CODE_PROMPT
            if outcome == "error":
                raise RuntimeError("sensitive-cli-output")
            if outcome == "exit":
                self.returncode = 1
            return "Select login method:\n> 1. Google OAuth"

        def select_google(self):
            assert not selected.is_set()
            selected.set()

        def submit_authorization_code(self, code):
            assert code == FAKE_CODE
            assert not submitted.is_set()
            submitted.set()

    terminal = None

    def create_terminal(*args):
        nonlocal terminal
        terminal = Terminal(*args)
        return terminal

    def kill(pid):
        assert pid == Terminal.pid
        calls.append("kill")
        terminal.returncode = 0

    async def capture(resolved, **kwargs):
        assert resolved.path == str(tmp_path / "agy.exe")
        assert kwargs["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
        assert selected.is_set()
        if outcome == "code":
            assert submitted.is_set()
        return ProcessResult(exit_code=0, stdout="model\tModel")

    async def probe(provider, overrides=None):
        return ProbeResult(
            provider=provider, display_name="agy", installed=True, executable_ok=True,
            auth_state=AuthState.OK if outcome in {"success", "code"} else AuthState.NOT_LOGGED_IN,
        )

    manager = ProviderLoginManager()
    monkeypatch.setattr(login_module.sys, "platform", "win32")
    monkeypatch.setattr(manager, "_login_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "_resolve", lambda *_: ResolvedExecutable(
        str(tmp_path / "agy.exe"), ExecutableKind.NATIVE_EXE,
    ))
    monkeypatch.setattr(login_module, "AgyLoginTerminal", create_terminal)
    monkeypatch.setattr(login_module, "kill_process_tree", kill)
    monkeypatch.setattr(login_module, "run_agy_models", capture)
    monkeypatch.setattr(login_module, "probe_one", probe)
    if outcome == "timeout":
        monkeypatch.setattr(login_module, "_LOGIN_TIMEOUT_SECONDS", 0.05)

    started = await manager.start("agy")
    assert started["mode"] == "browser"
    await terminal_created.wait()
    if outcome in {"code", "code_rejected"}:
        for _ in range(20):
            state = await manager.get("agy", started["session_id"])
            if state["needs_authorization_code"]:
                break
            await asyncio.sleep(0.02)
        result = await manager.submit_authorization_code("agy", started["session_id"], FAKE_CODE)
        assert result["state"] == "WAITING_FOR_USER"
        assert not result["needs_authorization_code"]
        assert FAKE_CODE not in str(result)
        await asyncio.sleep(0.2)  # The CLI may redraw its old prompt while processing.
        with pytest.raises(LoginError):
            await manager.submit_authorization_code("agy", started["session_id"], FAKE_CODE)
    if outcome == "cancel":
        await manager.cancel("agy", started["session_id"])
    await asyncio.wait_for(manager._sessions[started["session_id"]].task, timeout=5)
    finished = await manager.get("agy", started["session_id"])
    expected = "SUCCEEDED" if outcome in {"success", "code"} else "CANCELLED" if outcome == "cancel" else "FAILED"
    assert finished["state"] == expected
    assert not finished["can_cancel"]
    assert "sensitive-cli-output" not in str(finished)
    if outcome != "exit":
        assert calls == ["kill"]
    assert manager._sessions[started["session_id"]].process is None


async def test_cancelling_auth_probe_reaps_its_process(monkeypatch):
    from app.execution import process as proc

    started = asyncio.Event()
    killed = []

    class Process:
        pid = 654321

        async def communicate(self, data):
            started.set()
            await asyncio.Event().wait()

        async def wait(self):
            assert killed == [self.pid]
            killed.append("reaped")
            return 0

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(proc.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(proc, "kill_process_tree", lambda pid: killed.append(pid))
    task = asyncio.create_task(proc.run_capture(["agy", "models"]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed == [Process.pid, "reaped"]
