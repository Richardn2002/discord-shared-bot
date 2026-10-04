"""Template for a freshly created project's draft.py."""
from __future__ import annotations

HANDLER_TEMPLATE = '''\
# ============================================================================
# Project: {nickname!r}   (slug: {slug})
# Author:  {author}
#
# This file is the draft. It only becomes live when you press "Deploy".
#
# Available framework functions (they are injected, no imports needed):
#   send(text, channel_id=None)            queue a message (defaults to the
#                                          channel the event happened in)
#   reply(text)                            reply to the triggering message
#                                          (on_message / on_message_edit only)
#   add_reaction(emoji, message_id=None)   react to the triggering message
#                                          (or a message id you pass)
#   send_embed(title=None, description=None, color=0x5865F2, fields=None,
#              channel_id=None)            send a rich embed; fields is a list
#                                          of {{"name": ..., "value": ..., "inline": True}}
#   send_file(filename, content, channel_id=None)
#                                          attach a file; content is str or bytes
#   log(msg)                               write to this project's console
#   print(...)                             also goes to the project console
#
#   kv_get(key, default=None)              read from this project's KV store
#   kv_set(key, value)                     value must be JSON-serializable
#   kv_delete(key)
#   kv_keys()
#   kv_all()                               dict of the whole store
#   secret_get(key, default=None)          read the .env secret store
#                                          (KV tab; read-only from code)
#   get_message(message_id, channel_id=None)
#                                          fetch any message the bot can see;
#                                          returns a dict like below, or None
#
# Handlers below are called when the matching Discord event fires. Delete the
# ones you do not care about (or leave them commented). If a handler raises
# or runs longer than the project timeout, on_failure() is called.
# ============================================================================


def on_message(message):
    """message = {{
        "id", "content",
        "author": {{"id", "name", "display_name"}},
        "channel_id", "channel_name",
        "guild_id", "guild_name",      # None in DMs
        "attachments": [url, ...],
        "reply_to": {{"message_id", "channel_id"}} or None,  # when a reply
        "mentions": [{{"id", "name", "display_name"}}, ...],
        "mention_everyone": bool, "pinned": bool,
        "created_at": iso str, "edited_at": iso str or None,
        "jump_url": str,
    }}"""
    pass


# def on_message_edit(message):
#     """Same shape as on_message, with an extra "old_content" field
#     (None if the old message was not cached)."""
#     pass


# def on_message_delete(message):
#     """Same shape as on_message; "content" is None if the message
#     was not cached."""
#     pass


# def on_reaction_add(reaction):
#     """reaction = {{
#         "emoji",               # unicode emoji or "<:name:id>" for custom
#         "message_id", "channel_id", "channel_name",
#         "user": {{"id", "name", "display_name"}},   # who reacted
#         "guild_id", "guild_name",
#     }}"""
#     pass


# def on_reaction_remove(reaction):
#     """Same shape as on_reaction_add."""
#     pass


# def on_failure(event_name, event_data, error):
#     """Called when one of your handlers raises an exception or times out.
#     `error` is a string. If this function also fails, the error is logged
#     to the project console and nothing else happens."""
#     log(f"handler {{event_name}} failed: {{error}}")
'''
