"""Proxy UDP que simula uma impressora em economia de energia (PROMPT seção 13, cenário 6).

Encaminha SNMP de --listen para --target (snmpsim). Depois de --idle segundos sem tráfego a
"impressora dorme": o primeiro pacote que chega a acorda e, durante --wake segundos, todos os
pacotes são descartados. Com timeout de 1,5 s e 1 retentativa, o coletor só recebe resposta na
2ª tentativa — exatamente o comportamento das impressoras reais em economia de energia.

Uso:
    python scripts/sleepy_udp_proxy.py --listen 127.0.0.1:1166 --target 127.0.0.1:11166 \
        [--idle 20] [--wake 1.0]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from collections.abc import Callable

logger = logging.getLogger("sleepy_proxy")


def _addr(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port)


class SleepyState:
    def __init__(self, idle: float, wake: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.idle = idle
        self.wake = wake
        self.clock = clock
        self.last_activity = float("-inf")
        self.awake_at = 0.0
        self.dropped = 0

    def accept(self) -> bool:
        """Decides whether an incoming request is forwarded (True) or swallowed while waking up."""
        now = self.clock()
        if now < self.awake_at:  # ainda acordando
            self.dropped += 1
            return False
        if now - self.last_activity > self.idle:
            # Dormindo: este pacote acorda a impressora e é perdido; ela fica acordada a partir de awake_at.
            self.awake_at = now + self.wake
            self.last_activity = self.awake_at
            self.dropped += 1
            return False
        self.last_activity = now
        return True


class ClientSide(asyncio.DatagramProtocol):
    def __init__(self, target: tuple[str, int], state: SleepyState) -> None:
        self.target = target
        self.state = state
        self.transport: asyncio.DatagramTransport | None = None
        self.upstreams: dict[tuple[str, int], asyncio.DatagramTransport] = {}

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self.state.accept():
            logger.info("dormindo: pacote de %s descartado (total %d)", addr, self.state.dropped)
            return
        asyncio.get_running_loop().create_task(self._forward(data, addr))

    async def _forward(self, data: bytes, addr: tuple[str, int]) -> None:
        up = self.upstreams.get(addr)
        if up is None or up.is_closing():
            loop = asyncio.get_running_loop()
            up, _ = await loop.create_datagram_endpoint(
                lambda: UpstreamSide(self, addr), remote_addr=self.target
            )
            self.upstreams[addr] = up
        up.sendto(data)


class UpstreamSide(asyncio.DatagramProtocol):
    def __init__(self, client: ClientSide, addr: tuple[str, int]) -> None:
        self.client = client
        self.addr = addr

    def datagram_received(self, data: bytes, _: tuple[str, int]) -> None:
        if self.client.transport is not None:
            self.client.transport.sendto(data, self.addr)

    def error_received(self, exc: Exception) -> None:
        logger.error("erro no destino %s: %s", self.client.target, exc)


async def run(listen: tuple[str, int], target: tuple[str, int], idle: float, wake: float) -> None:
    loop = asyncio.get_running_loop()
    state = SleepyState(idle, wake)
    transport, _ = await loop.create_datagram_endpoint(lambda: ClientSide(target, state), local_addr=listen)
    logger.info("proxy sonolento %s -> %s (dorme após %.0fs; acorda em %.1fs)", listen, target, idle, wake)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await stop.wait()
    finally:
        transport.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--listen", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--idle", type=float, default=20.0)
    p.add_argument("--wake", type=float, default=1.0)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(_addr(a.listen), _addr(a.target), a.idle, a.wake))
    return 0


if __name__ == "__main__":
    sys.exit(main())
