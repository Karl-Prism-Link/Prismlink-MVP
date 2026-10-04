"""Small RTP/G.711 helpers for Aurora's PCMA/PCMU media contract."""

from __future__ import annotations

import struct

CODEC_PAYLOAD_TYPES = {"PCMU": 0, "PCMA": 8}
_BIAS = 0x84
_CLIP = 32635
_SEGMENT_END = (0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF)


def decode_g711(data: bytes, codec: str) -> bytes:
    """Decode one G.711 payload into little-endian signed 16-bit PCM."""
    codec = codec.upper()
    if codec not in ("PCMA", "PCMU"):
        raise ValueError("codec must be PCMA or PCMU")
    samples = bytearray(len(data) * 2)
    if codec == "PCMU":
        for index, value in enumerate(data):
            value = (~value) & 0xFF
            magnitude = ((value & 0x0F) << 3) + _BIAS
            magnitude <<= (value & 0x70) >> 4
            sample = _BIAS - magnitude if value & 0x80 else magnitude - _BIAS
            struct.pack_into("<h", samples, index * 2, sample)
    else:
        for index, value in enumerate(data):
            value ^= 0x55
            magnitude = (value & 0x0F) << 4
            segment = (value & 0x70) >> 4
            if segment == 0:
                magnitude += 8
            elif segment == 1:
                magnitude += 0x108
            else:
                magnitude += 0x108
                magnitude <<= segment - 1
            sample = magnitude if value & 0x80 else -magnitude
            struct.pack_into("<h", samples, index * 2, sample)
    return bytes(samples)


def _encode_ulaw(sample: int) -> int:
    sample = max(-32768, min(32767, sample))
    mask = 0x7F if sample < 0 else 0xFF
    magnitude = min(_CLIP, abs(sample)) + _BIAS
    segment = next((i for i, end in enumerate(_SEGMENT_END) if magnitude <= end), 7)
    value = (segment << 4) | ((magnitude >> (segment + 3)) & 0x0F)
    return value ^ mask


def _encode_alaw(sample: int) -> int:
    sample = max(-32768, min(32767, sample))
    if sample >= 0:
        mask = 0xD5
        magnitude = sample
    else:
        mask = 0x55
        magnitude = -sample - 1
    magnitude = min(magnitude, 32767)
    segment = next((i for i, end in enumerate(_SEGMENT_END) if magnitude <= end), 7)
    if segment == 0:
        value = (magnitude >> 4) & 0x0F
    else:
        value = (segment << 4) | ((magnitude >> (segment + 3)) & 0x0F)
    return value ^ mask


def encode_g711(pcm: bytes, codec: str) -> bytes:
    """Encode little-endian signed 16-bit PCM as PCMA or PCMU."""
    codec = codec.upper()
    if codec not in ("PCMA", "PCMU"):
        raise ValueError("codec must be PCMA or PCMU")
    if len(pcm) % 2:
        raise ValueError("PCM data must contain complete 16-bit samples")
    encoder = _encode_alaw if codec == "PCMA" else _encode_ulaw
    return bytes(encoder(sample[0]) for sample in struct.iter_unpack("<h", pcm))


def parse_rtp_packet(packet: bytes) -> tuple[int, bytes]:
    """Return payload type and media bytes from a valid RTP v2 packet."""
    if len(packet) < 12 or packet[0] >> 6 != 2:
        raise ValueError("invalid RTP v2 packet")
    header_length = 12 + (packet[0] & 0x0F) * 4
    if header_length > len(packet):
        raise ValueError("truncated RTP CSRC list")
    if packet[0] & 0x10:
        if header_length + 4 > len(packet):
            raise ValueError("truncated RTP extension header")
        extension_words = struct.unpack_from("!H", packet, header_length + 2)[0]
        header_length += 4 + extension_words * 4
        if header_length > len(packet):
            raise ValueError("truncated RTP extension data")
    end = len(packet)
    if packet[0] & 0x20:
        padding = packet[-1]
        if padding == 0 or padding > end - header_length:
            raise ValueError("invalid RTP padding")
        end -= padding
    if header_length >= end:
        raise ValueError("empty RTP media payload")
    return packet[1] & 0x7F, packet[header_length:end]


def build_rtp_packet(payload: bytes, *, payload_type: int, sequence: int, timestamp: int, ssrc: int, marker: bool = False) -> bytes:
    if not 0 <= payload_type <= 127:
        raise ValueError("RTP payload type must be between 0 and 127")
    header = struct.pack(
        "!BBHII",
        0x80,
        payload_type | (0x80 if marker else 0),
        sequence & 0xFFFF,
        timestamp & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )
    return header + payload


class PCM20ms:
    """Buffer PCM into 20 ms mono frames at the Aurora 8 kHz rate."""

    def __init__(self) -> None:
        self.pending = bytearray()

    def feed(self, pcm: bytes):
        self.pending.extend(pcm)
        frame_size = 8_000 // 50 * 2
        while len(self.pending) >= frame_size:
            frame = bytes(self.pending[:frame_size])
            del self.pending[:frame_size]
            yield frame
