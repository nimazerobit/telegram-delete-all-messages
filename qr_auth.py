from __future__ import annotations

import asyncio
import getpass
import os
import sys

from qrcode import QRCode
from telethon import TelegramClient
from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

QR_REFRESH_SECONDS = 25


def _clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _print_qr(login_url: str) -> None:
    """Render Telegram's login URL as a QR code, with a text fallback."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:
            pass

    qr = QRCode(border=1)
    qr.add_data(login_url)
    try:
        qr.print_ascii(invert=True)
    except (UnicodeEncodeError, UnicodeDecodeError):
        print("Terminal cannot display a QR code. Open this URL instead:")
        print(login_url)


async def _sign_in_with_password(client: TelegramClient) -> None:
    """Prompt until the account's two-step-verification password is accepted."""
    while True:
        password = getpass.getpass("Two-step verification password: ")
        try:
            await client.sign_in(password=password)
        except PasswordHashInvalidError:
            print("Wrong password. Try again.")
        else:
            return


async def login_with_qr(client: TelegramClient) -> None:
    """Authorize ``client`` by displaying refreshable Telegram login QR codes."""
    qr_login = await client.qr_login()

    while True:
        _clear_screen()
        print("Log in with QR code")
        print("On your phone: Settings -> Devices -> Link Desktop Device")
        print(f"Scan the code below (it refreshes every {QR_REFRESH_SECONDS} seconds)\n")
        _print_qr(qr_login.url)

        try:
            await asyncio.wait_for(qr_login.wait(), timeout=QR_REFRESH_SECONDS)
            break
        except asyncio.TimeoutError:
            await qr_login.recreate()
        except SessionPasswordNeededError:
            await _sign_in_with_password(client)
            break

    me = await client.get_me()
    username = f" (@{me.username})" if me and me.username else ""
    print(f"\nLogged in as {me.first_name}{username}")
