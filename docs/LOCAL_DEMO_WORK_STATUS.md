# Local demo work status

Last updated: 2026-07-23 (evening)

## Active bug / UX thread

Local WSL portal happy path on Agency FISMA revisions. Review and export mostly works; **self-approval UI in single-user demo mode** was the last sticky issue.

## Done in this branch (uncommitted until push)

- Export submit 500: fixed `assert_if_match` and `append_audit_event(occurred_at=...)` in export service
- Review resume: reopen submitted review instead of erroring on second create
- Single-user demo: `/auth/session` exposes `single_user_mode_enabled`; portal shows **Approve export** when flag is true
- WSL storage: upgrade no longer `chown`s bind-mounted `/opt/ato-analyzer/data`; worker crash-loop from permission denied fixed
- Analyzer worker: restore `ato:ato` on `/var/ato-packages` after bad upgrade

## Where you left off

- Revision `bc3c1a03…` (System 1): deterministic run succeeded, review submitted, export draft **pending_approval**
- API/runtime: `SINGLE_USER_MODE_ENABLED=true` on WSL config; backend allows self-approve
- If UI still says "a different approver must approve": **hard refresh** or restart portal dev server (stale Vite HMR can hide the fix)

## Next steps when you return

1. Scroll to **Review and Export** on the bc3c1a03 workflow
2. Click **Approve export** (not Submit for approval again)
3. Click **Download ZIP**
4. If no Approve button: restart portal from WSL (`bash scripts/start-portal.sh`) and reload

## Stack reminders

- API: `http://127.0.0.1:8001` (WSL systemd)
- Portal dev: `http://127.0.0.1:5173` (run from WSL, not Windows npm)
- Upgrade: `sudo bash scripts/upgrade.sh`
- OpenAI standup: `bash scripts/wsl-openai-standup.sh`

## Known non-blockers (ignore for demo)

- `/health/ready` 503 (HS-001 draft manifest)
- Intake panel `context_incomplete` on Ready revisions
- Matrix `insufficient_evidence` on tiny synthetic package
