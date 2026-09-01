from pathlib import Path

import pytest

from connectors import ConnectorParseError, FileInternalControlConnector, InternalControl

FIXTURES = Path(__file__).parent / "fixtures"
CONTROLS_FOLDER = FIXTURES / "internal_controls"


def test_fetch_normalizes_rows_into_internal_controls():
    connector = FileInternalControlConnector(source_system="sample_co", folder=CONTROLS_FOLDER)

    controls = connector.fetch_controls()

    assert all(isinstance(c, InternalControl) for c in controls)
    assert all(c.source_system == "sample_co" for c in controls)
    assert all(c.source_capability == "internal_controls" for c in controls)

    access = next(c for c in controls if c.control_id == "CTL-001")
    assert access.description == "Privileged system access requires MFA and quarterly recertification"
    assert access.category == "Access Management"
    assert access.raw["control_id"] == "CTL-001"


def test_results_sorted_by_control_id():
    connector = FileInternalControlConnector(source_system="sample_co", folder=CONTROLS_FOLDER)

    controls = connector.fetch_controls()

    assert [c.control_id for c in controls] == ["CTL-001", "CTL-002", "CTL-003", "CTL-004"]


def test_blank_category_becomes_empty_string(tmp_path):
    folder = tmp_path / "internal_controls"
    folder.mkdir()
    (folder / "c.csv").write_text(
        "control_id,description,category\n"
        "CTL-9,Some control with no category,\n"
    )
    connector = FileInternalControlConnector(source_system="sample_co", folder=folder)

    (control,) = connector.fetch_controls()
    assert control.category == ""


def test_multiple_csvs_in_folder_are_all_read(tmp_path):
    folder = tmp_path / "internal_controls"
    folder.mkdir()
    (folder / "batch_a.csv").write_text(
        "control_id,description,category\nCTL-1,First control,Access\n"
    )
    (folder / "batch_b.csv").write_text(
        "control_id,description,category\nCTL-2,Second control,Data\n"
    )
    connector = FileInternalControlConnector(source_system="sample_co", folder=folder)

    assert [c.control_id for c in connector.fetch_controls()] == ["CTL-1", "CTL-2"]


def test_missing_folder_returns_empty(tmp_path):
    connector = FileInternalControlConnector(
        source_system="sample_co", folder=tmp_path / "no_such_folder"
    )
    assert connector.fetch_controls() == []


def test_missing_required_value_raises_connector_parse_error(tmp_path):
    folder = tmp_path / "internal_controls"
    folder.mkdir()
    (folder / "bad.csv").write_text(
        "control_id,description,category\n"
        "CTL-9,,Access Management\n"
    )
    connector = FileInternalControlConnector(source_system="sample_co", folder=folder)

    with pytest.raises(ConnectorParseError, match="missing description"):
        connector.fetch_controls()
