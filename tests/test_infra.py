import io
import logging
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from inspect import signature
from unittest.mock import Mock, patch

from energyhome.cli import main
from energyhome.infra import configure_logging, timed, validate_positive


class TestLogging(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("energyhome")
        self.handlers = self.logger.handlers[:]
        self.level = self.logger.level
        self.propagate = self.logger.propagate
        self.logger.handlers.clear()

    def tearDown(self):
        for handler in self.logger.handlers:
            if handler not in self.handlers:
                handler.close()
        self.logger.handlers[:] = self.handlers
        self.logger.setLevel(self.level)
        self.logger.propagate = self.propagate

    def test_repeated_setup_has_one_handler_and_does_not_touch_root(self):
        root = logging.getLogger()
        original = (root.handlers[:], root.level)
        output = io.StringIO()
        with redirect_stderr(output):
            first = configure_logging("INFO")
            second = configure_logging("DEBUG")
            logging.getLogger("energyhome.cli").debug("ready")
        self.assertIs(first, second)
        self.assertEqual(len(second.handlers), 1)
        self.assertEqual(output.getvalue(), "DEBUG energyhome.cli: ready\n")
        self.assertEqual((root.handlers, root.level), original)

    def test_reconfiguration_uses_current_stderr_even_after_old_stream_is_closed(self):
        with io.StringIO() as old, redirect_stderr(old):
            configure_logging("INFO")
        current = io.StringIO()
        with redirect_stderr(current):
            configure_logging("WARNING")
            self.logger.info("hidden")
            self.logger.warning("visible")
        self.assertEqual(current.getvalue(), "WARNING energyhome: visible\n")

    def test_foreign_handler_is_preserved(self):
        foreign = logging.NullHandler()
        self.logger.addHandler(foreign)
        configure_logging()
        configure_logging()
        self.assertIn(foreign, self.logger.handlers)
        self.assertEqual(len(self.logger.handlers), 2)

    def test_invalid_level_does_not_add_handlers(self):
        with self.assertRaises(ValueError):
            configure_logging("UNKNOWN")
        self.assertEqual(self.logger.handlers, [])

    def test_import_does_not_configure_logging_or_print(self):
        script = (
            "import logging; "
            "root = logging.getLogger(); app = logging.getLogger('energyhome'); "
            "before = (root.handlers[:], root.level, app.handlers[:], app.level, app.propagate); "
            "import energyhome.infra; import energyhome.cli; "
            "assert before == (root.handlers, root.level, app.handlers, app.level, app.propagate)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, result.stderr), ("", ""))

    def test_cli_debug_logs_timing_to_stderr_and_report_to_stdout(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch("energyhome.cli._render_report", return_value="report"):
            with redirect_stdout(output), redirect_stderr(errors):
                code = main(["--log-level", "DEBUG", "report", "--input", "test.csv"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "report\n")
        self.assertIn(
            "DEBUG energyhome.timing: energyhome.cli.command_report took", errors.getvalue()
        )

    def test_cli_errors_are_logged_once_without_traceback(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch("energyhome.cli.Path.exists", return_value=False):
            with redirect_stdout(output), redirect_stderr(errors):
                code = main(["report", "--input", "absent.csv"])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            errors.getvalue(), "ERROR energyhome.cli: Error: Input CSV not found: absent.csv\n"
        )

    def test_invalid_cli_log_level_returns_parser_error(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--log-level", "VERBOSE", "report", "--input", "x"]), 2)


class TestTimed(unittest.TestCase):
    def test_result_metadata_and_timing_are_preserved(self):
        def calculate(value: int, *, extra: int = 2) -> int:
            """Example calculation."""
            return value + extra

        wrapped = timed(calculate)
        with patch("energyhome.infra.perf_counter", side_effect=[10.0, 10.125]):
            with self.assertLogs("energyhome.timing", "DEBUG") as records:
                self.assertEqual(wrapped(3, extra=4), 7)
        self.assertIn("took 0.125000 s", records.output[0])
        self.assertEqual(wrapped.__name__, calculate.__name__)
        self.assertEqual(wrapped.__doc__, calculate.__doc__)
        self.assertEqual(signature(wrapped), signature(calculate))
        self.assertIs(wrapped.__wrapped__, calculate)

    def test_original_exception_is_propagated_and_call_is_timed(self):
        error = RuntimeError("failure")

        @timed
        def fail():
            raise error

        with patch("energyhome.infra.perf_counter", side_effect=[1.0, 1.25]):
            with self.assertLogs("energyhome.timing", "DEBUG") as records:
                with self.assertRaises(RuntimeError) as caught:
                    fail()
        self.assertIs(caught.exception, error)
        self.assertIn("took 0.250000 s", records.output[0])


class TestValidatePositive(unittest.TestCase):
    def test_positional_keyword_and_default_values_keep_function_contract(self):
        def calculate(value: float = 2.0, *, factor: float = 3.0) -> float:
            """Scale a positive value."""
            return value * factor

        wrapped = validate_positive("value")(calculate)
        self.assertEqual(wrapped(), 6)
        self.assertEqual(wrapped(4, factor=2), 8)
        self.assertEqual(wrapped(value=0.5), 1.5)
        self.assertEqual(wrapped.__name__, calculate.__name__)
        self.assertEqual(wrapped.__doc__, calculate.__doc__)
        self.assertEqual(signature(wrapped), signature(calculate))
        self.assertIs(wrapped.__wrapped__, calculate)

    def test_invalid_values_are_rejected_before_function_is_called(self):
        called = Mock()

        @validate_positive("value")
        def accept(value):
            return called(value)

        for value in (0, -1, -0.0, float("nan"), float("inf"), -float("inf"),
                      True, False, "1", None, 1j):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "value must be a finite positive number"):
                    accept(value)
        called.assert_not_called()

    def test_positive_boundaries_are_preserved_without_numeric_conversion(self):
        @validate_positive("value")
        def accept(value):
            return value

        for value in (1, 10 ** 400, 5e-324, sys.float_info.max):
            with self.subTest(value=value):
                self.assertIs(accept(value), value)

    def test_keyword_only_and_positional_only_parameters(self):
        @validate_positive("value")
        def positional(value, /):
            return value

        @validate_positive("value")
        def keyword(*, value):
            return value

        self.assertEqual(positional(1), 1)
        self.assertEqual(keyword(value=2), 2)
        with self.assertRaises(ValueError):
            keyword(value=0)

    def test_method_and_stacked_decorators(self):
        class Example:
            @timed
            @validate_positive("value")
            def accept(self, value):
                return value

        self.assertEqual(Example().accept(value=3), 3)
        with self.assertRaises(ValueError):
            Example().accept(-1)

    def test_invalid_default_is_checked_at_call_time(self):
        @validate_positive("value")
        def accept(value=0):
            return value

        with self.assertRaises(ValueError):
            accept()
        self.assertEqual(accept(1), 1)

    def test_unknown_or_variadic_parameter_is_rejected_at_decoration_time(self):
        def accept(value, *args, **kwargs):
            return value

        for parameter in ("missing", "args", "kwargs"):
            with self.subTest(parameter=parameter):
                with self.assertRaises(ValueError):
                    validate_positive(parameter)(accept)

    def test_invalid_calls_keep_type_errors(self):
        @validate_positive("value")
        def accept(value):
            return value

        for args, kwargs in (((), {}), ((1, 2), {}), ((1,), {"value": 2}),
                             ((), {"unknown": 1})):
            with self.subTest(args=args, kwargs=kwargs):
                with self.assertRaises(TypeError):
                    accept(*args, **kwargs)

    def test_wrapped_exception_is_not_replaced(self):
        error = RuntimeError("function failure")

        @validate_positive("value")
        def accept(value):
            raise error

        with self.assertRaises(RuntimeError) as caught:
            accept(1)
        self.assertIs(caught.exception, error)
