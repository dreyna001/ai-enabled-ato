# P5 Gate Record (Portal, OIDC, review, approval, ZIP export)

**Gate:** Section 31 P5 — Portal, OIDC, review, approval, ZIP export  
**Outcome:** PASS (code-complete) for API authorization, review/export lifecycle, portal assets, and E2E contracts  
**Recorded:** 2026-07-14  
**Classification:** code-complete (API + static assets) | environment-not-run (Playwright live) | customer-gated (HS-003)

## Automated evidence (PASS code)

| Area | Path |
| --- | --- |
| OIDC/session auth | `src/ato_service/oidc_auth.py`, `session_auth.py`, `auth_router.py` |
| RBAC / object auth | `src/ato_service/package_rbac.py`, `route_role_matrix.py`, `object_authorization.py` |
| Review + export lifecycle | `src/ato_service/review_revisions.py`, `export_service.py` |
| Security matrix tests | `tests/ato_service/test_ep06_security_matrix.py` |
| Review/export tests | `tests/ato_service/test_review_export_lifecycle.py` |
| Portal React app | `portal/` |
| Portal/nginx contracts | `tests/test_portal_contract.py` |
| Playwright asset contracts | `tests/test_e2e_contract.py`, `portal/e2e/` |
| Workflow integration (CI optional) | `tests/ato_service/test_workflow_e2e_integration.py` |

```bash
python -m pytest tests/ato_service/test_ep06_security_matrix.py tests/ato_service/test_review_export_lifecycle.py tests/test_portal_contract.py tests/test_e2e_contract.py -m "not integration" -q
```

## environment-not-run

- Playwright browser suites against managed `e2e-stack-start.sh` stack
- Live customer IdP login and group-mapping drill

## Not claimed

- Production identity deployment (**HS-003**)
- Full EP-06 browser acceptance on customer hosts without live stack execution
