# Telegram Delete All Messages
A small Python tool that scans Telegram groups for your own messages and lets you delete them in bulk.

> [!WARNING]
> Message deletion is permanent.
> Your Telegram session is stored locally in:
> ```text
> telegram-cleaner.session
> ```
> Do not share this file or your API credentials.

## Usage
After logging in, the program lists your groups.
Choose one or more groups by entering their option numbers:

```text
Insert option numbers (comma separated): 1,3,5
```

The program scans each selected group and shows how many of your messages were found.
Before deleting, you must confirm:

```text
Found 123 of your messages in "My Group".
These messages are about to be permanently deleted.
Type "delete" to proceed, or press Enter to skip:
```

### Custom Chat ID
Choose the custom chat option to enter a chat ID or `@username` manually. This is useful for groups you have left.

## Delete all
The `DELETE ALL` option deletes your messages from every group found by the program.

It requires typing:
```text
I understand
```
before proceeding.