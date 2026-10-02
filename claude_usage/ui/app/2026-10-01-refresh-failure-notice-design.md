# Refresh Failure Notice — Design Spec

**Status:** Approved 2026-10-01
**Date:** 2026-10-01
**Author:** Joseph Hoppe (drafted with Claude Code)
**Parent:** [`SPEC.md`](../../../SPEC.md)
**Amends:** [`2026-08-16-manual-refresh-design.md`](2026-08-16-manual-refresh-design.md) §6
("Outcome display") and §9

When the Refresh button fails, the only signal today is a tooltip on the
button. This spec replaces that tooltip with an amber footer line in the
window and records every failure in a rotating log file in the OS log folder.

---

## 1. Problem

A user launched the app from outside a terminal. It inherited launchd's bare
`PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`), which does not contain the
`claude` install at `~/.local/bin/claude`. Every click resolved no
executable and returned `RefreshOutcome.NOT_FOUND` in well under a second.
The button flashed "Refreshing…" and went back to "Refresh". The only trace was
the tooltip "Last refresh: not_found", which only appears on hover. To the
user, the button looked broken.

Two gaps:

1. **Invisible failure.** By design, the tooltip is the entire outcome display
   (manual-refresh §6: "Nothing else in the window reports the outcome").
2. **No record.** Nothing persists the failure or the context needed to
   diagnose it (which executable was tried, which `PATH` was searched).

## 2. Scope

### In scope

- An amber footer line for every non-`REFRESHED` outcome (`NOT_FOUND`,
  `TIMED_OUT`, `FAILED`). It replaces the button tooltip.
- An `ERROR` log entry for every non-`REFRESHED` outcome, written to a
  rotating file in the OS log folder.
- Updating the manual-refresh spec and the README to match.

### Out of scope

- Finding `claude` outside `PATH` (fallback install locations). That is a
  separate, follow-up change.
- The CLI driver. `claude-usage --refresh` already prints
  `Refresh failed: <outcome> — showing cached data` to stderr
  ([`cli/main.py:101-104`](../cli/main.py)).
- Logging successes, poller reads, or anything other than refresh failures.
- Naming the log path in the help dialog.
- A dismiss control on the footer line.

## 3. Behaviour

| Outcome     | Footer line (amber)                             |
|-------------|-------------------------------------------------|
| `REFRESHED` | *(none; clears any existing line)*              |
| `NOT_FOUND` | `Refresh failed: claude not found`              |
| `TIMED_OUT` | `Refresh failed: timed out`                     |
| `FAILED`    | `Refresh failed: claude exited with an error`   |

- **Lifetime.** The line appears when a failed refresh is delivered. It stays
  until a refresh returns `REFRESHED`. Background polls never clear it,
  because a poll only re-reads `~/.claude.json` and never re-runs `claude`.
  Only a later click can show that the problem is fixed. A later failure
  replaces the line with its own text.
- **Placement.** The line is the last footer line, below the bars and any
  existing notices. It is also drawn when the view shows a message instead of
  bars (for example "No quota data cached yet — click Refresh"). That is the
  state where Refresh matters most.
- **Colour.** `theme.SEVERITY_FILLS["warning"]`, `(224, 168, 0)`. This is
  deliberately not the critical red, so it cannot be mistaken for a quota
  alarm.
- **Tooltip.** Removed. The Refresh button no longer carries an outcome
  tooltip, so the failure is reported in one place.
- **Focus.** Unchanged. Showing the line only repaints. It never calls
  `Raise()` or `SetFocus()` (frame module docstring).

`FAILED` covers both a non-zero exit and a spawn that raised `OSError`
([`claude_cli.py:65-69`](../../infrastructure/claude_cli.py)). The footer
text describes the common case. The log entry tells the two apart (§5).

## 4. Footer — design

### Footer approaches considered

1. **The frame overlays the failure onto each view (chosen).** `QuotaView`
   gains a `refresh_failure: str | None = None` field. `QuotaFrame` keeps the
   latest failure text, and in `show_view` renders
   `dataclasses.replace(view, refresh_failure=self._refresh_failure)`. This
   works for poller views and refresh-delivered views alike. `QuotaPanel`
   keeps rendering one view and computing nothing. The state lives on the GUI
   thread only.
2. **The poller composes it.** The poller would need the last refresh outcome,
   which means mutable state shared between the refresh worker thread and the
   poller thread. Rejected: it breaks the rule that only frozen data crosses
   threads (`poller.py`, `refresh.py` docstrings).
3. **A separate `wx.StaticText` under the panel.** Rejected: everything below
   the top row is painted by `QuotaPanel`, and a second widget brings in
   sizer, colour and height bookkeeping that `_fit_to_content` would have to
   learn.

### Footer changes

- **`presenter.py`.** `QuotaView.refresh_failure: str | None = None`.
  Defaulted, so existing constructors and tests keep working. `present()` and
  `present_error()` never set it, because the poller knows nothing about
  refreshes.
