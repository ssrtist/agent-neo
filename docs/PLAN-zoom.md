# Agent365 Chat Content Zoom - Implementation Plan

## Purpose
Add a persistent content-zoom feature to the agent365 desktop GUI while keeping application chrome at its existing 100% scale.

## Agreed behavior
- Zoom range: 80% through 150% inclusive.
- Step size: 10 percentage points.
- Valid values: 80, 90, 100, 110, 120, 130, 140, 150.
- Default: 100% when no saved preference exists.
- Preference is global to agent365, not per conversation/session.
- Persist the last selected zoom across application restarts.
- Missing, malformed, non-step, or out-of-range persisted values normalize to 100%; do not clamp invalid stored values.

## What zooms
Zoom the complete chat content surfaces:
- conversation history, including BOTH user prompts and assistant responses;
- rendered answer content including text, Markdown, code blocks, tables, inline images/previews, attachments/previews, and internal spacing;
- composer content including prompt text/content and attached content/chips as appropriate to the content surface.

## What must remain at 100%
Do NOT zoom application chrome:
- top toolbar/header;
- status/hint bar and other controls;
- outer conversation/composer panels;
- scrollbars;
- buttons and other surrounding application controls/layout chrome.

The composer outer footprint stays stable. Enlarged composer content uses the composer's existing internal sizing/scroll behavior rather than scaling the outer panel.

## Toolbar control
- Add one compact grouped zoom control near the RIGHT side of the existing toolbar.
- Visual form: - 100% + using minus button, informational percentage label, plus button.
- Percentage label displays current zoom but is NOT clickable.
- Minus and plus are real keyboard-focusable accessible buttons.
- Accessible names: Zoom out and Zoom in.
- Tooltips expose the keyboard shortcuts.
- Minus disables at 80%; plus disables at 150%.
- Clicking minus/plus changes by exactly 10 percentage points.

## Keyboard shortcuts
Shortcuts work application-wide whenever the agent365 window has focus, including when focus is in the composer:
- Ctrl++ : zoom in 10%.
- Ctrl+= : also zoom in 10% (standard US-keyboard unshifted form).
- Ctrl+- : zoom out 10%.
- Ctrl+0 : reset to 100%.

Requirements:
- Prevent/suppress native WebView/browser zoom for these bindings so only agent365 content zoom changes.
- At 80%/150%, further decrement/increment attempts are no-ops.
- Ctrl+0 persists 100% as the new preference.
- No Ctrl+mouse-wheel zoom in this implementation.

## Interaction behavior
- Zoom changes are immediate, with no animation.
- Preserve the conversation's current reading/scroll position as closely as possible when zoom changes; do not jump to top or bottom merely because zoom changed.
- Existing near-bottom/autoscroll behavior for new agent events must continue to work.

## Implementation surface
- gui/index.html: toolbar, conversation viewport, composer markup.
- gui/app.js: UI behavior, keyboard handling, state/rendering, persistence hook, scroll-position preservation.
- gui/app.css: scoped content scaling and compact zoom-control styling.
- tests/e2e/gui_e2e.py: Playwright GUI acceptance harness.
- tests/negative_controls_gui.py: mutation/negative-control harness.

## Implementation approach
1. Add the compact zoom control to the existing header, adjacent to the right-side toolbar controls.
2. Introduce zoom constants in gui/app.js: minimum 80, maximum 150, step 10, default 100.
3. Load the global persisted preference at startup and validate it strictly against the allowed values; normalize anything invalid to 100%.
4. Keep one in-page zoom state and update the control label/disabled states from that state.
5. Persist every accepted zoom change and Ctrl+0 reset.
6. Apply content scaling only to explicitly scoped content descendants/wrappers for the conversation and composer. Do not use global WebView/page zoom because that would scale toolbar/status/chrome too.
7. Route toolbar and keyboard operations through the same zoom-change function so bounds, persistence, UI state, and scroll handling cannot diverge.
8. Intercept Ctrl++, Ctrl+=, Ctrl+-, and Ctrl+0 at application level with preventDefault so native WebView zoom is not invoked, including while the textarea has focus.
9. Before changing zoom, capture an anchor/relative scroll position in the conversation; after reflow, restore the reading position as closely as possible. Preserve bottom anchoring when the user was already near the bottom.
10. Do not implement Ctrl+wheel zoom.

## Acceptance criteria / automated coverage
Feature is not complete until tests cover at least:
- default 100%;
- valid 80%-150% values;
- 10% increments/decrements;
- clamping/no-op behavior at 80% and 150%;
- Ctrl+0 reset to 100%;
- persistence and restoration across reload/restart-equivalent state initialization;
- invalid/malformed/non-step/out-of-range persisted preference normalizes to 100%;
- Ctrl++, Ctrl+=, Ctrl+-, Ctrl+0 bindings;
- keyboard shortcuts work while composer has focus;
- default/native WebView zoom is suppressed for handled shortcuts;
- toolbar percentage reflects the active zoom;
- minus button disabled at 80%; plus button disabled at 150%;
- zoom buttons have accessible names and shortcut tooltips;
- percentage display is informational/non-clickable;
- conversation content and composer content scale;
- header/toolbar, status/control chrome, outer panels, and scrollbars stay at 100%;
- no Ctrl+mouse-wheel zoom behavior is introduced.

## Manual smoke checks
Manually verify 80%, 100%, and 150% with representative content:
- long user prompt;
- normal assistant prose;
- Markdown table;
- code block;
- links and attachment/chip content;
- long composer text;
- scroll midway through a long conversation, then zoom in/out and confirm reading-position preservation;
- composer remains usable and its outer layout remains stable.
