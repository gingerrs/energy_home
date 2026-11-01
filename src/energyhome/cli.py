"""Command-line interface for EnergyHome system."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .infra import cache_profile, configure_logging, log_call, timed

logger = logging.getLogger(__name__)


class EnergyError(Exception):
    """Base exception for EnergyHome business logic errors"""
    pass


def build_parser() -> argparse.ArgumentParser:
    """Creates and configures the command-line argument parser"""
    parser = argparse.ArgumentParser(
        prog='energyhome',
        description='Energy Consumption Analysis System EnergyHome'
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Logging level on stderr (default: INFO)'
    )

    subparsers = parser.add_subparsers(dest='command', required=True)
    report_parser = subparsers.add_parser('report', help='')
    
    report_parser.add_argument(
        '--input',
        type=str,
        required=True,
        help='Path to the input CSV file with energy data'
    )
    report_parser.add_argument(
        '--tariff',
        type=str,
        choices=['single', 'two_zone', 'three_zone'],
        default='single',
        help='Tariff type (default:single)'
    )
    report_parser.add_argument(
        '--from',
        dest='date_from',
        type=str,
        default=None,
        help='Start date/time for the report interval (inclusive)'
    )
    report_parser.add_argument(
        '--to',
        dest='date_to',
        type=str,
        default=None,
        help='End date/time for the report interval (exclusive)'
    )

    return parser


def _parse_datetime(dt_str: str | None) -> datetime | None:
    """Parses a datetime string into a datetime object."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError as e:
        raise EnergyError(f"Invalid datetime format: {dt_str}") from e


@cache_profile
@log_call
def _load_cached_profile(input_path: Path, tariff: str):
    from .dataio import load_demo_profile
    return load_demo_profile(input_path)


@timed
def _render_report(input_path: Path, tariff_id: str, rate: float, date_from: str | None, date_to: str | None) -> str:
    from .analytics import build_demo1_summary, format_console_report, SubProfile
    from .domain import EnergyError as DomainError, SingleZoneTariff

    dt_from = _parse_datetime(date_from)
    dt_to = _parse_datetime(date_to)

    if dt_from and dt_to and dt_from > dt_to:
        raise EnergyError("Invalid period: --from cannot be later than --to")

    try:
        # Використовуємо кешовану та залоговану загрузку профілю
        profile = _load_cached_profile(input_path, tariff=tariff_id)
        
        # Фільтрація за напіввідкритим періодом [from, to) якщо задано
        if (dt_from or dt_to) and hasattr(profile, "timestamps") and hasattr(profile, "power_kw"):
            filtered_ts = []
            filtered_powers = []
            for ts, power in zip(profile.timestamps, profile.power_kw):
                if dt_from and ts < dt_from:
                    continue
                if dt_to and ts >= dt_to:
                    continue
                filtered_ts.append(ts)
                filtered_powers.append(power)
            
            resolution = getattr(profile, "resolution_minutes", 15)
            profile = SubProfile(filtered_ts, filtered_powers, resolution)

        tariff = SingleZoneTariff(tariff_id, tariff_id, "Europe/Kiev", rate)
        summary = build_demo1_summary(profile, tariff)
        return format_console_report(summary)
    except DomainError as error:
        raise EnergyError(str(error)) from error


def command_report(args: argparse.Namespace) -> int:
    """
    Executes the report generation command

    args: Parsed command-line arguments
    """
    try:
        input_path = Path(args.input)
        if not input_path.exists():
            raise EnergyError(f"Input CSV not found: {input_path}")

        config_path = Path("config/tariffs.demo.json")
        if not config_path.exists():
            raise EnergyError(f"Config file not found: {config_path}")

        with open(config_path, "r", encoding="utf-8-sig") as f:
            tariffs = json.load(f)

        if args.tariff not in tariffs:
            raise EnergyError(f"Unknown tariff configuration: {args.tariff}")

        rate = tariffs[args.tariff]["rate"]

        print(_render_report(input_path, args.tariff, rate, args.date_from, args.date_to))
        return 0

    except ModuleNotFoundError as e:
        if e.name not in {"energyhome.domain", "energyhome.dataio", "energyhome.core"}:
            raise
        logger.error("Error: Required EnergyHome component is unavailable: %s", e.name)
        return 1

    except EnergyError as e:
        logger.error("Error: %s", e)
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for the CLI application

    argv: Command-line arguments list
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        configure_logging(args.log_level)
        if args.command == 'report':
            return command_report(args)
        parser.print_help()
        return 1
    except SystemExit as e:
        if isinstance(e.code, int):
            return e.code
        return 2