- **`refresh.py`.** `outcome_tooltip()` becomes
  `outcome_notice(outcome) -> str | None` and returns the §3 strings (`None`
  for `REFRESHED`). The strings sit in a mapping keyed by `RefreshOutcome`, so
  a new outcome without text fails a test instead of falling through.
- **`frame.py`.** `end_refresh(tooltip)` becomes `end_refresh(failure)`. It
  restores the label, re-enables the button and stores `failure`. It does not
  render, because `deliver` calls `show_view` straight after (below). The
  tooltip calls go away. `show_view`
  overlays `self._refresh_failure` before fitting and rendering, so
  `_fit_to_content` measures the line.
- **`panels.py`.** `content_height` adds `_NOTICE_HEIGHT` when
  `refresh_failure` is set, in both the message branch and the bars branch.
  `_draw_footer` draws the line last in the warning colour. `_draw_message`
  draws it below the message and its detail. The literal `16` in
  `_draw_footer` ([`panels.py:102`](panels.py)) becomes `_NOTICE_HEIGHT`,
  which is already its named value.
- **`main.py`.** `deliver` calls `frame.end_refresh(outcome_notice(outcome))`
  before `frame.show_view(view)`, so the new view renders with the new
  failure state in a single pass.

## 5. Log file — design

### Location

`wx.StandardPaths` has no log folder: VERIFIED on wxPython 4.3.1, where
`Dir_*` is only Cache, Config, Desktop, Documents, Downloads, Music,
Pictures and Videos. The folder is therefore chosen from the environment,
not `sys.platform`. The project targets macOS and Windows only (README).

| Condition                       | File                                                 |
|---------------------------------|------------------------------------------------------|
| `LOCALAPPDATA` is set (Windows) | `%LOCALAPPDATA%\claude-usage\Logs\claude-usage.log` |
| otherwise (macOS)               | `~/Library/Logs/claude-usage/claude-usage.log`       |

On macOS, `~/Library/Logs` is also where Console.app looks.

### Log approaches considered

1. **The refresher logs its own failures through an injected stdlib
   `logging.Logger` (chosen).** `ClaudeCliRefresher` is the only code that
   knows which executable it tried and which `PATH` it searched, so the log
   entry is written where that detail exists. The `QuotaRefresher` protocol
   and `RefreshOutcome` stay unchanged. The CLI constructs the refresher
   without a logger and behaves exactly as it does today.
2. **Return a richer result (`outcome` + diagnostic) and log in the UI.** This
   changes the `QuotaRefresher` protocol, the CLI and the existing refresher
   tests, only so that a different layer can write the line. Rejected as more
   churn for the same output.
3. **A `LoggingRefresher` decorator around the refresher.** It sees only the
   outcome, not the lookup context, unless the inner refresher exposes extra
   state. Rejected.

### Log changes

- **New `infrastructure/log_file.py`:**
  - `default_log_path(env: Mapping[str, str], home: Path) -> Path` applies the
    table above. It is pure, so tests pass a fake environment and home folder
    and need no platform.
  - `open_refresh_log(path: Path) -> logging.Logger` returns the
    `claude_usage.refresh` logger with one `RotatingFileHandler(path,
    maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True)`.
    `propagate` is `False`. It creates the parent folder. If creating the
    folder or the handler raises `OSError`, it returns the same logger with a
    `NullHandler`, so the app runs without a log file. `delay=True` means a
    machine that never fails never gets a log file.
- **`claude_cli.py`.** `ClaudeCliRefresher.__init__` gains
  `log: logging.Logger | None = None`. Before each non-`REFRESHED` return, it
  logs one `ERROR` entry built only from data the app already holds:

  | Outcome              | Message                                                                  |
  |----------------------|--------------------------------------------------------------------------|
  | `NOT_FOUND`          | `refresh not_found: claude_executable unset; searched PATH=<PATH>`       |
  | `TIMED_OUT`          | `refresh timed_out after <n>s: executable=<exe>`                         |
  | `FAILED` (exit code) | `refresh failed: exit code <rc>: executable=<exe>`                       |
  | `FAILED` (spawn)     | `refresh failed: <OSError subclass name>: executable=<exe>`              |

  If `PATH` is unset, the entry reads `PATH=<unset>`.
  The formatter adds the timestamp and level
  (`%(asctime)s %(levelname)s %(message)s`).
  **The captured stdout and stderr of the child are never logged**: they may
  contain account details ([`claude_cli.py:3-4`](../../infrastructure/claude_cli.py),
  manual-refresh §5). The exception message is not logged either, only its
  type name, which matches `ClaudeJsonQuotaSource._fail`.
- **`main.py`.** `log = open_refresh_log(default_log_path(os.environ,
  Path.home()))`, passed as `ClaudeCliRefresher(..., log=log)`.

### Failure isolation

The log can never break a refresh:

- Setup failures fall back to `NullHandler` (above).
- Write failures at emit time go to stdlib `logging`'s `Handler.handleError`,
  which reports to stderr and does not raise to the caller (ASSUMED, from the
  stdlib `logging` documentation; covered by a test in §8).
