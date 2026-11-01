import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import islice
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
    """Параметри CSV, колонок, одиниць і часової сітки джерела.

    Мітка часу збирається з `timestamp_columns` через пробіл; потужність
    з `power_column` множиться на коефіцієнт `power_unit` ('kW' або 'W').
    Значення з `missing_values` дають reject «Missing value».
    """

    delimiter: str = ","
    timestamp_format: str = "%Y-%m-%dT%H:%M:%S"
    timezone: str = "Europe/Kyiv"
    step_minutes: int = 15
    timestamp_columns: tuple[str, ...] = ("timestamp",)
    power_column: str = "power_kw"
    power_unit: str = "kW"
    missing_values: tuple[str, ...] = ("", "?", "NA")


_UNIT_TO_KW = {"kW": 1.0, "W": 0.001}

UCI_HOUSEHOLD = SourceConfig(
    delimiter=";", timestamp_format="%d/%m/%Y %H:%M:%S",
    timezone="Europe/Paris", step_minutes=1,
    timestamp_columns=("Date", "Time"), power_column="Global_active_power",
)
"""Адаптер UCI Individual household electric power consumption (CC BY 4.0)."""


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
    *, allow_gaps: bool = False,
) -> Iterator[MeterReading]:
    """Перевіряє записи; allow_gaps дозволяє прогалини без накопичення їхнього журналу."""
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
        if delta > step and not allow_gaps:
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
    result = dt.replace(tzinfo=tz).astimezone(UTC)
    if result.astimezone(tz).replace(tzinfo=None) != dt:
        raise ValueError('Nonexistent local timestamp')
    return result


def iter_meter_csv(
    path: Path | str,
    source_config: SourceConfig | None = None,
    mode: str = 'strict',
    report: QualityReport | None = None,
    *, allow_gaps: bool = False,
) -> Iterator[MeterReading]:
    """Читає CSV потоково та передає записи спільній перевірці strict/reject."""
    cfg = source_config or SourceConfig()
    if mode == 'reject' and report is None:
        raise DataImporterError('Reject mode requires a QualityReport')
    report = report if report is not None else QualityReport()
    rows = _iter_meter_rows(path, cfg, report)
    yield from clean_readings(rows, cfg.step_minutes, mode, report, allow_gaps=allow_gaps)


def _iter_meter_rows(
    path: Path | str, cfg: SourceConfig, report: QualityReport,
) -> Iterator[MeterReading | RejectRecord]:
    """Читає сирі рядки, зберігаючи фізичні номери та помилки перетворення."""
    file_path = Path(path)
    if not file_path.is_file():
        raise DataImporterError(f"Файл не знайдено за шляхом: {file_path}")
    if cfg.power_unit not in _UNIT_TO_KW:
        raise DataImporterError(
            f"Невідома одиниця потужності: {cfg.power_unit}")
    columns = (*cfg.timestamp_columns, cfg.power_column)

    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=cfg.delimiter, strict=True)
        try:
            if reader.fieldnames is None:
                raise DataImporterError("Рядок 1: порожній заголовок CSV")
            if len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise DataImporterError('Рядок 1: повторювані назви колонок')
            if any(name not in reader.fieldnames for name in columns):
                names = '/'.join(columns)
                raise DataImporterError(f"Рядок 1: відсутні колонки {names}")
            for row in reader:
                report.line = reader.line_num - 1
                try:
                    if None in row or any(row.get(c) is None for c in columns):
                        raise ValueError('Missing field or extra CSV value')
                    values = [row[c].strip() for c in columns]
                    if any(v in cfg.missing_values for v in values):
                        raise ValueError('Missing value')
                    ts = parse_timestamp(' '.join(values[:-1]),
                                         cfg.timestamp_format, cfg.timezone)
                    power = float(values[-1]) * _UNIT_TO_KW[cfg.power_unit]
                    reading = MeterReading(ts, power)
                except (ValueError, TypeError) as error:
                    reading = RejectRecord(reader.line_num, str(error), dict(row))
                yield reading
        except csv.Error as error:
            message = f'Рядок {reader.reader.line_num}: Invalid CSV: {error}'
            raise DataImporterError(message) from error


def read_chunks(
    path: Path | str, source_config: SourceConfig | None = None,
    chunk_size: int = 1000, year: int | None = None,
) -> Iterator[list[tuple[int, MeterReading | RejectRecord]]]:
    """Ліниво читає CSV блоками до `chunk_size` пар (рядок, сирий запис).

    Наступний блок читається лише на запит; останній неповний блок
    повертається. Помилки структури файлу виникають при читанні їхнього блоку.
    Опційний year відбирає календарний рік джерела до поділу на чанки;
    роки джерела мають іти послідовно. Фізичні номери рядків зберігаються.
    """
    if type(chunk_size) is not int or chunk_size <= 0:
        raise DataImporterError('Chunk size must be a positive integer')
    if year is not None and (type(year) is not int or not 1 <= year <= 9998):
        raise DataImporterError('Year must be an integer between 1 and 9998')
    position = QualityReport()
    cfg = source_config or SourceConfig()
    rows = _iter_meter_rows(path, cfg, position)
    numbered = ((position.line + 1, row) for row in rows)
    if year is not None:
        numbered = _year_rows(numbered, cfg, year)
    while chunk := list(islice(numbered, chunk_size)):
        yield chunk


def _year_rows(rows: Iterable[tuple[int, MeterReading | RejectRecord]], cfg: SourceConfig,
               year: int) -> Iterator[tuple[int, MeterReading | RejectRecord]]:
    """Select a source-calendar year before chunk lookahead; input years must be ordered."""
    for line, item in rows:
        try:
            if isinstance(item, MeterReading):
                stamp = item.timestamp.astimezone(ZoneInfo(cfg.timezone))
            else:
                raw = ' '.join(item.value[column] for column in cfg.timestamp_columns)
                stamp = (datetime.fromisoformat(raw) if raw.endswith('Z') else
                         datetime.strptime(raw, cfg.timestamp_format))
                if stamp.tzinfo is not None:
                    stamp = stamp.astimezone(ZoneInfo(cfg.timezone))
        except (ValueError, TypeError, KeyError) as error:
            raise DataImporterError(f'Рядок {line}: cannot determine source year') from error
        if stamp.year > year:
            return
        if stamp.year == year:
            yield line, item


def iter_clean_readings(
    path: Path | str, report: QualityReport,
    source_config: SourceConfig | None = None, chunk_size: int = 1000,
    mode: str = 'reject',
) -> Iterator[MeterReading]:
    """Потік очищених UTC-вимірювань (kW на початку інтервалу) з чанків.

    Стан останньої мітки, дублів і номерів рядків живе в `report`, тому
    результат і журнал не залежать від `chunk_size`.
    """
    cfg = source_config or SourceConfig()
    for chunk in read_chunks(path, cfg, chunk_size):
        yield from clean_readings(_numbered(chunk, report),
                                  cfg.step_minutes, mode, report)

def _numbered(
    chunk: list[tuple[int, MeterReading | RejectRecord]],
    report: QualityReport,
) -> Iterator[MeterReading | RejectRecord]:
    """Відновлює фізичний номер рядка в report перед перевіркою запису."""
    for line, item in chunk:
        report.line = line - 1
        yield item


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
