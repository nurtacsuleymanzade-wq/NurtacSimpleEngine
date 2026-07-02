#!/usr/bin/env python3
import json
import subprocess
import time
import uuid
from pathlib import Path

SRC = Path("/root/NurtacCoreEngineClaude/data")
OUT = Path("/root/NurtacSimpleEngine/data")
STATE_FILE = OUT / "signal_observer_state.json"
OBS_FILE = OUT / "signal_observations.jsonl"
ZONE_FILE = OUT / "zone_v2.json"

SOURCES = {
    "initiative": SRC / "labels_initiative_flow.jsonl",
    "absorption": SRC / "labels_absorption.jsonl",
    "exhaustion": SRC / "labels_exhaustion.jsonl",
    "sweep": SRC / "labels_sweep.jsonl",
    "trapped": SRC / "labels_trapped_trader.jsonl",
}

OUTCOME_DELAYS_MS = [60000, 300000, 600000, 1800000]


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


def _tail_last_lines(path: Path, n: int = 300) -> list[dict]:
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


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_ts": [], "open": {}, "last_prices": [], "last_signal": {}}


def _save_state(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _price() -> float:
    rec = read_jsonl_tail1(SRC / "combined_1s_dna_btcusdt.jsonl")
    cdna = rec.get("candle_dna") or {}
    close = cdna.get("close")
    if isinstance(close, dict):
        p = close.get("price")
        if p is not None:
            return float(p)
    p = cdna.get("last_trade_price")
    return float(p or 0)


def _best_recent(path: Path, window_s: int = 30) -> dict:
    rows = _tail_last_lines(path, 120)
    now_ms = int(time.time() * 1000)
    window_ms = window_s * 1000
    best = {}
    best_score = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = row.get("label")
        if label == "none":
            continue
        ts = row.get("window_start_ts")
        try:
            ts_v = int(ts or 0)
        except Exception:
            continue
        if now_ms - ts_v > window_ms:
            continue
        try:
            score = float(row.get("score") or 0)
        except Exception:
            score = 0.0
        if best_score is None or score > best_score or (score == best_score and ts_v > int(best.get("window_start_ts") or 0)):
            best = row
            best_score = score
    return best


def _map_signal(detector: str, rec: dict) -> dict:
    direction = rec.get("direction")
    label = rec.get("label")
    score = float(rec.get("score") or 0)
    if detector == "initiative":
        signal_type = "initiative"
        signal_label = label or "initiative_signal"
        signal_direction = direction
        bias = "long_bias" if direction == "buy_initiative" else "short_bias" if direction == "sell_initiative" else None
    elif detector == "absorption":
        signal_type = "absorption"
        signal_label = label or "absorption_candidate"
        signal_direction = direction
        if direction == "buy_absorbed":
            bias = "short_bias"
        elif direction == "sell_absorbed":
            bias = "long_bias"
        else:
            bias = None
    elif detector == "exhaustion":
        signal_type = "exhaustion"
        signal_label = label or "exhaustion_candidate"
        signal_direction = direction
        if direction == "buy_exhaustion":
            bias = "short_bias"
        elif direction == "sell_exhaustion":
            bias = "long_bias"
        else:
            bias = None
    elif detector == "sweep":
        signal_type = "sweep"
        signal_label = label or "sweep_candidate"
        signal_direction = direction
        if direction == "upward_sweep":
            bias = "short_bias"
        elif direction == "downward_sweep":
            bias = "long_bias"
        else:
            bias = None
    else:
        signal_type = "trapped"
        signal_label = label or "trapped_candidate"
        signal_direction = direction
        if label == "long_trapped":
            bias = "short_bias"
        elif label == "short_trapped":
            bias = "long_bias"
        else:
            bias = None
    return {
        "signal_type": signal_type,
        "signal_label": signal_label,
        "signal_direction": signal_direction,
        "signal_bias": bias,
        "signal_score": score,
        "signal_ts": int(rec.get("window_start_ts") or 0),
    }


def _context():
    zone = read_json(ZONE_FILE)
    structure_1m = read_jsonl_tail1(SRC / "structure_1m.jsonl")
    trend_1m = ((structure_1m.get("trend") or {}).get("direction")) or "unknown"
    liquidation = zone.get("liquidation") or {}
    return {
        "price_location": zone.get("price_location"),
        "premium_discount": zone.get("premium_discount"),
        "vp_shape": zone.get("vp_shape"),
        "trend_1m": trend_1m,
        "poc": zone.get("poc"),
        "vah": zone.get("vah"),
        "val": zone.get("val"),
        "cascade_risk": liquidation.get("cascade_risk"),
        "nearest_supply_ob": zone.get("nearest_supply_ob"),
        "nearest_demand_ob": zone.get("nearest_demand_ob"),
    }


def _current_outcome(price_at_signal: float, current_price: float, signal_bias: str) -> str:
    if signal_bias == "long_bias":
        return "up" if current_price > price_at_signal else "down" if current_price < price_at_signal else "flat"
    if signal_bias == "short_bias":
        return "down" if current_price < price_at_signal else "up" if current_price > price_at_signal else "flat"
    return "flat"


def _signal_correct(price_at_signal: float, current_price: float, signal_bias: str):
    if signal_bias == "long_bias":
        if current_price > price_at_signal:
            return True
        if current_price < price_at_signal:
            return False
    if signal_bias == "short_bias":
        if current_price < price_at_signal:
            return True
        if current_price > price_at_signal:
            return False
    return None


def _update_memory_entry(mem: dict, price: float, bias: str) -> None:
    entry = float(mem.get("price_at_signal") or 0)
    if entry <= 0 or price <= 0:
        return
    delta = (price - entry) / entry * 100.0
    if bias == "long_bias":
        mfe = mem.get("mfe")
        mae = mem.get("mae")
        mem["mfe"] = delta if mfe is None else max(float(mfe), delta)
        mem["mae"] = delta if mae is None else min(float(mae), delta)
    elif bias == "short_bias":
        short_delta = (entry - price) / entry * 100.0
        adverse = (price - entry) / entry * 100.0
        mfe = mem.get("mfe")
        mae = mem.get("mae")
        mem["mfe"] = short_delta if mfe is None else max(float(mfe), short_delta)
        mem["mae"] = adverse if mae is None else min(float(mae), adverse)


def _build_record(detector: str, rec: dict, current_price: float, state: dict) -> dict:
    mapped = _map_signal(detector, rec)
    obs_id = uuid.uuid4().hex[:8]
    concurrent = {
        "initiative": None,
        "absorption": None,
        "exhaustion": None,
        "sweep": None,
        "trapped": None,
    }
    for key, source in SOURCES.items():
        if key == detector:
            continue
        other = _best_recent(source)
        if other.get("window_start_ts") == mapped.get("signal_ts") and other.get("direction") is not None:
            concurrent[key] = other.get("direction")

    now_ms = int(time.time() * 1000)
    obs = {
        "obs_id": obs_id,
        "signal_type": mapped.get("signal_type"),
        "signal_label": mapped.get("signal_label"),
        "signal_direction": mapped.get("signal_direction"),
        "signal_bias": mapped.get("signal_bias"),
        "signal_score": mapped.get("signal_score"),
        "signal_ts": mapped.get("signal_ts"),
        "obs_created_ts": now_ms,
        "price_at_signal": current_price,
        "context": _context(),
        "concurrent_signals": concurrent,
        "outcomes": {
            "60s": None,
            "300s": None,
            "600s": None,
            "1800s": None,
        },
        "mfe": None,
        "mae": None,
        "status": "pending",
    }
    seen = state.get("seen_ts")
    seen.append(mapped.get("signal_ts"))
    if len(seen) > 500:
        state["seen_ts"] = seen[-500:]
    state.get("open")[obs_id] = {
        "obs_id": obs_id,
        "price_at_signal": current_price,
        "signal_bias": mapped.get("signal_bias"),
        "signal_ts": mapped.get("signal_ts"),
        "obs_created_ts": now_ms,
        "mfe": None,
        "mae": None,
        "last_written_status": "pending",
    }
    return obs


def _append_obs(obs: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with OBS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obs, ensure_ascii=False) + "\n")


