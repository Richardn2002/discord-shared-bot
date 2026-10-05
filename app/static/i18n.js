/* Translations for the landing page and the console cheatsheet.
   Usage: elements with data-i18n="key" are filled by site.js applyI18n().
   The console cheatsheet renders window.CHEATSHEET[getLang()]. */
"use strict";

window.I18N = {
  en: {
    nav_about: "About",
    hero_lead: "A Discord bot our server runs together. Each feature is a \"project\": a single-file Python script you write in the web console.",
    cta_open: "Open the console →",
    cta_note: "The console asks for the access token. Ask the server owner for it.",
    h_console: "The console",
    console_body: "Everything happens in the console: a code editor per project, a test runner that fires fake events at your code (with a scratch copy of your data), a KV store editor, a per-project log view, and a Python package manager.",
    h_events: "Events",
    events_body: "Your functions get called when these happen:",
    events_note: "Each handler receives a dictionary describing what happened (author, channel, message text or emoji). Messages from bots never trigger events.",
    h_helpers: "Helpers",
    helpers_body: "Inside your handlers you can call:",
    helpers_note: "kv_* is your project's persistent key-value store (JSON values). log()/print() goes to the project's console.",
    h_behavior: "Behavior worth knowing",
    b_merge: "When several projects answer the same event, their replies are merged into one message with a [nickname]: section per project, oldest project first.",
    b_timeout: "Each project has a max handler run time. If your code exceeds it, the run is cancelled and on_failure(event_name, event_data, error) is called if you defined it.",
    b_deploy: "Nothing goes live until you press Deploy. Until then you're editing a draft.",
    b_test: "Test mode runs the draft against fake events and a throwaway copy of the KV store.",
    h_agents: "For AI agents",
    agents_body: "Full API and coding reference for agents:",
    agents_hint: "Or paste this into your agent (fill in the blanks):",
    h_source: "Source code",
    source_body: "The platform itself is open source:",
    h_ready: "Start",
    ready_note: "All project files live on the server as plain text, version-controlled by the owner.",
  },
  zh: {
    nav_about: "关于",
    hero_lead: "我们服务器共同使用的 Discord 机器人。每个功能是一个「项目」：在网页控制台里编写的单文件 Python 脚本。",
    cta_open: "打开控制台 →",
    cta_note: "控制台需要访问令牌，找服务器管理员获取。",
    h_console: "控制台",
    console_body: "所有操作都在控制台里完成：每个项目的代码编辑器、用假事件测试代码的测试运行器（使用数据的临时副本）、KV 存储编辑器、每个项目的日志查看器，以及 Python 包管理器。",
    h_events: "事件",
    events_body: "以下事件发生时会调用你写的函数：",
    events_note: "每个处理函数会收到一个描述事件的字典（作者、频道、消息内容或表情）。机器人发出的消息不会触发事件。",
    h_helpers: "辅助函数",
    helpers_body: "在处理函数里可以直接调用：",
    helpers_note: "kv_* 是项目专属的持久键值存储（值为 JSON）。log()/print() 会写入项目日志。",
    h_behavior: "需要知道的行为",
    b_merge: "多个项目响应同一事件时，回复会合并成一条消息，每个项目一个 [昵称]: 分段，创建早的项目排在前面。",
    b_timeout: "每个项目有最长运行时间。超时后本次运行会被取消，如果定义了 on_failure(event_name, event_data, error) 会被调用。",
    b_deploy: "按下 Deploy 之前，一切改动都不会生效，你编辑的只是草稿。",
    b_test: "测试模式会用假事件和 KV 存储的临时副本来运行草稿。",
    h_agents: "给 AI agent",
    agents_body: "面向 agent 的完整 API 与编程参考：",
    agents_hint: "或者把下面这段贴给你的 agent（填好空白处）：",
    h_source: "源代码",
    source_body: "本平台已开源：",
    h_ready: "开始",
    ready_note: "所有项目文件都以纯文本保存在服务器上，由管理员做版本管理。",
  },
};

