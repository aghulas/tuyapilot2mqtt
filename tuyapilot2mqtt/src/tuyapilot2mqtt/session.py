"""The Smart Life session of the sharing channel: its file, and the QR-code login.

The session is the bridge's own (a session shared with another client dies when the refresh token
rotates). It is a secret: written with owner-only permissions, never logged.

The login needs no terminal: the bridge asks Tuya for a login token, prints it as a QR code in its
log, and polls until the owner has scanned it with the Smart Life app. Every network call is made
from :meth:`Login.tick`, on the thread of the bridge's loop, one at a time.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
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


def _show(qr_text: str) -> None:
    print("", *render_qr(qr_text), "", sep="\n", flush=True)
    # The text of the code, for another QR renderer. It is a login token: single use, a few minutes.
    print(f"QR text (short-lived, do not share): {qr_text}", flush=True)


class Login:
    """One QR-code login at a time, advanced by :meth:`tick`."""

    def __init__(
        self,
        user_code: str,
        *,
        request: Callable[[str], str],
        poll: Callable[[str, str], Any],
        show: Callable[[str], None] = _show,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user_code = user_code
        self._request, self._poll, self._show, self._clock = request, poll, show, clock
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
            self.state, self.detail = WAITING_FOR_SCAN, "scan the QR code of the bridge's log with the Smart Life app"
            log.warning(
                "sharing channel: QR code %d of %d below. Smart Life app > scan. Valid %d s.",
                self._codes,
                MAX_QR_CODES,
                int(QR_LIFETIME),
            )
            self._show(self._qr)
            return None
        with contextlib.suppress(Exception):  # a failed poll is polled again
            session = self._poll(self._qr, self._user_code)
            if session is not None:
                self._qr = None
                log.info("sharing channel: logged in")
                return session
        return None