- Logging happens on the refresh worker thread, before the outcome is
  returned. `RefreshWorker._run`'s `finally` already keeps a misbehaving
  refresher from wedging the button ([`refresh.py:71-75`](refresh.py)).

## 6. Layering

- `log_file.py` lives in infrastructure. It imports `logging` and `pathlib`,
  and nothing from `wx` or `claude_usage.ui`. That satisfies
  `tests/test_architecture.py`'s `infrastructure` rules. It takes the
  environment as a parameter, and the composition root passes `os.environ`.
- Domain and application layers are untouched.
- The footer state lives in the UI layer (`QuotaFrame`). The text mapping
  stays in `refresh.py`, beside the strings it replaces.

## 7. Documentation updates

- **manual-refresh §6 "Outcome display".** Replace the tooltip paragraph with
  a pointer to this spec.
- **manual-refresh §9.** Replace the `Last refresh: <outcome>` strings with
  the §3 table.
- **README "Refresh" paragraph.** Replace the tooltip sentence with the footer
  behaviour and the two log paths.

## 8. Testing

Strict TDD. Each item below starts as a failing test.

- **`test_ui_app_refresh.py`.** `outcome_notice` returns the four §3 values.
  Every `RefreshOutcome` member has an entry, so a new outcome cannot go
  silently unmapped.
- **`test_ui_presenter.py`.** `present()` and `present_error()` leave
  `refresh_failure` as `None`.
- **`test_ui_app_frame.py`** (with the `wx_app` fixture):
  - `end_refresh("Refresh failed: claude not found")` followed by a fresh
    `show_view(view)` with no failure keeps the failure on the rendered view.
    This is the "polls don't clear it" rule.
  - `end_refresh(None)` clears it.
  - The button has no tooltip after a failed refresh.
  - `_fit_to_content` grows the minimum client height by `_NOTICE_HEIGHT` when
    the line is present, in both message mode and bars mode.
- **`test_infrastructure_log_file.py`** (new):
  - `default_log_path` with `{"LOCALAPPDATA": "C:\\x"}` gives
    `C:\x\claude-usage\Logs\claude-usage.log`. With `{}` it gives
    `<home>/Library/Logs/claude-usage/claude-usage.log`.
  - `open_refresh_log` on a `tmp_path` writes an `ERROR` entry. The rotation
    settings are asserted on the handler.
  - A parent "folder" that is actually a file (portable stand-in for an
    unwritable folder) gives a `NullHandler` logger and no raise.
  - A handler whose stream fails at emit time does not raise into the caller.
- **`test_infrastructure_claude_cli.py`.** With a recording logger, each
  failure path emits exactly one `ERROR` entry matching the §5 table, and
  `REFRESHED` emits none. A child that prints a sentinel string to stdout and
  to stderr: the sentinel never appears in any log entry. With `log=None`, the
  behaviour is unchanged (existing tests stay green).
- **Full suite, plus `ruff check .` and `ruff format --diff .`.**
- **GUI verification (AGENTS.md).** Offscreen screenshots at 377×216 in bars
  mode and in message mode with the `NOT_FOUND` line. All three §3 strings
  must fit without clipping at 377 px. The screenshots come from a new,
  committed `scripts/screenshot.py`. It opens a `QuotaFrame` at its real
  opening size, shows a given view, and saves the client area as a PNG, so
  later layout changes can reuse it. It is linted like the rest of the tree
  and is not under test.

## 9. Acceptance

With the app's `PATH` missing `claude` (the §1 setup):

1. Clicking Refresh shows the amber `Refresh failed: claude not found` line
   within a second.
2. `~/Library/Logs/claude-usage/claude-usage.log` gains one `ERROR` entry
   naming the searched `PATH`.
3. The line survives at least one background poll (default 10 s).
4. After `claude_executable` is set in `config.toml`, the app is restarted
   and Refresh is clicked, the line is gone and no new log entry appears.

## 10. Risks

- **A wrong `claude_executable` reads as "exited with an error".** Resolved
  2026-10-02 by the
  [executable lookup spec](../../infrastructure/2026-10-02-claude-executable-lookup-design.md):
  a configured path that is missing or not executable now returns
  `NOT_FOUND` and logs
  `refresh not_found: claude_executable=<path> is missing or not executable`.
- **Narrow windows clip the line.** The longest string,
  `Refresh failed: claude exited with an error`, is 43 characters. It fits
  at the 377 px opening width (to be confirmed by screenshot, §8), but may
  clip at the 240 px minimum width (`frame.py` `_MIN_WIDTH`). Accepted: the
  log holds the full detail, and a user who narrows the window has chosen
  density.
- **`PATH` in the log.** It contains local folder names, including the user's
  home folder name. The file is local and readable only by the user's own
  account. Accepted.
- **Windows test of `default_log_path`.** It is a pure function, so the
  Windows path is tested on macOS CI too. The real `%LOCALAPPDATA%` folder is
  exercised only on the Windows matrix leg.
