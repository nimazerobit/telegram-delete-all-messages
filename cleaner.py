from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError

from qr_auth import login_with_qr

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / ".telegram-cleaner.json"
LEGACY_CONFIG_PATH = PROJECT_DIR / "cache"
SESSION_PATH = PROJECT_DIR / "telegram-cleaner"
DELETE_CHUNK_SIZE = 100

Group = types.Chat | types.Channel


@dataclass(frozen=True)
class ApiCredentials:
    api_id: int
    api_hash: str


def read_cached_credentials() -> ApiCredentials | None:
    """Read current or legacy cached credentials without failing on bad data."""
    for path, id_key, hash_key in (
        (CONFIG_PATH, "api_id", "api_hash"),
        (LEGACY_CONFIG_PATH, "API_ID", "API_HASH"),
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ApiCredentials(api_id=int(data[id_key]), api_hash=str(data[hash_key]))
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def save_credentials(credentials: ApiCredentials) -> None:
    CONFIG_PATH.write_text(
        json.dumps({"api_id": credentials.api_id, "api_hash": credentials.api_hash}, indent=2),
        encoding="utf-8",
    )


def load_api_credentials() -> ApiCredentials:
    """Load credentials from the environment, cache, or interactive prompts."""
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    if api_id and api_hash:
        try:
            return ApiCredentials(api_id=int(api_id), api_hash=api_hash)
        except ValueError as error:
            raise ValueError("API_ID must be an integer.") from error

    cached = read_cached_credentials()
    if cached:
        return cached

    while True:
        try:
            entered_id = int(input("Enter your Telegram API id: ").strip())
        except ValueError:
            print("API id must be an integer. Try again.")
            continue
        break

    entered_hash = input("Enter your Telegram API hash: ").strip()
    if not entered_hash:
        raise ValueError("API hash cannot be empty.")

    credentials = ApiCredentials(api_id=entered_id, api_hash=entered_hash)
    save_credentials(credentials)
    return credentials


def chunks(items: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def is_group(entity: types.TypeChat) -> bool:
    return isinstance(entity, types.Chat) or (
        isinstance(entity, types.Channel) and bool(entity.megagroup)
    )


def is_migrated_group(entity: types.TypeChat) -> bool:
    return isinstance(entity, types.Chat) and entity.migrated_to is not None


def chat_title(chat: types.TypeChat, parent_channel: types.Channel | None = None) -> str:
    title = getattr(chat, "title", None) or "Unknown"
    username = getattr(chat, "username", None)
    if username:
        title = f"{title} (@{username})"
    if parent_channel:
        parent = f"@{parent_channel.username}" if parent_channel.username else parent_channel.title
        title = f"{title} [discussion of {parent or 'Unknown'}]"
    if is_migrated_group(chat):
        title = f"{title} [pre-migration history]"
    return title


@dataclass
class GroupCatalog:
    groups: list[Group]
    discussion_parents: dict[int, types.Channel]


class Cleaner:
    def __init__(
        self,
        client: TelegramClient,
        delete_chunk_size: int = DELETE_CHUNK_SIZE,
        status: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.delete_chunk_size = delete_chunk_size
        self.status = status
        self.me: types.User | None = None

    async def get_groups(self) -> GroupCatalog:
        """Return all group dialogs, including channel-linked discussions."""
        groups_by_id: dict[int, Group] = {}
        channels: list[types.Channel] = []

        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            if is_group(entity):
                groups_by_id[entity.id] = entity
            elif isinstance(entity, types.Channel):
                channels.append(entity)

        discussion_parents: dict[int, types.Channel] = {}
        if channels:
            self.status(f"Scanning {len(channels)} channels for linked discussion groups...")

        for channel in channels:
            linked = await self._get_linked_chat(channel)
            if linked:
                groups_by_id[linked.id] = linked
                discussion_parents[linked.id] = channel

        groups = sorted(groups_by_id.values(), key=lambda chat: chat_title(chat).lower())
        return GroupCatalog(groups=groups, discussion_parents=discussion_parents)

    async def _get_linked_chat(self, channel: types.Channel) -> Group | None:
        """Find the discussion chat attached to a broadcast channel, if present."""
        for attempt in range(2):
            try:
                full_channel = await self.client(functions.channels.GetFullChannelRequest(channel))
                linked_id = full_channel.full_chat.linked_chat_id
                if not linked_id:
                    return None
                linked = await self.client.get_entity(linked_id)
                return linked if is_group(linked) else None
            except FloodWaitError as error:
                if attempt:
                    return None
                self.status(f"Flood limit reached, waiting {error.seconds} seconds...")
                await asyncio.sleep(error.seconds)
            except Exception:
                return None
        return None

    async def resolve_group(self, reference: str) -> Group | None:
        """Resolve a group by numeric id or @username."""
        try:
            entity = await self.client.get_entity(int(reference))
        except ValueError:
            entity = await self.client.get_entity(reference)
        return entity if is_group(entity) else None

    async def enter_groups_manually(self) -> list[Group]:
        print(
            "\nEnter a chat id (like -1001234567890) or a @username, one per line.\n"
            "Press Enter on an empty line when you are done."
        )
        groups: list[Group] = []
        while reference := input("  Chat id or @username: ").strip():
            try:
                group = await self.resolve_group(reference)
            except Exception as error:
                print(f'  Could not open "{reference}": {error}')
                continue
            if not group:
                print(f'  "{reference}" is not a group, skipping.')
                continue
            print(f"  Added {chat_title(group)}.")
            groups.append(group)
        return groups

    async def select_groups(self) -> list[Group]:
        catalog = await self.get_groups()
        groups = catalog.groups
        delete_all_option = len(groups) + 1
        manual_option = len(groups) + 2

        print("Delete all your messages in")
        print(f"  ({len(groups)} groups found, including archived chats and channel discussions)\n")
        for index, group in enumerate(groups, start=1):
            print(f"  {index}. {chat_title(group, catalog.discussion_parents.get(group.id))}")
        print(f"  {delete_all_option}. (!) DELETE ALL YOUR MESSAGES IN ALL OF THOSE GROUPS (!)")
        print(f"  {manual_option}. Enter a chat id or @username by hand (for groups you have left)\n")

        selected_numbers = self._prompt_selection(manual_option)
        selected: list[Group] = []
        manual_groups: list[Group] = []

        for number in selected_numbers:
            if number == delete_all_option:
                if not self._confirm_delete_all():
                    return []
                selected = groups
                break
            if number == manual_option:
                manual_groups.extend(await self.enter_groups_manually())
            else:
                selected.append(groups[number - 1])

        selected_by_id = {group.id: group for group in selected}
        result = list(selected_by_id.values())
        if result:
            selected_names = ", ".join(
                chat_title(group, catalog.discussion_parents.get(group.id)) for group in result
            )
            print(f"\nSelected {selected_names}.\n")

        for group in manual_groups:
            await self.delete_my_messages(group)

        return result

    @staticmethod
    def _prompt_selection(maximum: int) -> list[int]:
        while True:
            raw_selection = input("Insert option numbers (comma separated): ").strip()
            try:
                numbers = [int(value.strip()) for value in raw_selection.split(",") if value.strip()]
            except ValueError:
                numbers = []
            if numbers and all(1 <= number <= maximum for number in numbers):
                return numbers
            print("Choose one or more valid option numbers. Try again.")

    @staticmethod
    def _confirm_delete_all() -> bool:
        print("\nTHIS WILL DELETE ALL YOUR MESSAGES IN ALL GROUPS!")
        return input('Please type "I understand" to proceed: ').strip().casefold() == "i understand"

    @staticmethod
    def _confirm_delete(group: Group, message_count: int) -> bool:
        print(f'\nFound {message_count} of your messages in "{chat_title(group)}".')
        print("These messages are about to be permanently deleted.")
        return (
            input('Type "delete" to proceed, or press Enter to skip: ')
            .strip()
            .casefold()
            == "delete"
        )

    async def delete_my_messages(self, group: Group) -> None:
        if not self.me:
            self.me = await self.client.get_me()

        message_ids: list[int] = []
        async for message in self.client.iter_messages(group, from_user=self.me):
            message_ids.append(message.id)
            if len(message_ids) % self.delete_chunk_size == 0:
                self.status(f'Found {len(message_ids)} of your messages in "{chat_title(group)}"')

        self.status(f'Found {len(message_ids)} of your messages in "{chat_title(group)}"')

        if not message_ids:
            return

        if not self._confirm_delete(group, len(message_ids)):
            self.status(f'Skipping deletion in "{chat_title(group)}".')
            return

        for message_ids_chunk in chunks(message_ids, self.delete_chunk_size):
            await self._delete_chunk(group, message_ids_chunk)

    async def _delete_chunk(self, group: Group, message_ids: list[int]) -> None:
        while True:
            try:
                await self.client.delete_messages(group, message_ids, revoke=True)
                return
            except FloodWaitError as error:
                self.status(f"Flood limit reached, waiting {error.seconds} seconds...")
                await asyncio.sleep(error.seconds)


def select_login_method() -> str:
    print("\nHow do you want to log in?")
    print("  1. Phone number and confirmation code")
    print("  2. QR code")
    while True:
        choice = input("Insert option number [1]: ").strip() or "1"
        if choice in {"1", "2"}:
            return choice
        print("Invalid option selected. Try again.")


async def ensure_logged_in(client: TelegramClient) -> None:
    await client.connect()
    if await client.is_user_authorized():
        return
    if select_login_method() == "2":
        await login_with_qr(client)
    else:
        await client.start()


async def main() -> None:
    credentials = load_api_credentials()
    client = TelegramClient(SESSION_PATH, credentials.api_id, credentials.api_hash)
    try:
        await ensure_logged_in(client)
        cleaner = Cleaner(client)
        groups = await cleaner.select_groups()
        if not groups:
            print("No groups selected. Exiting...")
            return
        for group in groups:
            await cleaner.delete_my_messages(group)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())