from __future__ import annotations

import struct

import pytest

from services.voice.aurora_rtp import (
    PCM20ms,
    build_rtp_packet,
    decode_g711,
    encode_g711,
    parse_rtp_packet,
)


@pytest.mark.parametrize(
    ("codec", "silence"),
    [("PCMA", b"\xd5"), ("PCMU", b"\xff")],
)
def test_g711_silence_vectors(codec: str, silence: bytes) -> None:
    assert encode_g711(b"\x00\x00", codec) == silence
    assert len(decode_g711(silence, codec)) == 2


@pytest.mark.parametrize("codec", ["PCMA", "PCMU"])
def test_g711_encodes_one_byte_per_pcm_sample(codec: str) -> None:
    pcm = struct.pack("<160h", *range(-80, 80))
    encoded = encode_g711(pcm, codec)
    assert len(encoded) == 160
    assert len(decode_g711(encoded, codec)) == len(pcm)


def test_rtp_round_trip() -> None:
    payload = b"\xd5" * 160
    packet = build_rtp_packet(
        payload,
        payload_type=8,
        sequence=65535,
        timestamp=0xFFFFFFF0,
        ssrc=0x12345678,
        marker=True,
    )
    assert packet[1] == 0x88
    assert parse_rtp_packet(packet) == (8, payload)


def test_rtp_parser_skips_extension_and_padding() -> None:
    # One 32-bit extension word followed by one padding byte.
    packet = struct.pack("!BBHII", 0x90 | 0x20, 8, 1, 2, 3)
    packet += struct.pack("!HH", 0xBEDE, 1) + b"EXT!" + b"\xd5\x01"
    assert parse_rtp_packet(packet) == (8, b"\xd5")


def test_pcm_is_reassembled_into_twenty_ms_frames() -> None:
    framer = PCM20ms()
    assert list(framer.feed(b"\x00" * 200)) == []
    frames = list(framer.feed(b"\x00" * 120))
    assert [len(frame) for frame in frames] == [320]
    assert len(framer.pending) == 0


def test_rejects_unsupported_codec_and_invalid_rtp() -> None:
    with pytest.raises(ValueError):
        encode_g711(b"\x00\x00", "G722")
    with pytest.raises(ValueError):
        parse_rtp_packet(b"too short")
