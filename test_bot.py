import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import bot


class BotTests(unittest.TestCase):
    def setUp(self):
        self.db = bot.connect(":memory:")
        self.now = time.time()
        self.topic = dict(name="AI", keywords=["ollama"], exclude=["spam"], sources=["hn"])

    def tearDown(self):
        self.db.close()

    def post(self, ident="1", url="https://example.com/a", engagement=10):
        return bot.item("hn", ident, "Ollama release", url, self.now - 100, "Local inference", engagement)

    def test_canonical(self):
        self.assertEqual(bot.canonical("http://EXAMPLE.com/a/?utm_source=x&b=2#x"), "https://example.com/a?b=2")
        with self.assertRaises(ValueError):
            bot.canonical("javascript:alert(1)")

    def test_dedup_and_snapshots(self):
        r = self.post()
        bot.save(self.db, self.topic, [r, r], 0, self.now)
        r["engagement"] = 30
        bot.save(self.db, self.topic, [r, r], 0, self.now + 30)
        saved = self.db.execute("SELECT * FROM posts").fetchone()
        self.assertEqual(saved["previous"], 10)
        bot.save(self.db, self.topic, [self.post("2", "https://example.com/a?utm_source=hn")], 0, self.now)
        self.assertEqual(len(bot.ranked(self.db, self.topic, 0, 10, self.now)), 1)

    def test_relevance_age_and_exclusion(self):
        r = self.post()
        r["title"] = "Spam ollama"
        self.assertEqual(bot.relevance(r, self.topic), 0)
        r["title"] = "ollamatic"
        self.assertEqual(bot.relevance(r, self.topic), 0)
        r["published"] = self.now - 10000
        bot.save(self.db, self.topic, [r], self.now - 100, self.now)
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts").fetchone()[0], 0)

    @patch.dict(os.environ, {"SUMMARY_PROVIDER": "ollama", "MODEL": "test"})
    def test_summary_and_fallback(self):
        http = Mock()
        http.request.return_value.json.return_value = {"message": {"content": "A local inference update."}}
        self.assertEqual(bot.summarize(http, self.post()), "A local inference update.")
        http.request.side_effect = RuntimeError()
        self.assertEqual(bot.summarize(http, self.post()), "Local inference")

    @patch.dict(os.environ, {"SUMMARY_PROVIDER": "hosted", "MODEL": "test", "HOSTED_URL": "https://model.test/chat", "HOSTED_API_KEY": "test"})
    def test_hosted_and_reddit_privacy(self):
        http = Mock()
        http.request.return_value.json.return_value = {"choices": [{"message": {"content": "Summary"}}]}
        self.assertEqual(bot.summarize(http, self.post()), "Summary")
        http.reset_mock()
        r = self.post()
        r["source"] = "reddit"
        with patch.dict(os.environ, {"ALLOW_REDDIT_LLM": "false"}):
            bot.summarize(http, r)
        http.request.assert_not_called()

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test", "TELEGRAM_CHAT_ID": "123", "SUMMARY_PROVIDER": "fallback"})
    def test_delivery_and_uncertain(self):
        http = Mock()
        bot.build_outbox(self.db, http, self.topic, [self.post()])
        http.request.side_effect = RuntimeError("timeout")
        with self.assertRaises(RuntimeError):
            bot.deliver(self.db, http)
        self.assertEqual(self.db.execute("SELECT status FROM outbox").fetchone()[0], "uncertain")
        http.request.side_effect = None
        http.request.return_value.json.return_value = {"ok": True, "result": {"message_id": 42}}
        bot.deliver(self.db, http)
        self.assertEqual(http.request.call_count, 1)  # uncertain is never automatically retried
        self.db.execute("UPDATE outbox SET status='pending'")
        bot.deliver(self.db, http)
        bot.save(self.db, self.topic, [self.post()], 0, self.now)
        self.assertEqual(bot.ranked(self.db, self.topic, 0, 10, self.now), [])

    def test_collectors_hn_github_rss(self):
        http = Mock()
        http.request.return_value.json.return_value = {"hits": [{"objectID": "1", "title": "ollama", "url": None, "created_at_i": self.now}], "nbPages": 1}
        c = bot.Collectors(http)
        self.assertEqual(c.collect("hn", self.topic, 0, 2)[0]["source"], "hn")
        http.request.return_value.json.return_value = {"items": [{"id": 1, "full_name": "ollama/test", "html_url": "https://github.com/ollama/test", "pushed_at": "2026-10-24T00:00:00Z", "stargazers_count": 10}]}
        self.assertEqual(c.collect("github", self.topic, 0, 1)[0]["engagement"], 10)
        http.request.return_value.content = b'<rss version="2.0"><channel><title>Test</title><item><title>ollama</title><link>https://example.com/a</link><description>Local AI</description></item></channel></rss>'
        self.topic["rss"] = ["https://example.com/feed"]
        self.assertEqual(c.collect("rss", self.topic, 0, 1)[0]["text"], "Local AI")

    @patch.dict(os.environ, {"REDDIT_CLIENT_ID": "id", "REDDIT_CLIENT_SECRET": "secret", "REDDIT_USER_AGENT": "python:topicbot:v1 (by /u/test)"})
    def test_reddit_oauth_and_purge(self):
        http = Mock()
        auth = Mock()
        auth.json.return_value = {"access_token": "token", "expires_in": 3600}
        posts = Mock()
        posts.json.return_value = {"data": {"after": None, "children": [{"data": {"name": "t3_1", "title": "ollama", "permalink": "/r/test/comments/1", "created_utc": self.now, "score": 3, "num_comments": 2}}]}}
        http.request.side_effect = [auth, posts, posts]
        rows = bot.Collectors(http).collect("reddit", self.topic, 0, 1)
        self.assertEqual(http.request.call_count, 3)
        bot.save(self.db, self.topic, rows, 0, self.now)
        bot.purge(self.db, self.now)
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts").fetchone()[0], 0)

    @patch("bot.time.sleep")
    def test_rate_limit_and_post_timeout(self, sleep):
        h = bot.HTTP()
        h.session = Mock()
        limited = Mock(status_code=429, headers={"retry-after": "0"})
        ok = Mock(status_code=200, headers={}, ok=True)
        h.session.request.side_effect = [limited, ok]
        self.assertIs(h.request("GET", "https://test.com"), ok)
        h.session.request.side_effect = bot.requests.Timeout()
        h.session.request.reset_mock()
        with self.assertRaises(RuntimeError):
            h.request("POST", "https://test.com")
        self.assertEqual(h.session.request.call_count, 1)

    @patch.dict(os.environ, {"SUMMARY_PROVIDER": "fallback"})
    def test_pipeline_dry_run_and_persistence(self):
        config = dict(lookback_hours=48, max_pages=1, digest_size=10, topics=[self.topic])
        with patch.object(bot.Collectors, "collect", return_value=[self.post()]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(bot.run(self.db, Mock(), config, dry=True), 0)
        self.assertIn("Ollama release", output.getvalue())
        self.assertEqual(self.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.sqlite")
            db = bot.connect(path)
            bot.save(db, self.topic, [self.post()], 0, self.now)
            db.close()
            db = bot.connect(path)
            self.assertEqual(db.execute("SELECT count(*) FROM posts").fetchone()[0], 1)
            db.close()

    def test_daily_schedule(self):
        early = datetime.fromisoformat("2026-10-24T07:59:00+07:00")
        due = datetime.fromisoformat("2026-10-24T08:00:00+07:00")
        self.assertFalse(bot.daily_due(self.db, early, "08:00"))
        self.assertTrue(bot.daily_due(self.db, due, "08:00"))
        self.db.execute("INSERT INTO schedule VALUES('2026-10-24')")
        self.assertFalse(bot.daily_due(self.db, due, "08:00"))
        self.assertTrue(bot.daily_due(self.db, datetime.fromisoformat("2026-10-25T09:00:00+07:00"), "08:00"))

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test", "TELEGRAM_CHAT_ID": "123", "SUMMARY_PROVIDER": "fallback"})
    def test_full_run_partial_failure_and_no_repeat(self):
        self.topic["sources"] = ["hn", "github"]
        config = dict(lookback_hours=48, max_pages=1, digest_size=10, topics=[self.topic])
        http = Mock()
        http.request.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        with patch.object(bot.Collectors, "collect", side_effect=[ [self.post()], RuntimeError("unavailable")]):
            self.assertEqual(bot.run(self.db, http, config), 1)
        self.assertEqual(http.request.call_count, 1)
        with patch.object(bot.Collectors, "collect", return_value=[self.post()]):
            self.assertEqual(bot.run(self.db, http, config), 0)
        self.assertEqual(http.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
