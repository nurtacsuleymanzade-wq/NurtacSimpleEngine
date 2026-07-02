#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import time
import uuid
from pathlib import Path

BASE = Path("/root/NurtacSimpleEngine")
SRC = Path("/root/NurtacCoreEngineClaude/data")
OUT = Path("/root/NurtacSimpleEngine/data")

BALANCE_FILE = OUT / "balance.json"
ZONE_FILE = OUT / "zone_v2.json"
CONFLUENCE_OPEN_FILE = OUT / "confluence_open.json"
CONFLUENCE_TRADES_FILE = OUT / "confluence_trades.jsonl"
TRADES_ALL_FILE = OUT / "trades_all.jsonl"

SLOTS = [
    "absorption_long",
    "absorption_short",
    "initiative_long",
    "initiative_short",
    "sweep_long",
    "sweep_short",
    "exhaustion_long",
    "exhaustion_short",
    "trapped_long",
    "trapped_short",
]

PRIMARY_ORDER = ["sweep", "absorption", "trapped", "initiative", "exhaustion", "bos"]

EVENT_DIRECTION_MAP = {
    "sell_absorbed": "LONG",
    "buy_absorbed": "SHORT",
    "downward_sweep": "LONG",
    "upward_sweep": "SHORT",
    "short_trapped": "LONG",
    "long_trapped": "SHORT",
    "buy_initiative": "LONG",
    "sell_initiative": "SHORT",
    "sell_exhaustion": "LONG",
    "buy_exhaustion": "SHORT",
}


