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
            result = main(["report", "--input", "test.csv", "--tariff", "wrong tariff"])
        self.assertEqual(result, 2)

    def test_command_prints_the_report_returned_by_analytics(self):
        output = io.StringIO()
        expected = (
            "Період: приклад\nІнтервалів: 4\n"
            "Енергія: 7.125 kWh\nВартість: 19.37 UAH"
        )
        with patch("energyhome.cli._render_report", return_value=expected) as render:
            with redirect_stdout(output):
                result = main(["report", "--input", "test.csv"])
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), expected + "\n")
        render.assert_called_once_with(Path("test.csv"), "single", 2.5)

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
            patch("energyhome.dataio.load_demo_profile", return_value=sentinel.profile) as load,
            patch("energyhome.analytics.build_demo1_summary") as build,
            patch("energyhome.analytics.format_console_report", return_value="report") as fmt,
        ):
            build.return_value = sentinel.summary
            result = _render_report(Path("measurements.csv"), "single", 2.5)
        self.assertEqual(result, "report")
        load.assert_called_once_with(Path("measurements.csv"))
        tariff.assert_called_once_with("single", "single", "Europe/Kyiv", 2.5)
        build.assert_called_once_with(sentinel.profile, sentinel.tariff)
        fmt.assert_called_once_with(sentinel.summary)

    def test_domain_error_is_preserved_for_cli_handling(self):
        error = DomainError("Invalid measurements")
        with patch("energyhome.dataio.load_demo_profile", side_effect=error):
            with self.assertRaises(EnergyError) as caught:
                _render_report(Path("measurements.csv"), "single", 2.5)
        self.assertIs(caught.exception.__cause__, error)

    def test_expected_report_error_returns_failure(self):
        errors = io.StringIO()
        with patch("energyhome.cli._render_report", side_effect=EnergyError("Invalid data")):
            with redirect_stderr(errors):
                result = main(["report", "--input", "test.csv"])
        self.assertEqual(result, 1)
        self.assertIn("Invalid data", errors.getvalue())
