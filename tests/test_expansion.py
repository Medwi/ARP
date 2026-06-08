"""
ARP Platform – tests/test_expansion.py
Tests for new modules: shadow book, briefing agent, rate limiter, token hashing.
Separate file from test_tools.py to keep it clean and independently runnable.
"""

import os, sqlite3, time
import pytest

os.environ.setdefault("DB_PATH", "/tmp/test_expansion.db")

from backend.rbac import User

pytestmark = pytest.mark.expansion

# ── Shared DB fixture ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("data") / "expansion.db")

    schema_path = os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")
    con = sqlite3.connect(p)
    con.executescript(open(schema_path).read())

    # Users
    from tests.db_users import insert_user
    insert_user(con, "analyst@local", "analyst", "tok_analyst")
    insert_user(con, "risk@local", "risk", "tok_risk")
    insert_user(con, "intern@local", "intern", "tok_intern")

    # Holdings
    holdings = [
        ("AAPL","Apple Inc","Equity",     100,150.0,185.0,18500.0,14.5),
        ("MSFT","Microsoft","Equity",      80,300.0,415.0,33200.0,12.0),
        ("BTC-USD","Bitcoin","Crypto",      1,30000.0,67000.0,67000.0,16.0),
        ("SPY","SPDR S&P 500","ETF",       50,400.0,520.0,26000.0,10.0),
    ]
    con.executemany(
        "INSERT INTO portfolio_holdings "
        "(ticker,name,asset_class,quantity,avg_cost,current_price,market_value,weight_pct) "
        "VALUES (?,?,?,?,?,?,?,?)", holdings,
    )

    # Market prices
    prices = [
        ("AAPL",185.0,1.2,50000000,"mock"),
        ("MSFT",415.0,-0.5,40000000,"mock"),
        ("BTC-USD",67000.0,3.1,20000,"mock"),
        ("SPY",520.0,0.8,100000000,"mock"),
    ]
    con.executemany(
        "INSERT INTO market_prices (ticker,price,change_pct,volume,source) VALUES (?,?,?,?,?)",
        prices,
    )

    # Trades
    con.execute(
        "INSERT INTO trades (ticker,direction,quantity,price,notional,trader,status,risk_score) "
        "VALUES ('NVDA','BUY',500,875.0,437500.0,'risk@local','FLAGGED',0.92)"
    )
    con.execute(
        "INSERT INTO trades (ticker,direction,quantity,price,notional,trader,status,risk_score) "
        "VALUES ('AAPL','BUY',100,185.0,18500.0,'analyst@local','EXECUTED',0.2)"
    )

    # Risk rules
    con.execute(
        "INSERT INTO risk_rules (rule_name,description,threshold,metric,severity) "
        "VALUES ('large_notional_trade','Single trade > $500k',500000,'notional','MEDIUM')"
    )
    con.execute(
        "INSERT INTO risk_rules (rule_name,description,threshold,metric,severity) "
        "VALUES ('max_single_position','Single position > 20%',20.0,'weight_pct','HIGH')"
    )
    con.execute(
        "INSERT INTO risk_rules (rule_name,description,threshold,metric,severity) "
        "VALUES ('max_crypto_exposure','Crypto > 15%',15.0,'asset_class_pct','HIGH')"
    )
    con.execute(
        "INSERT INTO risk_rules (rule_name,description,threshold,metric,severity) "
        "VALUES ('high_risk_score','Risk score > 0.8',0.8,'risk_score','CRITICAL')"
    )

    con.commit()
    con.close()
    return p


@pytest.fixture
def analyst(): return User("analyst@local", "analyst")
@pytest.fixture
def risk():    return User("risk@local",    "risk")
@pytest.fixture
def intern_(): return User("intern@local",  "intern")


# ── Shadow book tests ─────────────────────────────────────────────────────────

from backend.tools.shadow_book import (
    submit_trade_idea, get_shadow_book,
    get_shadow_book_report, resolve_trade_idea,
    update_trade_idea, delete_trade_idea,
)

