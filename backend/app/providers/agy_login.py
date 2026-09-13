"""Run agy's own Google OAuth flow in an invisible Windows pseudoconsole.

Only the observed Google OAuth menu and authorization-code prompt are answered.
Codes are forwarded in memory to the CLI, never persisted or returned to the UI.
Onboarding consent and model prompts are never supplied.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

from ..execution import process as proc
from .resolver import ResolvedExecutable


_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


class GoogleLoginMenu:
    def __init__(self) -> None:
        self._tail = ""
        self.selected = False

    def feed(self, output: str) -> bool:
        if self.selected:
            return False
        self._tail = (self._tail + output)[-16384:]
        text = _ANSI.sub("", self._tail)
        # Require the selected option, not just a mention of Google OAuth.
        # A changed CLI menu must never turn an Enter into consent or a prompt.
        if "Select login method:" in text and re.search(
            r">\s*1\.\s*Google OAuth", text
        ):
            self.selected = True
            self._tail = ""
            return True
        return False


class AuthorizationCodePrompt:
    """Detect the CLI's manual OAuth fallback, including split terminal writes."""

    def __init__(self) -> None:
        self._tail = ""

    def feed(self, output: str) -> bool:
        self._tail = (self._tail + output)[-8192:]
        text = _ANSI.sub("", self._tail).lower()
        if (
            "after authenticating, copy the code displayed in the browser and paste it below:" in text
            and "authorization code..." in text
        ):
            self._tail = ""
            return True
        return False


def validate_authorization_code(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2050:
        raise ValueError("Invalid authorization code")
    code = value.strip()
    # Google OAuth codes are data, never terminal keys or slash commands.
    if not re.fullmatch(r"4/[A-Za-z0-9_-]{10,2048}", code):
        raise ValueError("Invalid authorization code")
    return code


class AgyLoginTerminal:
    def __init__(
        self, resolved: ResolvedExecutable, cwd: str, env: dict[str, str],
        args: tuple[str, ...] = ("--sandbox",),
    ):
        try:
            from winpty import Backend, PTY, WinptyError
        except ImportError:
            raise OSError("Missing Windows pseudoconsole dependency") from None
        self._winpty_error = WinptyError
        argv = resolved.command(list(args))
        # Use raw PTY: PtyProcess starts its own socket/thread reader.
        try:
            self._pty = PTY(120, 40, backend=Backend.ConPTY)
            self._pty.spawn(
                argv[0],
                cmdline=" " + subprocess.list2cmdline(argv[1:]),
                cwd=cwd,
                env="\0".join(f"{key}={value}" for key, value in env.items()) + "\0",
            )
        except WinptyError:
            raise OSError("Could not start Windows pseudoconsole") from None
        self.pid = self._pty.pid

    @property
    def returncode(self) -> int | None:
        return None if self._pty.isalive() else self._pty.get_exitstatus()

    def read(self) -> str:
        try:
            return self._pty.read(blocking=False)
        except EOFError:
            return ""
        except self._winpty_error:
            if self.returncode is not None:
                return ""
            raise OSError("Could not read Windows pseudoconsole") from None

    def select_google(self) -> None:
        try:
            self._pty.write("\r")
        except self._winpty_error:
            raise OSError("Could not select Google login") from None

    def submit_authorization_code(self, value: str) -> None:
        code = validate_authorization_code(value)
        try:
            self._pty.write("\x1b[200~" + code + "\x1b[201~")
            self._pty.write("\r")
        except self._winpty_error:
            raise OSError("Could not submit authorization code") from None


def model_rows(output: str) -> str:
    """Normalize observed 1.0.12 console rows, excluding spinners/errors."""
    rows = []
    for line in _ANSI.sub("", output).splitlines():
        match = re.fullmatch(r"([a-z0-9][a-z0-9._-]*)[ \t]{2,}([^\r\n]+)", line.strip())
        if match:
            rows.append(f"{match[1]}\t{match[2]}")
    return "\n".join(rows)


async def run_agy_models(
    resolved: ResolvedExecutable, *, cwd: Path, env: dict[str, str],
    timeout_seconds: int = 15,
) -> proc.ProcessResult:
    result = await proc.run_capture(
        resolved.command(["models"]), cwd=cwd, env=env,
        timeout_seconds=timeout_seconds,
    )
    if sys.platform != "win32" or result.exit_code != 0 or result.stdout.strip():
        return result
    # agy 1.0.12 writes the list to its console, leaving redirected stdout empty.
    # Run the same read-only command in ConPTY; never infer auth from exit 0 alone.
    terminal = AgyLoginTerminal(resolved, str(cwd), {**env, "TERM": "xterm-256color"}, ("models",))
    output = ""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    try:
        while True:
            output = (output + terminal.read())[-65536:]
            if terminal.returncode is not None:
                result.exit_code = terminal.returncode
                result.stdout = model_rows(output)
                return result
            if asyncio.get_running_loop().time() >= deadline:
                result.timed_out = True
                result.exit_code = None
                return result
            await asyncio.sleep(0.1)
    finally:
        if terminal.returncode is None:
            await asyncio.to_thread(proc.kill_process_tree, terminal.pid)
