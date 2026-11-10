"""A loopback egress proxy that connects only to checked IP addresses."""

import asyncio
from functools import partial
from urllib.parse import urlsplit, urlunsplit

from .errors import LeashByteError
from .url_guard import Target, URLGuard


class GuardedProxy:
    """Forward HTTP and CONNECT traffic without a second DNS lookup."""

    def __init__(self, guard: URLGuard, connect_timeout: float = 5) -> None:
        self.guard = guard
        self.connect_timeout = connect_timeout
        self.server: asyncio.Server | None = None
        self.port = 0
        self.semaphore = asyncio.Semaphore(64)

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=16_384)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def connect(self, target: Target) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        last_error: Exception | None = None
        for address in target.addresses:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(str(address), target.port),
                    timeout=self.connect_timeout,
                )
            except (OSError, asyncio.TimeoutError) as error:
                last_error = error
        raise LeashByteError(
            502, "proxy_connect_failed", "The checked destination did not accept a connection."
        ) from last_error

    @staticmethod
    async def send_status(writer: asyncio.StreamWriter, status: int, text: str) -> None:
        writer.write(
            f"HTTP/1.1 {status} {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode(
                "ascii"
            )
        )
        await writer.drain()

    @staticmethod
    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            data = await asyncio.wait_for(reader.read(65_536), timeout=60)
            if not data:
                return
            writer.write(data)
            await writer.drain()

    @staticmethod
    async def relay_http_response(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, allow_upgrade: bool
    ) -> None:
        """Make upstream keep-alive unusable for a second client request."""
        while True:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=60)
            bare_lines = header.replace(b"\r\n", b"")
            if b"\n" in bare_lines or b"\r" in bare_lines:
                raise ValueError("The upstream response used invalid line endings.")
            lines = header.decode("iso-8859-1").split("\r\n")
            status = int(lines[0].split(" ")[1])
            if status == 101:
                if not allow_upgrade:
                    raise ValueError("The upstream sent an unsolicited protocol upgrade.")
                writer.write(header)
                await writer.drain()
                break
            forwarded = [lines[0], "Connection: close"]
            for line in lines[1:]:
                header_name = line.split(":", 1)[0].strip().lower()
                if line and header_name not in {"connection", "keep-alive", "proxy-connection"}:
                    forwarded.append(line)
            writer.write(("\r\n".join(forwarded) + "\r\n\r\n").encode("iso-8859-1"))
            await writer.drain()
            if not 100 <= status < 200:
                break
        await GuardedProxy.relay(reader, writer)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with self.semaphore:
            upstream_writer = None
            forwarding = False
            try:
                request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
                lines = request.decode("iso-8859-1").split("\r\n")
                method, destination, version = lines[0].split(" ")
                if method.upper() == "CONNECT":
                    raw_url = f"https://{destination}/"
                elif destination.startswith(("http://", "https://")):
                    raw_url = destination
                elif destination.startswith(("ws://", "wss://")):
                    raw_url = (
                        "http://" if destination.startswith("ws://") else "https://"
                    ) + destination.split("://", 1)[1]
                else:
                    await self.send_status(writer, 400, "Bad Request")
                    return
                target = await self.guard.resolve(raw_url)
                upstream_reader, upstream_writer = await self.connect(target)
                forwarding = True
                if method.upper() == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                else:
                    parts = urlsplit(target.url)
                    origin_path = urlunsplit(("", "", parts.path, parts.query, ""))
                    forwarded = [f"{method} {origin_path} {version}"]
                    for line in lines[1:]:
                        if line and not line.lower().startswith(
                            (
                                "connection:",
                                "keep-alive:",
                                "proxy-authorization:",
                                "proxy-connection:",
                            )
                        ):
                            forwarded.append(line)
                    forwarded.append("Connection: close")
                    upstream_writer.write(
                        ("\r\n".join(forwarded) + "\r\n\r\n").encode("iso-8859-1")
                    )
                    await upstream_writer.drain()
                response_relay = (
                    self.relay
                    if method.upper() == "CONNECT"
                    else partial(
                        self.relay_http_response,
                        allow_upgrade=any(
                            line.split(":", 1)[0].strip().lower() == "upgrade" for line in lines[1:]
                        ),
                    )
                )
                tasks = [
                    asyncio.create_task(self.relay(reader, upstream_writer)),
                    asyncio.create_task(response_relay(upstream_reader, writer)),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
            except LeashByteError:
                if not forwarding:
                    await self.send_status(writer, 403, "Forbidden")
            except (
                asyncio.TimeoutError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
                UnicodeError,
                ValueError,
                IndexError,
                OSError,
            ):
                if not forwarding:
                    try:
                        await self.send_status(writer, 400, "Bad Request")
                    except OSError:
                        pass
            finally:
                if upstream_writer is not None:
                    upstream_writer.close()
                writer.close()
                await asyncio.gather(
                    *(item.wait_closed() for item in [upstream_writer, writer] if item is not None),
                    return_exceptions=True,
                )
