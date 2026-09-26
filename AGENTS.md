I own a discord server with my friends. I want to make a discord bot that has an online programming interface so my friends of various programming skills can develop functionalites of it through a webpage. The plans are as follows:
 - the bot and the frontend and backend of the interface will be hosted on my server
 - the bot application handles all boilerplate to talk to discord and dispatch events, is always online
 - on the interface one can create arbitrarily many "projects" of chosen names
 - each project is associated with one python file and a KV persistent store
 - for each project, the python file is prepopulated with function prototypes representing event handlers (commented out for unused ones). one implements functionalities by populating functions, which will be called upon events
 - there is a list of predefined functions for the handler to do this framework-specific actions, for example letting the bot writing a message, r/w from KV store
 - when coding is done, one can run py-compile and test by triggering fake events and see what will happen as the handler runs. use a separate kv store for test for each project (on beginning of test, the test kv store content is identical to the real kv store)
 - when everything is ready, one can choose to deploy, which overwrites the old active python file for the project, and from now on true events will be handled by the updated handlers                                                                                                             
 - each project has some metadata: nickname, author, longest allowed handler execution time (use a small default, mainly for catching deadlocks)

details:
 - make a dedicated interface to manage currently installed python packages. one should be able to install more and use them in their latest projects
 - the main bot never needs to restart, and different projects do not interfere with each other. think about how to manage python environment and managing of processes and connecting them live
 - when handlers send messages, aggregate them together in the order of project creation, example the bot may send a message in the server:
   [nickname of older project]:
   hello hello
   [nickname of newer project]:
   hi I am the server meido

 - the code editing interface shall have proper editing experience and highlight
 - no need to think about security, I trust my friends
 - ensure kv store can be easily imported/exported from/to anywhere to/from anywhere
 - make sure all code is stored in plain format, basically I should be able to easily setup a git repo in the files folder and commit and track latest devs from my friends
 - you have dev-browser installed to help your frontend dev, use dev-browser --help to start
 - develop in working folder, create an venv here

think and ask questions for ambiguities/multiple implementation paths before you start

---

Agent response:

1. **Event set**: I propose supporting `on_ready`, `on_message`, `on_message_edit`, `on_message_delete`, `on_reaction_add`, `on_reaction_remove`, `on_member_join`, `on_member_leave`. Do you also want **scheduled/cron-style events** (e.g. `on_timer()` every N minutes) or **slash commands**? Both add real complexity; I'd skip them in v1 unless you want them.
2. **Bot-message loops**: default to ignoring messages from the bot itself and from other bots (vents feedback loops), handlers never see them. OK?
3. **Channel-less events**: for events without a channel (member join/leave, `on_ready`), `send()` goes to a per-project "default channel" pickable in project settings. Add that metadata field?
4. **Actions beyond plain text**: v1 framework functions: `send(text)`, `reply(text)`, `add_reaction(emoji)`, `kv_*`, `log()`. Enough, or do you need embeds/file uploads/DMs now?
5. **Aggregation confirm**: wait for all projects to finish before posting (bounded by timeout, ~small seconds), rather than streaming each project's section as it finishes. The example output stays exactly one message in creation order. OK?
6. **Shared package env** (one venv, no per-project isolation of packages) — confirm; per-project venvs would be heavier and you said projects merely shouldn't _interfere at runtime_.
7. **KV as JSON files** (git-friendly, trivial import/export) instead of SQLite — confirm; fine for friend-scale data.
8. **Frontend**: vanilla JS + vendored Monaco, no npm build step — OK, or do you prefer React/Vite?
9. **Auth**: none at all; "author" is just a name people type (remembered in their browser). OK?
10. **Deployment details**: which port should the web UI listen on (default `0.0.0.0:8000`)? And you'll create the Discord application yourself — remember the bot needs the **Message Content intent** enabled in the dev portal; I'll note it in the README.

One heads-up: a handler that deadlocks gets its worker killed mid-event, so that project's section is silently dropped for that event; I'll surface "project X timed out" in the UI's project status + optionally post it to the channel. Want the timeout notice posted to Discord too?

---

I forgot to say, kv store should also be easily viewed and edited on the interface

back to the questions:
1. on_message on_message_edit on_message_delete on_reaction_add on_reaction_remove is enough
2. ok
3. per 1, all supported events are channel-ful
4. these looks great, also enable embeds/file uploads. now you mention log(), yes create a console for each project
5. now you mention it, add one metadata to each project: max tolerance, after this amount of time message actions will be dispatched no matter what, no more waiting
6. yes this is perfert, shared venv
7. json yes
8. vanilla js + monaco yes
9. ok
10. make listen address, port and domain name configurable, I will set it later

expose a failure handler to each project. if the handler also fails, log and do nothing

I forgot one thing: as I will be exposing this to the public internet, make a simple token authentication so I can distribute the token to my friends to grant full access


when you are done, also make a landing page with very approachable and concise writing on what is this and how to develop, aiming for making audience with basic python skills understand and not making advanced python coders fill redundant. hide implementation details in the writing. at the end of writing include a one-liner my friends can copy to their agents to get them started in case they want their agents to develop. with the one liner the agent should be able to further grab info it needs


note that the agents should be able to debug and test just like the user (without ui, of course)


Very good work! I tested with a real app token and everything went well. Slight quality of life changes before I deploy and tell friends:
- make the top left 🤖 shared-bot title and server main text and code font configurable
- make logging level configurable
- the landing page's writing is too verbose, edgy and ai-speak. be concise, just say what is in there, don't emphasize on how simple it is, how boilerplate is not needed, etc. etc. we don't need the selling tone
- the landing page also feels detached to the rest of the site. just keep the topbar as any other page
- make a "cheatsheet" tab in the coding page, as people might delete the template and forget existing handlers and actions later
- landing page and cheatsheet should support both english and simplified chinese, make a switch button

finally, I will be deploying this behind nginx on my cloud server, which is behind cloudflare full TLS, using cloudflare certificates. I plan to host under https://discord.richardn.me/bot/. give me the nginx conf file content to use in your final message
