# Claude Executable Lookup — Design Spec

**Status:** Approved 2026-10-02
**Date:** 2026-10-02
**Author:** Joseph Hoppe (drafted with Claude Code)
**Parent:** [`SPEC.md`](../../SPEC.md)
**Follows:** [`2026-10-01-refresh-failure-notice-design.md`](../ui/app/2026-10-01-refresh-failure-notice-design.md)
§2 ("Finding `claude` outside `PATH` … a separate, follow-up change") and §10
("A wrong `claude_executable` reads as 'exited with an error'")

When the app cannot find `claude` on the `PATH` it inherited, the Refresh
button fails with `NOT_FOUND`. This spec adds one fallback location — the
native installer's launcher folder — and makes a bad `claude_executable`
report `NOT_FOUND` instead of `FAILED`.

---

## 1. Problem

A user launched the app from outside a terminal. It inherited launchd's bare
`PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`), which does not contain the native
install at `~/.local/bin/claude`. Every click resolved no executable and
returned `RefreshOutcome.NOT_FOUND`. The refresh-failure notice now makes
that visible, but the fix still requires the user to set `claude_executable`
in `config.toml` by hand.

Separately, when `claude_executable` is set to a path that does not exist,
the refresher spawns it anyway. The spawn raises `FileNotFoundError`, which
maps to `FAILED`, and the window says `Refresh failed: claude exited with an
error` — a description of a different problem.

## 2. Scope

### In scope

- Platforms: macOS and Windows (the CI matrix).
- One fallback location: the native installer's launcher folder,
  `<home>/.local/bin`, checked for `claude` then `claude.exe`.
- A configured `claude_executable` that is missing or not executable returns
  `NOT_FOUND` with its own log entry, and does not fall back.
- Updating the README and marking refresh-failure §10 as resolved.

### Out of scope

- Other install methods: npm global, Homebrew, WinGet, `~/.claude/local`.
- Asking the login shell (`$SHELL -lc 'command -v claude'`).
- Changing the app's process-wide `PATH`, or the child's environment.
- Caching the resolved path.
- Expanding `~` in `claude_executable`.
- Changing any footer string. `Refresh failed: claude not found` stays.
- Wiring the CLI driver to `config.toml`. `claude-usage --refresh` builds
  `ClaudeCliRefresher()` with no arguments
  ([`cli/main.py:96`](../ui/cli/main.py)) and so ignores `claude_executable`
  and `refresh_timeout_seconds` today. It gains the fallback automatically;
  its config gap is a separate change.

## 3. Facts this design relies on

