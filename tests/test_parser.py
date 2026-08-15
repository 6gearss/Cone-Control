"""Unit tests for JAC Normal protocol parser."""

from datetime import datetime
from src.farmtek.jac_normal_parser import JACNormalParser, FinishEvent, ResetEvent, InvalidFrameEvent


def test_jac_normal_finish_parsing():
    # R001702 -> Digits reversed: 207100 -> 207.100s
    res = JACNormalParser.parse_line("R001702\r\n")
    assert isinstance(res, FinishEvent)
    assert res.time_seconds == 207.100
    assert res.time_formatted == "207.100"
    assert res.raw_message == "R001702"
    assert not res.is_reset


def test_jac_normal_short_finish_parsing():
    # R052310 -> Digits reversed: 013250 -> 13.250s
    res = JACNormalParser.parse_line("R052310\r")
    assert isinstance(res, FinishEvent)
    assert res.time_seconds == 13.250
    assert res.time_formatted == "13.250"


def test_jac_normal_reset_parsing():
    res = JACNormalParser.parse_line("R000000\r\n")
    assert isinstance(res, ResetEvent)
    assert res.raw_message == "R000000"


def test_jac_normal_invalid_format():
    res = JACNormalParser.parse_line("INVALID\r\n")
    assert isinstance(res, InvalidFrameEvent)
    assert "does not match" in res.error


def test_jac_extended_start_parsing():
    from src.farmtek.jac_normal_parser import StartEvent
    res = JACNormalParser.parse_line("S000000\r\n")
    assert isinstance(res, StartEvent)
    assert res.eye_number == 1
    assert res.raw_message == "S000000"

    res2 = JACNormalParser.parse_line("START\r\n")
    assert isinstance(res2, StartEvent)


def test_jac_chrono_finish_parsing():
    # C001702 -> Digits reversed: 207100 -> 207.100s
    res = JACNormalParser.parse_line("C001702\r\n")
    assert isinstance(res, FinishEvent)
    assert res.time_seconds == 207.100
    assert res.time_formatted == "207.100"
    assert res.raw_message == "C001702"


def test_jac_chrono_eye_a_and_b_parsing():
    from src.farmtek.jac_normal_parser import StartEvent, EyeBEvent
    # A002147 -> Start beam / Eye A
    res_a = JACNormalParser.parse_line("A002147\r\n")
    assert isinstance(res_a, StartEvent)
    assert res_a.eye_number == 1
    assert res_a.raw_message == "A002147"

    # B004722 -> Finish beam / Eye B TOD timestamp frame
    res_b = JACNormalParser.parse_line("B004722\r\n")
    assert isinstance(res_b, EyeBEvent)
    assert res_b.eye_number == 2
    assert res_b.raw_message == "B004722"



