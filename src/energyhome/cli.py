import argparse
import logging
import json
from pathlib import Path
from typing import List, Optional

from .infra import configure_logging, timed

logger = logging.getLogger(__name__)

class EnergyError(Exception):
    """Base exception for EnergyHome business logic errors"""
    pass

def build_parser()->argparse.ArgumentParser:
    """Creates and configures the command-line argument parser"""
    parser = argparse.ArgumentParser(
    prog='energyhome',
    description='Energy Consumption Analysis System EnergyHome')

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Logging level on stderr (default: INFO)')

    subparsers = parser.add_subparsers(dest='command',required=True)
    report_parser = subparsers.add_parser('report',help='')
    report_parser.add_argument(
        '--input',
        type=str,
        required=True,
        help='Path to the input CSV file with energy data')
    report_parser.add_argument(
        '--tariff',
        type=str,
        choices=['single','two_zone','three_zone'],
        default='single',
        help='Tariff type (default:single)'
    )
    return parser

def _render_report(input_path: Path, tariff_id: str, rate: float) -> str:
    from .analytics import build_demo1_summary, format_console_report
    from .dataio import load_demo_profile
    from .domain import EnergyError as DomainError, SingleZoneTariff

    try:
        profile = load_demo_profile(input_path)
        tariff = SingleZoneTariff(tariff_id, tariff_id, "Europe/Kyiv", rate)
        return format_console_report(build_demo1_summary(profile, tariff))
    except DomainError as error:
        raise EnergyError(str(error)) from error


@timed
def command_report(args: argparse.Namespace)-> int:
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

        print(_render_report(input_path, args.tariff, rate))
        return 0

    except ModuleNotFoundError as e:
        if e.name not in {"energyhome.domain", "energyhome.dataio", "energyhome.core"}:
            raise
        logger.error("Error: Required EnergyHome component is unavailable: %s", e.name)
        return 1

    except EnergyError as e:
        logger.error("Error: %s", e)
        return 1

def main(argv: Optional[List[str]] = None)->int:
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
        if isinstance(e.code,int):
            return e.code
        return 2
    
