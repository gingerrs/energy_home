import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from energyhome.domain import (
    CyclicDevice, EnergyError, FixedDevice, LoadProfile, MeterDataError, MeterReading,
)


class DataImporterError(EnergyError):
    """Помилка імпорту або валідації вхідних даних джерела."""


@dataclass(frozen=True)
class SourceConfig:
    """Параметри CSV та часової сітки джерела."""

    delimiter: str = ","
    timestamp_format: str = "%Y-%m-%dT%H:%M:%S"
    timezone: str = "Europe/Kyiv"
    step_minutes: int = 15


@dataclass(frozen=True)
class RejectRecord:
    """Номер рядка, причина та початкове значення проблемного запису."""

    line: int
    reason: str
    value: object


@dataclass
class QualityReport:
    """Журнал якості та стан перевірки, спільний для послідовних чанків."""

    rejects: list[RejectRecord] = field(default_factory=list)
    gaps: list[RejectRecord] = field(default_factory=list)
    last_timestamp: datetime | None = None
    line: int = 1


def clean_readings(
    readings: Iterable[MeterReading | RejectRecord], step_minutes: int = 15,
    mode: str = 'strict', report: QualityReport | None = None,
) -> Iterator[MeterReading]:
    """Перевіряє записи по черзі; reject пропускає помилки без заповнення прогалин."""
    if mode not in ('strict', 'reject'):
        raise DataImporterError('Mode must be strict or reject')
    if mode == 'reject' and report is None:
        raise DataImporterError('Reject mode requires a QualityReport')
    if type(step_minutes) is not int or step_minutes <= 0:
        raise DataImporterError('Step must be a positive integer')
    report = report if report is not None else QualityReport()
    step = timedelta(minutes=step_minutes)
    for reading in readings:
        report.line += 1
        record = reading if isinstance(reading, RejectRecord) else None
        if record is None:
            try:
                point = LoadProfile([reading.timestamp], [reading.power_kw], step_minutes)
                timestamp = point.start_time()
                delta = timestamp - report.last_timestamp if report.last_timestamp else step
                if delta <= timedelta(0) or delta % step:
                    raise MeterDataError('Duplicate, out-of-order or off-grid timestamp')
            except MeterDataError as error:
                record = RejectRecord(report.line, str(error), reading)
        if record is not None:
            report.rejects.append(record)
            if mode == 'strict':
                raise DataImporterError(f'Рядок {record.line}: {record.reason}')
            continue
        if delta > step:
            gap = RejectRecord(report.line, 'Missing intervals',
                               (report.last_timestamp + step, timestamp))
            report.gaps.append(gap)
            if mode == 'strict':
                raise DataImporterError(f'Рядок {gap.line}: {gap.reason}')
        report.last_timestamp = timestamp
        yield MeterReading(timestamp, point.power_kw[0])


def validate_readings(
    readings: Iterable[MeterReading | RejectRecord], step_minutes: int = 15,
    report: QualityReport | None = None,
) -> QualityReport:
    """Переглядає весь ряд та повертає журнал без зупинки на поганому записі."""
    report = report if report is not None else QualityReport()
    for _ in clean_readings(readings, step_minutes, 'reject', report):
        pass
    return report


def parse_timestamp(value: str, fmt: str, tz_name: str) -> datetime:
    """Перетворює часову мітку джерела на datetime із часовим поясом UTC."""
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
    mode: str = 'strict',
    report: QualityReport | None = None,
) -> Iterator[MeterReading]:
    """Читає CSV потоково та передає записи спільній перевірці strict/reject."""
    cfg = source_config or SourceConfig()
    if mode == 'reject' and report is None:
        raise DataImporterError('Reject mode requires a QualityReport')
    report = report if report is not None else QualityReport()
    rows = _iter_meter_rows(path, cfg, report)
    yield from clean_readings(rows, cfg.step_minutes, mode, report)


def _iter_meter_rows(
    path: Path | str, cfg: SourceConfig, report: QualityReport,
) -> Iterator[MeterReading | RejectRecord]:
    """Читає сирі рядки, зберігаючи фізичні номери та помилки перетворення."""
    file_path = Path(path)
    if not file_path.is_file():
        raise DataImporterError(f"Файл не знайдено за шляхом: {file_path}")

    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=cfg.delimiter, strict=True)
        try:
            if reader.fieldnames is None:
                raise DataImporterError("Рядок 1: порожній заголовок CSV")
            if len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise DataImporterError('Рядок 1: повторювані назви колонок')
            if "timestamp" not in reader.fieldnames or "power_kw" not in reader.fieldnames:
                raise DataImporterError("Рядок 1: відсутні колонки timestamp/power_kw")
            for row in reader:
                report.line = reader.line_num - 1
                try:
                    if None in row or row.get('timestamp') is None or row.get('power_kw') is None:
                        raise ValueError('Missing field or extra CSV value')
                    ts = parse_timestamp(row['timestamp'], cfg.timestamp_format, cfg.timezone)
                    reading = MeterReading(ts, float(row['power_kw']))
                except (ValueError, TypeError) as error:
                    reading = RejectRecord(reader.line_num, str(error), dict(row))
                yield reading
        except csv.Error as error:
            message = f'Рядок {reader.reader.line_num}: Invalid CSV: {error}'
            raise DataImporterError(message) from error


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


def generate_demo_consumption(
    seed: int = 0,
) -> tuple[LoadProfile, tuple[tuple[datetime, str, str], ...]]:
    """Повертає чотири години base + cycle + noise та точні мітки on/off."""
    if type(seed) is not int:
        raise DataImporterError('Seed must be an integer')
    rng = Random(seed)
    start = datetime(2026, 1, 15, tzinfo=UTC)
    period = (start, start + timedelta(hours=4))
    base = FixedDevice('base', 0.25).profile_for(period, 15)
    device = CyclicDevice('heater', 1.5, 60, start_minute=60)
    cycle = device.profile_for(period, 15)
    powers = [round(a + b + rng.uniform(-0.02, 0.02), 6)
              for a, b in zip(base.power_kw, cycle.power_kw)]
    labels = tuple((start + timedelta(minutes=minute), device.device_id, event)
                   for minute, event in ((device.start_minute, 'on'),
                                         (device.start_minute + device.cycle_minutes, 'off')))
    return LoadProfile(base.timestamps, powers, 15), labels
