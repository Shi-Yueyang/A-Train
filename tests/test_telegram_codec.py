"""Unit tests for the pure CTCS balise telegram codec (docs/btm-telegram.md).

Bit-level golden vectors are built from the CTCS frame table (50-bit header +
772-bit user packet area + 8-bit ``1111 1111`` information-end field, zero
padding to the fixed 830-bit frame) and the packet tables in
``a_train.telegram.packets``: MSB-first, transmission order left to right.
"""

from __future__ import annotations

import pytest

from a_train.telegram import TelegramError, encode_telegram


def bits(*fields: tuple[int, int]) -> str:
    return "".join(f"{value:0{width}b}" for value, width in fields)


def stream_bits(data: bytes) -> str:
    return "".join(f"{b:08b}" for b in data)


HEADER = bits((1, 1), (3, 7), (0, 1), (0, 3), (0, 3), (0, 2), (255, 8), (0, 10), (0, 14), (0, 1))
EOT = bits((0xFF, 8))

PACKET_41 = {
    "packet": 41,
    "q_dir": 1,
    "q_scale": 0,
    "d_leveltr": 1234,
    "m_leveltr": 2,
    "l_ackleveltr": 5,
}

PKT41 = bits(
    (41, 8),  # NID_PACKET
    (1, 2),  # q_dir
    (40, 13),  # L_PACKET: bits that follow
    (0, 2),  # q_scale
    (1234, 15),  # d_leveltr
    (2, 3),  # m_leveltr
    (5, 15),  # l_ackleveltr
    (0, 5),  # N_ITER
)

# Golden frame for PACKET_41 with header defaults (104 bytes).
GOLDEN_41 = "83007f8000000a501401349000507f80" + "00" * 88


def test_minimal_packet_41_structural_bits() -> None:
    data = encode_telegram({"packets": [PACKET_41]})
    content = HEADER + PKT41 + EOT
    assert len(content) == 121
    assert stream_bits(data)[: len(content)] == content
    assert set(stream_bits(data)[len(content) :]) == {"0"}


def test_minimal_packet_41_golden_bytes() -> None:
    data = encode_telegram({"packets": [PACKET_41]})
    assert data.hex() == GOLDEN_41
    assert len(data) == 104  # fixed 830-bit frame


def test_packet_41_repeat_group_l_packet_and_count() -> None:
    repeats = [{"m_leveltr": i + 2, "l_ackleveltr": 100 * i} for i in range(2)]
    data = encode_telegram({"packets": [{**PACKET_41, "transitions": repeats}]})
    body = bits((0, 2), (1234, 15), (2, 3), (5, 15))
    body += bits((2, 5))
    for item in repeats:
        body += bits((item["m_leveltr"], 3), (item["l_ackleveltr"], 15))
    packet = bits((41, 8), (1, 2), (len(body), 13)) + body
    assert packet in stream_bits(data)


def test_packet_41_conditional_nid_stm_and_repeats() -> None:
    packet = {
        "packet": 41,
        "q_dir": 1,
        "q_scale": 0,
        "d_leveltr": 1234,
        "m_leveltr": 1,
        "nid_stm": 7,
        "l_ackleveltr": 5,
        "transitions": [
            {"m_leveltr": 3, "l_ackleveltr": 9},
            {"m_leveltr": 1, "nid_stm": 12, "l_ackleveltr": 3},
        ],
    }
    data = encode_telegram({"packets": [packet]})
    body = bits((0, 2), (1234, 15), (1, 3), (7, 8), (5, 15))
    body += bits((2, 5))
    body += bits((3, 3), (9, 15))
    body += bits((1, 3), (12, 8), (3, 15))
    packet_bits = bits((41, 8), (1, 2), (len(body), 13)) + body
    stream = stream_bits(data)
    assert HEADER in stream
    assert packet_bits in stream
    assert packet_bits + EOT in stream


def test_header_defaults_and_overrides() -> None:
    data = encode_telegram({"packets": [PACKET_41], "m_version": 5, "nid_c": 83, "nid_bg": 1000})
    stream = stream_bits(data)
    assert stream[:1] == "1"  # q_updown default uplink
    assert int(stream[1:8], 2) == 5
    assert stream[8] == "0"  # q_media default balise
    assert int(stream[12:15], 2) == 0  # n_total default (000 = one balise)
    assert int(stream[17:25], 2) == 255  # m_mcount default
    assert int(stream[25:35], 2) == 83
    assert int(stream[35:49], 2) == 1000
    assert stream[49] == "0"  # q_link default