window.CHEATSHEET = {
  en: [
    {
      title: "Events",
      rows: [
        ["on_message(message)", "new message. message: id, content, author{id, name, display_name}, channel_id, channel_name, guild_id, guild_name, attachments, reply_to{message_id, channel_id} or null, mentions[...], mention_everyone, pinned, created_at, edited_at, jump_url"],
        ["on_message_edit(message)", "message edited. same dict plus old_content (None if uncached)"],
        ["on_message_delete(message)", "message deleted. content/author may be None (uncached)"],
        ["on_reaction_add(reaction)", "reaction: emoji, message_id, channel_id, user{...}, message_author{...}"],
        ["on_reaction_remove(reaction)", "same fields as on_reaction_add"],
        ["on_failure(event_name, event_data, error)", "called when a handler raises or times out. If it also fails, the error is only logged."],
      ],
    },
    {
      title: "Actions",
      rows: [
        ["send(text, channel_id=None)", "post a message (default: the event's channel). Plain text is merged with other projects' replies."],
        ["reply(text)", "identical to send(), but the aggregated message quotes the triggering message (message events only)"],
        ["add_reaction(emoji, message_id=None)", "react to the message (or one you pick)"],
        ["send_embed(…)", "rich card. Params: title, description, color, fields (list of {name,value,inline}), url (title link), image, thumbnail (image URLs), author ({name,url,icon_url} or str), footer ({text,icon_url} or str), timestamp, channel_id."],
        ["send_file(filename, content, channel_id=None)", "attach a file; content is str or bytes"],
        ["log(msg) / print(...)", "write to this project's console (Console tab)"],
      ],
    },
    {
      title: "Lookups",
      rows: [
        ["get_message(message_id, channel_id=None)", "fetch any message the bot can see (default channel: the event's). Returns the same dict on_message gets, or None on failure (also logged). Example: get_message(message[\"reply_to\"][\"message_id\"]). In tests: returns the Test tab's fake lookup message when the id matches (else None) — never touches Discord."],
      ],
    },
    {
      title: "KV & secrets",
      rows: [
        ["kv_get(key, default=None)", "read a value"],
        ["kv_set(key, value)", "write (value must be JSON-serializable)"],
        ["kv_delete(key) / kv_keys() / kv_all()", "delete / list keys / whole store as dict"],
        ["secret_get(key, default=None)", "read the project's secret store (.env, edited in the KV tab). Read-only from code. Never committed to git. For API keys etc."],
      ],
      note: "KV is persistent across restarts and deploys; view/edit it in the KV tab — tests use a throwaway copy. Secrets edits apply immediately.",
    },
    {
      title: "Notes",
      bullets: [
        "Messages from bots (including this bot) never trigger handlers — no loops.",
        "timeout (Settings): max seconds a handler may run. tolerance: max delay before merged messages go out.",
        "Test runs the draft; Deploy makes the draft live.",
        "Your nickname is the label on your section of merged messages.",
        "Extra Python packages: Packages page (top bar), then restart your worker (Settings).",
      ],
    },
  ],
  zh: [
    {
      title: "事件",
      rows: [
        ["on_message(message)", "新消息。message 字段：id、content、author{id, name, display_name}、channel_id、channel_name、guild_id、guild_name、attachments、reply_to{message_id, channel_id} 或 null、mentions[...]、mention_everyone、pinned、created_at、edited_at、jump_url"],
        ["on_message_edit(message)", "消息被编辑。字段同上，另有 old_content（未缓存时为 None）"],
        ["on_message_delete(message)", "消息被删除。content/author 可能为 None（未缓存）"],
        ["on_reaction_add(reaction)", "reaction 字段：emoji、message_id、channel_id、user{...}、message_author{...}"],
        ["on_reaction_remove(reaction)", "字段与 on_reaction_add 相同"],
        ["on_failure(event_name, event_data, error)", "handler 抛异常或超时时调用；它本身再出错只会写入日志"],
      ],
    },
    {
      title: "动作",
      rows: [
        ["send(text, channel_id=None)", "发消息（默认发到事件所在频道）；纯文本会与其他项目的回复合并"],
        ["reply(text)", "与 send() 相同，但聚合消息会引用（quote）触发事件的那条消息（仅消息类事件）"],
        ["add_reaction(emoji, message_id=None)", "给消息添加表情回应（可指定其他消息）"],
        ["send_embed(…)", "发送 embed 卡片。参数：title、description、color、fields（{name,value,inline} 列表）、url（标题链接）、image、thumbnail（图片 URL）、author（{name,url,icon_url} 或字符串）、footer（{text,icon_url} 或字符串）、timestamp、channel_id。"],
        ["send_file(filename, content, channel_id=None)", "发送附件；content 为 str 或 bytes"],
        ["log(msg) / print(...)", "写入项目日志（Console 页查看）"],
      ],
    },
    {
      title: "查询",
      rows: [
        ["get_message(message_id, channel_id=None)", "获取机器人可见的任意消息（默认频道为事件所在频道）。返回与 on_message 相同的字典，失败时返回 None（并写入日志）。例：get_message(message[\"reply_to\"][\"message_id\"])。测试时：若 id 与 Test 页配置的假消息一致则返回它，否则返回 None；测试中不会访问 Discord。"],
      ],
    },
    {
      title: "KV 与密钥",
      rows: [
        ["kv_get(key, default=None)", "读取"],
        ["kv_set(key, value)", "写入（值必须可 JSON 序列化）"],
        ["kv_delete(key) / kv_keys() / kv_all()", "删除 / 列出所有键 / 返回整个存储的字典"],
        ["secret_get(key, default=None)", "读取项目密钥（.env，在 KV 页下方编辑）。代码中只读。不会被提交到 git。用于存放 API key 等。"],
      ],
      note: "KV 在重启和部署后仍然保留；KV 页可直接查看编辑，测试时使用临时副本。密钥的修改立即生效。",
    },
    {
      title: "须知",
      bullets: [
        "机器人（包括本 bot）的消息不会触发 handler，不会产生循环。",
        "timeout（设置页）：handler 最长运行秒数；tolerance：合并消息最长等待秒数。",
        "Test 运行的是草稿；Deploy 后草稿才生效。",
        "合并消息里你的分段标题就是项目的昵称。",
        "需要额外的 Python 包：顶部 Packages 页安装，然后在设置页重启 worker。",
      ],
    },
  ],
};
