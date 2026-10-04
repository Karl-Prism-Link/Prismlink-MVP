"""Pipecat transport for Aurora's local PCMA/PCMU RTP socket bridge.

Aurora starts forwarding media after the `/accept` control request. The pipeline
is started first, but input and output are gated until that request succeeds.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
from contextlib import suppress
from uuid import UUID

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.transports.base_transport import BaseTransport, TransportParams

try:
    from pipecat.transports.base_input import BaseInputTransport
    from pipecat.transports.base_output import BaseOutputTransport
except ImportError:  # Older Pipecat package layout.
    from pipecat.transports.base_input_transport import BaseInputTransport
    from pipecat.transports.base_output_transport import BaseOutputTransport

from services.voice.aurora_rtp import (
    CODEC_PAYLOAD_TYPES,
    PCM20ms,
    build_rtp_packet,
    decode_g711,
    encode_g711,
    parse_rtp_packet,
)

log = logging.getLogger(__name__)


class _Input(BaseInputTransport):
    def __init__(self, owner: AuroraRtpTransport, params: TransportParams):
        super().__init__(params)
        self.owner = owner
        self.reader: asyncio.Task | None = None

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        if self.reader is None:
            self.reader = self.create_task(self._receive())

    async def _receive(self):
        transport = self.owner
        pcm = PCM20ms()
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(transport.output_ready.wait(), transport.start_timeout)
            await asyncio.wait_for(transport.accepted.wait(), transport.start_timeout)
            if transport.closed.is_set():
                return
            transport.ready.set()
            await transport._call_event_handler("on_client_connected", transport.session_id)
            while not transport.closed.is_set():
                try:
                    packet, sender = await asyncio.wait_for(
                        loop.sock_recvfrom(transport.sock, 2048), timeout=0.5
                    )
                except TimeoutError:
                    continue
                if sender[0] not in {"100.105.4.49", "172.16.0.4"}:
                    continue
                if sender[1] != transport.aurora_ai_port:
                    continue
                try:
                    payload_type, payload = parse_rtp_packet(packet)
                except ValueError:
                    continue
                if payload_type != transport.payload_type:
                    continue
                decoded = decode_g711(payload, transport.codec)
                for chunk in pcm.feed(decoded):
                    await self.push_audio_frame(
                        InputAudioRawFrame(audio=chunk, sample_rate=8000, num_channels=1)
                    )
        except TimeoutError:
            log.warning("%s: Aurora call setup timed out", transport.session_id)
        except asyncio.CancelledError:
            raise
        except OSError:
            if not transport.closed.is_set():
                log.exception("%s: Aurora RTP receive failed", transport.session_id)
        except Exception:
            log.exception("%s: Aurora RTP input failed", transport.session_id)
        finally:
            transport.closed.set()
            await transport._call_event_handler("on_client_disconnected", transport.session_id)

    async def _stop_reader(self):
        if self.reader is not None:
            reader, self.reader = self.reader, None
            if reader is not asyncio.current_task():
                await self.cancel_task(reader)

    async def stop(self, frame):
        await self._stop_reader()
        await super().stop(frame)

    async def cancel(self, frame):
        await self._stop_reader()
        await super().cancel(frame)

    async def cleanup(self):
        await self._stop_reader()
        await super().cleanup()


class _Output(BaseOutputTransport):
    def __init__(self, owner: AuroraRtpTransport, params: TransportParams):
        super().__init__(params)
        self.owner = owner
        self.pending = bytearray()
        self.sequence = secrets.randbelow(1 << 16)
        self.timestamp = secrets.randbelow(1 << 32)
        self.ssrc = secrets.randbelow(1 << 32)
        self.first_packet = True
        self.next_send = 0.0

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        self.owner.output_ready.set()
        self.owner.pipeline_ready.set()

    async def write_audio_frame(self, frame):
        transport = self.owner
        if transport.closed.is_set() or not transport.accepted.is_set():
            return False
        if frame.num_channels != 1 or frame.sample_rate != 8000 or len(frame.audio) % 2:
            log.error("%s: invalid Aurora RTP PCM output format", transport.session_id)
            return False
        self.pending.extend(frame.audio)
        loop = asyncio.get_running_loop()
        try:
            while len(self.pending) >= 320:
                pcm = bytes(self.pending[:320])
                del self.pending[:320]
                payload = encode_g711(pcm, transport.codec)
                packet = build_rtp_packet(
                    payload,
                    payload_type=transport.payload_type,
                    sequence=self.sequence,
                    timestamp=self.timestamp,
                    ssrc=self.ssrc,
                    marker=self.first_packet,
                )
                await asyncio.sleep(max(0.0, self.next_send - loop.time()))
                await asyncio.wait_for(
                    loop.sock_sendto(transport.sock, packet, transport.aurora_destination),
                    timeout=transport.send_timeout,
                )
                self.first_packet = False
                self.sequence = (self.sequence + 1) & 0xFFFF
                self.timestamp = (self.timestamp + 160) & 0xFFFFFFFF
                self.next_send = loop.time() + 0.02
            return True
        except (TimeoutError, OSError):
            log.exception("%s: Aurora RTP send failed", transport.session_id)
            transport.close()
            return False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)


class AuroraRtpTransport(BaseTransport):
    """One local UDP socket and one Pipecat pipeline per Aurora call."""

    def __init__(
        self,
        session_id: str,
        *,
        aurora_host: str,
        aurora_ai_port: int,
        codec: str,
        payload_type: int,
        bind_host: str,
        send_timeout: float = 2.0,
        start_timeout: float = 20.0,
    ):
        super().__init__()
        self.session_id = str(UUID(session_id))
        self.codec = codec.upper()
        expected_payload_type = CODEC_PAYLOAD_TYPES.get(self.codec)
        if expected_payload_type is None or payload_type != expected_payload_type:
            raise ValueError("Aurora must offer static PCMA/8 or PCMU/0 RTP")
        if not 1 <= aurora_ai_port <= 65535:
            raise ValueError("Aurora RTP port is invalid")
        # Aurora's configured local_ip and the Pipecat bind address must be IPv4 literals.
        socket.inet_aton(aurora_host)
        socket.inet_aton(bind_host)
        self.aurora_host = aurora_host
        self.aurora_ai_port = aurora_ai_port
        self.payload_type = payload_type
        self.aurora_destination = (aurora_host, aurora_ai_port)
        self.send_timeout = send_timeout
        self.start_timeout = start_timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        self.sock.bind((bind_host, 0))
        self.sock.setblocking(False)
        self.local_host, self.local_port = self.sock.getsockname()
        self.ready = asyncio.Event()
        self.pipeline_ready = asyncio.Event()
        self.output_ready = asyncio.Event()
        self.accepted = asyncio.Event()
        self.closed = asyncio.Event()
        params = TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            audio_in_channels=1,
            audio_out_channels=1,
            audio_out_10ms_chunks=2,
            audio_in_passthrough=True,
        )
        self._input = _Input(self, params)
        self._output = _Output(self, params)
        for event in ("on_client_connected", "on_client_disconnected"):
            self._register_event_handler(event)

    def input(self):
        return self._input

    def output(self):
        return self._output

    def close(self) -> None:
        """Stop the RTP reader; its short receive timeout observes this promptly."""
        self.closed.set()
        # Release the input startup gates too, including calls cancelled before acceptance.
        self.accepted.set()
        self.output_ready.set()

    def dispose(self) -> None:
        self.close()
        with suppress(OSError):
            self.sock.close()
