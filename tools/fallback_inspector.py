#!/usr/bin/env python3
"""
fallback_inspector.py — ACP Bridge Fallback Routing Inspector

Shows per-agent scoring based on the get_best_fallback() formula:
  base_score = 100 * success_rate + 20 / (1 + avg_dur / 30)
  final_score = base_score * (1.5 if has_idle else 1.0)

Usage:
  python3 tools/fallback_inspector.py [--hours N] [--db PATH]
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path


def query_stats(db_path: str, hours: float) -> dict:
    if not Path(db_path).exists():
        print(f"[!] DB not found: {db_path}", file=sys.stderr)
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT * FROM agent_stats WHERE created_at > ?", (cutoff,)
    ).fetchall()
    conn.close()

    agents: dict[str, dict] = {}
    for r in rows:
        a = r["agent"]
        if a not in agents:
            agents[a] = {"total": 0, "success": 0, "durations": []}
        agents[a]["total"] += 1
        if r["success"]:
            agents[a]["success"] += 1
        agents[a]["durations"].append(r["duration"])

    result = {}
    for a, s in agents.items():
        durs = s["durations"]
        result[a] = {
            "total": s["total"],
            "success": s["success"],
            "avg_duration": round(sum(durs) / len(durs), 1) if durs else 0,
        }
    return result


def score_agent(success_rate: float, avg_dur: float, has_idle: bool) -> float:
    base = 100 * success_rate + 20 / (1 + avg_dur / 30)
    return base * (1.5 if has_idle else 1.0)


def render_bar(value: float, max_val: float, width: int = 20) -> str:
    filled = int(width * value / max_val) if max_val > 0 else 0
    return "█" * filled + "░" * (width - filled)


def query_cost(db_path: str, hours: float) -> dict:
    """Aggregate cost_usd, input_tokens, output_tokens per agent from jobs table."""
    if not Path(db_path).exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT agent, input_tokens, output_tokens, cost_usd FROM jobs "
        "WHERE completed_at > ? AND status = 'completed'", (cutoff,)
    ).fetchall()
    conn.close()
    agents: dict[str, dict] = {}
    for r in rows:
        a = r["agent"]
        if a not in agents:
            agents[a] = {"calls": 0, "in_tok": 0, "out_tok": 0, "cost_usd": 0.0}
        agents[a]["calls"] += 1
        agents[a]["in_tok"] += r["input_tokens"] or 0
        agents[a]["out_tok"] += r["output_tokens"] or 0
        agents[a]["cost_usd"] += r["cost_usd"] or 0.0
    return agents


def query_raw_rows(db_path: str, hours: float) -> list[dict]:
    """Return raw agent_stats rows ordered by created_at."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT agent, success, duration, created_at FROM agent_stats "
        "WHERE created_at > ? ORDER BY created_at", (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def monitor_alerts(db_path: str, hours: float, p95_threshold: float,
                   chain_path: str) -> int:
    """Monitor mode: alert + auto-disable. Returns exit code 0/1/2."""
    stats = query_stats(db_path, hours)
    raw = query_raw_rows(db_path, hours)
    if not stats:
        print("OK — no data")
        return 0

    alerts: list[tuple[str, str]] = []  # (level, message)

    # (1) Success rate < 80%
    for agent, s in stats.items():
        rate = s["success"] / s["total"] if s["total"] else 1.0
        if rate < 0.8:
            alerts.append(("WARNING", f"{agent} success rate {rate:.0%} ({s['success']}/{s['total']})"))

    # (2) P95 latency
    per_agent_durs: dict[str, list[float]] = {}
    for r in raw:
        per_agent_durs.setdefault(r["agent"], []).append(r["duration"])
    for agent, durs in per_agent_durs.items():
        durs.sort()
        p95 = durs[int(len(durs) * 0.95)] if len(durs) >= 2 else durs[-1]
        if p95 > p95_threshold:
            alerts.append(("WARNING", f"{agent} P95 latency {p95:.1f}s > {p95_threshold}s"))

    # (3) Consecutive failures → auto-disable
    tail: dict[str, int] = {}  # current consecutive failure streak (from latest)
    for r in raw:
        a = r["agent"]
        if not r["success"]:
            tail[a] = tail.get(a, 0) + 1
        else:
            tail[a] = 0

    disabled: list[str] = []
    for agent, streak in tail.items():
        if streak >= 3:
            alerts.append(("CRITICAL", f"{agent} {streak} consecutive failures — auto-disabled"))
            disabled.append(agent)

    if disabled and chain_path:
        _auto_disable_agents(disabled, chain_path)

    # Output
    for level, msg in alerts:
        print(f"{level}: {msg}")
    if not alerts:
        print("OK")
        return 0
    return 2 if any(l == "CRITICAL" for l, _ in alerts) else 1


def _auto_disable_agents(agents: list[str], chain_path: str) -> None:
    """Remove agents from all fallback chains and persist."""
    try:
        import yaml
        if not Path(chain_path).exists():
            return
        with open(chain_path) as f:
            chain = yaml.safe_load(f) or {}
        modified = False
        for primary, fallbacks in chain.items():
            for agent in agents:
                if agent in fallbacks:
                    fallbacks.remove(agent)
                    modified = True
        if modified:
            with open(chain_path, "w") as f:
                yaml.dump(chain, f, default_flow_style=False, allow_unicode=True)
            print(f"AUTO: removed {agents} from fallback chain ({chain_path})")
    except Exception as e:
        print(f"AUTO-DISABLE FAILED: {e}", file=sys.stderr)


def check_health(db_path: str, hours: float) -> int:
    """Health check mode. Returns exit code: 0=OK, 1=WARNING, 2=CRITICAL."""
    stats = query_stats(db_path, hours)
    if not stats:
        print("OK — no data")
        return 0

    level = 0  # 0=OK, 1=WARNING, 2=CRITICAL

    # Check success rate per agent
    for agent, s in stats.items():
        rate = s["success"] / s["total"] if s["total"] else 1.0
        if rate < 0.8:
            print(f"WARNING: {agent} success rate {rate:.0%} < 80% "
                  f"({s['success']}/{s['total']})")
            level = max(level, 1)

    # Check consecutive timeouts (duration > 440s) from raw rows
    if Path(db_path).exists():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - hours * 3600
        rows = conn.execute(
            "SELECT agent, duration FROM agent_stats "
            "WHERE created_at > ? ORDER BY agent, created_at", (cutoff,)
        ).fetchall()
        conn.close()

        streak: dict[str, int] = {}
        max_streak: dict[str, int] = {}
        for r in rows:
            a = r["agent"]
            if r["duration"] > 440:
                streak[a] = streak.get(a, 0) + 1
            else:
                streak[a] = 0
            max_streak[a] = max(max_streak.get(a, 0), streak[a])

        for agent, ms in max_streak.items():
            if ms >= 3:
                print(f"CRITICAL: {agent} had {ms} consecutive timeouts (>440s)")
                level = max(level, 2)

    if level == 0:
        print("OK")
    return level


def main():
    parser = argparse.ArgumentParser(description="ACP Bridge Fallback Inspector")
    parser.add_argument("--hours", type=float, default=1.0,
                        help="Stats window in hours (default: 1)")
    parser.add_argument("--db", type=str, default="data/jobs.db",
                        help="SQLite DB path (default: data/jobs.db)")
    parser.add_argument("--idle", nargs="*", default=[],
                        help="Agents to mark as idle (space-separated), e.g. --idle kiro claude")
    parser.add_argument("--check", action="store_true",
                        help="Health check mode: OK(0), WARNING(1), CRITICAL(2)")
    parser.add_argument("--monitor", action="store_true",
                        help="Monitor mode: alerts + auto-disable failing agents")
    parser.add_argument("--p95", type=float, default=120.0,
                        help="P95 latency alert threshold in seconds (default: 120)")
    parser.add_argument("--chain", type=str, default="config/fallback_chain.yaml",
                        help="Fallback chain YAML path for auto-disable")
    parser.add_argument("--cost", action="store_true",
                        help="Cost analysis mode: show duration share per agent as cost proxy")
    args = parser.parse_args()

    if args.check:
        sys.exit(check_health(args.db, args.hours))

    if args.monitor:
        sys.exit(monitor_alerts(args.db, args.hours, args.p95, args.chain))

    stats = query_stats(args.db, args.hours)
    idle_set = set(args.idle or [])

    if not stats:
        print(f"No agent stats in the last {args.hours}h — DB may be empty or path wrong.")
        return

    if args.cost:
        cost_data = query_cost(args.db, args.hours)
        RESET = "\033[0m"
        BOLD = "\033[1m"
        CYAN = "\033[96m"
        DIM = "\033[2m"
        if not cost_data:
            print(f"No cost data in the last {args.hours}h.")
            return
        total_usd = sum(c["cost_usd"] for c in cost_data.values())
        rows_cost = sorted(cost_data.items(), key=lambda x: -x[1]["cost_usd"])
        print()
        print(f"{BOLD}{'─'*68}{RESET}")
        print(f"{BOLD}  ACP Bridge — Cost Analysis  (last {args.hours}h){RESET}")
        print(f"{BOLD}{'─'*68}{RESET}")
        print(f"  {BOLD}{'AGENT':<12} {'CALLS':>6} {'IN_TOK':>8} {'OUT_TOK':>8} {'USD':>10} {'SHARE':>7}  BAR{RESET}")
        print(f"  {'─'*64}")
        for agent, c in rows_cost:
            share = c["cost_usd"] / total_usd if total_usd > 0 else 0
            bar = "█" * int(share * 25) + "░" * (25 - int(share * 25))
            print(f"{CYAN}  {agent:<12} {c['calls']:>6} {c['in_tok']:>8} {c['out_tok']:>8} ${c['cost_usd']:>8.4f} {share:>6.0%}  {bar}{RESET}")
        print(f"  {'─'*64}")
        print(f"{DIM}  Total: ${total_usd:.4f}  |  Pricing from BEDROCK_PRICING table{RESET}")
        print(f"{BOLD}{'─'*68}{RESET}")
        print()
        return

    # Compute scores
    rows = []
    for agent, s in stats.items():
        total = s["total"]
        success = s["success"]
        avg_dur = s["avg_duration"]
        rate = success / total if total > 0 else 0.5
        has_idle = agent in idle_set
        final = score_agent(rate, avg_dur, has_idle)
        rows.append({
            "agent": agent,
            "total": total,
            "success": success,
            "rate": rate,
            "avg_dur": avg_dur,
            "has_idle": has_idle,
            "score": final,
        })

    rows.sort(key=lambda x: -x["score"])
    max_score = rows[0]["score"] if rows else 1.0

    # Header
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    DIM = "\033[2m"

    print()
    print(f"{BOLD}{'─'*72}{RESET}")
    print(f"{BOLD}  ACP Bridge — Fallback Routing Inspector  "
          f"(last {args.hours}h){RESET}")
    print(f"{BOLD}{'─'*72}{RESET}")
    print(f"  {BOLD}{'AGENT':<12} {'TOTAL':>6} {'SUCCESS':>8} {'RATE':>7} "
          f"{'AVG_DUR':>8} {'IDLE':>5} {'SCORE':>7}  BAR{RESET}")
    print(f"  {'─'*68}")

    for i, r in enumerate(rows):
        color = GREEN if i == 0 else (CYAN if i == 1 else RESET)
        idle_str = "✓" if r["has_idle"] else "✗"
        bar = render_bar(r["score"], max_score)
        crown = " 👑" if i == 0 else ""
        print(
            f"{color}  {r['agent']:<12} {r['total']:>6} {r['success']:>8} "
            f"{r['rate']:>6.0%} {r['avg_dur']:>7.1f}s {idle_str:>5} "
            f"{r['score']:>7.1f}  {bar}{crown}{RESET}"
        )

    print(f"  {'─'*68}")
    print(f"{DIM}  Scoring: base = 100*rate + 20/(1+dur/30), "
          f"×1.5 if idle{RESET}")
    print(f"{BOLD}{'─'*72}{RESET}")
    print()


if __name__ == "__main__":
    main()