class TestShadowBook:
    def test_submit_idea_analyst(self, analyst, db_path):
        r = submit_trade_idea(
            analyst, "NVDA", "BUY",
            thesis="AI demand drives revenue beat in Q3",
            conviction=4, target_price=950.0, stop_loss=800.0,
            db_path=db_path,
        )
        assert r["allowed"] is True
        assert r["ticker"] == "NVDA"
        assert "idea_id" in r
        assert r["idea_id"] > 0

    def test_submit_idea_denied_to_intern(self, intern_, db_path):
        r = submit_trade_idea(
            intern_, "AAPL", "BUY",
            thesis="test", conviction=1, db_path=db_path,
        )
        assert r["allowed"] is False

    def test_invalid_direction_rejected(self, analyst, db_path):
        r = submit_trade_idea(
            analyst, "AAPL", "HOLD",
            thesis="test", conviction=3, db_path=db_path,
        )
        assert r["allowed"] is True
        assert "error" in r

    def test_invalid_conviction_rejected(self, analyst, db_path):
        r = submit_trade_idea(
            analyst, "AAPL", "BUY",
            thesis="test", conviction=9, db_path=db_path,
        )
        assert "error" in r

    def test_get_shadow_book_returns_ideas(self, analyst, db_path):
        # Submit one first
        submit_trade_idea(analyst, "SPY", "BUY", "Index hedge", 3, db_path=db_path)
        r = get_shadow_book(analyst, db_path=db_path)
        assert r["allowed"] is True
        assert r["count"] >= 1
        assert "ideas" in r

    def test_filter_by_status(self, analyst, db_path):
        r = get_shadow_book(analyst, status="OPEN", db_path=db_path)
        assert r["allowed"] is True
        for idea in r["ideas"]:
            assert idea["status"] == "OPEN"

    def test_resolve_idea(self, risk, db_path):
        # Submit an idea to resolve
        sub = submit_trade_idea(
            risk, "GS", "BUY", "Rate cut catalyst", 5, db_path=db_path,
        )
        idea_id = sub["idea_id"]
        r = resolve_trade_idea(risk, idea_id, "EXECUTED", outcome_pnl=12500.0, db_path=db_path)
        assert r["allowed"] is True
        assert r["updated"] is True
        assert r["new_status"] == "EXECUTED"

    def test_resolve_denied_to_analyst(self, analyst, db_path):
        r = resolve_trade_idea(analyst, 1, "EXECUTED", db_path=db_path)
        assert r["allowed"] is False

    def test_shadow_book_report(self, risk, db_path):
        r = get_shadow_book_report(risk, db_path=db_path)
        assert r["allowed"] is True
        assert "hit_rate_pct" in r
        assert "conviction_hit_rate_pct" in r
        assert "by_submitter" in r
        assert isinstance(r["total_ideas"], int)

    def test_report_denied_to_analyst(self, analyst, db_path):
        r = get_shadow_book_report(analyst, db_path=db_path)
        assert r["allowed"] is False

    def test_idea_structure(self, analyst, db_path):
        r = get_shadow_book(analyst, db_path=db_path)
        if r["ideas"]:
            idea = r["ideas"][0]
            for field in ("ticker","direction","thesis","conviction",
                          "submitted_by","status","created_at"):
                assert field in idea

    def test_short_direction_allowed(self, analyst, db_path):
        r = submit_trade_idea(
            analyst, "VXX", "SHORT",
            thesis="Volatility mean reversion", conviction=2, db_path=db_path,
        )
        assert r["allowed"] is True
        assert r["ticker"] == "VXX"

    def test_update_open_idea(self, analyst, db_path):
        sub = submit_trade_idea(
            analyst, "MSFT", "BUY", "Cloud growth", 3, db_path=db_path,
        )
        r = update_trade_idea(
            analyst, sub["idea_id"], "MSFT", "BUY",
            "Azure re-acceleration thesis", 4, db_path=db_path,
        )
        assert r["allowed"] is True
        assert r["updated"] is True
        ideas = get_shadow_book(analyst, db_path=db_path)["ideas"]
        updated = next(i for i in ideas if i["id"] == sub["idea_id"])
        assert updated["thesis"] == "Azure re-acceleration thesis"
        assert updated["conviction"] == 4

    def test_delete_open_idea(self, analyst, db_path):
        sub = submit_trade_idea(
            analyst, "TSLA", "SELL", "Valuation stretch", 2, db_path=db_path,
        )
        r = delete_trade_idea(analyst, sub["idea_id"], db_path=db_path)
        assert r["allowed"] is True
        assert r["deleted"] is True
        open_ideas = get_shadow_book(analyst, status="OPEN", db_path=db_path)["ideas"]
        assert sub["idea_id"] not in [i["id"] for i in open_ideas]

    def test_admin_can_delete_any_idea(self, admin, analyst, db_path):
        sub = submit_trade_idea(
            analyst, "AMD", "BUY", "AI PC cycle", 3, db_path=db_path,
        )
        r = delete_trade_idea(admin, sub["idea_id"], db_path=db_path)
        assert r["allowed"] is True
        assert r["deleted"] is True


# ── Briefing agent tests ──────────────────────────────────────────────────────

from backend.agents.briefing import run as briefing_run, _gather_briefing_data