| Fact | Status | Evidence |
| --- | --- | --- |
| macOS native launcher is `~/.local/bin/claude`, a symlink into `~/.local/share/claude/versions/` | VERIFIED | Claude Code docs, [Advanced setup → Auto-updates](https://code.claude.com/docs/en/setup); `ls -la ~/.local/bin/claude` on the author's machine |
| Windows native launcher is `%USERPROFILE%\.local\bin\claude.exe` | VERIFIED | Claude Code docs, [Advanced setup → Uninstall → Native installation](https://code.claude.com/docs/en/setup) |
| The native binary does not run through `node` | VERIFIED | `file -L ~/.local/bin/claude` → `Mach-O 64-bit executable arm64`; docs: the npm package's binary "does not itself invoke Node" |
| `Path.home()` resolves to `%USERPROFILE%` on Windows | ASSUMED | Python `pathlib` documentation; not exercised until the Windows CI leg runs |

Because the binary is self-contained, spawning it by absolute path is enough.
The child needs no `PATH` correction.

## 4. Behaviour

Lookup runs on **every** Refresh click; nothing is cached. It is a few
`stat` calls, and a user who installs or moves `claude` while the app runs
is picked up on the next click without a restart.

Order — first match wins:

| Step | Condition | Result |
| --- | --- | --- |
| 1 | `claude_executable` set, and it is a file and executable | use it |
| 1′ | `claude_executable` set, otherwise | `NOT_FOUND` — no further steps |
| 2 | `shutil.which("claude", path=env PATH)` returns a path | use it |
| 3 | `<home>/.local/bin/claude` is a file and executable | use it |
| 4 | `<home>/.local/bin/claude.exe` is a file and executable | use it |
| 5 | none of the above | `NOT_FOUND` |

"Is a file and executable" means `os.path.isfile(p) and os.access(p, os.X_OK)`.
`isfile` follows symlinks, so a dangling launcher left by a broken update
counts as not found. Checking both `claude` and `claude.exe` avoids a
`sys.platform` branch and lets macOS CI exercise the Windows candidate.

Outcomes after a path is resolved are unchanged: the spawn still maps to
`REFRESHED`, `TIMED_OUT` or `FAILED`. A file that passes the check but cannot
be executed (wrong architecture, not a program) still raises `OSError` and is
still `FAILED` — it was found.

## 5. Design

All changes are in `claude_usage/infrastructure/claude_cli.py`. No `wx`, no
inner-layer change; `domain/` and `application/` are untouched.

### 5.1 `resolve_claude`

```python
@dataclass(frozen=True)
class ClaudeNotFound:
    detail: str  # the log text after "refresh not_found: "


def resolve_claude(
    configured: str | None, env: Mapping[str, str], home: Path
) -> str | ClaudeNotFound: ...
```

A pure function over its arguments plus the filesystem. It never raises and
never spawns. It implements §4 steps 1–5.

### 5.2 `ClaudeCliRefresher`

- `__init__` gains `home: Path | None = None`; `None` means `Path.home()`,
  read at construction.
- `refresh()` calls `resolve_claude(self._executable, self._env, self._home)`.
  On `ClaudeNotFound` it logs `refresh not_found: %s` with `detail` and
  returns `NOT_FOUND`. Otherwise it spawns exactly as today.

### 5.3 Wiring

None. [`ui/app/main.py:101-105`](../ui/app/main.py) and the CLI's
`ClaudeCliRefresher()` both pick up the new behaviour from the default
`home`.

### 5.4 Log entries

Only `NOT_FOUND` changes. Verbatim:

| Case | Message |
| --- | --- |
| configured path fails the check | `refresh not_found: claude_executable=<p> is missing or not executable` |
| nothing configured, nothing found | `refresh not_found: claude_executable unset; searched PATH=<PATH>; fallback=<dir>` |

`<PATH>` is `<unset>` when `PATH` is absent, as today. `<dir>` is
`str(home / ".local" / "bin")` — the folder, since both `claude` and
`claude.exe` were tried there. The other outcomes' messages
and every refresh-failure §5 rule (no child output, no exception messages)
are unchanged.

## 6. Testing

Strict TDD in `tests/test_infrastructure_claude_cli.py`. No test may reach
the real `claude`, the real home folder, or the real log folder.

**`resolve_claude`** — direct calls; `tmp_path` is both the fake home and
the fake `PATH`; stubs are written there and `chmod`ed:

1. A valid configured path is returned.
2. A missing configured path is `ClaudeNotFound` with the §5.4 text, even
   when `<home>/.local/bin/claude` exists (no fallback).
3. A configured file that is not executable is `ClaudeNotFound` (POSIX only;
   skipped on Windows, where `X_OK` reduces to existence).
4. `PATH` wins over the fallback when both have `claude`.
5. The fallback returns `<home>/.local/bin/claude` when `PATH` has none.
6. The fallback returns `<home>/.local/bin/claude.exe` when only it exists.
7. A dangling symlink at `<home>/.local/bin/claude` is not found (skipped
   where the OS cannot create symlinks).
8. Nothing anywhere is `ClaudeNotFound` with the full §5.4 text, including
   `PATH=<unset>` when `env` has no `PATH`.

**Existing tests that change:**

- `test_no_executable_resolved_is_not_found`,
  `test_not_found_logs_the_searched_path` and
  `test_not_found_with_no_path_says_unset` only stub `shutil.which`. With the
  fallback, on a machine with a native install they would spawn the real
  `~/.local/bin/claude`. Each passes `home=tmp_path`, and the two log tests
  assert the new §5.4 text.
- `test_explicit_executable_that_cannot_spawn_is_failed` now expects
  `NOT_FOUND` and is renamed to say so. A new test keeps the `FAILED`-on-spawn
  path covered: an executable file the OS cannot run — no shebang on POSIX
  (`ENOEXEC`), not a PE image on Windows — raises `OSError` → `FAILED`, and
  logs `refresh failed: OSError: executable=<p>` (or the subclass name the
  platform raises).

**Gate:** `ruff format .`, `ruff check .`, and the full `pytest` suite clean.
No GUI change, so no screenshot is required.

## 7. Documentation

- README, Refresh paragraph: drop "(set `claude_executable` in config.toml to
  its path)" from the not-found clause in favour of a sentence saying the app
  looks on `PATH` and then in `~/.local/bin` (`%USERPROFILE%\.local\bin` on
  Windows), and that `claude_executable` overrides both.
- README, config table row for `claude_executable`: "if unset, resolved from
  `PATH`, then `~/.local/bin`".
- Refresh-failure spec §10, first risk: mark resolved, linking here.

## 8. Acceptance

1. With `claude_executable` unset and the app launched from Finder or the
   Dock (launchd's bare `PATH`), clicking Refresh succeeds: no amber line and
   no new log entry.
2. With `claude_executable = "/nope/claude"` and the app restarted, clicking
   Refresh shows `Refresh failed: claude not found`, and the log gains
   `refresh not_found: claude_executable=/nope/claude is missing or not executable`.
3. The §6 tests pass on both CI legs without invoking the real `claude`.

## 9. Risks

- **The fallback may run a different `claude` than the user's shell.** Only
  when `PATH` finds none, so a shell-visible install always wins. Accepted.
- **Windows `X_OK` is effectively an existence check.** A non-program file
  named `claude.exe` passes the check and then fails to spawn, reporting
  `FAILED`. That is still a true statement. Accepted.
- **`~` in `claude_executable` is not expanded.** `~/.local/bin/claude` in
  the config now reads clearly as `NOT_FOUND`, and the fallback makes that
  setting unnecessary for native installs. Accepted.
- **Windows home resolution** is ASSUMED (§3) until the Windows CI leg runs
  the fallback tests.
