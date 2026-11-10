"""Forward the host-loopback model only to the LeashByte Compose network."""

import asyncio
import ipaddress
import os
from contextlib import suppress


def source_allowed(raw_address: str, raw_network: str) -> bool:
    """Allow only source addresses in the configured private Compose network."""
    try:
        return ipaddress.ip_address(raw_address) in ipaddress.ip_network(raw_network, strict=False)
    except ValueError:
        return False


class ModelRelay:
    def __init__(self, network: str, upstream_port: int) -> None:
        self.network = network
        self.upstream_port = upstream_port
        self.connections = asyncio.Semaphore(2)

    async def copy(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65_536):
                writer.write(data)
                await writer.drain()
            with suppress(OSError):
                writer.write_eof()
        except (ConnectionError, OSError):
            return

    async def handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        peer = client_writer.get_extra_info("peername")
        if self.connections.locked() or not peer or not source_allowed(peer[0], self.network):
            client_writer.close()
            await client_writer.wait_closed()
            return
        async with self.connections:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.upstream_port), timeout=10
                )
                await asyncio.wait_for(
                    asyncio.gather(
                        self.copy(client_reader, upstream_writer),
                        self.copy(upstream_reader, client_writer),
                    ),
                    timeout=150,
                )
            except (ConnectionError, OSError, TimeoutError):
                pass
            finally:
                client_writer.close()
                with suppress(ConnectionError, OSError):
                    await client_writer.wait_closed()
                if "upstream_writer" in locals():
                    upstream_writer.close()
                    with suppress(ConnectionError, OSError):
                        await upstream_writer.wait_closed()


async def serve() -> None:
    bind_host = os.environ.get("LEASHBYTE_MODEL_RELAY_BIND_HOST", "")
    network = os.environ.get("LEASHBYTE_MODEL_RELAY_NETWORK", "")
    bind_port = int(os.environ.get("LEASHBYTE_MODEL_RELAY_BIND_PORT", "11434"))
    upstream_port = int(os.environ.get("LEASHBYTE_MODEL_RELAY_UPSTREAM_PORT", "11434"))
    address = ipaddress.ip_address(bind_host)
    ipaddress.ip_network(network, strict=False)
    if not address.is_private or address.is_loopback or address.is_unspecified:
        raise RuntimeError("The model relay must use one private gateway address.")
    if not 1 <= bind_port <= 65_535 or not 1 <= upstream_port <= 65_535:
        raise RuntimeError("The model relay port is not valid.")
    relay = ModelRelay(network, upstream_port)
    server = await asyncio.start_server(relay.handle, bind_host, bind_port, limit=131_072)
    print("Model relay ready on the private Compose gateway.", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
