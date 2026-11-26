import gzip
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from energyhome.dataio import (
    DataImporterError,
    iter_meter_csv,
    load_domain_json,
    resample_readings,
    save_domain_json,
)
from energyhome.domain import MeterReading


class TestW5DataIO(unittest.TestCase):
    """Перевірки потокового стиснення, ресемплінгу та JSON load/save для W5-B."""

    def test_csv_gz_zip_same_normalized_readings(self) -> None:
        csv_content = (
            "timestamp,power_kw\n"
            "2026-01-15T00:00:00Z,1.0\n"
            "2026-01-15T00:15:00Z,0.5\n"
        )
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = tmp_path / "sample.csv"
            gz_path = tmp_path / "sample.csv.gz"
            zip_path = tmp_path / "sample.zip"

            csv_path.write_text(csv_content, encoding="utf-8")
            with gzip.open(gz_path, mode="wt", encoding="utf-8", newline="") as gz_f:
                gz_f.write(csv_content)
            with zipfile.ZipFile(zip_path, mode="w") as zf:
                zf.writestr("sample.csv", csv_content)

            expected = list(iter_meter_csv(csv_path))
            from_gz = list(iter_meter_csv(gz_path))
            from_zip = list(iter_meter_csv(zip_path))

            self.assertEqual(from_gz, expected)
            self.assertEqual(from_zip, expected)

    def test_resample_15_to_60_preserves_energy(self) -> None:
        start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        readings = [
            MeterReading(start + timedelta(minutes=15 * i), 1.0)
            for i in range(4)
        ]

        buckets = list(
            resample_readings(readings, step_in_minutes=15, step_out_minutes=60)
        )

        self.assertEqual(len(buckets), 1)
        self.assertAlmostEqual(buckets[0].energy_kwh, 1.0)
        self.assertAlmostEqual(buckets[0].power_kw, 1.0)
        self.assertTrue(buckets[0].complete)

    def test_chunk_boundary_incomplete_bucket_and_zip_errors(self) -> None:
        start = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        chunk1 = [MeterReading(start + timedelta(minutes=15 * i), 2.0) for i in range(2)]
        chunk2 = [MeterReading(start + timedelta(minutes=15 * 2), 2.0)]

        carry: dict[str, object] = {}
        out_c1 = list(
            resample_readings(
                chunk1,
                step_in_minutes=15,
                step_out_minutes=60,
                flush=False,
                carry_state=carry,
            )
        )
        out_c2 = list(
            resample_readings(
                chunk2,
                step_in_minutes=15,
                step_out_minutes=60,
                flush=True,
                carry_state=carry,
            )
        )

        self.assertEqual(out_c1, [])
        self.assertEqual(len(out_c2), 1)
        self.assertEqual(out_c2[0].samples, 3)
        self.assertFalse(out_c2[0].complete)
        self.assertAlmostEqual(out_c2[0].energy_kwh, 1.5)

        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "multi.zip"
            with zipfile.ZipFile(zip_path, mode="w") as zf:
                zf.writestr("a.csv", "timestamp,power_kw\n")
                zf.writestr("b.csv", "timestamp,power_kw\n")

            with self.assertRaises(DataImporterError):
                list(iter_meter_csv(zip_path))

            json_path = Path(tmp) / "domain.json"
            payload = {"house_id": "h1", "devices": []}
            save_domain_json(json_path, payload)
            self.assertEqual(load_domain_json(json_path), payload)


if __name__ == "__main__":
    unittest.main()