"""M3 transaction extraction and structural validation.

This module identifies source-level transaction regions.  It deliberately does
not assign handshake behavior, analyze communication dependencies, or rewrite
conditional communication.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    If,
    Parallel,
    Process,
    Receive,
    Send,
    Sequence,
    Skip,
    SourceLocation,
)


SourcePath = tuple[int | str, ...]


@dataclass(frozen=True)
class RegionOperation:
    """A source operation together with its structural path in the process."""

    operation: Receive | Assign | Send
    path: SourcePath


@dataclass(frozen=True)
class TransactionWarning:
    message: str
    location: SourceLocation | None


@dataclass(frozen=True)
class StructurallyValidatedTransaction:
    behavioral: BehavioralModule
    receives: tuple[RegionOperation, ...]
    combinational: tuple[RegionOperation, ...]
    sends: tuple[RegionOperation, ...]
    warnings: tuple[TransactionWarning, ...]


class TransactionStructureError(ValueError):
    """The behavioral process is not a single-stage transaction."""


class _Phase(IntEnum):
    RECEIVE = 0
    COMBINATIONAL = 1
    SEND = 2


@dataclass
class _Extraction:
    receives: list[RegionOperation]
    combinational: list[RegionOperation]
    sends: list[RegionOperation]
    warnings: list[TransactionWarning]
    endpoints: set[ChannelEndpoint]


def extract_transaction(behavioral: BehavioralModule) -> StructurallyValidatedTransaction:
    """Extract and structurally validate one receive/compute/send transaction."""

    extraction = _Extraction([], [], [], [], set())
    _visit(behavioral.body, (), _Phase.RECEIVE, extraction)
    if not extraction.receives:
        raise TransactionStructureError('a transaction requires at least one Receive')
    if not extraction.sends:
        raise TransactionStructureError('a transaction requires at least one Send')
    return StructurallyValidatedTransaction(
        behavioral,
        tuple(extraction.receives),
        tuple(extraction.combinational),
        tuple(extraction.sends),
        tuple(extraction.warnings),
    )


def _visit(process: Process, path: SourcePath, phase: _Phase, extraction: _Extraction) -> _Phase:
    if isinstance(process, Receive):
        if phase is not _Phase.RECEIVE:
            raise TransactionStructureError('Receive occurs after the computation or Send region')
        _record_endpoint(process.channel, extraction)
        extraction.receives.append(RegionOperation(process, path))
        return phase
    if isinstance(process, Assign):
        if phase is _Phase.SEND:
            raise TransactionStructureError('Assign occurs after the Send region')
        extraction.combinational.append(RegionOperation(process, path))
        return _Phase.COMBINATIONAL
    if isinstance(process, Send):
        _record_endpoint(process.channel, extraction)
        extraction.sends.append(RegionOperation(process, path))
        return _Phase.SEND
    if isinstance(process, Skip):
        return phase
    if isinstance(process, Sequence):
        _warn_for_sequential_communication(process, extraction)
        for index, item in enumerate(process.items):
            phase = _visit(item, path + (index,), phase, extraction)
        return phase
    if isinstance(process, Parallel):
        return _visit_parallel(process, path, phase, extraction)
    if isinstance(process, If):
        then_phase = _visit(process.then_branch, path + ('then',), phase, extraction)
        else_phase = _visit(process.else_branch, path + ('else',), phase, extraction)
        return max(then_phase, else_phase)
    raise TransactionStructureError(f'unsupported behavioral process {type(process).__name__}')


def _visit_parallel(
    process: Parallel, path: SourcePath, phase: _Phase, extraction: _Extraction
) -> _Phase:
    branch_regions = [_regions_in(branch) for branch in process.branches]
    occupied_regions = {next(iter(regions)) for regions in branch_regions if regions}
    if any(len(regions) > 1 for regions in branch_regions) or len(occupied_regions) > 1:
        raise TransactionStructureError('Parallel mixes transaction regions')

    final_phases = [
        _visit(branch, path + ('parallel', index), phase, extraction)
        for index, branch in enumerate(process.branches)
    ]
    return max(final_phases, default=phase)


def _regions_in(process: Process) -> set[_Phase]:
    if isinstance(process, Receive):
        return {_Phase.RECEIVE}
    if isinstance(process, Assign):
        return {_Phase.COMBINATIONAL}
    if isinstance(process, Send):
        return {_Phase.SEND}
    if isinstance(process, Skip):
        return set()
    if isinstance(process, Sequence):
        return set().union(*(_regions_in(item) for item in process.items)) if process.items else set()
    if isinstance(process, Parallel):
        return set().union(*(_regions_in(branch) for branch in process.branches)) if process.branches else set()
    if isinstance(process, If):
        return _regions_in(process.then_branch) | _regions_in(process.else_branch)
    raise TransactionStructureError(f'unsupported behavioral process {type(process).__name__}')


def _record_endpoint(endpoint: ChannelEndpoint, extraction: _Extraction) -> None:
    if endpoint in extraction.endpoints:
        raise TransactionStructureError(f'repeated communication on endpoint {endpoint.name}')
    extraction.endpoints.add(endpoint)


def _warn_for_sequential_communication(sequence: Sequence, extraction: _Extraction) -> None:
    """Warn only for direct sequential same-region communication syntax."""

    direct_operations = [item for item in sequence.items if isinstance(item, (Receive, Send))]
    for operation_type, noun in (
        (Receive, 'Receives'),
        (Send, 'Sends'),
    ):
        operations = [item for item in direct_operations if isinstance(item, operation_type)]
        if len(operations) > 1:
            extraction.warnings.append(TransactionWarning(
                f'sequential {noun} are interpreted as concurrent; use explicit fork/join',
                operations[1].location,
            ))
