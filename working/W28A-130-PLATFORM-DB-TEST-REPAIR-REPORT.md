# W28A-130 — platform-db Test Suite Repair Report

Date (UTC): 2026-03-11  
Project: `packages/backend/platform-db`

## 1) Root cause

`pytest` rc=3 across UT/ST/IT/AT was caused by a conftest configuration bug:

- `tests/conftest.py` called `config.getoption("--env")` in `pytest_configure`.
- `--env` was never registered via `pytest_addoption`.
- This raised `ValueError: no option named '--env'` as an INTERNALERROR, so suites aborted before collection/execution.

Evidence:
- `working/w28a-130-collect.log` (initial failure signature before fix)

## 2) Fix applied

1. Added missing `pytest_addoption` registration for `--env` in:
- `tests/conftest.py`

2. Resolved Ruff errors found during instruction step by removing unused imports in:
- `tests/unit/UT1.5_PlatformBase/test_platform_base.py`

No test logic/assertions were weakened.

## 3) Test results (REAL counts)

- QT: **MISSING** (no `tests/security` suite in this package)
  - log: `working/w28a-130-qt.log`
- UT: **18 passed, 0 failed, 0 skipped**
  - log: `working/w28a-130-ut.log`
- ST: **1 passed, 0 failed, 0 skipped**
  - log: `working/w28a-130-st.log`
- IT: **1 passed, 0 failed, 2 skipped**
  - sqlite env run; mariadb/postgres tests are dialect-gated skips in sqlite mode
  - log: `working/w28a-130-it.log`
- AT: **1 passed, 0 failed, 0 skipped**
  - log: `working/w28a-130-at.log`
- Collection sanity after fix: **23 tests collected**
  - log: `working/w28a-130-collect.log`

## 4) Ruff

Instruction-specified command `ruff check src/` fails in this package because `src/` path does not exist.

- `ruff check src/` -> E902 path error (captured)
- Fallback run used package paths:
  - `ruff check cloud_dog_db tests` -> pass

Log:
- `working/w28a-130-ruff.log`

## 5) Verdict

**PASS**

The rc=3 root cause is repaired; suites now collect and execute with real results.

## 6) Evidence logs

- `working/w28a-130-collect.log`
- `working/w28a-130-qt.log`
- `working/w28a-130-ut.log`
- `working/w28a-130-st.log`
- `working/w28a-130-it.log`
- `working/w28a-130-at.log`
- `working/w28a-130-ruff.log`

I warrant that:
1. I have read RULES.md IN FULL before starting work
2. ALL code I produced is 100% compliant with RULES.md
3. ALL test results reported are REAL — exact counts from actual runs
4. I have NOT weakened any test
5. I have NOT stored, copied, or exposed any credentials
6. ALL credentials come from Vault or git-ignored env files
7. I have NOT modified files outside this package
