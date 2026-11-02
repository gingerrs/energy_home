"""Smoke-test the installed wheel from a directory outside the checkout."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the installed package without source-tree import overrides."""
    env = dict(os.environ, PYTHONUTF8='1')
    env.pop('PYTHONPATH', None)
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, text=True,
                          encoding='utf-8', capture_output=True, timeout=30)


def main() -> None:
    """Check import location, help, real data, custom config and error handling."""
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        imported = run(folder, '-c', 'import energyhome; print(energyhome.__file__)')
        if imported.returncode or Path(imported.stdout.strip()).is_relative_to(ROOT / 'src'):
            raise RuntimeError(f'The installed package is unavailable: {imported.stderr}')
        path = folder / 'meter.csv'
        path.write_bytes((ROOT / 'data/demo/meter_15min.csv').read_bytes())
        config = folder / 'tariffs.json'
        config.write_bytes((ROOT / 'config/tariffs.json').read_bytes())
        base = ['-m', 'energyhome', 'report', '--input', str(path), '--tariffs', str(config)]
        result = run(folder, *base)
        expected = (ROOT / 'docs/demo1/expected_small.txt').read_text(encoding='utf-8')
        if result.returncode or result.stdout != expected or result.stderr:
            raise RuntimeError(f'Installed demo differs from control: {result.stderr}')
        help_result = run(folder, '-m', 'energyhome', 'report', '--help')
        if help_result.returncode or '--tariffs' not in help_result.stdout:
            raise RuntimeError('Installed CLI help is incomplete')
        selected = run(folder, *base, '--from', '2026-01-15T00:15:00Z',
                        '--to', '2026-01-15T00:30:00Z')
        if selected.returncode or 'Вартість: 1.25 UAH' not in selected.stdout:
            raise RuntimeError(f'Installed period selection failed: {selected.stderr}')
        config.write_text(json.dumps({'single': {'rate': 1.0}}), encoding='utf-8')
        custom = run(folder, *base)
        if custom.returncode or 'Вартість: 1.00 UAH' not in custom.stdout:
            raise RuntimeError(f'Installed custom config failed: {custom.stderr}')
        config.write_text('{"single": {}}', encoding='utf-8')
        invalid = run(folder, *base)
        if invalid.returncode != 1 or invalid.stdout or 'Traceback' in invalid.stderr:
            raise RuntimeError('Installed CLI does not reject invalid configuration cleanly')
        print('Installed wheel: import, help, shared demo, bounds, custom config and errors OK')


if __name__ == '__main__':
    main()
