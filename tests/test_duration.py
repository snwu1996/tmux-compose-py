import pytest

from tmux_compose.errors import TmuxComposeError
from tmux_compose.model import parse_duration


def test_seconds():
    assert parse_duration("3s") == 3.0


def test_milliseconds():
    assert parse_duration("100ms") == pytest.approx(0.1)


def test_compound():
    assert parse_duration("1m30s") == 90.0


def test_micro_variants():
    assert parse_duration("5us") == pytest.approx(5e-6)
    assert parse_duration("5µs") == pytest.approx(5e-6)


def test_fractional():
    assert parse_duration("1.5h") == pytest.approx(5400.0)


def test_zero_and_empty():
    assert parse_duration("0") == 0.0
    assert parse_duration("") == 0.0
    assert parse_duration(None) == 0.0


def test_integer_is_nanoseconds():
    assert parse_duration(1000000000) == pytest.approx(1.0)


def test_invalid_errors():
    with pytest.raises(TmuxComposeError):
        parse_duration("3 seconds")
