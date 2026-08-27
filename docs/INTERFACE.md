# CryoPit interface contract

This document describes CryoPit's presentation and interaction contract. Interface
changes must preserve the scientific fields, calculations, validation rules, archive
semantics, attachment consistency, ownership model, and API contracts unless those
behaviors are deliberately changed and tested elsewhere.

## Field-use priorities

CryoPit is designed for a laptop or tablet used outdoors, often in glare and
with gloves. The interface therefore favors legible labels, explicit units,
large touch targets, restrained density, visible record state, and actions that
remain distinguishable in both light and dark themes.

The command bar exposes the current workflow: Workspace, New pit, Download,
Archive or Archive Changes, theme, and server status. The workspace presents the
two primary paths—start a new record or find an existing one—before secondary
status cards for recent pits, recovery, and photo queues.
The current-work card names its return action by lifecycle state: **Continue
draft** for unarchived work and **Continue record** for a loaded archived pit.
Primary accent controls use a theme-specific foreground token so their labels
remain readable at rest, on hover, and under keyboard focus.

## Offline visual operation

The application uses platform UI and monospace font stacks. It makes no remote
font request, so loss of network connectivity does not alter form metrics or
remove the intended hierarchy. Scientific and coordinate libraries already ship
with the application.

## Form hierarchy

Each scientific section retains its stable number, title, fields, IDs, and
collection behavior. Each section includes a concise subtitle and a separate status
badge. Field cards expose labels and units before values, and populated cards
receive a small non-semantic marker. The marker is never used to determine
validity or completeness.

The presentation enhancement layer may add accessible names, CSS classes, and
measured layout variables. It must not collect, validate, calculate, archive, upload,
delete, or mutate field values.

## Responsive behavior

The desktop layout retains the section index, form, and live-profile rail. At
intermediate widths the rail and workspace cards reorganize to protect form
space. Narrow layouts collapse secondary command text while preserving named
controls and minimum touch targets. The page must not rely on horizontal
scrolling for primary form use.

## Accessibility

- Native labels remain authoritative; the enhancer labels controls only when a
  usable accessible name is absent.
- Table inputs receive a column-and-row name.
- In Temperature, Density, LWC/Permittivity, and Stratigraphy tables, Enter moves to the same editable input in the next existing row. It never creates a row or submits the form; Tab and Shift+Tab retain native horizontal navigation.
- Optional single-select groups keep native radio semantics. After a choice is made, an explicit **Clear selection** action returns the group to unanswered; clicking the selected radio again does not act as a non-standard toggle.
- Keyboard focus is visible and is not represented by colour alone.
- Attachment states combine text, shape, and colour.
- `prefers-contrast: more` strengthens boundaries and focus treatment.
- `prefers-reduced-motion: reduce` disables nonessential movement.
- Print rules remove application chrome and preserve the scientific record.
- Browser zoom and long institutional names must not create horizontal page
  overflow.

## Lifecycle banners

Edit-mode and post-archive banners are fixed beneath the command bar. Their
actual rendered height is measured into `--lifecycle-banner-h`, and the form
shell is offset by that amount. This prevents overlap when text wraps at narrow
widths or under browser zoom.

## Testing

Source-level and lightweight runtime tests cover offline assets, workspace
hierarchy, section subtitles, accessible names, populated-state behavior, lifecycle
banners, responsive rules, theme completeness, contrast, reduced motion, and print.
The DOM assertion floor remains mandatory, and representative layouts are also
exercised in a real Chromium runtime.

### Toggle-card focus geometry

Weather, ground-observation, vegetation, and density-cutter options retain native radio/checkbox controls. The invisible native control fills the complete pill card rather than using a one-pixel focus target. This preserves keyboard and screen-reader behavior and prevents browsers from scrolling a field card merely to reveal the focused control.

Weather uses native checkboxes because conditions can change during one pit.
Every checked precipitation-rate, precipitation-type, sky, and wind value is
recorded. `None` is exclusive within precipitation rate and type. Optional
single-select radio groups elsewhere retain the explicit **Clear selection**
action.
