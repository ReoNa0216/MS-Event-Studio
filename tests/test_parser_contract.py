from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from _fixtures import PRIMARY_MARKER_MZ, QC782_MZ, spectrum_lines, write_ms_file
from ms_event_studio.errors import CancelledError, InputChangedError, MSParseError
from ms_event_studio.parser import parse_ms_scan_summary


class ParserContractTest(unittest.TestCase):
    def test_declared_zero_length_spectrum_is_complete_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "zero-length.txt"
            source.write_text(
                "\n".join(
                    [
                        "spectrumList (2 spectra)",
                        "spectrum:",
                        "  index: 0",
                        "  id: scanId=1",
                        "  defaultArrayLength: 0",
                        "  cvParam: base peak m/z, 100",
                        "  cvParam: base peak intensity, 0",
                        "  cvParam: total ion current, 1",
                        "  cvParam: scan start time, 0, minute",
                        "  cvParam: m/z array, m/z",
                        "  cvParam: intensity array, number of detector counts",
                        *spectrum_lines(1, 2, "0.1"),
                    ]
                ),
                encoding="ascii",
            )
            result = parse_ms_scan_summary(source)
        self.assertEqual(result.summary.metadata_spectrum_count, 2)
        self.assertEqual(result.summary.parsed_spectrum_count, 2)
        self.assertEqual(result.scans["array_length"].astype(int).tolist(), [0, 4])
        self.assertEqual(result.scans["primary_marker_max_intensity"].tolist(), [0.0, 0.0])

    def test_zero_length_requires_both_array_declarations(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad-zero-length.txt"
            source.write_text(
                "\n".join(
                    [
                        "spectrumList (1 spectra)",
                        "spectrum:",
                        "  index: 0",
                        "  id: scanId=1",
                        "  defaultArrayLength: 0",
                        "  cvParam: scan start time, 0, minute",
                        "  cvParam: m/z array, m/z",
                    ]
                ),
                encoding="ascii",
            )
            with self.assertRaisesRegex(MSParseError, "truncated"):
                parse_ms_scan_summary(source)

    def test_required_metadata_and_unique_array_declarations_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_tic = write_ms_file(root / "missing-tic.txt", [spectrum_lines(0, 1, 0)])
            missing_tic.write_text(
                "\n".join(
                    line
                    for line in missing_tic.read_text(encoding="ascii").splitlines()
                    if "total ion current" not in line
                ),
                encoding="ascii",
            )
            with self.assertRaisesRegex(MSParseError, "missing.*tic"):
                parse_ms_scan_summary(missing_tic)

            duplicate_array = write_ms_file(
                root / "duplicate-array.txt", [spectrum_lines(0, 2, 0)]
            )
            duplicate_array.write_text(
                duplicate_array.read_text(encoding="ascii").replace(
                    "  cvParam: m/z array, m/z\n",
                    "  cvParam: m/z array, m/z\n  cvParam: m/z array, m/z\n",
                ),
                encoding="ascii",
            )
            with self.assertRaisesRegex(MSParseError, "duplicate.*m/z array"):
                parse_ms_scan_summary(duplicate_array)

            negative_time = write_ms_file(
                root / "negative-time.txt", [spectrum_lines(0, 3, "-0.1")]
            )
            with self.assertRaisesRegex(MSParseError, "negative scan start time"):
                parse_ms_scan_summary(negative_time)

    def test_closed_12ppm_window_and_complete_stream_hash(self):
        lower = PRIMARY_MARKER_MZ * (1.0 - 12e-6)
        upper = PRIMARY_MARKER_MZ * (1.0 + 12e-6)
        outside = PRIMARY_MARKER_MZ * (1.0 + 12.001e-6)
        mz = [100.0, lower, PRIMARY_MARKER_MZ, upper, outside, QC782_MZ, 900.0]
        intensity = [0.0, 100.0, 200.0, 300.0, 9999.0, 400.0, 0.0]
        with tempfile.TemporaryDirectory() as tmp:
            source = write_ms_file(
                Path(tmp) / "mass-window.txt",
                [spectrum_lines(7, 7001, "1.23456789", mz_values=mz, intensities=intensity)],
            )
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            result = parse_ms_scan_summary(source)
            after = hashlib.sha256(source.read_bytes()).hexdigest()

        self.assertEqual(before, after)
        self.assertEqual(result.fingerprint.sha256, before)
        self.assertEqual(result.summary.metadata_spectrum_count, 1)
        self.assertEqual(result.summary.parsed_spectrum_count, 1)
        row = result.scans.iloc[0]
        self.assertEqual(int(row["primary_marker_n_mz"]), 3)
        self.assertAlmostEqual(float(row["primary_marker_max_intensity"]), 300.0)
        self.assertAlmostEqual(float(row["primary_marker_sum_intensity"]), 600.0)
        self.assertAlmostEqual(
            float(row["primary_marker_ppm_error_at_max_intensity"]),
            12.0,
            places=8,
        )
        self.assertEqual(int(row["qc_marker_n_mz"]), 1)
        self.assertEqual(int(row["scan_time_ns"]), 74_074_073_400)

    def test_custom_primary_marker_changes_extraction_without_widening_ppm_window(self):
        custom_marker = 500.1234
        mz = [100.0, custom_marker, PRIMARY_MARKER_MZ, QC782_MZ, 900.0]
        intensities = [0.0, 321.0, 900.0, 10.0, 0.0]
        with tempfile.TemporaryDirectory() as tmp:
            source = write_ms_file(
                Path(tmp) / "custom-marker.txt",
                [spectrum_lines(0, 1, "0", mz_values=mz, intensities=intensities)],
            )
            parsed = parse_ms_scan_summary(source, primary_marker_mz=custom_marker)
        row = parsed.scans.iloc[0]
        self.assertEqual(parsed.summary.primary_marker_mz, custom_marker)
        self.assertEqual(parsed.summary.tolerance_ppm, 12.0)
        self.assertEqual(float(row["primary_marker_max_intensity"]), 321.0)
        self.assertEqual(float(row["primary_marker_mz_at_max_intensity"]), custom_marker)

    def test_metadata_count_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = write_ms_file(
                Path(tmp) / "count.txt",
                [spectrum_lines(0, 1, 0)],
                declared_count=2,
            )
            with self.assertRaisesRegex(MSParseError, "spectrum count"):
                parse_ms_scan_summary(source)

    def test_truncated_spectrum_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "truncated.txt"
            source.write_text(
                "\n".join(
                    [
                        "spectrumList (1 spectra)",
                        "spectrum:",
                        "  index: 0",
                        "  id: scanId=1",
                        "  defaultArrayLength: 3",
                        "  cvParam: scan start time, 0, minute",
                        "  cvParam: m/z array, m/z",
                        "  binary: [3] 100 760.5851 800",
                    ]
                ),
                encoding="ascii",
            )
            with self.assertRaisesRegex(MSParseError, "truncated"):
                parse_ms_scan_summary(source)

    def test_duplicate_scan_id_and_nonmonotone_time_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            duplicate = write_ms_file(
                root / "duplicate.txt",
                [spectrum_lines(0, 7, 0), spectrum_lines(1, 7, 0.1)],
            )
            with self.assertRaisesRegex(MSParseError, "duplicate scan_id"):
                parse_ms_scan_summary(duplicate)
            nonmonotone = write_ms_file(
                root / "nonmonotone.txt",
                [spectrum_lines(0, 1, 1.0), spectrum_lines(1, 2, 0.5)],
            )
            with self.assertRaisesRegex(MSParseError, "strictly increasing"):
                parse_ms_scan_summary(nonmonotone)

    def test_bad_or_unsorted_arrays_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsorted_path = root / "unsorted.txt"
            lines = ["spectrumList (1 spectra)"] + spectrum_lines(
                0,
                1,
                0,
                mz_values=[PRIMARY_MARKER_MZ, 100.0, 900.0],
                intensities=[10.0, 0.0, 0.0],
            )
            unsorted_path.write_text("\n".join(lines), encoding="ascii")
            with self.assertRaisesRegex(MSParseError, "m/z.*sorted"):
                parse_ms_scan_summary(unsorted_path)

            bad = write_ms_file(root / "bad.txt", [spectrum_lines(0, 1, 0)])
            payload = bad.read_text(encoding="ascii").replace(
                "100 760.5851", "100 BAD"
            )
            bad.write_text(payload, encoding="ascii")
            with self.assertRaisesRegex(MSParseError, "numeric array"):
                parse_ms_scan_summary(bad)

            malformed = write_ms_file(root / "malformed-token.txt", [spectrum_lines(0, 2, 0)])
            payload = malformed.read_text(encoding="ascii").replace(
                "100 760.5851", "100 760.5851e"
            )
            malformed.write_text(payload, encoding="ascii")
            with self.assertRaisesRegex(MSParseError, "numeric array"):
                parse_ms_scan_summary(malformed)

    def test_cancel_and_mutation_are_typed_failures(self):
        spectra = [spectrum_lines(i, i + 1, str(i / 600.0)) for i in range(20)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cancelled = write_ms_file(root / "cancelled.txt", spectra)
            with self.assertRaises(CancelledError):
                parse_ms_scan_summary(cancelled, cancel_check=lambda: True)

            changing = write_ms_file(root / "changing.txt", spectra)
            mutated = False

            def mutate_on_progress(progress):
                nonlocal mutated
                if not mutated:
                    with changing.open("ab") as handle:
                        handle.write(b"\nchanged")
                    mutated = True

            with self.assertRaises(InputChangedError):
                parse_ms_scan_summary(
                    changing,
                    progress_callback=mutate_on_progress,
                    progress_interval_bytes=1,
                )


if __name__ == "__main__":
    unittest.main()
