"""Chrome lifecycle, marked observations, request controls, and CDP grants."""

import asyncio
import base64
import fcntl
import hashlib
import hmac
import html
import importlib
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

import httpx
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from playwright.async_api import BrowserContext, ElementHandle, Route, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Request as PlaywrightRequest

from .config import Settings
from .errors import LeashByteError
from .fetch import Fetcher, HostThrottle
from .models import BrowserActionRequest
from .proxy import GuardedProxy
from .url_guard import URLGuard
from .vault import https_page_origin, placeholder_parts

USER_AGENT = "LeashByte/0.1 (+https://leashbyte.com)"
# The model sees at most this much visible page text per observation. The page script
# reads up to the scan limit, so redaction runs on more text than the model gets.
PAGE_TEXT_LIMIT = 4_000
PAGE_TEXT_SCAN_LIMIT = 100_000
NODE_NAME_LIMIT = 300
NODE_NAME_SCAN_LIMIT = 2_000
PAGE_TEXT_SCRIPT = """limit => {
  const body = document.body;
  const raw = body ? String(body.innerText || '') : '';
  const text = raw
    .replace(/[ \\t\\f\\v\\u00a0]+/g, ' ')
    .replace(/ *\\n */g, '\\n')
    .replace(/\\n{3,}/g, '\\n\\n')
    .trim();
  return {text: text.slice(0, limit), cut: text.length > limit};
}"""


class ProfileLease:
    """An advisory lock that works across browser-pool processes."""

    def __init__(self, root: Path, profile: str) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / f"{profile}.lock"
        self.handle = self.path.open("a+b")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.handle.close()
            raise LeashByteError(
                409, "profile_in_use", "The profile is in another live session."
            ) from error

    def release(self) -> None:
        if self.handle.closed:
            return
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


