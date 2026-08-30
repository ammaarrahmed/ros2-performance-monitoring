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

import json
from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_FAILURE = REPOSITORY_ROOT / 'scripts' / 'report-comparison-failure'


def _run_report(results_dir):
    return subprocess.run(
        [str(REPORT_FAILURE), str(results_dir)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_report_prints_failed_attempt_status_and_log_tail(tmp_path):
    attempt = (
        tmp_path / 'trials' / 'reference-measured-001' / 'attempts' / '0001-failed'
    )
    attempt.mkdir(parents=True)
    (attempt / 'status.json').write_text(json.dumps({
        'outcome': 'failed',
        'error': 'benchmark command exited with status 1',
    }))
    lines = [f'line {index}' for index in range(510)]
    (attempt / 'trial.log').write_text('\n'.join(lines) + '\n')

    result = _run_report(tmp_path)

    assert result.returncode == 0
    assert 'reference-measured-001/attempts/0001-failed' in result.stdout
    assert 'benchmark command exited with status 1' in result.stdout
    assert 'line 9\n' not in result.stdout
    assert 'line 10\n' in result.stdout
    assert 'line 509\n' in result.stdout


def test_report_handles_missing_and_empty_trial_directories(tmp_path):
    missing = _run_report(tmp_path)
    assert missing.returncode == 0
    assert 'No trial diagnostics directory exists' in missing.stdout

    (tmp_path / 'trials').mkdir()
    empty = _run_report(tmp_path)
    assert empty.returncode == 0
    assert 'No failed or incomplete trial attempts were found' in empty.stdout


def test_report_rejects_the_wrong_number_of_arguments():
    result = subprocess.run(
        [str(REPORT_FAILURE)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert 'Usage:' in result.stderr
