# Verification

Tested October 24, 2026 on Windows, Python 3.14.5.

Command: `python -m unittest discover -s outputs/topic-intelligence-bot -v`

Result: **12 tests passed**. All network calls were mocked; no real Telegram messages were sent.

Covered: all source adapters, Reddit OAuth token reuse and transient purge, canonical URL deduplication, snapshot changes, exclusion/phrase matching, Ollama and hosted response handling, deterministic fallback, POST timeout behavior, rate-limit retries, SQLite reopening, dry-run behavior, full pipeline with partial source failure, no repeated delivery, ambiguous delivery handling, and daily schedule suppression/catch-up.

No live source integration, Reddit access approval, hosted model availability, or Telegram delivery was verified. Follow README setup and perform a dry run, then a live run with your own credentials.

Installed dependency versions for this verification: requests 2.34.2, feedparser 6.0.14, python-dotenv 1.2.4, tzdata 2026.5. The requirements file allows compatible versions.
