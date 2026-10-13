# Language preference

The portable default is **English (`en`)**. This policy overrides the language of bundled guides and examples. Preserve the user's technical vocabulary and original evidence.

At the start of a new goal, resolve the output language in this order:

1. The user's explicit language instruction for this task, including a host preference actually provided in context.
2. An existing task card's `output_locale` or a plan's `locale` when continuing that goal.
3. The local preference returned by `python scripts/workflow_locale.py`, resolved relative to this Skill.
4. English when no preference is available. Do not infer a host setting from the guide's language or browser language.

Use the result for grill-me questions, new node titles, public progress, execution plans and human-facing reports. Record `locale` (`en` or `zh-CN`) when preparing a new plan so the selected language travels with the goal. Language selection does not confirm requirements, grant authority, change task dependencies, or certify acceptance. Existing frozen runs remain unchanged.

For a lasting local preference, `python scripts/workflow_locale.py --set zh-CN` selects simplified Chinese; `--set en` restores English. This writes `~/.config/dev-workflow/preferences.json`, outside the Skill installation. Codex and Claude Code on the same machine use that file. A portable ZIP does not contain personal preferences. It does not read or modify either host's global settings. On another machine, set the preference there or carry a plan with an explicit `locale`.

The dashboard's visible language selector switches UI copy immediately and remembers the choice in this browser, including across localhost ports through a non-secret preference cookie. With no browser choice, the local preference supplies its initial language; with neither, it uses English. UI selection does not mutate a running plan or the AI's local preference. Ask the AI to change future task output if desired.

Use concise `title` values: CJK titles 2–12 characters, other titles 2–40 characters; single line, no surrounding whitespace or control characters. Optional `titles: {"en":"Create booking","zh-CN":"创建预约"}` provides labels for either UI language. Stable IDs remain unchanged. For switchable dashboard content, prepare source-bound `display` variants for the run name/goal and task descriptions; never put a task-specific translation in the UI dictionary. Logs, message payloads, commands and evidence remain verbatim. Details provide a collapsed original-text disclosure when showing a translated goal. An old task without a display variant retains its original text until the AI prepares one.


## Display content

When preparing a switchable run, include concise English and simplified Chinese labels in `titles`, and display copies for its name, goal and task descriptions. Use `display: {"en": {"goal": {"source": "原始目标", "text": "Original goal"}}}`. `source` must exactly match the canonical field. A missing or stale variant falls back to the actual current text. These copies are presentation metadata and cannot replace requirements, scope, checks, messages or evidence.

Interactive views accept root `name` / `goal`, and task `goal` / `module` / `action` / `reason`. Session `update` accepts a root `display` and per-task display updates using the current `expected_sequence`; it validates sources and preserves execution facts. After changing an action, update its translations together so old progress cannot appear as current progress. Do not mark tasks running or completed just to add translations.

Depth plans accept task `goal` / `output` / `verification` (the exact `; `-joined verification strings). Projection builds source-bound translated full descriptions including Output, Acceptance and Checks, while retaining canonical descriptions and all criterion IDs.

Coordinated progress can supply `data.display.en.text` with `{ "source": "exact public text", "text": "English public summary" }`, and the corresponding `zh-CN` entry. Only the active attempt's public progress is displayed; invalid optional copy is ignored. This does not authorize dispatch or alter acceptance.
