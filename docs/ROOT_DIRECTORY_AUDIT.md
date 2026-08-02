# Project Vuka — Root Directory Audit (Corrected)

Repository: `Zen-sen/Project-Vuka` · Scope: root-level files & dirs · **2026-08-02**

This is a **corrected** version of a previous audit. Original findings that did not
survive verification against source are marked below.

---

## 1. Executive Summary

| Question | Answer |
|----------|--------|
| Live-execution files at root? | No. `.bat` launchers delegate to `src/vuka/`. Root Python files are v4.6 dead code. |
| Root a security risk? | Low direct risk, high confusion risk. 9 duplicate Kronos server copies, ~13.7MB CSVs in Git. |
| Immediate action? | Delete duplicate kronos copies + v4.6 modules + CSVs; purge CSVs from history; move torch/transformers to optional extra. |

## 2. Entry Points (corrected from original)

`pyproject.toml` `[project.scripts]`:

```toml
ingwe = "vuka.core.bot:main"
vuka-dashboard = "vuka.core.dashboard:main"
vuka-supervisor = "vuka.core.supervisor:main"
vuka-kronos = "vuka.ai.kronos_server:main"
```

**Verified status:**

| Script | `main()` present | Entry point OK? |
|--------|------------------|-----------------|
| `ingwe` (`bot.py`) | Yes — `bot.py:1081` top-level | OK |
| `vuka-supervisor` | Yes — `supervisor.py:390` top-level | OK |
| `vuka-dashboard` | **Was missing** — added (now `dashboard.py:396`) | Fixed |
| `vuka-kronos` | **Was missing** — added (now `kronos_server.py:945`) | Fixed |

> Correction to original: the audit claimed `bot.py` and `supervisor.py` had no
> top-level `main()`. **False** — both had top-level functions. Only `dashboard` and
> `kronos_server` were actually broken. Fixed and committed (`a819371`).

## 3. Duplicate Kronos Server Copies

9 tracked copies are **byte-identical** (SHA1 `B4BE5EE84B9E...`):
```
kronos_server.py.bak2
kronos_server.py.before_fix
kronos_server.py.fixed
kronos_server.py.orig
kronos_server.py.patch_me
kronos_server.py.work
kronos_server.py.working
kronos_server.py.backup       (gitignored §`*.backup*`)
kronos_server.py.backup2      (gitignored)
```
The `.backup-<date>` variants (different content) are already gitignored/untracked.

> Original miscounted (said 7) and mislabeled the hash. It's **9** identical copies.

## 4. Confirmed Issues

- **CSVs in Git:** 4 tracked `EURUSDc_*.csv` (~13.7MB). `.gitignore` has NO `*.csv` rule.
- **Duplicate docs:** root `*_v4.6*.md` duplicate `archive/` copies (tracked).
- **Monolithic dead code:** `ingwe.py.test`, `ingwe_v4.6_test.py`, `ingwe_backtest.py` (tracked).
- **Old v4.6 modules:** `health_monitor_v4-6.py`, `state_manager_v4-6.py`,
  `kronos_guardian_v4-6.py`, `kronos_guardian_v4.5.py` (tracked).
- **Root supervisor.py:** tracked, and `supervisor.py:71` runs non-existent `ingwe.py`.
  Canonical = `src/vuka/core/supervisor.py`.
- **Heavy deps in core:** `torch`/`transformers`/`huggingface-hub`/`sentencepiece` are
  in core `dependencies` (pyproject.toml:37-40), not an optional extra.
- **CI syntax check:** `ast.parse` block is superficial (`isinstance(e, SyntaxError)` is
  dead code — ast.parse raises, never returns).
- **conftest.py:** `_mt5 = MagicMock()` with only `account_info` mocked; no
  `history_deals_get`/`positions_get` mocks; permissive MagicMock can hide bugs.

## 5. Non-Issues / Already Handled

- `.gitignore` already ignores `*.bat`, `logs/`, `*.backup*`. So `.bat` files are never
  tracked and concern about an empty `logs/` folder is moot.
- `Kronos` submodule is properly configured (`.gitmodules` → `Zen-sen/Kronos`).

## 6. Remaining Recommended Actions

1. `git rm` the 9 duplicate `kronos_server.py.*` copies.
2. `git rm` duplicate v4.6 module files, root `supervisor.py`, monolithic test files,
   duplicate root `*_v4.6*.md`, root `config_v4.6.json`.
3. Move CSVs to a gitignored path; purge from history with `git filter-repo`/BFG.
4. Add `*.csv` to `.gitignore`.
5. Move `torch` + `transformers` to `[project.optional-dependencies] kronos=[...]`.
6. Add mocks for `history_deals_get`/`positions_get` in `tests/conftest.py`.
7. Replace CI "Check syntax" block with `python -m compileall src/vuka/` or `mypy`.