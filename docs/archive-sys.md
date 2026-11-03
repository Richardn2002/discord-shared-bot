### Scope of "all messages"

Yes all messages but excluding DM, no DM functionalities are planned for the bot. Of course include messages in threads and posts, actually most of our conversations happen there.
But for respecting people's privacy, if a message is deleted, remove it from the database. If edited, only keep the last version.

### Backfill depth

Yes I want everything, please do initial scanning

### Attachments

For manually attached files, of course download. Don't use a size limit. If someone put a link that directly resolves to a file (for example a link to png/gif/pdf), also download. But for previews, don't keep the media artifacts, just preview textual content.

### Nextcloud layout

Performance, efficiency and ease of transfer are the top priority, I can always use an accompanying service/script to browse files (though keeping original filenames would be favorable)

### Backups

Configurable frequency of rotating backups would be enough, daily backup rotating every 7 days as default sounds super good enough, backups are mainly for preparing unexpected channel deletion/server explosion events

### Live DB placement

yeah local is good

### Archive query in tests

As an exception, let tests directly query real data, as real data already exists and one may want to test on an actual conversation

### Retention

As said above, I want to preserve privacy, so deleted stuff is deleted. Don't care about backups containing them though, they will be rotated out eventually.

### Access

The same as the existing platform

### Explorer scope

Whatever you come up with as a good default, plus, I want a "heatmap" feature where we can easily scroll zoom and find time regions of heated discussions, and then browse accordingly.

Also make the search good, we heavily rely on the discord search in our server, the search should be at least as good as discord's one, you can try do research on discord's search logic