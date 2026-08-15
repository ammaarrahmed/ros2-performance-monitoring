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

from dataclasses import dataclass
from dataclasses import fields
import hashlib
import json
import math
from pathlib import Path

from ros2_performance_monitoring import benchmark_layout
from ros2_performance_monitoring.model import MetricRecord
from ros2_performance_monitoring.model import SUPPORTED_SCHEMA_VERSIONS
from ros2_performance_monitoring.writers.jsonl import write_json
from ros2_performance_monitoring.writers.jsonl import write_jsonl


AGGREGATION_FIELDS = frozenset({
    'run_kind',
    'aggregation_method',
    'repeat_count',
})
MODEL_FIELDS = frozenset(field.name for field in fields(MetricRecord))
LEGACY_MODEL_FIELDS = MODEL_FIELDS - AGGREGATION_FIELDS
STRING_FIELDS = frozenset({
    field.name for field in fields(MetricRecord)
    if field.type is str
})
NON_EMPTY_STRING_FIELDS = STRING_FIELDS - {'node_role', 'timestamp'}
RUN_PROVENANCE_FIELDS = (
    'schema_version',
    'timestamp',
    'benchmark_ref',
    'benchmark_commit',
    'client_library_ref',
    'client_library_commit',
    'client_library',
    'client_library_source',
    'platform',
    'ros_distro',
    'executor',
    'run_kind',
    'aggregation_method',
    'repeat_count',
)
SCENARIO_IDENTITY_FIELDS = (
    'topology',
    'process_mode',
    'rmw_implementation',
    'communication_mode',
    'payload_size',
    'frequency',
    'node_role',
)
METRIC_IDENTITY_FIELDS = SCENARIO_IDENTITY_FIELDS + (
    'metric_name',
    'unit',
    'aggregation',
)


class DatasetError(ValueError):
    """Report invalid or incompatible normalized dataset input."""


@dataclass(frozen=True)
class DatasetBuildResult:
    """Summarize the files and records written for one dataset build."""

    record_count: int
    run_count: int
    aggregate_count: int
    manifest_path: Path
    skipped_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class _InputFile:
    path: Path
    checksum: str
    run_ids: tuple[str, ...]


@dataclass
class _Run:
    run_id: str
    input_path: Path
    input_checksum: str
    provenance: dict
    records: list
    identities: dict


def build_dataset(input_paths, output_path, exclude_runs=()):
    """Validate normalized inputs and write a deterministic comparison dataset."""
    output = Path(output_path).expanduser().resolve()
    manifest_path = manifest_path_for(output)
    inputs = _resolve_inputs(input_paths, output, manifest_path)
    runs, input_files = _load_inputs(inputs)
    excluded = frozenset(exclude_runs)
    selected_runs = {
        run_id: run for run_id, run in runs.items()
        if run_id not in excluded
    }
    if not selected_runs:
        raise DatasetError('no normalized runs remain after applying exclusions')

    records = sorted(
        (
            record
            for run in selected_runs.values()
            for record in run.records
        ),
        key=_record_sort_key,
    )
    manifest = _build_manifest(input_files, selected_runs, excluded, output)

    write_json(manifest, manifest_path)
    count = write_jsonl(records, output)
    return DatasetBuildResult(
        record_count=count,
        run_count=len(selected_runs),
        aggregate_count=0,
        manifest_path=manifest_path,
    )


def manifest_path_for(output_path):
    """Return the sidecar manifest path for a dataset output path."""
    output = Path(output_path).expanduser().resolve()
    return output.with_suffix('.manifest.json')


def _resolve_inputs(input_paths, output, manifest_path):
    paths = [Path(path).expanduser().resolve() for path in input_paths]
    if not paths:
        raise DatasetError('at least one normalized JSONL input is required')

    duplicates = sorted({path for path in paths if paths.count(path) > 1})
    if duplicates:
        raise DatasetError(f'duplicate input path: {duplicates[0]}')

    for path in paths:
        if path == output:
            raise DatasetError(f'output path is also an input: {path}')
        if path == manifest_path:
            raise DatasetError(f'manifest path is also an input: {path}')
    return tuple(sorted(paths))


def _load_inputs(paths):
    runs = {}
    input_files = []
    for path in paths:
        try:
            contents = path.read_bytes()
        except FileNotFoundError as exc:
            raise DatasetError(f'normalized input does not exist: {path}') from exc
        except IsADirectoryError as exc:
            raise DatasetError(f'normalized input is not a file: {path}') from exc

        checksum = hashlib.sha256(contents).hexdigest()
        try:
            text = contents.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise DatasetError(f'{path}: invalid UTF-8 at byte {exc.start}') from exc

        input_run_ids = set()
        record_count = 0
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            record_count += 1
            record = _parse_record(line, path, line_number)
            input_run_ids.add(record.run_id)
            _add_record(runs, record, path, checksum, line_number)
        if record_count == 0:
            raise DatasetError(f'{path}: normalized input contains no records')
        input_files.append(_InputFile(path, checksum, tuple(sorted(input_run_ids))))
    return runs, tuple(input_files)


