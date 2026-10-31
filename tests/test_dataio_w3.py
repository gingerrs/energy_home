import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from energyhome.dataio import (
    UCI_HOUSEHOLD, DataImporterError, QualityReport, SourceConfig,
    iter_clean_readings, iter_meter_csv, read_chunks,
)
START = datetime(2026, 1, 15, tzinfo=UTC)

def _row(minutes: int, power: str = '1.0') -> str:
    stamp = (START + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
    return f'{stamp},{power}'

def _messy_rows() -> list[str]:
    rows = [_row(15 * i) for i in range(40)]
    rows[17] = _row(15 * 16)        
    rows[25] = _row(15 * 25, 'bad')
    rows[30] = _row(15 * 30, '?')
    del rows[33]                   
    return rows

def _summary(report: QualityReport) -> tuple[object, ...]:
    return (report.rejects, report.gaps, report.last_timestamp, report.line)

class ChunkPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, lines: list[str], name: str = 'meter.csv') -> Path:
        path = Path(self.tmp.name) / name
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def run_pipeline(self, path: Path, size: int) -> tuple[list, tuple]:
        report = QualityReport()
        readings = list(iter_clean_readings(path, report, chunk_size=size))
        return readings, _summary(report)

    def test_chunk_size_independence(self) -> None:
        rows = _messy_rows()
        path = self.write(['timestamp,power_kw', *rows])
        whole = QualityReport()
        expected = (list(iter_meter_csv(path, mode='reject', report=whole)),
                    _summary(whole))
        self.assertEqual(len(whole.rejects), 3)
        self.assertEqual(len(whole.gaps), 4)
        for size in (1, 17, 1000, *range(2, len(rows) + 2)):
            with self.subTest(chunk_size=size):
                self.assertEqual(self.run_pipeline(path, size), expected)

    def test_last_incomplete_chunk_is_kept(self) -> None:
        path = self.write(['timestamp,power_kw',
                           *(_row(15 * i) for i in range(40))])
        chunks = list(read_chunks(path, chunk_size=17))
        self.assertEqual([len(chunk) for chunk in chunks], [17, 17, 6])
        lines = [line for chunk in chunks for line, _ in chunk]
        self.assertEqual(lines, list(range(2, 42)))

    def test_iterator_does_not_read_whole_file_ahead(self) -> None:
        path = self.write(['timestamp,power_kw', _row(0), _row(15),
                           _row(30), '2026-01-15T00:45:00Z,"1.0'])
        chunks = read_chunks(path, chunk_size=2)
        self.assertEqual(len(next(chunks)), 2)
        stream = iter_clean_readings(path, QualityReport(), chunk_size=2)
        self.assertEqual(next(stream).power_kw, 1.0)
        with self.assertRaises(DataImporterError):
            list(chunks)

    def test_uci_adapter_units_missing_values_and_timezone(self) -> None:
        path = self.write([
            'Date;Time;Global_active_power;Voltage',
            '16/12/2006;17:24:00;4.216;234.84',
            '16/12/2006;17:25:00;?;233.63',
            '16/12/2006;17:26:00;5.360;233.29',
        ])
        report = QualityReport()
        readings = list(iter_clean_readings(path, report, UCI_HOUSEHOLD))
        self.assertEqual([r.timestamp.hour for r in readings], [16, 16])
        self.assertEqual([r.power_kw for r in readings], [4.216, 5.36])
        self.assertEqual(report.rejects[0].reason, 'Missing value')
        self.assertEqual(len(report.gaps), 1)

        watts = SourceConfig(power_unit='W')
        path = self.write(['timestamp,power_kw', _row(0, '1500')], 'w.csv')
        stream = iter_clean_readings(path, QualityReport(), watts)
        self.assertEqual(next(stream).power_kw, 1.5)

    def test_invalid_chunk_size_and_unit(self) -> None:
        path = self.write(['timestamp,power_kw', _row(0)])
        for size in (0, -1, 2.0, True):
            with self.subTest(size=size):
                with self.assertRaises(DataImporterError):
                    next(read_chunks(path, chunk_size=size))
        with self.assertRaises(DataImporterError):
            next(read_chunks(path, SourceConfig(power_unit='MW')))
            
