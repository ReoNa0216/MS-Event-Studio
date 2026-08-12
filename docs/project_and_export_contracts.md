# Project and export contracts

## Project v1

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

Consumers must filter status explicitly. This contract is not permission to
overwrite an LMA Studio `ms_events.parquet`; formal LMA import remains Phase 3.