def _parse_record(line, path, line_number):
    context = f'{path}:{line_number}'
    try:
        item = json.loads(line)
    except json.JSONDecodeError as exc:
        raise DatasetError(f'{context}: invalid JSON: {exc.msg}') from exc
    if not isinstance(item, dict):
        raise DatasetError(f'{context}: normalized record must be a JSON object')

    schema_version = item.get('schema_version')
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ', '.join(str(version) for version in SUPPORTED_SCHEMA_VERSIONS)
        raise DatasetError(
            f'{context}: unsupported schema_version {schema_version!r} '
            f'(supported: {supported})'
        )

    expected_fields = MODEL_FIELDS if schema_version >= 5 else LEGACY_MODEL_FIELDS
    missing = sorted(expected_fields - item.keys())
    unexpected = sorted(item.keys() - expected_fields)
    if missing:
        raise DatasetError(f'{context}: normalized record is missing field {missing[0]}')
    if unexpected:
        raise DatasetError(
            f'{context}: normalized record has unexpected field {unexpected[0]}'
        )

    normalized = dict(item)
    if schema_version < 5:
        normalized.update({
            'run_kind': 'measured',
            'aggregation_method': 'none',
            'repeat_count': 1,
        })
    _validate_field_types(normalized, context)
    _validate_aggregation_metadata(normalized, context)
    _validate_benchmark_layout(normalized, context)
    return MetricRecord(**normalized)


def _validate_field_types(item, context):
    for name in STRING_FIELDS:
        if type(item[name]) is not str:
            raise DatasetError(f'{context}: field {name} must be a string')
        if name in NON_EMPTY_STRING_FIELDS and not item[name]:
            raise DatasetError(f'{context}: field {name} must not be empty')

    if type(item['payload_size']) is not int or item['payload_size'] < 0:
        raise DatasetError(f'{context}: field payload_size must be a non-negative integer')
    if type(item['repeat_count']) is not int or item['repeat_count'] < 1:
        raise DatasetError(f'{context}: field repeat_count must be a positive integer')
    for name in ('frequency', 'numeric_value'):
        value = item[name]
        if type(value) not in (int, float):
            raise DatasetError(f'{context}: field {name} must be numeric')
        if not math.isfinite(value):
            raise DatasetError(f'{context}: non-finite metric value in field {name}')


def _validate_aggregation_metadata(item, context):
    if item['run_kind'] == 'measured':
        if item['aggregation_method'] != 'none' or item['repeat_count'] != 1:
            raise DatasetError(
                f'{context}: measured runs require aggregation_method none '
                'and repeat_count 1'
            )
        return
    if item['run_kind'] == 'aggregate':
        if item['aggregation_method'] != 'median' or item['repeat_count'] < 2:
            raise DatasetError(
                f'{context}: aggregate runs require aggregation_method median '
                'and repeat_count of at least 2'
            )
        return
    raise DatasetError(f'{context}: unsupported run_kind {item["run_kind"]!r}')


def _validate_benchmark_layout(item, context):
    family = next((
        candidate
        for candidate in benchmark_layout.BENCHMARK_FAMILIES.values()
        if candidate.topology == item['topology']
        and candidate.process_mode == item['process_mode']
    ), None)
    if family is None:
        raise DatasetError(
            f'{context}: unsupported benchmark layout '
            f'{item["topology"]}/{item["process_mode"]}'
        )

    payload_sizes = {
        payload.size_bytes for payload in benchmark_layout.PAYLOADS.values()
    }
    if item['payload_size'] not in payload_sizes:
        raise DatasetError(f'{context}: unsupported payload size {item["payload_size"]}')

    rmw = next((
        candidate
        for candidate in benchmark_layout.RMW_IMPLEMENTATIONS.values()
        if candidate.implementation_name == item['rmw_implementation']
    ), None)
    if rmw is None:
        raise DatasetError(
            f'{context}: unsupported RMW implementation {item["rmw_implementation"]}'
        )
    if item['communication_mode'] not in family.communication_modes[rmw.short_name]:
        raise DatasetError(
            f'{context}: unsupported communication mode '
            f'{item["communication_mode"]} for {rmw.implementation_name} '
            f'in {family.name}'
        )


def _add_record(runs, record, path, checksum, line_number):
    run = runs.get(record.run_id)
    if run is None:
        run = _Run(
            run_id=record.run_id,
            input_path=path,
            input_checksum=checksum,
            provenance=_record_values(record, RUN_PROVENANCE_FIELDS),
            records=[],
            identities={},
        )
        runs[record.run_id] = run
    elif run.input_path != path:
        raise DatasetError(
            f'{path}:{line_number}: run_id {record.run_id!r} also appears in '
            f'{run.input_path}'
        )

    provenance = _record_values(record, RUN_PROVENANCE_FIELDS)
    if provenance != run.provenance:
        conflicts = sorted(
            name for name in RUN_PROVENANCE_FIELDS
            if provenance[name] != run.provenance[name]
        )
        raise DatasetError(
            f'{path}:{line_number}: conflicting run-level provenance for '
            f'run_id {record.run_id!r}: {", ".join(conflicts)}'
        )

    identity = _record_identity(record)
    if identity in run.identities:
        raise DatasetError(
            f'{path}:{line_number}: duplicate metric identity for '
            f'run_id {record.run_id!r}: {_format_identity(identity)}'
        )
    run.identities[identity] = record
    run.records.append(record)


def _build_manifest(input_files, selected_runs, excluded, output):
    selected_ids = frozenset(selected_runs)
    return {
        'manifest_version': 1,
        'dataset': output.name,
        'aggregation_method': 'none',
        'excluded_run_ids': sorted(excluded),
        'inputs': [
            {
                'path': str(input_file.path),
                'sha256': input_file.checksum,
                'run_ids': [
                    run_id for run_id in input_file.run_ids
                    if run_id in selected_ids
                ],
            }
            for input_file in input_files
        ],
        'aggregates': [],
    }


def _record_values(record, names):
    return {name: getattr(record, name) for name in names}


def _record_identity(record):
    return tuple(getattr(record, name) for name in METRIC_IDENTITY_FIELDS)


def _record_sort_key(record):
    return (record.run_id, *_record_identity(record), record.source_file)


def _format_identity(identity):
    return '/'.join(str(value) for value in identity)
