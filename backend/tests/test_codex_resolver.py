"""Windows에서 Node를 찾지 못해도 설치된 Codex를 직접 실행한다."""
import pytest

from app.providers import resolver


@pytest.mark.parametrize("layout", ["nested", "hoisted", "bundled"])
@pytest.mark.parametrize("machine,arch,triple", [
    ("AMD64", "x64", "x86_64-pc-windows-msvc"),
    ("ARM64", "arm64", "aarch64-pc-windows-msvc"),
])
def test_native_codex_without_node(tmp_path, monkeypatch, layout, machine, arch, triple):
    root = tmp_path / "node_modules"
    package = root / "@openai" / "codex"
    vendor = {
        "nested": package / "node_modules" / "@openai" / f"codex-win32-{arch}" / "vendor",
        "hoisted": root / "@openai" / f"codex-win32-{arch}" / "vendor",
        "bundled": package / "vendor",
    }[layout]
    exe = vendor / triple / "bin" / "codex.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(resolver.sys, "platform", "win32")
    monkeypatch.setattr(resolver.platform, "machine", lambda: machine)
    monkeypatch.setattr(resolver, "_npm_roots", lambda: [root])
    monkeypatch.setattr(resolver.shutil, "which", lambda _: None)
    result = resolver.resolve_simple("codex")
    assert result.path == str(exe)
    assert result.command(["--version"]) == [str(exe), "--version"]
    assert result.kind == resolver.ExecutableKind.NATIVE_EXE


def test_explicit_override_and_native_path_take_priority(tmp_path, monkeypatch):
    exe = tmp_path / "codex.exe"
    exe.touch()
    direct = resolver.ResolvedExecutable(str(exe), resolver.ExecutableKind.NATIVE_EXE)
    monkeypatch.setattr(resolver, "_from_path_env", lambda _: direct)
    monkeypatch.setattr(resolver, "_codex_npm_native", lambda: pytest.fail("unexpected npm lookup"))
    assert resolver.resolve_simple("codex", str(exe)).source == "사용자 지정"
    assert resolver.resolve_simple("codex") is direct


def test_wrapper_remains_fallback(monkeypatch):
    wrapper = resolver.ResolvedExecutable("codex.cmd", resolver.ExecutableKind.CMD_WRAPPER)
    monkeypatch.setattr(resolver, "_from_path_env", lambda _: None)
    monkeypatch.setattr(resolver, "_codex_npm_native", lambda: None)
    monkeypatch.setattr(resolver, "_cmd_wrapper_from_path", lambda _: wrapper)
    assert resolver.resolve_simple("codex") is wrapper
