from __future__ import annotations

import asyncio
import socket
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


def test_asyncio_reader_receives_udp_datagram() -> None:
    from services.voice.aurora_rtp_transport import _recvfrom

    async def exercise() -> None:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.setblocking(False)
        try:
            loop = asyncio.get_running_loop()
            expected = b"rtp-regression"
            sender.sendto(expected, receiver.getsockname())
            packet, address = await asyncio.wait_for(_recvfrom(loop, receiver, 2048), 1.0)
            assert packet == expected
            assert address[0] == "127.0.0.1"
        finally:
            receiver.close()
            sender.close()

    asyncio.run(exercise())


def test_nonblocking_udp_sendto_works_under_uvloop() -> None:
    import uvloop

    async def exercise() -> None:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        sender.setblocking(False)
        try:
            payload = b"rtp-send-regression"
            assert sender.sendto(payload, receiver.getsockname()) == len(payload)
            receiver.setblocking(False)
            loop = asyncio.get_running_loop()
            packet, _ = await _recv_for_test(loop, receiver, 2048)
            assert packet == payload
        finally:
            receiver.close()
            sender.close()

    async def run_with_loop() -> None:
        await exercise()

    # Exercise the same synchronous non-blocking send path used by the RTP transport.
    from services.voice.aurora_rtp_transport import _recvfrom as _recv_for_test

    uvloop.run(run_with_loop())


@pytest.mark.asyncio
async def test_rtp_send_retries_transient_socket_backpressure(monkeypatch):
    from services.voice import aurora_rtp_transport as transport_module

    class BackpressureSocket:
        def __init__(self):
            self.attempts = 0

        def sendto(self, packet, destination):
            self.attempts += 1
            if self.attempts == 1:
                raise BlockingIOError("temporary UDP send buffer pressure")
            return len(packet)

    async def writable(_loop, _sock):
        return None

    monkeypatch.setattr(transport_module, "_wait_writable", writable)
    sock = BackpressureSocket()
    packet = b"rtp-packet"
    sent = await transport_module._send_datagram(
        asyncio.get_running_loop(), sock, packet, ("127.0.0.1", 9000), timeout=0.5
    )
    assert sent == len(packet)
    assert sock.attempts == 2


@pytest.mark.asyncio
async def test_rtp_send_times_out_only_after_backpressure_deadline(monkeypatch):
    from services.voice import aurora_rtp_transport as transport_module

    class PermanentlyBlockedSocket:
        def sendto(self, packet, destination):
            raise BlockingIOError("socket remains unwritable")

    async def writable(_loop, _sock):
        await asyncio.sleep(0)

    monkeypatch.setattr(transport_module, "_wait_writable", writable)
    with pytest.raises(TimeoutError, match="remained unwritable"):
        await transport_module._send_datagram(
            asyncio.get_running_loop(),
            PermanentlyBlockedSocket(),
            b"rtp-packet",
            ("127.0.0.1", 9000),
            timeout=0.01,
        )