class CDPGrantStore:
    """One-time, short-lived CDP credentials stored as hashes."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.grants: dict[tuple[str, str], float] = {}

    def issue(self, session_id: str) -> tuple[str, int]:
        now = time.time()
        self.grants = {key: expiry for key, expiry in self.grants.items() if expiry > now}
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        self.grants[(session_id, digest)] = now + self.ttl_seconds
        return token, self.ttl_seconds

    def consume(self, session_id: str, token: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        try:
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        except UnicodeEncodeError:
            return False
        expires = self.grants.get((session_id, digest))
        if expires is None or expires <= now:
            return False
        del self.grants[(session_id, digest)]
        return True


class WebBotAuthSigner:
    """Optional HTTP message signatures for the identified bot."""

    def __init__(self, settings: Settings) -> None:
        self.key = None
        self.key_id = settings.web_bot_auth_key_id
        self.agent_url = settings.web_bot_auth_agent_url
        if settings.web_bot_auth_key_file is None:
            return
        if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.key_id) is None:
            raise RuntimeError("LEASHBYTE_WEB_BOT_AUTH_KEY_ID is not valid.")
        key = load_pem_private_key(settings.web_bot_auth_key_file.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError("The Web Bot Auth key must be an Ed25519 private key.")
        self.key = key

    @property
    def enabled(self) -> bool:
        return self.key is not None

    def headers(self, method: str, url: str) -> dict[str, str]:
        if self.key is None:
            return {}
        parts = urlsplit(url)
        authority = parts.netloc
        created = int(time.time())
        parameters = (
            '("@method" "@authority" "@target-uri")'
            f';created={created};expires={created + 300};keyid="{self.key_id}";tag="web-bot-auth"'
        )
        covered = (
            f'"@method": {method}\n"@authority": {authority}\n"@target-uri": {url}\n'
            f'"@signature-params": {parameters}'
        )
        signature = self.key.sign(covered.encode("utf-8"))
        return {
            "Signature-Agent": f'"{self.agent_url}"',
            "Signature-Input": f"sig1={parameters}",
            "Signature": f"sig1=:{base64.b64encode(signature).decode('ascii')}:",
        }


@dataclass
class Session:
    id: str
    profile: str
    context: BrowserContext
    lease: ProfileLease
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    latest_snapshot: str = ""
    marks: dict[int, ElementHandle] = field(default_factory=dict)
    redactions: list[str] = field(default_factory=list)
    cdp_issued: bool = False
    closed: bool = False


class BrowserPool:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guard = URLGuard(settings.allowlist())
        self.proxy = GuardedProxy(self.guard, settings.fetch_connect_timeout_seconds)
        self.fetcher = Fetcher(settings)
        self.throttle = HostThrottle(settings.fetch_min_host_interval_seconds)
        self.sessions: dict[str, Session] = {}
        self.guard_lock = asyncio.Lock()
        self.playwright = None
        self.grants = CDPGrantStore(settings.cdp_grant_seconds)
        self.signer = WebBotAuthSigner(settings)
        self.plugins = []
        if settings.browser_plugins_enabled:
            for name in settings.browser_plugin_modules.split(","):
                if name.strip():
                    self.plugins.append(importlib.import_module(name.strip()))

    async def start(self) -> None:
        await self.proxy.start()
        self.playwright = await async_playwright().start()

    async def stop(self) -> None:
        for session_id in list(self.sessions):
            await self.close(session_id, missing_ok=True)
        if self.playwright is not None:
            await self.playwright.stop()
        await self.proxy.stop()

    async def reap(self) -> None:
        now = time.time()
        for session_id, session in list(self.sessions.items()):
            if session.expires_at <= now:
                await self.close(session_id, missing_ok=True)

    async def create(
        self, profile: str, start_url: str | None, proxy: str | None, lease_seconds: int | None
    ) -> dict:
        async with self.guard_lock:
            if len(self.sessions) >= self.settings.browser_max_sessions:
                raise LeashByteError(429, "browser_capacity", "The browser pool is full.")
            if any(session.profile == profile for session in self.sessions.values()):
                raise LeashByteError(
                    409, "profile_in_use", "The profile is in another live session."
                )
            if proxy is not None:
                raise LeashByteError(
                    422,
                    "proxy_not_supported",
                    "Custom proxies are not supported. The browser uses its own guarded proxy.",
                )
            lease = ProfileLease(self.settings.browser_profile_root, profile)
            try:
                duration = min(
                    lease_seconds or self.settings.browser_lease_seconds,
                    self.settings.browser_lease_seconds,
                )
                args = [
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-debugging-port=0",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-dev-shm-usage",
                    "--disable-quic",
                    "--disable-sync",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
                    "--metrics-recording-only",
                    "--no-default-browser-check",
                    "--no-first-run",
                    "--proxy-bypass-list=<-loopback>",
                ]
                spkis = [
                    item.strip()
                    for item in self.settings.browser_tls_spki_allowlist.split(",")
                    if item.strip()
                ]
                if spkis:
                    args.append(f"--ignore-certificate-errors-spki-list={','.join(spkis)}")
                proxy_settings = {"server": f"http://127.0.0.1:{self.proxy.port}"}
                context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.settings.browser_profile_root / profile),
                    channel="chrome",
                    headless=True,
                    chromium_sandbox=False,
                    accept_downloads=False,
                    proxy=proxy_settings,
                    args=args,
                    user_agent=USER_AGENT,
                    service_workers="block",
                    env={
                        "HOME": "/tmp",  # noqa: S108 - private container tmpfs
                        "LANG": "C.UTF-8",
                        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                        "TMPDIR": "/tmp",  # noqa: S108 - private container tmpfs
                        "TZ": "UTC",
                    },
                )
                session = Session(
                    id=uuid.uuid4().hex,
                    profile=profile,
                    context=context,
                    lease=lease,
                    expires_at=time.time() + duration,
                )
                self.sessions[session.id] = session

                def release_closed_context() -> None:
                    if not session.closed:
                        session.closed = True
                        session.lease.release()
                        self.sessions.pop(session.id, None)

                context.on("close", release_closed_context)
                await context.route(
                    "**/*", lambda route, request: self.intercept(session, route, request)
                )
                if start_url:
                    await self.navigate(session, start_url)
                await self.snapshot(session)
                return self.describe(session)
            except BaseException:
                if "session" in locals():
                    await self.close(session.id, missing_ok=True)
                else:
                    lease.release()
                raise

    @staticmethod
    def redaction_variants(secret: str) -> set[str]:
        encoded = quote(secret, safe="")
        form_encoded = quote_plus(secret, safe="")
        lower_escapes = re.sub(r"%[0-9A-F]{2}", lambda match: match.group().lower(), encoded)
        return {
            secret,
            html.escape(secret),
            html.escape(secret, quote=False),
            encoded,
            form_encoded,
            lower_escapes,
            encoded.lower(),
        }

    @staticmethod
    def redact(session: Session, value: str) -> str:
        for secret in session.redactions:
            for variant in BrowserPool.redaction_variants(secret):
                value = re.sub(re.escape(variant), "[redacted]", value, flags=re.IGNORECASE)
        return value

    @staticmethod
    def bounded_redact(
        session: Session, value: object, scan_cut: bool, limit: int
    ) -> tuple[str, bool]:
        """Redact page text, then cut it to the limit.

        The page script already cut the text at its scan limit. A secret that crosses
        that cut is only partly there, so redaction cannot match it. In that case we drop
        a tail as long as the longest secret form before the final cut.
        """
        if not isinstance(value, str):
            return "", False
        redacted = BrowserPool.redact(session, value)
        if scan_cut:
            margin = max(
                (
                    len(variant)
                    for secret in session.redactions
                    for variant in BrowserPool.redaction_variants(secret)
                ),
                default=0,
            )
            redacted = redacted[: max(0, len(redacted) - margin)]
        return redacted[:limit], scan_cut or len(redacted) > limit

    def describe(self, session: Session) -> dict:
        page = session.context.pages[0] if session.context.pages else None
        return {
            "id": session.id,
            "profile": session.profile,
            "state": "closed" if session.closed else "active",
            "current_url": self.redact(session, page.url) if page else "about:blank",
            "expires_at": datetime.fromtimestamp(session.expires_at, UTC).isoformat(),
            "capabilities": ["snapshot", "actions", "screenshot", "cdp"],
        }

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None or session.closed:
            raise LeashByteError(404, "session_not_found", "The browser session was not found.")
        if session.expires_at <= time.time():
            raise LeashByteError(410, "session_expired", "The browser session lease expired.")
        return session

    async def close(self, session_id: str, missing_ok: bool = False) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            if missing_ok:
                return
            raise LeashByteError(404, "session_not_found", "The browser session was not found.")
        async with session.lock:
            if not session.closed:
                session.closed = True
                try:
                    await session.context.close()
                finally:
                    session.lease.release()
                    self.sessions.pop(session_id, None)

    async def snapshot(self, session: Session) -> dict:
        page = session.context.pages[-1]
        marks: dict[int, ElementHandle] = {}
        nodes = []
        elements = await page.query_selector_all(
            "a[href],button,input,select,textarea,[role='button'],[role='link'],[tabindex]"
        )
        for element in elements[:200]:
            try:
                if not await element.is_visible() or not await element.is_enabled():
                    continue
                details = await element.evaluate(
                    """(element, limit) => {
                      const tag = element.tagName.toLowerCase();
                      const text = String(
                        element.getAttribute('aria-label') || element.innerText ||
                        element.getAttribute('placeholder') || element.getAttribute('name') || ''
                      ).trim();
                      const roles = {
                        a: 'link', button: 'button', input: 'textbox',
                        select: 'combobox', textarea: 'textbox'
                      };
                      return {
                        tag,
                        role: element.getAttribute('role') || (roles[tag] || tag),
                        name: text.slice(0, limit),
                        name_cut: text.length > limit,
                        input_type: tag === 'input' ? element.type : null
                      };
                    }""",
                    NODE_NAME_SCAN_LIMIT,
                )
                name, _ = self.bounded_redact(
                    session, details.pop("name"), bool(details.pop("name_cut")), NODE_NAME_LIMIT
                )
                mark = len(marks) + 1
                marks[mark] = element
                nodes.append({"mark": mark, **details, "name": name})
            except PlaywrightError:
                continue
        try:
            page_text = await page.evaluate(PAGE_TEXT_SCRIPT, PAGE_TEXT_SCAN_LIMIT)
        except PlaywrightError:
            page_text = {}
        if not isinstance(page_text, dict):
            page_text = {}
        text, text_truncated = self.bounded_redact(
            session, page_text.get("text"), page_text.get("cut") is True, PAGE_TEXT_LIMIT
        )
        session.marks = marks
        session.latest_snapshot = uuid.uuid4().hex
        return {
            "session_id": session.id,
            "snapshot_id": session.latest_snapshot,
            "url": self.redact(session, page.url),
            "title": self.redact(session, await page.title()),
            "nodes": nodes,
            "text": text,
            "text_truncated": text_truncated,
        }

    async def intercept(self, session: Session, route: Route, request: PlaywrightRequest) -> None:
        url = request.url
        if url.startswith(("about:", "data:", "blob:", "chrome:")):
            await route.continue_()
            return
        try:
            target = await self.guard.resolve(url)
            if request.is_navigation_request():
                if self.settings.respect_robots:
                    await self.fetcher.check_robots(
                        target.url,
                        self.settings.browser_navigation_timeout_seconds,
                        512_000,
                    )
                await self.throttle.wait(target.host)
            headers = {
                **await request.all_headers(),
                **self.signer.headers(request.method, target.url),
            }
            for plugin in self.plugins:
                hook = getattr(plugin, "before_request", None)
                if hook is not None:
                    headers = hook(headers=headers, url=target.url, method=request.method)
            await route.continue_(headers=headers)
        except Exception:
            await route.abort("blockedbyclient")

    async def navigate(self, session: Session, raw_url: str) -> None:
        target = await self.guard.resolve(raw_url)
        if self.settings.respect_robots:
            await self.fetcher.check_robots(
                target.url,
                self.settings.browser_navigation_timeout_seconds,
                512_000,
            )
        try:
            page = session.context.pages[-1]
            response = await page.goto(
                target.url,
                wait_until="domcontentloaded",
                timeout=self.settings.browser_navigation_timeout_seconds * 1000,
            )
            if response is not None and response.status >= 400:
                raise LeashByteError(
                    502, "navigation_failed", f"The destination returned HTTP {response.status}."
                )
        except PlaywrightError as error:
            raise LeashByteError(
                403, "browser_request_denied", "The browser could not open this destination."
            ) from error

    async def act(self, session: Session, body: BrowserActionRequest) -> dict:
        async with session.lock:
            if body.snapshot_id != session.latest_snapshot:
                raise LeashByteError(
                    409, "stale_snapshot", "Take a new snapshot before the next action."
                )
            if body.action == "navigate":
                if not body.url:
                    raise LeashByteError(422, "invalid_action", "Navigation needs a URL.")
                await self.navigate(session, body.url)
            elif body.action == "scroll":
                await session.context.pages[-1].mouse.wheel(body.x, body.y)
                await asyncio.sleep(0.1)
            else:
                if body.mark is None or body.mark not in session.marks:
                    raise LeashByteError(
                        422, "invalid_mark", "The action needs a mark from the current snapshot."
                    )
                element = session.marks[body.mark]
                try:
                    if body.action == "click":
                        await element.click()
                    elif body.action == "type":
                        if body.text is None or "{{credential:" in body.text:
                            raise LeashByteError(
                                422,
                                "invalid_action",
                                "Use public text or a credential placeholder action.",
                            )
                        await element.type(body.text)
                    elif body.action == "select":
                        if not body.values:
                            raise LeashByteError(
                                422, "invalid_action", "Select needs at least one option value."
                            )
                        await element.select_option(body.values)
                    elif body.action == "fill_secret":
                        if body.placeholder is None:
                            raise LeashByteError(
                                422, "invalid_action", "Secret fill needs a credential placeholder."
                            )
                        await self.fill_secret(session, element, body.placeholder)
                except PlaywrightError as error:
                    raise LeashByteError(
                        409, "action_failed", "The marked element did not accept the action."
                    ) from error
            return await self.snapshot(session)

    async def fill_secret(self, session: Session, element: ElementHandle, placeholder: str) -> None:
        if session.cdp_issued:
            raise LeashByteError(
                409,
                "cdp_fill_conflict",
                "Use a different session for CDP or credential fills.",
            )
        alias, field_name = placeholder_parts(placeholder)
        frame = await element.owner_frame()
        if frame is None or frame.parent_frame is not None:
            raise LeashByteError(
                422, "invalid_secret_target", "Fill credentials only in a main-page input."
            )
        page_url = frame.url
        origin = https_page_origin(page_url)

        async def check_target() -> None:
            target = await element.evaluate(
                """element => ({
                  tag: element.tagName.toLowerCase(),
                  type: element.type || '',
                  form_action: element.form ? element.form.action : ''
                })"""
            )
            allowed = {"password"} if field_name == "password" else {"text", "email", "tel"}
            if target["tag"] != "input" or target["type"] not in allowed:
                raise LeashByteError(
                    422,
                    "invalid_secret_target",
                    "The credential field does not match the marked input.",
                )
            if target["form_action"] and https_page_origin(target["form_action"]) != origin:
                raise LeashByteError(
                    422,
                    "invalid_secret_target",
                    "The credential form must submit to the current origin.",
                )

        await check_target()
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.post(
                    f"{self.settings.vault_base_url}/internal/resolve",
                    headers={"X-Vault-Resolve-Key": self.settings.require_vault_resolve_key()},
                    json={"alias": alias, "field": field_name, "origin": origin},
                )
        except httpx.HTTPError as error:
            raise LeashByteError(502, "vault_unavailable", "The vault did not answer.") from error
        if response.status_code >= 400:
            try:
                code = response.json()["error"]["code"]
            except (ValueError, KeyError, TypeError):
                code = "vault_request_failed"
            if code == "origin_not_allowed":
                raise LeashByteError(403, code, "This origin cannot use the credential.")
            if code == "credential_not_found":
                raise LeashByteError(404, code, "The credential was not found.")
            raise LeashByteError(
                502, "vault_request_failed", "The vault did not complete the fill."
            )
        secret = response.json().get("value")
        if not isinstance(secret, str):
            raise LeashByteError(
                502, "vault_request_failed", "The vault returned an invalid value."
            )
        if session.cdp_issued:
            raise LeashByteError(
                409,
                "cdp_fill_conflict",
                "Use a different session for CDP or credential fills.",
            )
        if frame.url != page_url or https_page_origin(frame.url) != origin:
            raise LeashByteError(
                409, "origin_changed", "The page origin changed before the credential fill."
            )
        await check_target()
        if secret not in session.redactions:
            session.redactions.append(secret)
            session.redactions = session.redactions[-20:]
        await element.fill(secret)
        secret = ""

    async def screenshot(self, session: Session) -> bytes:
        async with session.lock:
            if session.redactions:
                raise LeashByteError(
                    409,
                    "screenshot_redaction_active",
                    "Screenshots are disabled after a credential fill in this session.",
                )
            return await session.context.pages[-1].screenshot(type="png", full_page=False)

    async def cdp_grant(self, session: Session, supplied_key: str | None) -> dict:
        async with session.lock:
            if session.redactions:
                raise LeashByteError(
                    409,
                    "cdp_fill_conflict",
                    "Use a different session for CDP or credential fills.",
                )
            expected = self.settings.require_cdp_key().encode("utf-8")
            if supplied_key is None or not hmac.compare_digest(
                supplied_key.encode("utf-8"), expected
            ):
                raise LeashByteError(
                    403, "cdp_capability_required", "Provide the separate CDP capability key."
                )
            token, expires = self.grants.issue(session.id)
            session.cdp_issued = True
            return {
                "session_id": session.id,
                "token": token,
                "expires_in": expires,
                "path": f"/v1/browser/sessions/{session.id}/cdp/connect",
            }

    async def debugger_url(self, session: Session) -> str:
        port_file = self.settings.browser_profile_root / session.profile / "DevToolsActivePort"
        try:
            port = int(port_file.read_text(encoding="ascii").splitlines()[0])
        except (OSError, ValueError, IndexError) as error:
            raise LeashByteError(
                503, "cdp_unavailable", "Chrome debugging is not available."
            ) from error
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.get(f"http://127.0.0.1:{port}/json/list")
                response.raise_for_status()
                targets = response.json()
            page_url = session.context.pages[-1].url
            matches = [
                item
                for item in targets
                if item.get("type") == "page" and item.get("url") == page_url
            ]
            if len(matches) != 1:
                raise ValueError
            url = matches[0]["webSocketDebuggerUrl"]
            if not urlsplit(url).path.startswith("/devtools/page/"):
                raise ValueError
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise LeashByteError(
                503, "cdp_unavailable", "Chrome page debugging is not available."
            ) from error
        return url

    async def open_cdp(self, session_id: str, token: str):
        session = self.get(session_id)
        if not self.grants.consume(session_id, token):
            raise LeashByteError(
                403, "invalid_cdp_grant", "The CDP grant is invalid, used, or expired."
            )
        url = await self.debugger_url(session)
        return await websockets.connect(url, max_size=32 * 1024 * 1024)
