"""Tests for janasunani.pipeline.ticket: deriving ticket numbers / doc ids from
a document's path relative to the documents/ root."""

from janasunani.pipeline.ticket import ticket_from_relpath


def test_ticket_from_relpath_nested():
    assert (
        ticket_from_relpath("OR107/E/2021/00324_complaint_20250916_102200.pdf")
        == "OR107/E/2021/00324"
    )


def test_ticket_from_relpath_flat():
    assert ticket_from_relpath("CMO2022115810_complaint_20250915_144909.jpeg") == "CMO2022115810"


def test_ticket_from_relpath_normalizes_windows_separators():
    assert (
        ticket_from_relpath("OR107\\E\\2021\\00324_complaint_20250916_102200.pdf")
        == "OR107/E/2021/00324"
    )


def test_ticket_from_relpath_no_extension():
    assert ticket_from_relpath("CMO2022115810_complaint_20250915_144909") == "CMO2022115810"


def test_ticket_from_relpath_empty_string_returns_none():
    assert ticket_from_relpath("") is None


def test_ticket_from_relpath_none_returns_none():
    assert ticket_from_relpath(None) is None


def test_ticket_from_relpath_missing_complaint_marker_returns_none():
    # No "_complaint_" marker -> can't reliably parse a ticket.
    assert ticket_from_relpath("OR107/E/2021/random_file.pdf") is None

