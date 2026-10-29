import unittest
from pathlib import Path

from energyhome.dataio import (
    DataImporterError,
    SourceConfig,
    iter_meter_csv,
    load_demo_profile,
)


class TestDataIOW1(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures_dir = Path("tests/fixtures")
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)
        self.config = SourceConfig(step_minutes=15)

    def test_valid_csv_4_records_and_one_kwh(self) -> None:
        """Вимога 1: Валідний CSV повертає 4 записи; підсумок 1.0 kWh при step=15."""
        path = Path("data/demo/meter_15min.csv")
        profile = load_demo_profile(path, self.config)
        self.assertIsNotNone(profile)
        self.assertEqual(len(profile), 4)
        self.assertAlmostEqual(profile.energy_kwh(), 1.0)

    def test_malformed_csv_raises_error_with_line_number(self) -> None:
        """Вимога 2: Неправильний заголовок/число/timestamp -> DataImporterError з номером рядка."""
        path = self.fixtures_dir / "malformed_meter.csv"
        with self.assertRaises(DataImporterError) as ctx:
            list(iter_meter_csv(path, self.config))
        self.assertIn("Рядок 3", str(ctx.exception))

    def test_empty_csv_and_missing_file_handling(self) -> None:
        """Вимога 3: Порожній CSV дає порожній профіль; відсутній шлях -> читабельна помилка."""
        empty_path = self.fixtures_dir / "empty_meter.csv"
        profile = load_demo_profile(empty_path, self.config)
        self.assertIsNone(profile)

        missing_path = Path("non_existent_file.csv")
        with self.assertRaises(DataImporterError) as ctx:
            list(iter_meter_csv(missing_path, self.config))
        self.assertIn("Файл не знайдено", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
