"""Unit tests for src/rate_limiter.py"""

import time
import pytest
from src.rate_limiter import AgentQuota, RateLimiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_limiter(agent: str, rpm: int = 5, tpm: int = 1000, fallback: str | None = None) -> RateLimiter:
    """Create a RateLimiter with a single agent quota (no config file needed)."""
    rl = RateLimiter.__new__(RateLimiter)
    rl.quotas = {}
    rl._windows = {}
    import threading
    rl._lock = threading.Lock()
    rl.configure(agent, AgentQuota(rpm=rpm, tpm=tpm, fallback=fallback))
    return rl


# ---------------------------------------------------------------------------
# No-config passthrough
# ---------------------------------------------------------------------------

class TestNoConfig:
    def test_unknown_agent_always_allowed(self):
        rl = make_limiter("claude")
        allowed, fb = rl.check_and_consume("qwen", 100)
        assert allowed is True
        assert fb is None

    def test_empty_config_file(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("# empty\n")
        rl = RateLimiter(str(cfg))
        allowed, fb = rl.check_and_consume("any_agent", 50)
        assert allowed is True


# ---------------------------------------------------------------------------
# RPM limiting
# ---------------------------------------------------------------------------

class TestRPMLimit:
    def test_within_rpm_allowed(self):
        rl = make_limiter("claude", rpm=3, tpm=100_000)
        for _ in range(3):
            allowed, _ = rl.check_and_consume("claude", 10)
            assert allowed is True

    def test_over_rpm_blocked(self):
        rl = make_limiter("claude", rpm=3, tpm=100_000)
        for _ in range(3):
            rl.check_and_consume("claude", 10)
        allowed, _ = rl.check_and_consume("claude", 10)
        assert allowed is False

    def test_over_rpm_returns_fallback(self):
        rl = make_limiter("claude", rpm=2, tpm=100_000, fallback="qwen")
        rl.check_and_consume("claude", 10)
        rl.check_and_consume("claude", 10)
        allowed, fb = rl.check_and_consume("claude", 10)
        assert allowed is False
        assert fb == "qwen"

    def test_over_rpm_no_fallback_returns_none(self):
        rl = make_limiter("claude", rpm=1, tpm=100_000, fallback=None)
        rl.check_and_consume("claude", 10)
        allowed, fb = rl.check_and_consume("claude", 10)
        assert allowed is False
        assert fb is None


# ---------------------------------------------------------------------------
# TPM limiting
# ---------------------------------------------------------------------------

class TestTPMLimit:
    def test_within_tpm_allowed(self):
        rl = make_limiter("claude", rpm=100, tpm=500)
        allowed, _ = rl.check_and_consume("claude", 400)
        assert allowed is True

    def test_over_tpm_blocked(self):
        rl = make_limiter("claude", rpm=100, tpm=500)
        rl.check_and_consume("claude", 300)
        allowed, _ = rl.check_and_consume("claude", 300)
        assert allowed is False

    def test_over_tpm_returns_fallback(self):
        rl = make_limiter("claude", rpm=100, tpm=100, fallback="qwen")
        rl.check_and_consume("claude", 80)
        allowed, fb = rl.check_and_consume("claude", 30)
        assert allowed is False
        assert fb == "qwen"

    def test_zero_token_request_counts_rpm(self):
        rl = make_limiter("claude", rpm=2, tpm=100_000)
        rl.check_and_consume("claude", 0)
        rl.check_and_consume("claude", 0)
        allowed, _ = rl.check_and_consume("claude", 0)
        assert allowed is False


# ---------------------------------------------------------------------------
# Sliding window expiry
# ---------------------------------------------------------------------------

class TestSlidingWindow:
    def test_old_records_expire(self, monkeypatch):
        """Records >60s old should be evicted, freeing up quota."""
        rl = make_limiter("claude", rpm=2, tpm=100_000)

        fake_time = [0.0]
        monkeypatch.setattr("src.rate_limiter.time", lambda: fake_time[0])

        fake_time[0] = 0.0
        rl.check_and_consume("claude", 10)
        rl.check_and_consume("claude", 10)

        # Advance clock past 60 s — old records should expire
        fake_time[0] = 61.0
        allowed, _ = rl.check_and_consume("claude", 10)
        assert allowed is True


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_stats_reflect_usage(self):
        rl = make_limiter("claude", rpm=10, tpm=1000)
        rl.check_and_consume("claude", 200)
        rl.check_and_consume("claude", 300)
        stats = rl.get_stats("claude")
        assert stats["rpm_used"] == 2
        assert stats["tpm_used"] == 500
        assert stats["rpm_limit"] == 10
        assert stats["tpm_limit"] == 1000

    def test_stats_unknown_agent_returns_defaults(self):
        rl = make_limiter("claude")
        stats = rl.get_stats("unknown")
        assert stats["rpm_used"] == 0
        assert stats["tpm_used"] == 0

    def test_all_stats_covers_all_agents(self):
        rl = RateLimiter.__new__(RateLimiter)
        rl.quotas = {}
        rl._windows = {}
        import threading
        rl._lock = threading.Lock()
        rl.configure("claude", AgentQuota(rpm=50, tpm=80_000))
        rl.configure("qwen", AgentQuota(rpm=120, tpm=150_000))
        all_s = rl.all_stats()
        assert "claude" in all_s
        assert "qwen" in all_s



# ---------------------------------------------------------------------------
# Claude review fixes (P0 bugs + edge cases)
# ---------------------------------------------------------------------------

class TestClaudeReviewFixes:
    def test_negative_tokens_raises(self):
        """Issue #3: negative estimated_tokens must be rejected (would bypass TPM limit)."""
        rl = make_limiter("claude", rpm=10, tpm=1000)
        with pytest.raises(ValueError, match="estimated_tokens must be >= 0"):
            rl.check_and_consume("claude", -1)

    def test_new_agent_window_created_on_first_check(self):
        """Issue #1: quota added via configure() must not KeyError on first check_and_consume."""
        rl = RateLimiter.__new__(RateLimiter)
        rl.quotas = {}
        rl._windows = {}
        import threading
        rl._lock = threading.Lock()
        # Add quota without pre-creating a window (simulate configure() race)
        rl.quotas["late_agent"] = AgentQuota(rpm=5, tpm=1000)
        # Should NOT raise KeyError
        allowed, _ = rl.check_and_consume("late_agent", 10)
        assert allowed is True

    def test_quota_read_inside_lock(self):
        """Issue #2: check_and_consume must see quota set by concurrent configure()."""
        import threading
        rl = make_limiter("claude", rpm=10, tpm=1000)
        results = []

        def writer():
            rl.configure("new_agent", AgentQuota(rpm=5, tpm=500))

        def reader():
            # After configure runs, check_and_consume should see the quota safely
            allowed, _ = rl.check_and_consume("new_agent", 10)
            results.append(allowed)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start(); t1.join()
        t2.start(); t2.join()
        assert results[0] is True  # quota present → allowed

    def test_fallback_cycle_detected(self):
        """Issue #4: A→B→A cycle must be rejected by configure()."""
        rl = RateLimiter.__new__(RateLimiter)
        rl.quotas = {}
        rl._windows = {}
        import threading
        rl._lock = threading.Lock()
        rl.configure("a", AgentQuota(rpm=5, tpm=1000, fallback="b"))
        with pytest.raises(ValueError, match="cycle"):
            rl.configure("b", AgentQuota(rpm=5, tpm=1000, fallback="a"))

    def test_self_referential_fallback_cycle(self):
        """Issue #4: A→A self-loop must also be rejected."""
        rl = RateLimiter.__new__(RateLimiter)
        rl.quotas = {}
        rl._windows = {}
        import threading
        rl._lock = threading.Lock()
        with pytest.raises(ValueError, match="cycle"):
            rl.configure("a", AgentQuota(rpm=5, tpm=1000, fallback="a"))


class TestConfigLoading:
    def test_tpm_boundary_exact_limit_blocked(self):
        """Bug #1: tpm_used + estimated == tpm must be blocked (>= not >)."""
        rl = make_limiter("claude", rpm=100, tpm=500)
        allowed, _ = rl.check_and_consume("claude", 500)
        assert allowed is False

    def test_rollback_removes_last_record(self):
        """Bug #2: rollback should free quota after LLM failure."""
        rl = make_limiter("claude", rpm=2, tpm=1000)
        rl.check_and_consume("claude", 100)
        rl.check_and_consume("claude", 200)
        # At rpm limit now
        allowed, _ = rl.check_and_consume("claude", 10)
        assert allowed is False
        # Rollback last call
        assert rl.rollback("claude", 200) is True
        # Now should be allowed again
        allowed, _ = rl.check_and_consume("claude", 10)
        assert allowed is True

    def test_rollback_fails_on_token_mismatch(self):
        rl = make_limiter("claude", rpm=10, tpm=1000)
        rl.check_and_consume("claude", 100)
        assert rl.rollback("claude", 999) is False

    def test_rollback_fails_on_stale_record(self, monkeypatch):
        rl = make_limiter("claude", rpm=10, tpm=1000)
        fake_time = [0.0]
        monkeypatch.setattr("src.rate_limiter.time", lambda: fake_time[0])
        fake_time[0] = 0.0
        rl.check_and_consume("claude", 100)
        fake_time[0] = 2.0  # >1s later
        assert rl.rollback("claude", 100) is False

    def test_rollback_empty_window(self):
        rl = make_limiter("claude", rpm=10, tpm=1000)
        assert rl.rollback("claude", 0) is False

    def test_rollback_unknown_agent(self):
        rl = make_limiter("claude", rpm=10, tpm=1000)
        assert rl.rollback("unknown", 0) is False

    def test_loads_from_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "rate_limits:\n"
            "  claude:\n"
            "    rpm: 50\n"
            "    tpm: 80000\n"
            "    fallback: qwen\n"
        )
        rl = RateLimiter(str(cfg))
        assert rl.quotas["claude"].rpm == 50
        assert rl.quotas["claude"].tpm == 80_000
        assert rl.quotas["claude"].fallback == "qwen"

    def test_missing_config_file_is_tolerated(self):
        rl = RateLimiter("/nonexistent/path/config.yaml")
        allowed, _ = rl.check_and_consume("any", 0)
        assert allowed is True   # no quota → allow


# ---------------------------------------------------------------------------
# Concurrent flooding test
# ---------------------------------------------------------------------------

class TestConcurrentFlood:
    def test_rpm_limiter_with_20_threads(self):
        """ concurrent flooding test: 20 threads, rpm=5, expect exactly 5 allowed, 15 blocked """
        import threading
        rl = make_limiter("claude", rpm=5, tpm=100_000)

        results = {"allowed": 0, "blocked": 0}
        results_lock = threading.Lock()
        errors = []
        barrier = threading.Barrier(20)

        def worker():
            try:
                barrier.wait()  # synchronize all threads
                allowed, _ = rl.check_and_consume("claude", 10)
                with results_lock:
                    if allowed:
                        results["allowed"] += 1
                    else:
                        results["blocked"] += 1
            except Exception as e:
                with results_lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Got errors: {errors}"
        assert results["allowed"] == 5, f"Expected 5 allowed, got {results['allowed']}"
        assert results["blocked"] == 15, f"Expected 15 blocked, got {results['blocked']}"
