import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from energyhome.analytics import build_demo1_summary, format_console_report


class TestAnalyticsIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            domain = import_module("energyhome.domain")
            dataio = import_module("energyhome.dataio")
            import_module("energyhome.core")
        except ModuleNotFoundError as error:
            if error.name in {"energyhome.domain", "energyhome.dataio", "energyhome.core"}:
                message = "Integration requires real W1-A domain, W1-B dataio and W1-C core"
                raise RuntimeError(message) from error
            raise
        cls.profile_type = domain.LoadProfile
        cls.tariff_type = domain.SingleZoneTariff
        cls.load_profile = staticmethod(dataio.load_demo_profile)

    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        self.tariff = self.tariff_type("single", "Demo", "UTC", 2.5)

    def test_shared_fixture_with_real_core(self):
        timestamps = [self.start + timedelta(minutes=15 * i) for i in range(4)]
        profile = self.profile_type(timestamps, [1.0, 2.0, 0.0, 1.0], 15)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertEqual(summary.period_start, self.start)
        self.assertEqual(summary.period_end_exclusive, self.start + timedelta(hours=1))
        self.assertEqual(summary.interval_count, 4)
        self.assertAlmostEqual(summary.energy_kwh, 1.0)
        self.assertEqual(summary.cost_uah, Decimal("2.50"))
        self.assertIn("Вартість: 2.50 UAH", format_console_report(summary))

    def test_empty_real_profile(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "empty.csv"
            path.write_text("timestamp,power_kw\n", encoding="utf-8")
            profile = self.load_profile(path)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertIsNone(summary.period_start)
        self.assertIsNone(summary.period_end_exclusive)
        self.assertEqual(summary.interval_count, 0)
        self.assertEqual(summary.energy_kwh, 0.0)
        self.assertEqual(summary.cost_uah, Decimal("0.00"))
        self.assertIn("немає даних", format_console_report(summary))

    def test_period_across_repeated_dst_hour(self):
        zone = ZoneInfo("Europe/Kyiv")
        timestamps = [
            datetime(2026, 10, 25, 3, 45, tzinfo=zone, fold=0),
            datetime(2026, 10, 25, 3, 0, tzinfo=zone, fold=1),
        ]
        profile = self.profile_type(timestamps, [1, 1], 15)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertEqual(summary.period_start, datetime(2026, 10, 25, 0, 45, tzinfo=timezone.utc))
        self.assertEqual(
            summary.period_end_exclusive,
            datetime(2026, 10, 25, 1, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(summary.interval_count, 2)
        self.assertEqual(summary.energy_kwh, 0.5)
        self.assertEqual(summary.cost_uah, Decimal("1.25"))

    def test_cli_reads_csv_and_uses_real_analytics(self):
        package = import_module("energyhome")
        env = dict(os.environ, PYTHONPATH=str(Path(package.__file__).resolve().parents[1]))
        cases = (([1, 2, 0, 1], "1.0", "2.50"), ([2, 4, 0, 2], "2.0", "5.00"),
                 ([], "0.0", "0.00"))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config").mkdir()
            (root / "config/tariffs.demo.json").write_text(
                json.dumps({"single": {"rate": 2.5}}), encoding="utf-8"
            )
            for powers, energy, cost in cases:
                with self.subTest(powers=powers):
                    csv_path = root / "meter.csv"
                    rows = ["timestamp,power_kw"]
                    for i, power in enumerate(powers):
                        stamp = self.start + timedelta(minutes=15 * i)
                        rows.append(f"{stamp.isoformat().replace('+00:00', 'Z')},{power}")
                    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, "-m", "energyhome", "report", "--input", str(csv_path)],
                        cwd=root, env=env, capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    self.assertIn(f"Інтервалів: {len(powers)}", result.stdout)
                    self.assertIn(f"Енергія: {energy} kWh", result.stdout)
                    self.assertIn(f"Вартість: {cost} UAH", result.stdout)
                    if not powers:
                        self.assertIn("немає даних", result.stdout)
