#!/usr/bin/env python3
import json
import importlib.util
import subprocess
import time
import uuid
from pathlib import Path

BASE = Path("/root/NurtacSimpleEngine")
DATA = BASE / "data"
SRC = Path("/root/NurtacCoreEngineClaude/data")

ZONE_FILE = DATA / "zone_v2.json"
BALANCE_FILE = DATA / "balance.json"
OPEN_FILE = DATA / "ict_open.json"
ICT_TRADES_FILE = DATA / "ict_trades.jsonl"
TRADES_ALL_FILE = DATA / "trades_all.jsonl"

OUTCOME_TYPES = ("sl_hit", "tp1_hit", "tp2_hit")

SETUP_SLOTS = {
    "ob_mitigation_short": None,
    "ob_mitigation_long": None,
    "fvg_fill_short": None,
    "fvg_fill_long": None,
    "liquidity_sweep_short": None,
    "liquidity_sweep_long": None,
    "msb_momentum_short": None,
    "msb_momentum_long": None,
}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def read_jsonl_tail1(path: Path) -> dict:
    try:
        raw = subprocess.getoutput(f"tail -1 {path}")
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def tail_jsonl(path: Path, n: int = 2000) -> list[dict]:
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


def safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def load_telegram_reporter():
    path = BASE / "07_telegram_reporter.py"
    try:
        spec = importlib.util.spec_from_file_location("telegram_reporter_07", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def current_price(zone: dict) -> float:
    return safe_float(zone.get("current_price"))


def read_balance() -> float:
    bal = read_json(BALANCE_FILE)
    return safe_float(bal.get("current_balance"), 500.0) or 500.0


def read_signals(window_s: int = 30) -> dict:
    now_ms = int(time.time() * 1000)
    signals = {
        "initiative": None,
        "absorption": None,
        "exhaustion": None,
        "sweep": None,
        "trapped": None,
    }
    files = {
        "initiative": SRC / "labels_initiative_flow.jsonl",
        "absorption": SRC / "labels_absorption.jsonl",
        "exhaustion": SRC / "labels_exhaustion.jsonl",
        "sweep": SRC / "labels_sweep.jsonl",
        "trapped": SRC / "labels_trapped_trader.jsonl",
    }
    for name, path in files.items():
        rows = tail_jsonl(path, 120)
        best = None
        best_score = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("label") == "none":
                continue
            ts = row.get("window_start_ts")
            try:
                ts_v = int(ts or 0)
            except Exception:
                continue
            if now_ms - ts_v > window_s * 1000:
                continue
            score = safe_float(row.get("score"))
            if best_score is None or score > best_score or (score == best_score and ts_v > int(best.get("window_start_ts") or 0)):
                best = row
                best_score = score
        signals[name] = best
    return signals


def signal_bias(detector: str, rec: dict | None) -> str | None:
    if not isinstance(rec, dict):
        return None
    direction = rec.get("direction")
    label = rec.get("label")
    if detector == "initiative":
        if direction == "buy_initiative":
            return "long"
        if direction == "sell_initiative":
            return "short"
    elif detector == "absorption":
        if direction == "buy_absorbed":
            return "short"
        if direction == "sell_absorbed":
            return "long"
    elif detector == "exhaustion":
        if direction == "buy_exhaustion":
            return "short"
        if direction == "sell_exhaustion":
            return "long"
    elif detector == "sweep":
        if direction == "upward_sweep":
            return "short"
        if direction == "downward_sweep":
            return "long"
    elif detector == "trapped":
        if label == "long_trapped":
            return "short"
        if label == "short_trapped":
            return "long"
    return None


def setup_biases(signals: dict) -> dict:
    return {
        "long": [
            signal_bias("initiative", signals.get("initiative")) == "long",
            signal_bias("absorption", signals.get("absorption")) == "long",
            signal_bias("exhaustion", signals.get("exhaustion")) == "long",
            signal_bias("sweep", signals.get("sweep")) == "long",
            signal_bias("trapped", signals.get("trapped")) == "long",
        ],
        "short": [
            signal_bias("initiative", signals.get("initiative")) == "short",
            signal_bias("absorption", signals.get("absorption")) == "short",
            signal_bias("exhaustion", signals.get("exhaustion")) == "short",
            signal_bias("sweep", signals.get("sweep")) == "short",
            signal_bias("trapped", signals.get("trapped")) == "short",
        ],
    }


def trend_1m(structure_1m: dict) -> str:
    trend = structure_1m.get("trend") or {}
    return trend.get("direction") or "unknown"


def msb_1m(structure_1m: dict) -> str | None:
    trend = structure_1m.get("trend") or {}
    return trend.get("msb")


def active_obs(zone: dict) -> list[dict]:
    obs = zone.get("multi_tf_obs") or []
    out = []
    for item in obs:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "active":
            continue
        out.append(item)
    return out


def active_fvgs(zone: dict) -> list[dict]:
    fvgs = zone.get("multi_tf_fvgs") or []
    out = []
    for item in fvgs:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "active":
            continue
        out.append(item)
    return out


def price_inside_active(zone_item: dict) -> bool:
    return bool(zone_item.get("price_inside"))


def pick_tp1_long(zone: dict, structure_1m: dict) -> tuple[float | None, str | None]:
    price = current_price(zone)
    liq = zone.get("liquidation") or {}
    nsl = liq.get("nearest_short_liq")
    if isinstance(nsl, dict):
        p = safe_float(nsl.get("price"))
        if p > price:
            return p, "likidasyon cluster"
    vah = safe_float(zone.get("vah"))
    if vah > price:
        return vah, "VAH"
    poc = safe_float(zone.get("poc"))
    if poc > price:
        return poc, "POC"
    swing = structure_1m.get("swing") or {}
    lsh = swing.get("last_swing_high") or {}
    sh = safe_float(lsh.get("price"))
    atr = safe_float(structure_1m.get("atr_used"))
    if sh > price:
        return sh - atr * 0.1, "swing high"
    return None, None


def pick_tp1_short(zone: dict, structure_1m: dict) -> tuple[float | None, str | None]:
    price = current_price(zone)
    liq = zone.get("liquidation") or {}
    nll = liq.get("nearest_long_liq")
    if isinstance(nll, dict):
        p = safe_float(nll.get("price"))
        if p < price:
            return p, "likidasyon cluster"
    val = safe_float(zone.get("val"))
    if val < price:
        return val, "VAL"
    poc = safe_float(zone.get("poc"))
    if poc < price:
        return poc, "POC"
    swing = structure_1m.get("swing") or {}
    lsl = swing.get("last_swing_low") or {}
    sl = safe_float(lsl.get("price"))
    atr = safe_float(structure_1m.get("atr_used"))
    if sl < price:
        return sl + atr * 0.1, "swing low"
    return None, None


def make_trade_setup(zone: dict, structure_1m: dict, structure_15m: dict, signals: dict, slot_name: str):
    price = current_price(zone)
    trend = trend_1m(structure_1m)
    msb1 = msb_1m(structure_1m)
    msb15 = msb_1m(structure_15m)
    atr1 = safe_float(structure_1m.get("atr_used"))
    atr15 = safe_float(structure_15m.get("atr_used"))
    active = active_obs(zone)
    active_fvg_list = active_fvgs(zone)
    prem = zone.get("premium_discount")
    biases = setup_biases(signals)
    current_balance = read_balance()
    risk_usd = current_balance * 0.01

    def open_trade(payload: dict) -> dict:
        entry = price
        sl = safe_float(payload.get("sl"))
        tp1 = safe_float(payload.get("tp1"))
        if entry <= 0 or sl <= 0 or tp1 <= 0:
            return {}
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return {}
        rr = abs(tp1 - entry) / sl_dist
        if rr <= 0:
            return {}
        position_usd = risk_usd / (sl_dist / entry)
        leverage = position_usd / current_balance if current_balance > 0 else 0.0
        contracts = position_usd / entry
        return {
            "trade_id": uuid.uuid4().hex[:8],
            "engine": "ict",
            "signal_type": payload.get("signal_type"),
            "signal_label": payload.get("signal_label"),
            "direction": payload.get("direction"),
            "open_ts": int(time.time() * 1000),
            "close_ts": None,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": payload.get("tp2"),
            "sl_dist": sl_dist,
            "rr": rr,
            "risk_usd": risk_usd,
            "reward_usd": risk_usd * rr,
            "leverage": leverage,
            "contracts": contracts,
            "position_usd": position_usd,
            "balance_at_open": current_balance,
            "status": "open",
            "outcome": None,
            "close_reason": None,
            "pnl_r": None,
            "pnl_usd": None,
            "balance_after": None,
            "duration_s": None,
            "price_location": zone.get("price_location"),
            "premium_discount": prem,
            "vp_shape": zone.get("vp_shape"),
            "why_opened": payload.get("why_opened"),
            "why_closed": None,
            "setup_type": payload.get("setup_type"),
            "ob_tf": payload.get("ob_tf"),
            "fvg_tf": payload.get("fvg_tf"),
        }

    def long_ok() -> bool:
        return trend in {"uptrend", "ranging"}

    def short_ok() -> bool:
        return trend in {"downtrend", "ranging"}

    # OB mitigation
    if slot_name == "ob_mitigation_short":
        for ob in active:
            if ob.get("ob_type") != "bearish_ob":
                continue
            if not price_inside_active(ob):
                continue
            if not short_ok():
                continue
            if not any(biases.get("short") or []):
                continue
            sl = safe_float(ob.get("ob_high")) + atr1 * 0.15
            tp1, tp_name = pick_tp1_short(zone, structure_1m)
            if tp1 is None:
                continue
            tp2 = tp1 - (price - tp1) * 0.5
            return open_trade({
                "signal_type": "ob_mitigation",
                "signal_label": "bearish_ob_mitigation",
                "direction": "short",
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "setup_type": "ob_mitigation",
                "ob_tf": ob.get("tf"),
                "fvg_tf": None,
                "why_opened": f"OB mitigation {ob.get('tf')} bearish_ob + short bias",
            })
    if slot_name == "ob_mitigation_long":
        for ob in active:
            if ob.get("ob_type") != "bullish_ob":
                continue
            if not price_inside_active(ob):
                continue
            if not long_ok():
                continue
            if signal_bias("absorption", signals.get("absorption")) == "short":
                continue
            if not any([signal_bias("initiative", signals.get("initiative")) == "long",
                        signal_bias("absorption", signals.get("absorption")) == "long",
                        signal_bias("exhaustion", signals.get("exhaustion")) == "long",
                        signal_bias("sweep", signals.get("sweep")) == "long",
                        signal_bias("trapped", signals.get("trapped")) == "long"]):
                continue
            sl = safe_float(ob.get("ob_low")) - atr1 * 0.15
            tp1, tp_name = pick_tp1_long(zone, structure_1m)
            if tp1 is None:
                continue
            tp2 = tp1 + (tp1 - price) * 0.5
            return open_trade({
                "signal_type": "ob_mitigation",
                "signal_label": "bullish_ob_mitigation",
                "direction": "long",
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "setup_type": "ob_mitigation",
                "ob_tf": ob.get("tf"),
                "fvg_tf": None,
                "why_opened": f"OB mitigation {ob.get('tf')} bullish_ob + long bias",
            })

    # FVG fill
    if slot_name == "fvg_fill_short":
        for fvg in active_fvg_list:
            if fvg.get("fvg_type") != "bearish_fvg":
                continue
            if not price_inside_active(fvg):
                continue
            if not short_ok():
                continue
            if not any([signal_bias("initiative", signals.get("initiative")) == "short",
                        signal_bias("absorption", signals.get("absorption")) == "short",
                        signal_bias("exhaustion", signals.get("exhaustion")) == "short"]):
                continue
            sl = safe_float(fvg.get("gap_high")) + atr1 * 0.15
            tp1, tp_name = pick_tp1_short(zone, structure_1m)
            if tp1 is None:
                continue
            tp2 = tp1 - (price - tp1) * 0.5
            return open_trade({
                "signal_type": "fvg_fill",
                "signal_label": "bearish_fvg_fill",
                "direction": "short",
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "setup_type": "fvg_fill",
                "ob_tf": None,
                "fvg_tf": fvg.get("tf"),
                "why_opened": f"FVG fill {fvg.get('tf')} bearish_fvg + short bias",
            })
    if slot_name == "fvg_fill_long":
        for fvg in active_fvg_list:
            if fvg.get("fvg_type") != "bullish_fvg":
                continue
            if not price_inside_active(fvg):
                continue
            if not long_ok():
                continue
            if not any([signal_bias("absorption", signals.get("absorption")) == "long",
                        signal_bias("exhaustion", signals.get("exhaustion")) == "long",
                        signal_bias("trapped", signals.get("trapped")) == "long"]):
                continue
            sl = safe_float(fvg.get("gap_low")) - atr1 * 0.15
            tp1, tp_name = pick_tp1_long(zone, structure_1m)
            if tp1 is None:
                continue
            tp2 = tp1 + (tp1 - price) * 0.5
            return open_trade({
                "signal_type": "fvg_fill",
                "signal_label": "bullish_fvg_fill",
                "direction": "long",
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "setup_type": "fvg_fill",
                "ob_tf": None,
                "fvg_tf": fvg.get("tf"),
                "why_opened": f"FVG fill {fvg.get('tf')} bullish_fvg + long bias",
            })

    # Liquidity sweep
    if slot_name == "liquidity_sweep_short":
        s = signals.get("sweep") or {}
        if s.get("direction") == "upward_sweep" and safe_float(s.get("score")) >= 3:
            swing = structure_1m.get("swing") or {}
            lsh = swing.get("last_swing_high") or {}
            sweep_high = safe_float(lsh.get("price"))
            if sweep_high > price:
                sl = sweep_high + atr1 * 0.15
                tp1, tp_name = pick_tp1_short(zone, structure_1m)
                if tp1 is not None:
                    tp2 = tp1 - (price - tp1) * 0.5
                    return open_trade({
                        "signal_type": "liquidity_sweep",
                        "signal_label": "upward_sweep_reclaim",
                        "direction": "short",
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "setup_type": "liquidity_sweep",
                        "ob_tf": None,
                        "fvg_tf": None,
                        "why_opened": "Liquidity sweep upward_sweep + reclaim",
                    })
    if slot_name == "liquidity_sweep_long":
        s = signals.get("sweep") or {}
        if s.get("direction") == "downward_sweep":
            swing = structure_1m.get("swing") or {}
            lsl = swing.get("last_swing_low") or {}
            sweep_low = safe_float(lsl.get("price"))
            if sweep_low < price:
                sl = sweep_low - atr1 * 0.15
                tp1, tp_name = pick_tp1_long(zone, structure_1m)
                if tp1 is not None:
                    tp2 = tp1 + (tp1 - price) * 0.5
                    return open_trade({
                        "signal_type": "liquidity_sweep",
                        "signal_label": "downward_sweep_reclaim",
                        "direction": "long",
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "setup_type": "liquidity_sweep",
                        "ob_tf": None,
                        "fvg_tf": None,
                        "why_opened": "Liquidity sweep downward_sweep + reclaim",
                    })

    # MSB momentum
    if slot_name == "msb_momentum_short":
        if (msb1 == "bearish" or msb_1m(structure_15m) == "bearish") and any(biases.get("short") or []) and zone.get("premium_discount") == "premium":
            swing = structure_1m.get("swing") or {}
            lsh = swing.get("last_swing_high") or {}
            sh = safe_float(lsh.get("price"))
            if sh > 0:
                sl = sh + atr1 * 0.15
                tp1, tp_name = pick_tp1_short(zone, structure_1m)
                if tp1 is not None:
                    tp2 = tp1 - (price - tp1) * 0.5
                    return open_trade({
                        "signal_type": "msb_momentum",
                        "signal_label": "bearish_msb_momentum",
                        "direction": "short",
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "setup_type": "msb_momentum",
                        "ob_tf": None,
                        "fvg_tf": None,
                        "why_opened": "MSB bearish momentum in premium",
                    })
    if slot_name == "msb_momentum_long":
        if (msb1 == "bullish" or msb_1m(structure_15m) == "bullish") and any(biases.get("long") or []) and zone.get("premium_discount") == "discount":
            swing = structure_1m.get("swing") or {}
            lsl = swing.get("last_swing_low") or {}
            slw = safe_float(lsl.get("price"))
            if slw > 0:
                sl = slw - atr1 * 0.15
                tp1, tp_name = pick_tp1_long(zone, structure_1m)
                if tp1 is not None:
                    tp2 = tp1 + (tp1 - price) * 0.5
                    return open_trade({
                        "signal_type": "msb_momentum",
                        "signal_label": "bullish_msb_momentum",
                        "direction": "long",
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "setup_type": "msb_momentum",
                        "ob_tf": None,
                        "fvg_tf": None,
                        "why_opened": "MSB bullish momentum in discount",
                    })
    return {}


def persist_trade_open(trade: dict, slot_name: str, open_slots: dict) -> None:
    if not trade:
        return
    open_slots[slot_name] = trade
    DATA.mkdir(parents=True, exist_ok=True)
    with ICT_TRADES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(trade, ensure_ascii=False) + "\n")
    with TRADES_ALL_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(trade, ensure_ascii=False) + "\n")


