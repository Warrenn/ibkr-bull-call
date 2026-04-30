"""Regression tests for CloudFormation templates.

These don't deploy anything — they just parse the YAML and assert
operational-safety properties (e.g. stateful resources have explicit
``DeletionPolicy: Retain``). The goal is to catch policy regressions in
PR review long before they'd silently delete prod data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_INFRA = Path(__file__).resolve().parent.parent / "infra" / "cloudformation"


class _CfnLoader(yaml.SafeLoader):
    """Permissive loader that ignores CFN intrinsic-function tags
    (``!Sub``, ``!Ref``, ``!GetAtt``, etc.) by treating them as scalars
    or mappings/sequences as appropriate."""


def _ignore_unknown_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    # Fail fast rather than silently substituting None — a yaml node we
    # don't recognise means the CFN intrinsic-function shape changed (or
    # the loader is broken) and a downstream test would just see KeyError
    # / TypeError without telling us why.
    raise TypeError(
        f"unexpected YAML node type for CFN intrinsic !{tag_suffix}: "
        f"{type(node).__name__}"
    )


_CfnLoader.add_multi_constructor("!", _ignore_unknown_tag)


def _load(name: str) -> dict[str, Any]:
    text = (_INFRA / name).read_text()
    template = yaml.load(text, Loader=_CfnLoader)
    if not isinstance(template, dict):
        raise AssertionError(
            f"{name} did not parse to a mapping at the top level "
            f"(got {type(template).__name__}); the file is structurally "
            "broken or not a CFN template."
        )
    if "Resources" not in template:
        raise AssertionError(
            f"{name} has no top-level `Resources` key; CFN templates must "
            "declare resources here. Did the file get renamed or rewritten?"
        )
    return template


@pytest.mark.parametrize(
    "logical_id",
    ["StateTable", "ArtifactsBucket"],
)
def test_stateful_resources_retained_on_stack_delete(logical_id: str) -> None:
    """Regression: a `cfn delete-stack` (intentional or accidental) MUST
    NOT take stateful resources with it.

    StateTable holds the canonical history of every spread we've ever
    opened plus the stop journal. ArtifactsBucket holds the release
    history. Both must persist beyond the stack."""

    template = _load("data.yaml")
    resource = template["Resources"][logical_id]

    assert resource.get("DeletionPolicy") == "Retain", (
        f"{logical_id} must have DeletionPolicy: Retain — otherwise a "
        "stack delete would wipe operator-critical data."
    )
    assert resource.get("UpdateReplacePolicy") == "Retain", (
        f"{logical_id} must have UpdateReplacePolicy: Retain — otherwise "
        "a logical-id rename would wipe operator-critical data."
    )


def test_state_table_has_point_in_time_recovery() -> None:
    """Defense-in-depth: even with Retain, PITR gives 35-day continuous
    backups so an operational mishap that *deliberately* deletes a row
    can be rolled back."""

    template = _load("data.yaml")
    state_table = template["Resources"]["StateTable"]
    pitr = state_table["Properties"]["PointInTimeRecoverySpecification"]
    assert pitr["PointInTimeRecoveryEnabled"] is True


def test_artifacts_bucket_has_versioning_enabled() -> None:
    """Versioning means an accidental overwrite (e.g. push of a corrupt
    tarball under an existing version key) can be reverted."""

    template = _load("data.yaml")
    bucket = template["Resources"]["ArtifactsBucket"]
    versioning = bucket["Properties"]["VersioningConfiguration"]
    assert versioning["Status"] == "Enabled"
