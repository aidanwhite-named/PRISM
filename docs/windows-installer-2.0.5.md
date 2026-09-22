# Windows installer 2.0.5

The previous releases ran directly from extracted ZIP folders. Version 2.0.5 introduces a per-user Inno Setup installer with a stable AppId and `%LOCALAPPDATA%\Programs\PRISM` default location. Subsequent installers reuse the registered location. The download ZIP contains the same installer EXE, so it cannot accidentally create another portable installation.

The installer preserves edited prompt files (`onlyifdoesntexist`), external PRISM data and the existing Python virtual environment. A first-install migration page can copy prompts from an older ZIP's app directory. It blocks downgrades, checks a launcher mutex, and propagates dependency setup failures. A dependency failure may leave installed application files and partially updated dependencies; it is reported as incomplete and can be retried using `설치.cmd`. This is not a transactional environment rollback.

The generated Inno uninstaller is reachable through Windows Apps, Start menu and `제거.cmd`. Before removing its own tracked files, it invokes `uninstall-cleanup.ps1`. That script removes PRISM data, generated runtime files and CLI packages recorded as newly installed by setup. Existing/shared CLI profiles and untracked CLI installations require separate selections. Node/Python removal also requires a separate selection and an ownership record. PRISM's agy MCP registration is removed without changing other servers or permission rules.

Every recursive deletion is checked against an explicit directory boundary; junctions and symlinks in targets or ancestors abort cleanup. Custom data/profile paths require manual cleanup instead of accepting arbitrary deletion paths. Failures and cancellation stop uninstallation. Downloads, old portable folders, cloud conversations, OS credential stores, shared package caches and WinGet are outside the automatic deletion scope.

## Validation

- Existing setup control-flow tests mock external installers; no user CLI is installed or removed by these tests.
- Cleanup tests use a fake Windows profile and check data deletion, shared-profile opt-in, ownership plans, MCP preservation, custom paths and junction rejection.
- Lifecycle smoke tests compile actual installer executables with a unique test AppId. They exercise first install, update without `/DIR`, edited prompt/history/venv preservation, downgrade refusal, dependency failure and generated uninstallation. Only dependency setup is stubbed.
- Source runtime smoke checks create a fresh venv and launch the real API/UI from the allowlisted archive without invoking AI models.

Compiler behavior follows the [Inno Setup file flags](https://jrsoftware.org/ishelp/topic_filessection.htm), [AppMutex](https://jrsoftware.org/ishelp/topic_setup_appmutex.htm) and [uninstaller events](https://jrsoftware.org/ishelp/topic_scriptevents.htm). The agy directories also match the repository's existing provider implementation and [official configuration documentation](https://antigravity.google/docs/cli-using).
