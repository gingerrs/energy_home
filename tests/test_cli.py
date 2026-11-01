from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch, sentinel

from energyhome.cli import EnergyError, _render_report, main
from energyhome.domain import EnergyError as DomainError


class TestCli(unittest.TestCase):
    def test_report_success(self):
        with patch("energyhome.cli.command_report", return_value=0) as report:
            result = main(["report", "--input", "test.csv"])
            self.assertEqual(result, 0)
            self.assertEqual(report.call_args.args[0].input, "test.csv")
            self.assertEqual(report.call_args.args[0].tariff, "single")

    def test_missing_input(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["report"]), 2)

    def test_invalid_tariff(self):
        with redirect_stderr(io.StringIO()):
            result = main(["report", "--input", "test.csv", "--tariff", "wrong_tariff"])
            self.assertEqual(result, 2)

    def test_command_prints_the_report_returned_by_analytics(self):
        output = io.StringIO()
        expected = (
            "Період: приклад\nІнтервалiв: 4\n"
            "Енергiя: 7.125 kWh\nВартiсть: 19.37 UAH"
        )
        with patch("energyhome.cli._render_report", return_value=expected) as render:
            with redirect_stdout(output):
                result = main(["report", "--input", "test.csv"])
                self.assertEqual(result, 0)
                self.assertEqual(output.getvalue(), expected + "\n")
                render.assert_called_once()

    def test_missing_component_is_an_error_instead_of_a_fake_report(self):
        output, errors = io.StringIO(), io.StringIO()
        error = ModuleNotFoundError("Core is unavailable", name="energyhome.core")

        with patch("energyhome.cli._render_report", side_effect=error):
            with redirect_stdout(output), redirect_stderr(errors):
                result = main(["report", "--input", "test.csv"])
                self.assertEqual(result, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("energyhome.core", errors.getvalue())

    def test_unrelated_missing_module_is_not_hidden(self):
        error = ModuleNotFoundError("Internal import failed", name="internal_dependency")
        with patch("energyhome.cli._render_report", side_effect=error):
            with self.assertRaises(ModuleNotFoundError) as caught:
                main(["report", "--input", "test.csv"])
            self.assertIs(caught.exception, error)

    def test_render_report_passes_inputs_to_analytics(self):
        with (
            patch("energyhome.domain.SingleZoneTariff", return_value=sentinel.tariff) as tariff,
            patch("energyhome.cli._load_cached_profile", return_value=sentinel.profile) as load,
            patch("energyhome.analytics.build_demo1_summary") as build,
            patch("energyhome.analytics.format_console_report", return_value="report") as fmt,
        ):
            build.return_value = sentinel.summary
            result = _render_report(Path("measurements.csv"), "single", 2.5, None, None)
            self.assertEqual(result, "report")
            load.assert_called_once_with(Path("measurements.csv"), tariff="single")
            tariff.assert_called_once_with("single", "single", "Europe/Kiev", 2.5)
            build.assert_called_once_with(sentinel.profile, sentinel.tariff)
            fmt.assert_called_once_with(sentinel.summary)

    def test_domain_error_is_preserved_for_cli_handling(self):
        error = DomainError("Invalid measurements")
        with patch("energyhome.cli._load_cached_profile", side_effect=error):
            with self.assertRaises(EnergyError) as caught:
                _render_report(Path("measurements.csv"), "single", 2.5, None, None)
            self.assertIs(caught.exception.__cause__, error)

    def test_expected_report_error_returns_failure(self):
        errors = io.StringIO()
        with patch("energyhome.cli._render_report", side_effect=EnergyError("Invalid data")):
            with redirect_stderr(errors):
                result = main(["report", "--input", "test.csv"])
                self.assertEqual(result, 1)
                self.assertIn("Invalid data", errors.getvalue())

    def test_report_with_valid_interval_period(self):
        """Тест успішного виконання звіту з коректними параметрами --from та --to"""
        with patch("energyhome.cli._render_report", return_value="report") as render:
            result = main([
                "report", "--input", "test.csv",
                "--from", "2026-01-01T00:00",
                "--to", "2026-01-02T00:00"
            ])
            self.assertEqual(result, 0)
            render.assert_called_once()

    def test_report_invalid_period_from_after_to(self):
        """Edge case: перевірка помилки, коли --from йде пізніше за --to"""
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = main([
                "report", "--input", "test.csv",
                "--from", "2026-01-05T00:00",
                "--to", "2026-01-01T00:00"
            ])
            self.assertEqual(result, 1)
            self.assertIn("Invalid period", errors.getvalue())

    def test_report_invalid_datetime_format(self):
        """Edge case: перевірка невалідного формату дати/часу"""
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = main([
                "report", "--input", "test.csv",
                "--from", "invalid-date-format"
            ])
            self.assertEqual(result, 1)
            self.assertIn("Invalid datetime format", errors.getvalue())