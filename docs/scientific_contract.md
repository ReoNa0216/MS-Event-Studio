# Scientific contract v1

- Input v1 is the existing ASCII mzML-like text export and is always read only.
- Parsing is strict and streaming. The same pass computes complete SHA-256 and
  structured byte progress. Input mutation, cancellation, truncation, malformed
  arrays, duplicate physical scan identity, or non-monotone time fail closed.
- A zero-length spectrum is retained only when `defaultArrayLength` is zero and
  both m/z and intensity array declarations are present; missing payload for a
  nonzero spectrum is truncation. Metadata spectrum count must equal retained
  spectrum count exactly.
- PC34/MS760 is extracted at 760.5851 and MS782 at 782.5616 using a closed
  ±12 ppm window. PC34 alone defines primary events; TIC/MS782 are evidence.
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
