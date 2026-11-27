"""The Smart Life session of the sharing channel: its file, and the QR-code login.

The session is the bridge's own (a session shared with another client dies when the refresh token
rotates). It is a secret: written with owner-only permissions, never logged.

The login needs no terminal: the bridge asks Tuya for a login token, shows it as a QR code and
polls until the owner has scanned it with the Smart Life app. In the add-on the code is shown on a
page of its own behind Home Assistant's ingress ("Open web UI"), never in the log: the code is a
login token. Without that page (outside Home Assistant) it is printed in the log. Every network
call is made from :meth:`Login.tick`, on the thread of the bridge's loop, one at a time.
"""

from __future__ import annotations

import contextlib
import html
import http.server
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pytuya_pilot_wire.errors import AuthenticationError
from pytuya_pilot_wire.model import DeviceSpec, Reading

log = logging.getLogger("tuyapilot2mqtt")

POLL_INTERVAL = 3.0  # seconds between two polls of a pending login
QR_LIFETIME = 120.0  # seconds a QR code is polled before a new one is asked
MAX_QR_CODES = 15  # then the login stops until the bridge restarts: the account is not hammered

# States of the sharing channel while it has no usable session (``bridge/info``, contract §1.1).
LOGIN_REQUIRED = "login_required"
WAITING_FOR_SCAN = "waiting_for_scan"
LOGIN_EXPIRED = "login_expired"