def test_q_updown_and_q_media_are_settable() -> None:
    data = encode_telegram({"packets": [PACKET_41], "q_updown": 0, "q_media": 1})
    stream = stream_bits(data)
    assert stream[:1] == "0"  # downlink
    assert stream[8] == "1"  # loop medium


def test_q_link_sets_bit() -> None:
    data = encode_telegram({"packets": [PACKET_41], "q_link": True})
    assert stream_bits(data)[49] == "1"


def test_content_beyond_frame_is_rejected() -> None:
    with pytest.raises(TelegramError, match="exceeds the 830-bit frame"):
        encode_telegram({"packets": [PACKET_41] * 60})


def test_removed_gradient_packet_is_rejected() -> None:
    with pytest.raises(
        TelegramError, match=r"telegram\.packets\[0\]\.packet: unknown packet number 21"
    ):
        encode_telegram({"packets": [{"packet": 21}]})


def test_missing_required_field() -> None:
    with pytest.raises(
        TelegramError, match=r"telegram\.packets\[0\]\.q_scale: missing required field"
    ):
        encode_telegram({"packets": [{"packet": 41, "q_dir": 2}]})


def test_conditional_nid_stm_required_with_ntc_level() -> None:
    with pytest.raises(TelegramError, match=r"nid_stm: required while m_leveltr == 1"):
        encode_telegram(
            {
                "packets": [
                    {
                        "packet": 41,
                        "q_dir": 1,
                        "q_scale": 0,
                        "d_leveltr": 1,
                        "m_leveltr": 1,
                        "l_ackleveltr": 0,
                    }
                ]
            }
        )


def test_conditional_nid_stm_ignored_when_level_not_ntc() -> None:
    packet = {**PACKET_41, "nid_stm": 9}  # ignored: M_LEVELTR != 1
    data = encode_telegram({"packets": [packet]})
    body = bits((0, 2), (1234, 15), (2, 3), (5, 15))
    packet_bits = bits((41, 10), (1, 2), (len(body) + 5, 13)) + body + bits((0, 5))
    assert packet_bits in stream_bits(data)


def test_field_out_of_range() -> None:
    with pytest.raises(TelegramError, match=r"d_leveltr: value 32768 out of range for 15 bits"):
        encode_telegram({"packets": [{**PACKET_41, "d_leveltr": 32768}]})


def test_boolean_rejected_for_coded_integers() -> None:
    with pytest.raises(TelegramError, match=r"l_ackleveltr: must be an integer coded value"):
        encode_telegram({"packets": [{**PACKET_41, "l_ackleveltr": True}]})


def test_unknown_packet_field_lists_expectations() -> None:
    with pytest.raises(TelegramError, match=r"n_iter: unknown field"):
        encode_telegram({"packets": [{**PACKET_41, "n_iter": 0}]})


def test_removed_etcs_fields_rejected() -> None:
    with pytest.raises(TelegramError, match="telegram.format: unknown field"):
        encode_telegram({"format": "ctcs", "packets": [PACKET_41]})
    with pytest.raises(TelegramError, match="telegram.form: unknown field"):
        encode_telegram({"form": "long", "packets": [PACKET_41]})


def test_unknown_telegram_field() -> None:
    with pytest.raises(TelegramError, match=r"telegram.q_direction: unknown field"):
        encode_telegram({"packets": [PACKET_41], "q_direction": 0})


def test_group_items_must_be_objects() -> None:
    with pytest.raises(TelegramError, match=r"transitions\[1\]: must be an object"):
        encode_telegram(
            {"packets": [{**PACKET_41, "transitions": [{"m_leveltr": 2, "l_ackleveltr": 1}, 5]}]}
        )


def test_group_exceeding_count_width() -> None:
    items = [{"m_leveltr": 2, "l_ackleveltr": 0} for _ in range(32)]
    with pytest.raises(TelegramError, match=r"transitions: too many items for 5-bit count"):
        encode_telegram({"packets": [{**PACKET_41, "transitions": items}]})


def test_empty_or_missing_packets_rejected() -> None:
    with pytest.raises(TelegramError, match="non-empty list"):
        encode_telegram({"packets": []})
    with pytest.raises(TelegramError, match="non-empty list"):
        encode_telegram({})
