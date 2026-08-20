# Scientific contract v2

- Input v1 is the existing ASCII mzML-like text export and is always read only.
- Parsing is strict and streaming. The same pass computes complete SHA-256 and
  structured byte progress. Input mutation, cancellation, truncation, malformed
  arrays, duplicate physical scan identity, or non-monotone time fail closed.
- A zero-length spectrum is retained only when `defaultArrayLength` is zero and
  both m/z and intensity array declarations are present; missing payload for a
  nonzero spectrum is truncation. Metadata spectrum count must equal retained
  spectrum count exactly.
- Each project declares one primary cell-event marker m/z (default 760.5851)
  before source inspection. The parser extracts that marker and the fixed
  782.5616 quality-control marker using closed ±12 ppm windows. Only the
  declared primary marker defines events; TIC and the quality-control marker
  remain evidence. Changing the marker requires a new project and never
  reinterprets columns from an older project.
- Each project also declares the distance used only to flag nearby events
  (default 0.60 s). That review threshold does not merge, delete, or change the
  identity of a called event.
- The detector preserves the confirmed LMA Studio v0.4.4 adaptive background
  behavior, while explicitly versioning the exact-bin-endpoint and physical
  irregular-time-width corrections.
- Analysis range is a fixed-point closed interval and ownership follows the
  current apex. Detection uses the complete parsed trace before range clipping.
- Every automatic result has immutable source, detector, parameter, generation,
  scan, apex, support, and quality evidence.
- Project EventID is a persistent UUID identity; auto_event_id is an immutable
  generation-specific content identity. Neither is derived from display order.
- Human review is an overlay. Rejection never deletes evidence. All writes,
  including undo/redo and export, append audit rows.
- Human CSV defaults to accepted only. Pending requires an explicit switch;
  unreviewed and rejected never enter the main CSV.
- A current-window bulk accept is one atomic review command. It may update only
  active, visible, unreviewed events for which both the immutable automatic-apex
  collision risk and the derived current-apex collision risk are false. The live
  risk is computed globally across all active events, independent of status,
  filters, and viewport. The complete active revision set is revalidated in the
  write transaction; undo and redo move the whole command.
- Analysis-range changes are new immutable automatic generations. They require
  a read-only diff and explicit confirmation; old evidence/reviews are archived,
  mapped EventIDs are reused, and ambiguous or out-of-range history is stale
  rather than deleted.
- Stale generation rows are never active export rows. The machine contract
  retains every review status in the active generation, while historical
  generations remain recoverable only through the bound project history.

## Configurable primary marker: implementation and validation boundary

For target `M`, each sorted scan array is sliced with a closed
`[M * (1 - 12e-6), M * (1 + 12e-6)]` interval. The scan summary records the
number of ions, maximum and summed intensity, m/z of the maximum-intensity ion,
and its ppm error. Automatic detection uses the maximum-intensity series as its
primary trace.

The adaptive detector is marker-agnostic in code, not marker-validated in
biology: it applies the same v0.4.4-derived quiet-platform, height, prominence,
and two-scan minimum-distance rules to whichever primary trace was selected.
The default 760.5851 path has four large real-source regressions and is the only
routine recommendation. Every non-default marker is experimental. A synthetic
500.1234 test proves exact extraction and project binding only; it does not
prove event recall, specificity, morphology, or biological meaning for an
arbitrary marker. The fixed 782.5616 quality-control trace remains evidence and
does not create events.

Changing the primary marker changes the parameter hash and automatic-event
generation. It therefore requires a new project. Until a real alternative
marker dataset is adjudicated, non-default marker use is experimental.

## Nearby-event threshold

After automatic peaks are called and sorted by apex time, each event stores its
previous and next apex gaps and the smaller finite value. It is flagged only
when `nearest_event_gap_sec < collision_gap_sec`; equality is not flagged and
both members of a close pair are normally marked. The default 0.60 s comes from
the frozen LMA v0.4.4 caller and has not been re-estimated from MS Event Studio
human ground truth.

This threshold is separate from the detector's own minimum peak distance
(approximately two physical scan intervals for the primary trace). Changing it
does not call, merge, suppress, or re-identify peaks; it changes only stored
review-risk evidence and the bulk-accept skip set. The immutable automatic-apex
flag remains in the automatic artifact and is never recomputed after a manual
edit. A separate live flag is derived from the current apexes of every active
automatic, adjusted, and manually added event. A strict gap below the project
threshold flags both neighbours; equality is not flagged. Bulk accept skips the
union of the two sets and records the threshold and separate skip counts in the
append-only audit details. The live flag never overwrites stored automatic
evidence. The Chinese mathematical explanation and examples are in
`docs/marker_mz_principles_zh.md`.

## Confirmed numeric rules

- Time enters through decimal text and is rounded once to integer nanoseconds
  (half-even). Analysis ownership is `start_ns <= current_apex_ns <= end_ns`.
- m/z arrays are finite, non-negative, and monotone non-decreasing; intensity
  arrays are finite and non-negative. Parsed lengths must equal each other and
  `defaultArrayLength`.
- Manual Add snaps only to a positive local maximum or deterministic plateau
  representative within twice the local median positive scan interval. A click
  inside, or a path crossing, a gap larger than three times that median is
  rejected. The lower row wins an exactly even plateau midpoint.
- Automatic Adjust may never leave its immutable original left/right support.
  Reject and review filters never delete automatic evidence.
