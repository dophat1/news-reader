# Topic intelligence Telegram bot

A Python 3.11+ MVP: bounded multi-source searches, SQLite persistence, relevance and engagement ranking, optional summaries, and a daily Telegram digest delivered as one message per selected item. No scraping, browser automation, or paid service is required for fallback summaries. Search coverage is best-effort, not exhaustive.

## Quick start

Run from this folder. Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python bot.py run-once --dry-run
.\.venv\Scripts\python -m unittest -v
```

Linux/macOS: use `python3 -m venv .venv`, `.venv/bin/python`, and `cp .env.example .env` instead. Python's bundled SQLite is used; tzdata supports named time zones on Windows. Dependencies have compatible version bounds, not a platform-specific lockfile.

Edit `topics.json`: each topic has a unique name, keyword phrases (OR matching), optional exclusions, sources, subreddit names, and explicit RSS/Atom URLs. Defaults are an example, not your inferred interests. `lookback_hours` is 1–168; `max_pages` is 1–10 per keyword/order; `digest_size` is 1–50 per topic. Restart the scheduler after configuration changes. Dry runs collect and update observations but never queue or mark messages delivered.

## Credentials and source access

* Telegram: create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN`, then send your bot a message. Obtain your chat ID from the official Bot API `getUpdates` response (or your own client), and set `TELEGRAM_CHAT_ID`. In groups/channels grant the bot appropriate posting permissions. Keep tokens and `.env` private; do not paste token-bearing URLs into logs.
* Reddit: obtain approved Data API access and a registered OAuth client. Set client ID, secret, and a truthful descriptive user agent containing your Reddit username. This MVP uses read-only client-credentials OAuth for approved confidential clients. It does not support installed-client or user-login flows. Missing/denied credentials cause Reddit to be skipped with a source failure, while other sources continue. Tokens renew on expiry; a rejected token/source is retried on the next run. No unauthenticated JSON workaround is used.
* HN: Algolia's public HN search API, newest and relevance/points searches, story-only, with timestamp cutoff and bounded pagination. No key needed.
* GitHub: official repository search, newly created repositories and recently pushed repositories sorted by stars. Optional `GITHUB_TOKEN` increases available quota; use a least-privilege token. This is a repository discovery proxy for trends, not GitHub's Trending page or comprehensive issue/post coverage. Updated time is pushed time; star counts are cumulative.
* RSS/Atom: explicit publisher feed URLs only; no linked article scraping. Respect feed publisher terms and attribution. Undated entries use first observation time; missing links are skipped. Feed failures do not stop other feeds.

Reddit requires approved access and applicable permission for your use case. Review current policies before enabling redistribution, commercial use, or external model processing. The code collects no author identifiers, purges Reddit raw posts and digest bodies before and after each run, and defaults to deterministic Reddit excerpts rather than LLM processing. A crash can leave transient data until the next invocation; database WAL/backups and copies sent to Telegram are not automatically erased. Daily retention is not a substitute for deletion compliance: if you need automatic Reddit deletion synchronization across Telegram/backups, implement it before operating that use case. Do not use this MVP for training on Reddit data. Stop using Reddit and purge copies if required by its terms or access approval.

## Summaries

`SUMMARY_PROVIDER=fallback` produces deterministic cleaned excerpts (or the title when no excerpt exists), capped at 240 characters. These are labeled here as excerpts, not inferred full-article summaries. Only title and available feed/API excerpt are read.

For local Ollama, install/start Ollama and pull a model you choose, then set:

```dotenv
SUMMARY_PROVIDER=ollama
OLLAMA_URL=http://localhost:11434
MODEL=llama3.2
```

For a hosted chat-completions-compatible provider, set `SUMMARY_PROVIDER=hosted`, `HOSTED_URL` to its complete chat-completions endpoint, `HOSTED_API_KEY`, and `MODEL` to a model available in that account. No particular hosted vendor is required. Non-Reddit titles/excerpts go to the selected provider; costs and data handling depend on that provider. Reddit stays on excerpts unless `ALLOW_REDDIT_LLM=true` and you have permission. Model failures/empty responses fall back to excerpts. Posts are framed as untrusted data; models have no tools. Model factuality is not guaranteed.

## Run and schedule

```powershell
.\.venv\Scripts\python bot.py run-once
.\.venv\Scripts\python bot.py schedule
```

