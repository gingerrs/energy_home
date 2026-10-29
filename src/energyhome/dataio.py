import csv
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from energyhome.domain import EnergyError, LoadProfile, MeterReading  # type: ignore[import-not-found]
except (ImportError, ModuleNotFoundError):

    class EnergyError(Exception):
        """Базовий виняток проєкту."""

    @dataclass(frozen=True)
    class MeterReading:
        timestamp: datetime
        power_kw: float

    @dataclass(frozen=True)
    class LoadProfile:
        timestamps: list[datetime]
        power_kw: list[float]
        resolution_minutes: int

        def __len__(self) -> int:
            return len(self.timestamps)

        def energy_kwh(self) -> float:
            return sum(self.power_kw) * (self.resolution_minutes / 60.0)


class DataImporterError(EnergyError):
    """Помилка імпорту або валідації вхідних даних CSV у strict mode."""


@dataclass(frozen=True)
class SourceConfig:
    # Конфігурація параметрів джерела CSV.

    delimiter: str = ","
    timestamp_format: str = "%Y-%m-%dT%H:%M:%S"
    timezone: str = "Europe/Kyiv"
    step_minutes: int = 15


def parse_timestamp(value: str, fmt: str, tz_name: str) -> datetime:
    # Парсить часову мітку та повертає aware datetime в UTC."
    val_clean = value.strip()
    if val_clean.endswith("Z"):
        try:
            return datetime.fromisoformat(val_clean.replace("Z", "+00:00")).astimezone(
                UTC
            )
        except ValueError:
            pass
    try:
        dt = datetime.strptime(val_clean, fmt)
    except ValueError as err:
        raise ValueError(f"Некоректний формат дати '{val_clean}'") from err

    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as err:
        raise ValueError(f"Невідома часова зона: '{tz_name}'") from err
    return dt.replace(tzinfo=tz).astimezone(UTC)


def iter_meter_csv(
    path: Path | str,
    source_config: SourceConfig | None = None,
) -> Iterator[MeterReading]:
    """
    Потоковий ітератор MeterReading із перевіркою кожного рядка.
    Strict mode будь-яка невідповідність викликає DataImporterError із номером рядка.
    """
    cfg = source_config or SourceConfig()
    file_path = Path(path)
    if not file_path.is_file():
        raise DataImporterError(f"Файл не знайдено за шляхом: {file_path}")

    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=cfg.delimiter)
        if reader.fieldnames is None:
            raise DataImporterError("Рядок 1: порожній заголовок CSV")
        if "timestamp" not in reader.fieldnames or "power_kw" not in reader.fieldnames:
            raise DataImporterError(
                "Рядок 1: відсутні обов'язкові колонки timestamp/power_kw"
            )

        for line_no, row in enumerate(reader, start=2):
            raw_ts = row.get("timestamp")
            raw_p = row.get("power_kw")
            if raw_ts is None or raw_p is None:
                raise DataImporterError(f"Рядок {line_no}: відсутнє обов'язкове поле")

            try:
                ts = parse_timestamp(raw_ts, cfg.timestamp_format, cfg.timezone)
            except ValueError as err:
                raise DataImporterError(
                    f"Рядок {line_no}: помилка дати: {err}"
                ) from err

            try:
                power = float(raw_p.strip())
            except (ValueError, TypeError) as err:
                raise DataImporterError(
                    f"Рядок {line_no}: некоректне число power_kw"
                ) from err

            if power < 0:
                raise DataImporterError(f"Рядок {line_no}: від'ємна потужність {power}")

            yield MeterReading(timestamp=ts, power_kw=power)


def load_demo_profile(
    path: Path | str,
    source_config: SourceConfig | None = None,
) -> LoadProfile | None:
    """
    Завантажує та збирає LoadProfile.
    Порожній CSV повертає задокументований порожній профіль (None).
    """
    cfg = source_config or SourceConfig()
    readings = list(iter_meter_csv(path, cfg))
    if not readings:
        return None

    timestamps = [r.timestamp for r in readings]
    powers = [r.power_kw for r in readings]
    return LoadProfile(
        timestamps=timestamps,
        power_kw=powers,
        resolution_minutes=cfg.step_minutes,
    )
