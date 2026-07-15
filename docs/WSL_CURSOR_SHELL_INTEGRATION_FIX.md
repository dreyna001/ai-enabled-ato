# WSL Cursor Shell Integration Fix

Applied: 2026-07-15

## Problem

When opening an integrated terminal in Cursor on WSL2, bash failed to load shell integration with:

```text
. "\home\dreyna\.cursor-server\bin\...\shellIntegration-bash.sh"
bash: ... No such file or directory
```

Cursor injected a **Windows-style path** (`\home\...`) instead of the correct Linux path (`/home/...`). The script exists on disk; only the path format was wrong.

## Fix

Added a small fallback block at the end of `~/.bashrc` that:

1. Detects an interactive Cursor/VS Code terminal (`VSCODE_IPC_HOOK_CLI` or `TERM_PROGRAM=vscode`)
2. Skips if shell integration is already loaded (`VSCODE_SHELL_INTEGRATION`)
3. Finds the latest `shellIntegration-bash.sh` under `~/.cursor-server/bin`
4. Sources it with the correct Linux path

Cursor may still print the one-time bad-path error at terminal startup. Integration should load immediately after via the fallback.

## Files changed

| File | Change |
|------|--------|
| `~/.bashrc` | Added shell integration fallback block |
| `~/.bashrc.backup-20260715-222145` | Pre-fix backup |
| `~/.local/bin/revert-cursor-shell-integration-fix.sh` | One-command revert script |

## Verify

Open a **new** integrated terminal in Cursor, then run:

```bash
echo "VSCODE_SHELL_INTEGRATION=${VSCODE_SHELL_INTEGRATION:-unset}"
type __vsc_prompt_cmd
```

Expected: `VSCODE_SHELL_INTEGRATION=1` and `__vsc_prompt_cmd is a function`.

## Revert

```bash
~/.local/bin/revert-cursor-shell-integration-fix.sh
```

Or manually restore the backup:

```bash
cp ~/.bashrc.backup-20260715-222145 ~/.bashrc
```

Open a new terminal after reverting.
