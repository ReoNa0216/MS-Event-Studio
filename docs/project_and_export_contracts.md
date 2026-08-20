# Project and export contracts

## Project v2

`ms_event_project.json` is the root manifest. All runtime paths are portable
project-relative paths and are rejected if they are absolute, drive-relative,
ADS-like, contain `..`, or resolve through a symlink outside the root.

Required immutable artifacts are the project README, scan summary Parquet,
automatic event Parquet, input manifest, detector protocol, and processing log.
Their role and path are unique; size and complete SHA-256 are checked before a
project opens. `annotations/review.sqlite` is mutable but is bound internally and
in the manifest to the same project ID, generation ID, and schema version.

Creation builds a sibling directory named
`.TARGET.ms-event-building-<uuid>`. Nothing is published until parsing,
detection, all writes, and a full open-project preflight succeed. Cancellation
or failure cleans only that validated staging directory and preserves an
existing empty target.

The original multi-GB source is externally referenced and read only. Its
absolute path is deliberately not serialized; the input manifest stores file
name, complete SHA-256, size, mtime, and head/tail hashes. Recalculation must ask
the user to reselect a source and verify its fingerprint.

The manifest and detector protocol bind one `scientific_settings` object:
the project primary marker m/z, fixed closed ±12 ppm extraction window, fixed
782.5616 quality-control marker, and the nearby-event review threshold. The
primary marker is also bound to source inspection and parser output. A marker
change therefore creates a new v2 project; v1 projects are not reinterpreted or
migrated.

## Range generation changes

An analysis-range change is preview-only until explicit confirmation. The
preview binds the root-manifest hash, complete review-state token, detector
payload hash, proposed reconciliation, and new generation identity. Apply fails
if any bound state changed.

Confirmed apply holds a SQLite writer reservation while it creates a unique
generation activation. The retired review database is copied to a new immutable
archive before the root manifest switches; this prevents a stale second
application from corrupting bound history if it later writes the obsolete path.
The new automatic table, detector protocol, active review database, and retired
review archive are complete before the single atomic manifest replacement.
Failed post-switch validation restores the old manifest and removes only the
recognized orphan activation.

Exact or confirmed unique mappings retain project EventID and status. Ambiguous
or unmatched old automatic reviews remain with `generation_state=stale`;
in-range manual events remain active/manual, while out-of-range manual events
become stale. Recalculation clears the old generation's undo stack but preserves
all audit rows and appends `recalculate_analysis_range` with the confirmed diff.

## Human CSV v1

The exact columns are:

```text
EventID,scan_id,scan_start_time,apex_intensity,review_status,source
```

Time is decimal minutes derived from integer nanoseconds. Default status is
`accepted`; `pending` requires an explicit switch. Range ownership is closed and
uses the current apex. Rejected and unreviewed rows never enter this CSV.

## Machine contract v1

An atomic machine-export directory contains:

- `manifest.json`: source fingerprint, detector/parameter/generation binding,
  closed range rule, status counts, event-table schema, size, and SHA-256;
- `events.parquet`: all current statuses plus stable EventID, immutable
  automatic identity/evidence, original support, current apex, review revision,
  and provenance;
- `checksums.sha256`: SHA-256 for both files above.

The table contains all review statuses from the active generation. Stale
generation history is preserved in the project but excluded from both export
contracts. The desktop export chooser selects an existing parent directory and
the application publishes a new, uniquely named child directory atomically;
the user never has to prepare an empty target. Consumers must filter review
status explicitly. This contract is not permission to overwrite an LMA Studio
`ms_events.parquet`.
