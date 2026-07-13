from alerter.clock import format_clock, parse_game_clock


def test_typical_q4_clock():
    assert parse_game_clock("PT03M42.00S") == 222.0


def test_full_quarter_clock():
    assert parse_game_clock("PT12M00.00S") == 720.0


def test_under_a_minute():
    assert parse_game_clock("PT00M23.70S") == 23.7


def test_zero_clock():
    assert parse_game_clock("PT00M00.00S") == 0.0


def test_seconds_only():
    assert parse_game_clock("PT58.4S") == 58.4


def test_empty_string_is_none():
    assert parse_game_clock("") is None


def test_none_is_none():
    assert parse_game_clock(None) is None


def test_garbage_is_none():
    assert parse_game_clock("3:42") is None
    assert parse_game_clock("PT") is None
    assert parse_game_clock("Final") is None


def test_non_string_is_none():
    assert parse_game_clock(222) is None
    assert parse_game_clock({"clock": "PT03M42S"}) is None


def test_format_clock():
    assert format_clock(222.0) == "3:42"
    assert format_clock(0) == "0:00"
    assert format_clock(59.9) == "0:59"
    assert format_clock(600) == "10:00"