The foreground scheduler runs once per local calendar day at or after `daily_time` in the configured IANA timezone. Default: 08:00 Asia/Ho_Chi_Minh. It catches up on startup for today only, records completed days in SQLite, and handles daylight-saving transitions using calendar dates. Keep the machine awake and process supervised; it does not install a background service. Stop with Ctrl+C. An unresolved delivery stops the process so a supervisor/operator can inspect it. Source failures are logged; a completed partial daily run is recorded and is not retried automatically that day. `run-once` exits 2 on source failures, 1 on operational failure, 0 on success.

Alternatively schedule `run-once` with Windows Task Scheduler (daily trigger, absolute `.venv\Scripts\python.exe` program, arguments `bot.py run-once`, and this folder as **Start in**), or cron:

```cron
0 8 * * * cd /absolute/path/topic-intelligence-bot && .venv/bin/python bot.py run-once >> bot.log 2>&1
```

Cron uses the operating system's timezone, not `topics.json`; configure that deliberately. Do not use cron and the built-in scheduler together. OS locking prevents overlapping runs using the same DB path. Different working folders/DB paths are independent installations. Use `--db` and `--config` for explicit paths.

## Ranking, persistence, delivery

Keyword phrase boundary matches in titles weigh 3; excerpt matches weigh 1. Exclusions suppress results. Rank = 4 × relevance plus logarithmic engagement and positive engagement change, decayed by age. HN/Reddit engagement includes comments; GitHub uses stars; RSS has no engagement. These signals are heuristic and not directly comparable across platforms. At least two separate observations are needed for growth; daily operation measures daily changes. Running more frequently improves observations but sent links remain suppressed for 90 days.

SQLite stores per-topic source identities and snapshots. Tracking parameters/fragments are removed and equivalent HTTP/HTTPS URLs collapse. Cross-source matching uses canonical URLs and normalized exact titles within each digest. Reddit retains its discussion permalink, so it may not collapse with an external article except by identical title. Fuzzy semantic deduplication is outside scope. A successful delivery suppresses its canonical URL for that topic. Other copies with different URLs/titles can recur. Old raw non-Reddit observations expire after 30 days.

Outbox messages are committed before delivery and marked individually after Telegram confirms. Messages use plain text without Markdown parsing and fit comfortably below Telegram's length limit. Requests have bounded timeouts/retries, conservative pacing, rate-limit header handling, and a 120-second wait ceiling. Longer limits defer work to the next run. HTTP POST network errors are not retried automatically. Telegram 429 rejections can safely be retried after Retry-After.

There is no distributed transaction with Telegram: a timeout/crash after send can leave `uncertain` or `sending`. Inspect Telegram and:

```powershell
python bot.py outbox
python bot.py resolve --id 1 --action skip
# Only if you have confirmed the message was NOT delivered:
python bot.py resolve --id 1 --action retry
python bot.py run-once
```

`skip` suppresses that item's future delivery. `retry` may duplicate a remotely accepted message; never assume a timeout means failure. Reddit pending/uncertain bodies are purged instead of retried to minimize content retention, so Reddit can be rediscovered after an ambiguous failure. Exactly-once delivery is not promised. Sent outbox bodies for non-Reddit currently remain for audit; protect or remove the DB when no longer needed.

## Tests and limitations

`python -m unittest -v` runs offline mocked tests of all four adapters, OAuth reuse, rate-limit retries, no unsafe POST retry, deduplication, snapshot deltas, exclusions, fallback/model paths, durable delivery, ambiguous outcomes, dry runs, disk persistence, and Reddit purge. Tests never send Telegram messages. Real OAuth approval, live source availability, and end-to-end delivery require your credentials and a live smoke test. Large searches may be truncated by page bounds or upstream caps; RSS has no engagement and GitHub stars are only a trend proxy. No claim of complete topic coverage.

## API references (checked October 24, 2026)

* Reddit authentication, quotas, and content deletion: https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
* Reddit terms: https://redditinc.com/policies/data-api-terms
* HN search: https://hn.algolia.com/api
* GitHub repository search and search limits: https://docs.github.com/en/rest/search/search
* Telegram sendMessage: https://core.telegram.org/bots/api#sendmessage
* Ollama chat: https://docs.ollama.com/api/chat
