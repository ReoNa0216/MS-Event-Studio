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
  active, visible, unreviewed events whose immutable automatic evidence is not
  flagged as a nearby-event collision; undo and redo move the whole command.
- Analysis-range changes are new immutable automatic generations. They require
  a read-only diff and explicit confirmation; old evidence/reviews are archived,
  mapped EventIDs are reused, and ambiguous or out-of-range history is stale
  rather than deleted.
- Stale generation rows are never active export rows. The machine contract
  retains every review status in the active generation, while historical
  generations remain recoverable only through the bound project history.

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
