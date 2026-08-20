# MS Event Studio agent rules

Before changing product behavior, read `docs/product_status.md`,
`docs/scientific_contract.md`, `docs/marker_mz_principles_zh.md`, and
`docs/project_and_export_contracts.md`. They describe the current product and
scientific boundaries; Git history is the source for completed Phase 1/2
implementation details.

- Treat `../lma-studio` as a frozen, read-only reference at v0.4.4. Reuse its
  product language, WebView architecture, design tokens, and component ideas by
  copying/adapting code into this repository; never edit it or import it at
  runtime.
- Preserve the scientific, project, review, range-change, audit, and export
  contracts documented under `docs/`. Raw MS source files are read-only, and an
  MS Event Studio export must never overwrite an LMA Studio project artifact.
- Do not continue polishing the legacy `ttk` review workbench. Phase 2R moves
  the desktop shell to pywebview plus HTML/CSS/SVG while retaining the tested
  Python scientific core.
- User-facing copy is Chinese-first. Necessary compact scientific English such
  as PC34, MS782, TIC, m/z, and ppm is allowed. Hide storage/schema/revision
  terminology from normal screens.
- A release candidate must use one coherent UI renderer. Do not ship a mixed
  Tk/WebView workbench.
- `dist/`, `release/`, and real-data UAT projects are generated evidence, not
  source of truth. Keep them out of Git and do not treat an old executable as
  proof that current source passed UX review. Put named intermediate candidates
  under `build/`; before handoff, `dist/` contains only the current candidate.
- Before asking the user to perform UAT, run the complete automated suite,
  packaged smoke tests, the standard screenshot matrix, and three independent
  agent reviews: interaction/task flow, LMA visual consistency, and
  accessibility/QA. Give the user only a candidate that has passed those gates.
- User-facing UAT starts on Windows. Do not give a macOS-only operation guide to
  a Windows-only user; schedule Apple Silicon Retina UAT only after the Windows
  candidate has received human sign-off.
