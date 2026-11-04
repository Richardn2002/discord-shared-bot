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
#   reply(text)                            identical to send(), but the
#                                          aggregated message quotes the
#                                          triggering message (message events only)
#   add_reaction(emoji, message_id=None)   react to the triggering message
#                                          (or a message id you pass)
#   send_embed(title=None, description=None, color=0x5865F2, fields=None,
#              channel_id=None, url=None, image=None, thumbnail=None,
#              author=None, footer=None, timestamp=None)
#                                          send a rich embed card.
#                                          fields: list of {{"name","value","inline"}}
#                                          url: makes the title a link
#                                          image/thumbnail: image URLs
#                                          author: {{"name","url","icon_url"}} or str
#                                          footer: {{"text","icon_url"}} or str
#                                          timestamp: ISO-8601 str or epoch secs
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
#   archive_search(query=None, guild_id=None, channel_id=None, author_id=None,
#                  after=None, before=None, limit=50, cursor=None,
#                  order="desc", include_bots=True)
#                                          read-only search of the message
#                                          archive; all terms must match
#   archive_query(filter=None, ...same filters...)
#                                          structured archive query with nested
#                                          AND/OR/NOT groups and field conditions
#   archive_context(message_id, channel_id=None, before=20, after=20)
#                                          messages around an archived message
#   archive_message(message_id, channel_id=None)
#   archive_channels(guild_id=None) / archive_guilds()
#   archive_stats(guild_id=None)
#   archive_heatmap(guild_id=None, channel_id=None, after=None, before=None,
#                   bucket="hour", tz_offset=0, include_bots=True)
#   archive_attachment_meta(attachment_id)
#                                          read-only archive helpers; see the
#                                          cheatsheet/agent guide for filter
#                                          fields, operators and return shapes.
#                                          Tests may query the real archive.
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
