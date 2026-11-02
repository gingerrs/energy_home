"""Read meter data and print consumption reports."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .domain import EnergyError, TariffError
from .infra import cache_profile, configure_logging, log_call, timed

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Define the report command and its input options."""
    parser = argparse.ArgumentParser(
        prog='energyhome', description='Energy Consumption Analysis System EnergyHome'
    )
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Logging level on stderr (default: INFO)')
    commands = parser.add_subparsers(dest='command', required=True)
    report = commands.add_parser('report', help='Summarize a meter CSV')
    report.add_argument('--input', required=True, help='Meter CSV path')
    report.add_argument('--tariff', default='single',
                        choices=['single', 'two_zone', 'three_zone'],
                        help='Base tariff (default: single)')
    report.add_argument('--tariffs', type=Path, help='Tariff configuration JSON path')
    report.add_argument('--from', dest='date_from', help='Inclusive ISO time with Z or UTC offset')
    report.add_argument('--to', dest='date_to', help='Exclusive ISO time with Z or UTC offset')
    return parser


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        if 'T' not in value:
            raise ValueError('time component is required')
        stamp = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        if stamp.utcoffset() is None:
            raise ValueError('explicit timezone is required')
        return stamp.astimezone(timezone.utc)
    except ValueError as error:
        raise EnergyError(f'Invalid datetime format: {value}; {error}') from error


@cache_profile
@log_call
def _load_registry(path: Path) -> dict:
    """Read modern or legacy tariff entries, requiring an explicit rate."""
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise TariffError('Invalid tariff configuration: expected an object')
    registry = config.get('tariffs', config)
    if not isinstance(registry, dict) or not registry:
        raise TariffError('Invalid tariff registry')
    if 'tariffs' in config:
        if 'schema_version' in config and (
            type(config['schema_version']) is not int or config['schema_version'] != 1
        ):
            raise TariffError('Unsupported tariff schema_version; expected 1')
        for key, expected in (('currency', 'UAH'), ('energy_unit', 'kWh')):
            if key in config and config[key] != expected:
                raise TariffError(f'Unsupported {key}; expected {expected}')
        if 'synthetic' in config and type(config['synthetic']) is not bool:
            raise TariffError('Tariff synthetic flag must be boolean')
    normalized = {}
    for tariff_id, entry in registry.items():
        if not isinstance(entry, dict):
            raise TariffError(f'Invalid tariff {tariff_id!r}: expected an object')
        if 'type' in entry:
            normalized[tariff_id] = entry
        else:
            if tariff_id != 'single' or set(entry) != {'rate'}:
                raise TariffError(f'Invalid legacy tariff {tariff_id!r}: explicit rate required')
            normalized[tariff_id] = dict(type='single', name=tariff_id,
                                         timezone_name='Europe/Kyiv', rate=entry['rate'])
    return normalized


def _source_step(path: Path) -> int:
    metadata_path = path.with_suffix('.quality.json')
    if not metadata_path.exists():
        return 15
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        raise EnergyError(f'Corrupt quality metadata: {error}') from error
    if not isinstance(metadata, dict):
        raise EnergyError('Invalid quality metadata: expected an object')
    step = metadata.get('step_minutes', 15)
    if type(step) is not int or step <= 0:
        raise EnergyError('Invalid quality metadata: step_minutes must be a positive integer')
    from .dataio import SourceConfig

    SourceConfig(step_minutes=step).validate()
    return step


@timed
@log_call
def _render_report(input_path: Path, tariff_id: str, registry: dict,
                   date_from: str | None, date_to: str | None) -> str:
    from .analytics import build_annual_report
    from .dataio import SourceConfig, iter_meter_csv
    from .domain import tariff_from_config

    begin, end = _parse_datetime(date_from), _parse_datetime(date_to)
    if begin is not None and end is not None and begin >= end:
        raise EnergyError('Invalid period: --from must precede --to')
    tariffs = {key: tariff_from_config(key, registry) for key in registry}
    if tariff_id not in tariffs:
        raise TariffError(f'Unknown tariff configuration: {tariff_id}')
    other_id = 'two_zone' if tariff_id == 'single' else 'single'
    step = _source_step(input_path)
    readings = iter_meter_csv(input_path, SourceConfig(step_minutes=step), allow_gaps=True)
    try:
        return build_annual_report(readings, tariffs[tariff_id], tariffs.get(other_id),
                                   step, begin, end)
    except OverflowError as error:
        raise EnergyError('Input values or intervals exceed the supported numeric range') from error


def command_report(args: argparse.Namespace) -> int:
    """Print the report or one readable error, returning a process exit code."""
    try:
        path = Path(args.input)
        if not path.is_file():
            raise EnergyError(f'Input CSV not found: {path}')
        config_path = args.tariffs
        if config_path is None:
            config_path = Path('config/tariffs.json')
            if not config_path.exists():
                config_path = Path('config/tariffs.demo.json')
        if not config_path.is_file():
            raise EnergyError(f'Config file not found: {config_path}')
        registry = _load_registry(config_path)
        print(_render_report(path, args.tariff, registry, args.date_from, args.date_to))
        return 0
    except (EnergyError, ValueError, OSError) as error:
        logger.error('Error: %s', error)
        return 1


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    configure_logging(args.log_level)
    return command_report(args)