def load_telegram_reporter():
    try:
        path = BASE / "07_telegram_reporter.py"
        spec = importlib.util.spec_from_file_location("telegram_reporter", path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def load_balance_module():
    try:
        path = BASE / "03_balance.py"
        spec = importlib.util.spec_from_file_location("balance_rebuilder", path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_jsonl_tail1(path: Path) -> dict:
    try:
        raw = subprocess.getoutput(f"tail -n 1 {path}")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def tail_jsonl(path: Path, n: int) -> list[dict]:
    try:
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


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def append_jsonl(path: Path, record: dict):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def write_json(path: Path, payload: dict):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_balance():
    balance = read_json(BALANCE_FILE)
    if not balance:
        balance = {"current_balance": 500.0, "closed_trades": 0, "wins": 0, "win_rate": 0.0}
    balance.setdefault("current_balance", 500.0)
    balance.setdefault("closed_trades", 0)
    balance.setdefault("wins", 0)
    balance.setdefault("win_rate", 0.0)
    return balance


def slot_name(primary_signal: str, direction: str) -> str:
    return f"{primary_signal}_{direction}"


def count_same_direction_trapped(trappeds: list[dict], target_direction: str) -> tuple[int, str]:
    valid = [r for r in trappeds if r.get("label") != "none" and safe_int(r.get("score"), 0) >= 3]
    direction = "short_trapped" if target_direction == "long" else "long_trapped"
    same = [r for r in valid if r.get("direction") == direction]
    if len(same) >= 2:
        return 2, "strong"
    if len(same) == 1:
        return 1, "weak"
    return 0, "none"


def pick_primary_signal(points: dict) -> str:
    best = None
    best_points = -1
    for name in PRIMARY_ORDER:
        pts = safe_int(points.get(name), 0)
        if pts > best_points:
            best = name
            best_points = pts
    return best or "bos"


def calc_confluence(zone, str_1m, str_15m, absorptions, initiatives, exhaustions, sweeps, trappeds):
    trend_1m = ((str_1m.get("trend") or {}).get("direction")) or "unknown"
    trend_15m = ((str_15m.get("trend") or {}).get("direction")) or "unknown"
    price_location = zone.get("price_location", "")
    premium_discount = zone.get("premium_discount", "")

    long_context = 0
    short_context = 0
    if trend_1m == "uptrend" and trend_15m == "uptrend":
        long_context += 2
    if trend_1m == "downtrend" and trend_15m == "downtrend":
        short_context += 2
    if premium_discount == "discount":
        long_context += 1
    if premium_discount == "premium":
        short_context += 1
    if "in_demand" in price_location:
        long_context += 2
    if "in_supply" in price_location:
        short_context += 2
    if "in_fvg_bullish" in price_location:
        long_context += 1
    if "in_fvg_bearish" in price_location:
        short_context += 1

    long_signal = 0
    short_signal = 0
    active_signals = {"absorption": None, "initiative": None, "exhaustion": None, "sweep": None, "trapped": None, "bos": None}

    abs_rows = [r for r in absorptions if r.get("label") != "none"]
    buy_abs = sum(1 for r in abs_rows if r.get("direction") == "buy_absorbed")
    sell_abs = sum(1 for r in abs_rows if r.get("direction") == "sell_absorbed")
    if sell_abs >= 3:
        long_signal += 2
        active_signals["absorption"] = "sell_absorbed"
    if buy_abs >= 3:
        short_signal += 2
        active_signals["absorption"] = "buy_absorbed"

    init_rows = [r for r in initiatives if r.get("label") != "none"]
    buy_init = sum(1 for r in init_rows if r.get("direction") == "buy_initiative")
    sell_init = sum(1 for r in init_rows if r.get("direction") == "sell_initiative")
    if buy_init >= 5:
        long_signal += 2
        active_signals["initiative"] = "buy_initiative"
    if sell_init >= 5:
        short_signal += 2
        active_signals["initiative"] = "sell_initiative"

    ex_rows = [r for r in exhaustions if r.get("label") != "none"]
    sell_ex = sum(1 for r in ex_rows if r.get("direction") == "sell_exhaustion")
    buy_ex = sum(1 for r in ex_rows if r.get("direction") == "buy_exhaustion")
    if sell_ex >= 3:
        long_signal += 1
        active_signals["exhaustion"] = "sell_exhaustion"
    if buy_ex >= 3:
        short_signal += 1
        active_signals["exhaustion"] = "buy_exhaustion"

    sweep_rows = [r for r in sweeps if r.get("label") != "none"]
    sweep_dir = None
    if any(r.get("direction") == "downward_sweep" for r in sweep_rows):
        long_signal += 2
        sweep_dir = "downward_sweep"
    if any(r.get("direction") == "upward_sweep" for r in sweep_rows):
        short_signal += 2
        sweep_dir = "upward_sweep" if sweep_dir is None else sweep_dir
    active_signals["sweep"] = sweep_dir

    bos = ((str_1m.get("bos") or {}).get("macro_bos"))
    if bos == "bullish":
        long_signal += 1
        active_signals["bos"] = "bullish"
    elif bos == "bearish":
        short_signal += 1
        active_signals["bos"] = "bearish"

    trapped_strength = "none"
    trapped_rows = [r for r in trappeds if r.get("label") != "none" and safe_int(r.get("score"), 0) >= 3]
    long_trapped_ct = sum(1 for r in trapped_rows if r.get("direction") == "long_trapped")
    short_trapped_ct = sum(1 for r in trapped_rows if r.get("direction") == "short_trapped")
    if short_trapped_ct >= 2:
        long_signal += 2
        trapped_strength = "strong"
        active_signals["trapped"] = "short_trapped"
    elif short_trapped_ct == 1:
        long_signal += 1
        trapped_strength = "weak"
        active_signals["trapped"] = "short_trapped"
    elif long_trapped_ct >= 2:
        short_signal += 2
        trapped_strength = "strong"
        active_signals["trapped"] = "long_trapped"
    elif long_trapped_ct == 1:
        short_signal += 1
        trapped_strength = "weak"
        active_signals["trapped"] = "long_trapped"

    long_score = long_context + long_signal
    short_score = short_context + short_signal

    breakdown = {
        "long_context": long_context,
        "long_signal": long_signal,
        "short_context": short_context,
        "short_signal": short_signal,
    }
    context = {
        "price_location": price_location,
        "premium_discount": premium_discount,
        "trend_1m": trend_1m,
        "trend_15m": trend_15m,
        "vp_shape": zone.get("vp_shape"),
        "poc": zone.get("poc"),
        "vah": zone.get("vah"),
        "val": zone.get("val"),
    }
    signal_points = {
        "absorption": 2 if active_signals["absorption"] else 0,
        "initiative": 2 if active_signals["initiative"] else 0,
        "sweep": 2 if active_signals["sweep"] else 0,
        "trapped": 2 if active_signals["trapped"] else 0,
        "exhaustion": 1 if active_signals["exhaustion"] else 0,
        "bos": 1 if active_signals["bos"] else 0,
    }
    meta = {
        "trapped_signal_strength": trapped_strength,
        "primary_signal": pick_primary_signal(signal_points),
    }
    return long_score, short_score, breakdown, active_signals, context, meta


def calculate_sl_tp(direction, entry, atr, zone, str_1m):
    order_blocks = zone.get("multi_tf_obs") or []
    liquidations = zone.get("liquidation") or {}
    primary = None
    if direction == "long":
        nearest = [ob for ob in order_blocks if safe_float(ob.get("ob_low"), 0) < entry]
        nearest.sort(key=lambda ob: abs(entry - safe_float(ob.get("ob_low"), 0)))
        nearest_demand_ob = next((ob for ob in nearest if "bullish" in str(ob.get("ob_type", ""))), None)
        if nearest_demand_ob:
            primary = safe_float(nearest_demand_ob.get("ob_low")) - (atr * 0.5)
        else:
            primary_signal = str_1m.get("_primary_signal")
            if primary_signal in {"sweep", "trapped"}:
                primary = entry - (atr * 1.5)
            else:
                primary = entry - (atr * 2.0)
    else:
        nearest = [ob for ob in order_blocks if safe_float(ob.get("ob_high"), 0) > entry]
        nearest.sort(key=lambda ob: abs(safe_float(ob.get("ob_high"), 0) - entry))
        nearest_supply_ob = next((ob for ob in nearest if "bearish" in str(ob.get("ob_type", ""))), None)
        if nearest_supply_ob:
            primary = safe_float(nearest_supply_ob.get("ob_high")) + (atr * 0.5)
        else:
            primary_signal = str_1m.get("_primary_signal")
            if primary_signal in {"sweep", "trapped"}:
                primary = entry + (atr * 1.5)
            else:
                primary = entry + (atr * 2.0)

    sl = primary
    if not sl or sl == entry:
        return None, None, 0.0
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return None, None, 0.0

    tp = None
    MAX_RR = 3.0

    if direction == "long":
        liq = liquidations.get("nearest_short_liq") or {}
        tp_candidate = safe_float(liq.get("price"), 0.0)
        if tp_candidate > entry and (tp_candidate - entry) / sl_dist <= MAX_RR:
            tp = tp_candidate
        if tp is None:
            liq = liquidations.get("nearest_long_liq") or {}
            tp_candidate = safe_float(liq.get("price"), 0.0)
            if tp_candidate > entry and (tp_candidate - entry) / sl_dist <= MAX_RR:
                tp = tp_candidate
    else:
        liq = liquidations.get("nearest_long_liq") or {}
        tp_candidate = safe_float(liq.get("price"), 0.0)
        if 0 < tp_candidate < entry and (entry - tp_candidate) / sl_dist <= MAX_RR:
            tp = tp_candidate
        if tp is None:
            liq = liquidations.get("nearest_short_liq") or {}
            tp_candidate = safe_float(liq.get("price"), 0.0)
            if 0 < tp_candidate < entry and (entry - tp_candidate) / sl_dist <= MAX_RR:
                tp = tp_candidate

    if tp is None:
        tp = entry + (sl_dist * 2.0) if direction == "long" else entry - (sl_dist * 2.0)
    return sl, tp, sl_dist


def open_trade(direction, long_score, short_score, breakdown, active_signals, zone, str_1m, telegram):
    balance = load_balance()
    entry = safe_float(zone.get("current_price"), 0.0)
    atr = safe_float(str_1m.get("atr_used"), 50.0)
    if entry <= 0:
        return
    primary_signal = None
    signal_label = None
    if active_signals.get("sweep"):
        primary_signal = "sweep"
        signal_label = active_signals["sweep"]
    elif active_signals.get("absorption"):
        primary_signal = "absorption"
        signal_label = active_signals["absorption"]
    elif active_signals.get("trapped"):
        primary_signal = "trapped"
        signal_label = active_signals["trapped"]
    elif active_signals.get("initiative"):
        primary_signal = "initiative"
        signal_label = active_signals["initiative"]
    elif active_signals.get("exhaustion"):
        primary_signal = "exhaustion"
        signal_label = active_signals["exhaustion"]
    elif ((str_1m.get("bos") or {}).get("macro_bos")) in {"bullish", "bearish"}:
        primary_signal = "bos"
        signal_label = ((str_1m.get("bos") or {}).get("macro_bos"))
    if not primary_signal:
        primary_signal = "bos"
        signal_label = "bullish" if direction == "long" else "bearish"
    str_1m = dict(str_1m)
    str_1m["_primary_signal"] = primary_signal
    sl, tp, sl_dist = calculate_sl_tp(direction, entry, atr, zone, str_1m)
    if sl is None or tp is None or sl_dist <= 0:
        return
    rr = abs(tp - entry) / sl_dist
    if rr <= 0:
        return
    risk_usd = safe_float(balance.get("current_balance"), 500.0) * 0.01
    position_usd = risk_usd * (entry / sl_dist)
    leverage = position_usd / safe_float(balance.get("current_balance"), 500.0) if safe_float(balance.get("current_balance"), 500.0) > 0 else 0.0
    slot = slot_name(primary_signal, direction)
    open_state = read_json(CONFLUENCE_OPEN_FILE)
    if open_state.get(slot):
        return
    trade_id = str(uuid.uuid4())[:8]
    trade = {
        "trade_id": trade_id,
        "engine": "confluence",
        "primary_signal": primary_signal,
        "signal_label": signal_label,
        "direction": direction,
        "long_score": long_score,
        "short_score": short_score,
        "score_breakdown": breakdown,
        "active_signals": active_signals,
        "context": {
            "price_location": zone.get("price_location", ""),
            "premium_discount": zone.get("premium_discount", ""),
            "trend_1m": (str_1m.get("trend") or {}).get("direction", "unknown"),
            "trend_15m": (read_jsonl_tail1(SRC / "structure_15m.jsonl").get("trend") or {}).get("direction", "unknown"),
            "vp_shape": zone.get("vp_shape"),
            "poc": zone.get("poc"),
            "vah": zone.get("vah"),
            "val": zone.get("val"),
        },
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_usd": risk_usd,
        "leverage": leverage,
        "rr": rr,
        "open_ts": int(time.time() * 1000),
        "close_ts": None,
        "outcome": None,
        "pnl_r": None,
        "pnl_usd": None,
        "duration_s": None,
        "why_closed": None,
        "balance_after": None,
        "status": "open",
        "slot": slot,
    }
    open_state[slot] = trade
    write_json(CONFLUENCE_OPEN_FILE, open_state)
    append_jsonl(CONFLUENCE_TRADES_FILE, trade)
    append_jsonl(TRADES_ALL_FILE, trade)
    if telegram and hasattr(telegram, "send_signal"):
        try:
            telegram.send_signal(
                f"{'🟢' if direction == 'long' else '🔴'} {direction.upper()} — CONFLUENCE\n"
                f"📊 Skor: {long_score}/13 (Long) vs {short_score}/13 (Short)\n"
                f"🎯 Ana Sinyal: {primary_signal} ({signal_label})\n"
                f"📍 Lokasyon: {zone.get('price_location', '')} | {zone.get('premium_discount', '')}\n"
                f"📈 Trend: 1M {(str_1m.get('trend') or {}).get('direction', 'unknown')} | 15M {(trade['context'].get('trend_15m'))}\n"
                f"💰 Entry: {entry} | SL: {sl} | TP: {tp}\n"
                f"⚖️ Risk: ${risk_usd:.2f} | RR: 1:{rr:.1f}"
            )
        except Exception:
            pass


def check_and_close_trades(zone, telegram):
    try:
        open_state = read_json(CONFLUENCE_OPEN_FILE)
        if not open_state:
            return
        current_price = safe_float(zone.get("current_price"), 0.0)
        if current_price <= 0:
            return
        balance = load_balance()
        current_balance = safe_float(balance.get("current_balance"), 500.0)
        changed = False
        wins_delta = 0
        closed_delta = 0
        for slot in list(open_state.keys()):
            trade = open_state.get(slot) or {}
            if trade.get("status") != "open":
                continue
            direction = trade.get("direction")
            sl = safe_float(trade.get("sl"), 0.0)
            tp = safe_float(trade.get("tp"), 0.0)
            hit = None
            if direction == "long":
                if current_price <= sl:
                    hit = "SL_HIT"
                    outcome = "loss"
                elif current_price >= tp:
                    hit = "TP_HIT"
                    outcome = "win"
                else:
                    continue
            else:
                if current_price >= sl:
                    hit = "SL_HIT"
                    outcome = "loss"
                elif current_price <= tp:
                    hit = "TP_HIT"
                    outcome = "win"
                else:
                    continue
            risk_usd = safe_float(trade.get("risk_usd"), current_balance * 0.01)
            pnl_r = 2.0 if outcome == "win" else -1.0
            pnl_usd = risk_usd * pnl_r
            close_ts = int(time.time() * 1000)
            duration_s = max(0, (close_ts - safe_int(trade.get("open_ts"), close_ts)) // 1000)
            balance_after = current_balance + pnl_usd
            trade.update({
                "close_ts": close_ts,
                "outcome": outcome,
                "pnl_r": pnl_r,
                "pnl_usd": pnl_usd,
                "duration_s": duration_s,
                "why_closed": hit,
                "balance_after": balance_after,
                "status": "closed",
            })
            open_state.pop(slot, None)
            current_balance = balance_after
            changed = True
            closed_delta += 1
            if outcome == "win":
                wins_delta += 1
            append_jsonl(CONFLUENCE_TRADES_FILE, trade)
            append_jsonl(TRADES_ALL_FILE, trade)
            if telegram and hasattr(telegram, "send_signal"):
                try:
                    duration_dk = duration_s // 60
                    duration_s_kalan = duration_s % 60
                    telegram.send_signal(
                        f"{'✅ TP HIT' if outcome == 'win' else '❌ SL HIT'} — CONFLUENCE\n"
                        f"🆔 {trade.get('trade_id')}\n"
                        f"🎯 Sinyal: {trade.get('primary_signal')}\n"
                        f"⏱️ Süre: {duration_dk}dk {duration_s_kalan}s\n"
                        f"💵 PnL: {pnl_r:+.2f}R / ${pnl_usd:+.2f}\n"
                        f"💰 Bakiye: ${balance_after:.2f}"
                    )
                except Exception:
                    pass
        if changed:
            wins = safe_int(balance.get("wins"), 0) + wins_delta
            closed = safe_int(balance.get("closed_trades"), 0) + closed_delta
            balance.update({
                "current_balance": current_balance,
                "closed_trades": closed,
                "wins": wins,
                "win_rate": (wins / closed * 100.0) if closed > 0 else 0.0,
            })
            write_json(BALANCE_FILE, balance)
            write_json(CONFLUENCE_OPEN_FILE, open_state)
            balance_module = load_balance_module()
            if balance_module and hasattr(balance_module, "rebuild"):
                try:
                    balance_module.rebuild()
                except Exception:
                    pass
    except Exception:
        return


def maybe_open_trade(direction, long_score, short_score, breakdown, active_signals, zone, str_1m, telegram):
    primary_signal = None
    if active_signals.get("sweep"):
        primary_signal = "sweep"
    elif active_signals.get("absorption"):
        primary_signal = "absorption"
    elif active_signals.get("trapped"):
        primary_signal = "trapped"
    elif active_signals.get("initiative"):
        primary_signal = "initiative"
    elif active_signals.get("exhaustion"):
        primary_signal = "exhaustion"
    else:
        primary_signal = "bos"
    slot = slot_name(primary_signal, direction)
    open_state = read_json(CONFLUENCE_OPEN_FILE)
    if slot in open_state and (open_state.get(slot) or {}).get("status") == "open":
        return
    open_trade(direction, long_score, short_score, breakdown, active_signals, zone, str_1m, telegram)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    telegram = load_telegram_reporter()
    while True:
        try:
            zone = read_json(ZONE_FILE)
            str_1m = read_jsonl_tail1(SRC / "structure_1m.jsonl")
            str_15m = read_jsonl_tail1(SRC / "structure_15m.jsonl")
            absorptions = tail_jsonl(SRC / "labels_absorption.jsonl", 30)
            initiatives = tail_jsonl(SRC / "labels_initiative_flow.jsonl", 30)
            exhaustions = tail_jsonl(SRC / "labels_exhaustion.jsonl", 30)
            sweeps = tail_jsonl(SRC / "labels_sweep.jsonl", 5)
            trappeds = tail_jsonl(SRC / "labels_trapped_trader.jsonl", 10)

            long_score, short_score, breakdown, active_signals, context, meta = calc_confluence(
                zone, str_1m, str_15m, absorptions, initiatives, exhaustions, sweeps, trappeds
            )
            str_1m = dict(str_1m)
            str_1m["_meta"] = meta
            check_and_close_trades(zone, telegram)

            if long_score >= 4 and long_score > short_score:
                maybe_open_trade("long", long_score, short_score, breakdown, active_signals, zone, str_1m, telegram)
            elif short_score >= 4 and short_score > long_score:
                maybe_open_trade("short", long_score, short_score, breakdown, active_signals, zone, str_1m, telegram)
        except Exception as e:
            print(f"[CONFLUENCE] loop error: {e}", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
