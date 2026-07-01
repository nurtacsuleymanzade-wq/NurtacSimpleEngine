#!/usr/bin/env python3
import json
import os
import time
import uuid
from pathlib import Path

BASE_DIR = Path("/root/NurtacSimpleEngine")
DATA_DIR = BASE_DIR / "data"
ENV_FILE = BASE_DIR / ".env"
BALANCE_FILE = DATA_DIR / "balance.json"
TRADES_FILE = DATA_DIR / "trades_all.jsonl"


def _read_env_initial_balance() -> float:
    value = 500.0
    try:
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("INITIAL_BALANCE="):
                value = float(line.split("=", 1)[1].strip() or "500.0")
                break
    except Exception:
        pass
    return value


def _tail_jsonl(path: Path, n: int = 1000) -> list[dict]:
    try:
        import subprocess
        raw = subprocess.getoutput(f"tail -n {n} {path}")
        rows = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows
    except Exception:
        return []


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _trade_metrics(trade: dict, current_balance: float) -> dict:
    entry = _safe_float(trade.get("entry"))
    sl = _safe_float(trade.get("sl"))
    if entry <= 0 or sl <= 0:
        return {}
    risk_usd = current_balance * 0.01
    sl_dist_pct = abs(entry - sl) / entry
    if sl_dist_pct <= 0:
        return {}
    position_usd = risk_usd / sl_dist_pct
    leverage = position_usd / current_balance if current_balance > 0 else 0.0
    contracts = position_usd / entry
    reward_usd = risk_usd * _safe_float(trade.get("rr"))
    return {
        "risk_usd": risk_usd,
        "position_usd": position_usd,
        "leverage": leverage,
        "contracts": contracts,
        "sl_dist": abs(entry - sl),
        "balance_at_open": current_balance,
        "reward_usd": reward_usd,
    }


def _calc_pnl(trade: dict) -> tuple[float, float]:
    entry = _safe_float(trade.get("entry"))
    sl = _safe_float(trade.get("sl"))
    close_price = _safe_float(trade.get("close_price"))
    risk_usd = _safe_float(trade.get("risk_usd"))
    if entry <= 0 or sl <= 0 or risk_usd <= 0:
        return 0.0, 0.0
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0, 0.0
    if trade.get("direction") == "short":
        pnl_r = (entry - close_price) / sl_dist
    else:
        pnl_r = (close_price - entry) / sl_dist
    pnl_usd = pnl_r * risk_usd
    return pnl_r, pnl_usd


def _load_balance(initial_balance: float) -> dict:
    try:
        return json.loads(BALANCE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "initial_balance": initial_balance,
            "current_balance": initial_balance,
            "total_pnl_usd": 0.0,
            "total_pnl_pct": 0.0,
            "open_trades": 0,
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_pnl_r": 0.0,
            "max_consecutive_loss": 0,
            "total_risk_deployed": 0.0,
            "by_engine": {
                "ict": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
                "liq": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
                "auction": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
                "ob": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
            },
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }


def rebuild():
    initial_balance = _read_env_initial_balance()
    trades = _tail_jsonl(TRADES_FILE, 5000)
    closed = [t for t in trades if t.get("status") == "closed"]
    open_trades = [t for t in trades if t.get("status") == "open"]
    balance = _load_balance(initial_balance)
    current_balance = initial_balance
    wins = 0
    losses = 0
    gross_win = 0.0
    gross_loss = 0.0
    pnl_r_sum = 0.0
    max_consec_loss = 0
    consec_loss = 0
    by_engine = {
        "ict": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
        "liq": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
        "auction": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
        "ob": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
    }
    for trade in closed:
        pnl_usd = _safe_float(trade.get("pnl_usd"))
        pnl_r = _safe_float(trade.get("pnl_r"))
        current_balance += pnl_usd
        pnl_r_sum += pnl_r
        eng = trade.get("engine")
        if eng in by_engine:
            by_engine[eng]["trades"] += 1
            by_engine[eng]["pnl_usd"] += pnl_usd
            if pnl_usd > 0:
                by_engine[eng]["wins"] += 1
        if pnl_usd > 0:
            wins += 1
            gross_win += pnl_usd
            consec_loss = 0
        else:
            losses += 1
            gross_loss += abs(pnl_usd)
            consec_loss += 1
            if consec_loss > max_consec_loss:
                max_consec_loss = consec_loss
    total_trades = len(closed)
    total_pnl_usd = current_balance - initial_balance
    total_pnl_pct = (total_pnl_usd / initial_balance * 100.0) if initial_balance > 0 else 0.0
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 0.0
    avg_pnl_r = (pnl_r_sum / total_trades) if total_trades > 0 else 0.0
    total_risk_deployed = sum(_safe_float(t.get("risk_usd")) for t in open_trades)
    balance = {
        "initial_balance": initial_balance,
        "current_balance": current_balance,
        "total_pnl_usd": total_pnl_usd,
        "total_pnl_pct": total_pnl_pct,
        "open_trades": len(open_trades),
        "closed_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_pnl_r": avg_pnl_r,
        "max_consecutive_loss": max_consec_loss,
        "total_risk_deployed": total_risk_deployed,
        "by_engine": by_engine,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BALANCE_FILE.write_text(json.dumps(balance, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    rebuild()