def close_trade(trade: dict, close_price: float, reason: str, close_reason: str) -> dict:
    entry = safe_float(trade.get("entry"))
    sl = safe_float(trade.get("sl"))
    risk_usd = safe_float(trade.get("risk_usd"))
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        sl_dist = 1.0
    if trade.get("direction") == "short":
        pnl_r = (entry - close_price) / sl_dist
    else:
        pnl_r = (close_price - entry) / sl_dist
    pnl_usd = pnl_r * risk_usd
    balance = read_balance()
    balance_after = balance + pnl_usd
    outcome = "win" if pnl_usd > 0 else "loss"
    if reason == "tp2_hit":
        outcome = "win"
    closed = dict(trade)
    closed["close_ts"] = int(time.time() * 1000)
    closed["close_price"] = close_price
    closed["status"] = "closed"
    closed["outcome"] = outcome
    closed["close_reason"] = reason
    closed["pnl_r"] = pnl_r
    closed["pnl_usd"] = pnl_usd
    closed["balance_after"] = balance_after
    closed["duration_s"] = int((closed.get("close_ts") - int(trade.get("open_ts") or closed.get("close_ts"))) / 1000)
    closed["why_closed"] = close_reason
    return closed


def write_open_state(open_slots: dict) -> None:
    payload = {
        "generated_at": time.time(),
        "open_count": sum(1 for v in open_slots.values() if v),
        "slots": open_slots,
    }
    OPEN_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def update():
    reporter = load_telegram_reporter()
    zone = read_json(ZONE_FILE)
    structure_1m = read_jsonl_tail1(SRC / "structure_1m.jsonl")
    structure_15m = read_jsonl_tail1(SRC / "structure_15m.jsonl")
    signals = read_signals()
    price = current_price(zone)
    open_slots = read_json(OPEN_FILE).get("slots") or dict(SETUP_SLOTS)

    # Close open trades
    for slot_name, trade in list(open_slots.items()):
        if not isinstance(trade, dict):
            continue
        if trade.get("status") != "open":
            continue
        direction = trade.get("direction")
        sl = safe_float(trade.get("sl"))
        tp1 = safe_float(trade.get("tp1"))
        tp2 = safe_float(trade.get("tp2"))
        if direction == "short":
            if price >= sl:
                closed = close_trade(trade, price, "sl_hit", f"SL: {trade.get('setup_type')} — fiyat {sl:.1f} üstüne çıktı")
            elif tp2 > 0 and price <= tp2:
                closed = close_trade(trade, price, "tp2_hit", "TP2: Uzatılmış hedef")
            elif price <= tp1:
                target_name = "likidasyon cluster"
                closed = close_trade(trade, price, "tp1_hit", f"TP1: {target_name} hedefine ulaşıldı")
            else:
                closed = {}
        else:
            if price <= sl:
                closed = close_trade(trade, price, "sl_hit", f"SL: {trade.get('setup_type')} — fiyat {sl:.1f} altına düştü")
            elif tp2 > 0 and price >= tp2:
                closed = close_trade(trade, price, "tp2_hit", "TP2: Uzatılmış hedef")
            elif price >= tp1:
                target_name = "likidasyon cluster"
                closed = close_trade(trade, price, "tp1_hit", f"TP1: {target_name} hedefine ulaşıldı")
            else:
                closed = {}
        if closed:
            with ICT_TRADES_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(closed, ensure_ascii=False) + "\n")
            with TRADES_ALL_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(closed, ensure_ascii=False) + "\n")
            open_slots[slot_name] = None
            try:
                if reporter and hasattr(reporter, "notify_trade_close"):
                    reporter.notify_trade_close(closed)
            except Exception:
                pass

    # Open new trades
    for slot_name in SETUP_SLOTS:
        if open_slots.get(slot_name):
            continue
        trade = make_trade_setup(zone, structure_1m, structure_15m, signals, slot_name)
        if trade:
            persist_trade_open(trade, slot_name, open_slots)
            try:
                if reporter and hasattr(reporter, "notify_trade_open"):
                    reporter.notify_trade_open(trade)
            except Exception:
                pass

    write_open_state(open_slots)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if not OPEN_FILE.exists():
        write_open_state(dict(SETUP_SLOTS))
    while True:
        try:
            update()
        except Exception:
            pass
        time.sleep(1)


if __name__ == "__main__":
    main()