class TestBriefingAgent:
    def test_briefing_denied_to_analyst(self, analyst, db_path):
        r = briefing_run(analyst, db_path=db_path)
        assert r["allowed"] is False

    def test_briefing_allowed_to_risk(self, risk, db_path):
        r = briefing_run(risk, db_path=db_path)
        assert r["allowed"] is True

    def test_briefing_has_required_keys(self, risk, db_path):
        r = briefing_run(risk, db_path=db_path)
        for key in ("briefing", "data", "generated_at", "tool_called", "sections"):
            assert key in r

    def test_briefing_response_is_string(self, risk, db_path):
        r = briefing_run(risk, db_path=db_path)
        assert isinstance(r["briefing"], str)
        assert len(r["briefing"]) > 0

    def test_briefing_data_contains_market(self, risk, db_path):
        data = _gather_briefing_data(risk, db_path)
        # Should have at least one market data key
        market_keys = {"market_gainers", "market_losers", "market_error"}
        assert bool(market_keys & set(data.keys()))

    def test_briefing_data_contains_portfolio(self, risk, db_path):
        data = _gather_briefing_data(risk, db_path)
        assert "portfolio_headline" in data or "portfolio_error" in data
        if "portfolio_headline" in data:
            assert "total_aum" in data["portfolio_headline"]

    def test_briefing_data_contains_inbox(self, risk, db_path):
        data = _gather_briefing_data(risk, db_path)
        assert "inbox" in data
        assert "total_messages" in data["inbox"]

    def test_briefing_generated_at_format(self, risk, db_path):
        r = briefing_run(risk, db_path=db_path)
        # Should be "YYYY-MM-DD HH:MM:SS UTC"
        assert "UTC" in r["generated_at"]
        assert len(r["generated_at"]) >= 20

    def test_briefing_sections_list(self, risk, db_path):
        r = briefing_run(risk, db_path=db_path)
        assert "INBOX HIGHLIGHTS" in r["sections"]
        assert "ACTIONS BEFORE OPEN" in r["sections"]


# ── Rate limiter tests ────────────────────────────────────────────────────────

from backend.middleware.rate_limiter import SlidingWindowRateLimiter

class TestRateLimiter:
    def _make_limiter(self, limit=5, window=10):
        # Pass a dummy app — we test the internal logic directly
        limiter = SlidingWindowRateLimiter.__new__(SlidingWindowRateLimiter)
        limiter.default_limit   = limit
        limiter.default_window  = window
        limiter.endpoint_limits = {"/ask": (2, 10)}
        from collections import defaultdict, deque
        limiter._windows = defaultdict(deque)
        import threading
        limiter._lock = threading.Lock()
        return limiter

    def test_allows_requests_under_limit(self):
        rl = self._make_limiter(limit=5)
        for _ in range(5):
            limited, _ = rl._is_limited("key:test", 5, 10)
            assert limited is False

    def test_blocks_at_limit(self):
        rl = self._make_limiter(limit=3)
        for _ in range(3):
            rl._is_limited("key:block", 3, 10)
        limited, retry = rl._is_limited("key:block", 3, 10)
        assert limited is True
        assert retry > 0

    def test_different_keys_independent(self):
        rl = self._make_limiter(limit=2)
        rl._is_limited("key:a", 2, 10)
        rl._is_limited("key:a", 2, 10)
        limited_a, _ = rl._is_limited("key:a", 2, 10)
        limited_b, _ = rl._is_limited("key:b", 2, 10)
        assert limited_a is True
        assert limited_b is False

    def test_window_eviction(self):
        rl = self._make_limiter(limit=2, window=1)
        rl._is_limited("key:evict", 2, 1)
        rl._is_limited("key:evict", 2, 1)
        time.sleep(1.1)  # let window expire
        limited, _ = rl._is_limited("key:evict", 2, 1)
        assert limited is False   # window reset

    def test_retry_after_is_positive(self):
        rl = self._make_limiter(limit=1)
        rl._is_limited("key:retry", 1, 10)
        limited, retry = rl._is_limited("key:retry", 1, 10)
        assert limited is True
        assert isinstance(retry, int)
        assert retry > 0


# ── Token hashing tests ───────────────────────────────────────────────────────

class TestTokenHashing:
    def test_hash_token_deterministic(self):
        from backend.rbac import hash_token
        assert hash_token("abc123") == hash_token("abc123")

    def test_hash_token_different_inputs(self):
        from backend.rbac import hash_token
        assert hash_token("abc") != hash_token("xyz")

    def test_hash_token_is_hex_string(self):
        from backend.rbac import hash_token
        h = hash_token("testtoken")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_resolve_hashed_token(self, db_path):
        """
        Seed a user with a hashed token, then verify resolve_token
        finds them using the plain token.
        """
        import hashlib, secrets
        plain = secrets.token_hex(16)
        hashed = hashlib.sha256(plain.encode()).hexdigest()

        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT OR REPLACE INTO users (email,role,token) "
            "VALUES ('hashed@test','analyst',?)", (hashed,)
        )
        con.commit()
        con.close()

        from backend.rbac import resolve_token
        user = resolve_token(db_path, plain)
        assert user is not None
        assert user.email == "hashed@test"
        assert user.role  == "analyst"

    def test_resolve_wrong_token_returns_none(self, db_path):
        from backend.rbac import resolve_token
        user = resolve_token(db_path, "this_is_not_a_valid_token_xyz123")
        assert user is None
