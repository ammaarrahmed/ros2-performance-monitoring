# Copyright 2026 Ammaar Ahmed
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUN_WITH_RETRY = REPOSITORY_ROOT / 'scripts' / 'run-comparison-with-retry'


def _write_command(tmp_path):
    command = tmp_path / 'comparison_command.py'
    command.write_text(
        'from pathlib import Path\n'
        'import sys\n'
        'state = Path(sys.argv[1])\n'
        'results = Path(sys.argv[2])\n'
        'exit_codes = [int(value) for value in sys.argv[3].split(",")]\n'
        'attempt = int(state.read_text()) + 1 if state.exists() else 1\n'
        'state.write_text(str(attempt))\n'
        'print(f"command attempt {attempt}")\n'
        'exit_code = exit_codes[min(attempt - 1, len(exit_codes) - 1)]\n'
        'if exit_code == 4:\n'
        '    failed = results / "trials/reference/attempts" / f"{attempt:04d}-failed"\n'
        '    failed.mkdir(parents=True)\n'
        '    (failed / "trial.log").write_text(f"inner failure {attempt}\\n")\n'
        'raise SystemExit(exit_code)\n'
    )
    return command


def _run_retry(tmp_path, exit_codes, max_attempts=2):
    state = tmp_path / 'state'
    results = tmp_path / 'results'
    log = tmp_path / 'comparison.log'
    command = _write_command(tmp_path)
    result = subprocess.run(
        [
            str(RUN_WITH_RETRY),
            str(log),
            str(results),
            str(max_attempts),
            '--',
            sys.executable,
            str(command),
            str(state),
            str(results),
            exit_codes,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result, state, log


def test_operational_failure_is_reported_and_retried_once(tmp_path):
    result, state, log = _run_retry(tmp_path, '4,0')

    assert result.returncode == 0
    assert state.read_text() == '2'
    assert 'inner failure 1' in result.stdout
    assert log.read_text() == 'command attempt 1\ncommand attempt 2\n'


def test_completed_comparison_outcome_is_not_retried(tmp_path):
    result, state, log = _run_retry(tmp_path, '3')

    assert result.returncode == 3
    assert state.read_text() == '1'
    assert log.read_text() == 'command attempt 1\n'


def test_final_operational_failure_is_returned_after_bound(tmp_path):
    result, state, log = _run_retry(tmp_path, '4,4')

    assert result.returncode == 4
    assert state.read_text() == '2'
    assert log.read_text() == 'command attempt 1\ncommand attempt 2\n'


def test_retry_rejects_an_invalid_attempt_limit(tmp_path):
    result = subprocess.run(
        [str(RUN_WITH_RETRY), str(tmp_path / 'log'), str(tmp_path), '0', '--', 'true'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert 'positive integer' in result.stderr