class SessionStore:
    """The session file. Unreadable, missing or incomplete is the same thing: no session."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(temporary, self.path)


class NoSession:
    """Stands for the sharing channel until a login gives a session: every call is refused."""

    def _refuse(self) -> Any:
        raise AuthenticationError("no Smart Life session: QR-code login needed")

    def discover(self) -> list[Any]:
        return self._refuse()

    def read(self, dev_ids: list[str]) -> list[Reading]:
        return self._refuse()

    def send(self, dev_id: str, commands: list[dict[str, Any]]) -> None:
        self._refuse()

    def spec(self, dev_id: str) -> DeviceSpec | None:
        return None


def render_qr(text: str) -> list[str]:
    """The text as a QR code, one string per line: half-height blocks, black on white (ANSI)."""
    import segno

    rows = [list(row) for row in segno.make(text, error="m").matrix_iter(border=3)]
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))
    glyph = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    return [
        "\x1b[30;47m" + "".join(glyph[bool(t), bool(b)] for t, b in zip(top, bottom, strict=True)) + "\x1b[0m"
        for top, bottom in zip(rows[0::2], rows[1::2], strict=True)
    ]


def _show(qr_text: str | None) -> None:
    """Without a login page: the code in the log, as a picture only (its text is never written)."""
    if qr_text is not None:
        print("", *render_qr(qr_text), "", sep="\n", flush=True)


# Home Assistant's ingress proxy: the only client the login page answers in the add-on.
INGRESS_CLIENT = "172.30.32.2"

_STATE_TEXT = {
    "online": "Connected. Nothing to do: the session is kept and renewed by the add-on.",
    WAITING_FOR_SCAN: "Scan this code with the Smart Life app, then confirm in the app.",
    LOGIN_REQUIRED: "No QR code yet.",
    LOGIN_EXPIRED: "Nobody scanned in time. Restart the add-on for a new code.",
    "disabled": "The sharing channel is not enabled: add 'sharing' to the option 'channels'.",
}


class LoginPage:
    """The QR code of a pending login, on a small web page for Home Assistant's ingress.

    The loop thread calls :meth:`show` and :meth:`update`; the server thread only reads a snapshot.
    The page refreshes itself, holds no script and no link, and answers only ``allowed`` clients.
    """

    def __init__(self, port: int, *, allowed: tuple[str, ...] = (INGRESS_CLIENT,), host: str = "0.0.0.0") -> None:
        self.port, self.allowed, self.host = port, allowed, host
        self._lock = threading.Lock()
        self._qr: str | None = None
        self._state, self._detail = "starting", ""
        self._server: http.server.ThreadingHTTPServer | None = None

    def show(self, qr_text: str | None) -> None:
        with self._lock:
            self._qr = qr_text

    def update(self, state: str, detail: str = "") -> None:
        with self._lock:
            self._state, self._detail = state, detail
            if state != WAITING_FOR_SCAN:
                self._qr = None

    def render(self) -> str:
        import segno

        with self._lock:
            qr, state, detail = self._qr, self._state, self._detail
        text = _STATE_TEXT.get(state, f"Sharing channel: {state}.")
        picture = ""
        if qr is not None and state == WAITING_FOR_SCAN:
            picture = segno.make(qr, error="m").svg_inline(scale=8, border=4, dark="#000", light="#fff")
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta http-equiv='refresh' content='5'><title>tuyapilot2mqtt login</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:24px;background:#fff;color:#111}"
            "svg{display:block;margin:16px 0;max-width:100%;height:auto}"
            ".muted{color:#555;font-size:14px}</style></head><body>"
            "<h1>Smart Life account</h1>"
            f"<p><strong>{html.escape(text)}</strong></p>"
            + (f"<p class='muted'>{html.escape(detail)}</p>" if detail and state != WAITING_FOR_SCAN else "")
            + picture
            + (
                "<p class='muted'>Smart Life app: the + or scan icon on the home screen. A code is valid two"
                " minutes; this page shows the next one by itself. Do not share a screenshot of it.</p>"
                if picture
                else ""
            )
            + "</body></html>"
        )

    def start(self) -> None:
        page = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
                if self.client_address[0] not in page.allowed:
                    self.send_error(403)
                    return
                body = page.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
                )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature of http.server
                pass  # no request log: nothing to learn there, and the page carries a token

        self._server = http.server.ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="login-page", daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


class Login:
    """One QR-code login at a time, advanced by :meth:`tick`."""

    def __init__(
        self,
        user_code: str,
        *,
        request: Callable[[str], str],
        poll: Callable[[str, str], Any],
        show: Callable[[str | None], None] = _show,
        where: str = "in the log below",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user_code = user_code
        self._request, self._poll, self._show, self._clock = request, poll, show, clock
        self._where = where
        self._qr: str | None = None
        self._qr_until = 0.0
        self._next_poll = 0.0
        self._codes = 0
        self.state = LOGIN_REQUIRED
        self.detail = "" if user_code else "option 'sharing_user_code' is empty"
        self._said: str | None = None

    def _say(self, message: str) -> None:
        if message != self._said:
            self._said = message
            log.warning("sharing channel: %s", message)

    def tick(self) -> Any:
        """Advance the login by at most one request. Returns the session once the owner scanned."""
        if not self._user_code:
            self._say("no session and no user code: fill the option 'sharing_user_code', then restart")
            return None
        now = self._clock()
        if now < self._next_poll:
            return None
        self._next_poll = now + POLL_INTERVAL
        if self._qr is None or now >= self._qr_until:
            if self._codes >= MAX_QR_CODES:
                if self._qr is not None:
                    self._qr = None
                    self._show(None)
                self.state, self.detail = LOGIN_EXPIRED, "no scan in time: restart the bridge for a new QR code"
                self._say(self.detail)
                return None
            try:
                self._qr = self._request(self._user_code)
            except Exception as err:  # refused user code, network: say it, try again later
                self._qr = None
                self._next_poll = now + 60.0
                self.state, self.detail = LOGIN_REQUIRED, f"QR-code request failed ({type(err).__name__})"
                self._say(self.detail + ": check the option 'sharing_user_code'")
                return None
            self._codes += 1
            self._qr_until = now + QR_LIFETIME
            self.state, self.detail = WAITING_FOR_SCAN, f"scan the QR code {self._where} with the Smart Life app"
            log.warning(
                "sharing channel: QR code %d of %d %s. Smart Life app > scan. Valid %d s.",
                self._codes,
                MAX_QR_CODES,
                self._where,
                int(QR_LIFETIME),
            )
            self._show(self._qr)
            return None
        with contextlib.suppress(Exception):  # a failed poll is polled again
            session = self._poll(self._qr, self._user_code)
            if session is not None:
                self._qr = None
                self._show(None)
                log.info("sharing channel: logged in")
                return session
        return None
