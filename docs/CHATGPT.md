# Next workout from your own GPT in ChatGPT

Ask a GPT of your own in ChatGPT "what's my next workout?". It reads your programme and recent training from this
site, answers in the chat, and posts the same pick to the site, where it shows in the homepage's "Next up" card.
It runs on your ChatGPT plan (creating a GPT needs a paid plan), so there is no API billing, and nothing runs until
you ask. When you haven't asked since your last workout, the card shows the free built-in rules instead.

Hevy's own ChatGPT app (`@hevy`) can't do this: only Hevy can change what it does, and it can't write to your site.

## How it is protected

- The site login can't be used by ChatGPT's servers, so `/hevy/gpt/` is the one path outside it
  ([deploy/nginx/hevy.conf](../deploy/nginx/hevy.conf)). Only three addresses answer there:
  - `GET /hevy/gpt/openapi.json`: the Actions description. Public; it holds no data.
  - `GET /hevy/gpt/context`: programme, last 21 days, recent muscle work, last performance per programme exercise.
  - `POST /hevy/gpt/suggestion`: a pick, checked against the programme and plausible loads before it is kept.
- The last two need the key. Only its SHA-256 hash is stored on your server; the key itself exists only in your GPT's
  settings. Making a new key stops the old one at once.
- The example nginx config limits these to 10 requests a minute (a question uses two) and 64 KB bodies.
- Nothing else of the site is reachable with the key, and the site login is not accepted in its place.

## Set up (about 5 minutes)

1. **Make a key** on your server (shown once; copy it somewhere safe for step 3):
   ```bash
   cd /opt/hevy-analytics && sudo -u hevy-sync env HEVY_ENV=production HEVY_DATA_DIR=/var/lib/hevy-analytics \
     .venv/bin/python -m hevy.cli gpt-key new
   ```
2. **Create the GPT**: in ChatGPT on the web, Explore GPTs → Create → Configure.
   - Name it (for example "Coach"), and paste the instructions from the System page ("Copy GPT instructions").
   - Under Capabilities, nothing else is needed.
3. **Add the Action**: Create new action.
   - Import from URL: the schema link from the System page (`https://<your site>/hevy/gpt/openapi.json`).
   - Authentication: API Key, Auth Type **Bearer**, paste the key from step 1.
   - Privacy policy: not needed while the GPT is shared with "Only me".
4. **Save** with sharing set to **Only me**.
5. Ask it "what's my next workout?". The first time, ChatGPT asks whether it may talk to your site: allow it
   ("Always allow" saves asking again). The answer appears in the chat and on the homepage.

What you write in "Instructions" on the System page (your coaching preferences) is sent to the GPT with your data
each time, so you can change how it coaches without editing the GPT.

## Commands

```bash
# on your server, from the app folder, as the worker's user (see step 1 for the full form)
.venv/bin/python -m hevy.cli gpt-key status    # set or not, when made, when last used
.venv/bin/python -m hevy.cli gpt-key new       # replace the key (then paste the new one into the GPT)
.venv/bin/python -m hevy.cli gpt-key revoke    # turn the GPT's access off
```

## Other ways to get the same card

- **OpenAI API key** (pay as you go): the worker asks OpenAI itself after each new workout or when asked
  ([SELF-HOSTING.md](SELF-HOSTING.md#next-workout-from-the-openai-api)).
- **Built-in rules** (free, always on): [METRICS.md](METRICS.md), "Next workout".