def _update_open_observations(state: dict, current_price: float) -> None:
    now_ms = int(time.time() * 1000)
    open_map = state.get("open") or {}
    for obs_id, mem in list(open_map.items()):
        _update_memory_entry(mem, current_price, mem.get("signal_bias"))
        age = now_ms - int(mem.get("obs_created_ts") or now_ms)
        outcomes = {
            "60s": 60000,
            "300s": 300000,
            "600s": 600000,
            "1800s": 1800000,
        }
        latest = {
            "60s": None,
            "300s": None,
            "600s": None,
            "1800s": None,
        }
        status = "pending"
        completed = 0
        for key, delay in outcomes.items():
            if age >= delay:
                completed += 1
                move_pct = (current_price - float(mem.get("price_at_signal") or 0)) / float(mem.get("price_at_signal") or 1) * 100.0
                latest[key] = {
                    "ts": now_ms,
                    "price": current_price,
                    "move_pct": move_pct,
                    "direction": _current_outcome(float(mem.get("price_at_signal") or 0), current_price, mem.get("signal_bias")),
                    "signal_correct": _signal_correct(float(mem.get("price_at_signal") or 0), current_price, mem.get("signal_bias")),
                }
        if completed == 0:
            status = "pending"
        elif completed < 4:
            status = "partial"
        else:
            status = "complete"
        if status == "complete":
            obs = {
                "obs_id": obs_id,
                "signal_type": None,
                "signal_label": None,
                "signal_direction": None,
                "signal_bias": mem.get("signal_bias"),
                "signal_score": None,
                "signal_ts": mem.get("signal_ts"),
                "obs_created_ts": mem.get("obs_created_ts"),
                "price_at_signal": mem.get("price_at_signal"),
                "context": _context(),
                "concurrent_signals": {
                    "initiative": None,
                    "absorption": None,
                    "exhaustion": None,
                    "sweep": None,
                    "trapped": None,
                },
                "outcomes": latest,
                "mfe": mem.get("mfe"),
                "mae": mem.get("mae"),
                "status": status,
            }
            _append_obs(obs)
            open_map.pop(obs_id, None)
            continue
        if mem.get("last_written_status") != status:
            obs = {
                "obs_id": obs_id,
                "signal_type": None,
                "signal_label": None,
                "signal_direction": None,
                "signal_bias": mem.get("signal_bias"),
                "signal_score": None,
                "signal_ts": mem.get("signal_ts"),
                "obs_created_ts": mem.get("obs_created_ts"),
                "price_at_signal": mem.get("price_at_signal"),
                "context": _context(),
                "concurrent_signals": {
                    "initiative": None,
                    "absorption": None,
                    "exhaustion": None,
                    "sweep": None,
                    "trapped": None,
                },
                "outcomes": latest,
                "mfe": mem.get("mfe"),
                "mae": mem.get("mae"),
                "status": status,
            }
            _append_obs(obs)
            mem["last_written_status"] = status


def main():
    state = _load_state()
    while True:
        current_price = _price()
        for detector, path in SOURCES.items():
            rec = _best_recent(path)
            if not rec:
                continue
            signal_ts = int(rec.get("window_start_ts") or 0)
            if signal_ts <= 0:
                continue
            direction = rec.get("direction")
            last = state.get("last_signal", {}).get(detector)
            COOLDOWN_MS = 60000
            if last and last.get("direction") == direction and (signal_ts - int(last.get("ts") or 0)) < COOLDOWN_MS:
                continue
            if signal_ts in state.get("seen_ts"):
                continue
            obs = _build_record(detector, rec, current_price, state)
            state.setdefault("last_signal", {})[detector] = {"direction": direction, "ts": signal_ts}
            _append_obs(obs)
        _update_open_observations(state, current_price)
        _save_state(state)
        time.sleep(1)


if __name__ == "__main__":
    main()
