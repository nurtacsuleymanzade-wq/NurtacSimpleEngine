#!/usr/bin/env python3
import argparse
import json
import subprocess
import time
from pathlib import Path

SRC = Path("/root/NurtacCoreEngineClaude/data")
OUT = Path("/root/NurtacSimpleEngine/data")
TF_FILES = {
    "1S": "structure_1s.jsonl",
    "1M": "structure_1m.jsonl",
    "5M": "structure_5m.jsonl",
    "15M": "structure_15m.jsonl",
    "1H": "structure_1h.jsonl",
}


def read_jsonl_tail1(path: Path) -> dict:
    try:
        raw = subprocess.getoutput(f"tail -1 {path}")
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def read_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _px(v) -> float:
    if isinstance(v, dict):
        return float(v.get("price") or 0)
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _current_price() -> float:
    rec = read_jsonl_tail1(SRC / "combined_1s_dna_btcusdt.jsonl")
    cdna = rec.get("candle_dna") or {}
    close = cdna.get("close")
    price = _px(close)
    if price > 0:
        return price
    return _px(cdna.get("last_trade_price"))


def _inside_range(price: float, low, high) -> bool:
    try:
        low_v = float(low or 0)
        high_v = float(high or 0)
    except Exception:
        return False
    if low_v <= 0 or high_v <= 0:
        return False
    return low_v <= price <= high_v


def _mid(low, high) -> float:
    try:
        low_v = float(low or 0)
        high_v = float(high or 0)
    except Exception:
        return 0.0
    if low_v <= 0 or high_v <= 0:
        return 0.0
    return (low_v + high_v) / 2.0


def _distance_pct(price: float, zone_mid: float) -> float:
    if price <= 0 or zone_mid <= 0:
        return 999.0
    return abs(price - zone_mid) / price * 100.0


def _collect_tf_levels(price: float):
    multi_tf_obs = []
    multi_tf_fvgs = []
    obs_by_tf = []
    fvgs_by_tf = []
    for tf, fname in TF_FILES.items():
        rec = read_jsonl_tail1(SRC / fname)
        obs = rec.get("order_blocks") or []
        fvgs = rec.get("fvg") or []
        for ob in obs:
            if not isinstance(ob, dict):
                continue
            ob_high = ob.get("ob_high")
            ob_low = ob.get("ob_low")
            entry = {
                "tf": tf,
                "ob_type": ob.get("ob_type"),
                "ob_high": ob_high,
                "ob_low": ob_low,
                "status": ob.get("status"),
                "created_ts": ob.get("created_ts"),
                "price_inside": _inside_range(price, ob_low, ob_high),
            }
            multi_tf_obs.append(entry)
            obs_by_tf.append(entry)
        for fvg in fvgs:
            if not isinstance(fvg, dict):
                continue
            gap_high = fvg.get("gap_high")
            gap_low = fvg.get("gap_low")
            entry = {
                "tf": tf,
                "fvg_type": fvg.get("fvg_type"),
                "gap_high": gap_high,
                "gap_low": gap_low,
                "status": fvg.get("status"),
                "created_ts": fvg.get("created_ts"),
                "price_inside": _inside_range(price, gap_low, gap_high),
            }
            multi_tf_fvgs.append(entry)
            fvgs_by_tf.append(entry)
    return multi_tf_obs, multi_tf_fvgs, obs_by_tf, fvgs_by_tf


def _volume_profile():
    vp = read_json(SRC / "volume_profile.json")
    poc = vp.get("poc_price")
    vah = vp.get("vah")
    val = vp.get("val")
    return vp, poc, vah, val


def _price_location(price: float, obs_by_tf: list[dict], fvgs_by_tf: list[dict], poc, vah, val):
    tf_order = ["1H", "15M", "5M", "1M", "1S"]
    for tf in tf_order:
        for ob in obs_by_tf:
            if ob.get("tf") != tf:
                continue
            if ob.get("status") != "active":
                continue
            if not ob.get("price_inside"):
                continue
            if ob.get("ob_type") == "bullish_ob":
                return "in_demand_" + tf.lower()
            if ob.get("ob_type") == "bearish_ob":
                return "in_supply_" + tf.lower()
    for fvg in fvgs_by_tf:
        if fvg.get("status") != "active":
            continue
        if not fvg.get("price_inside"):
            continue
        if fvg.get("fvg_type") == "bearish_fvg":
            return "in_fvg_bearish"
        if fvg.get("fvg_type") == "bullish_fvg":
            return "in_fvg_bullish"

    at_vah = abs(price - float(vah or 0)) / price < 0.001 if price > 0 and float(vah or 0) > 0 else False
    at_val = abs(price - float(val or 0)) / price < 0.001 if price > 0 and float(val or 0) > 0 else False
    at_poc = abs(price - float(poc or 0)) / price < 0.001 if price > 0 and float(poc or 0) > 0 else False

    if float(vah or 0) > 0 and price > float(vah or 0) and not at_vah:
        return "above_vah"
    if at_vah:
        return "at_vah"
    if float(val or 0) > 0 and float(vah or 0) > 0 and float(val) < price < float(vah):
        return "in_value_area"
    if at_val:
        return "at_val"
    if float(val or 0) > 0 and price < float(val or 0) and not at_val:
        return "below_val"
    if float(poc or 0) > 0 and price > float(poc or 0):
        return "above_poc"
    if float(poc or 0) > 0 and price <= float(poc or 0):
        return "below_poc"
    return "neutral"


def _premium_discount(price_location: str) -> str:
    premium = [
        "above_vah",
        "at_vah",
        "in_supply_1h",
        "in_supply_15m",
        "in_supply_5m",
        "in_supply_1m",
        "in_supply_1s",
    ]
    discount = [
        "below_val",
        "at_val",
        "in_demand_1h",
        "in_demand_15m",
        "in_demand_5m",
        "in_demand_1m",
        "in_demand_1s",
    ]
    equilibrium = [
        "in_value_area",
        "above_poc",
        "below_poc",
        "in_fvg_bearish",
        "in_fvg_bullish",
        "at_poc",
    ]
    if price_location in premium:
        return "premium"
    if price_location in discount:
        return "discount"
    if price_location in equilibrium:
        return "equilibrium"
    return "neutral"


def _vp_shape(poc, vah, val) -> str:
    try:
        poc_v = float(poc or 0)
        vah_v = float(vah or 0)
        val_v = float(val or 0)
    except Exception:
        return "unknown"
    va_range = vah_v - val_v
    if va_range <= 0:
        return "unknown"
    poc_position = (poc_v - val_v) / va_range
    if 0.40 <= poc_position <= 0.60:
        return "D"
    if poc_position < 0.35:
        return "P"
    if poc_position > 0.65:
        return "b"
    return "B"


def _nearest_supply_demand_fvg(price: float, obs_by_tf: list[dict], fvgs_by_tf: list[dict]):
    nearest_supply_ob = None
    nearest_demand_ob = None
    nearest_active_fvg = None
    best_supply = None
    best_demand = None
    best_fvg = None

    for ob in obs_by_tf:
        if ob.get("status") != "active":
            continue
        ob_mid = _mid(ob.get("ob_low"), ob.get("ob_high"))
        dist = _distance_pct(price, ob_mid)
        base = {
            "tf": ob.get("tf"),
            "ob_high": ob.get("ob_high"),
            "ob_low": ob.get("ob_low"),
            "distance_pct": dist,
            "price_inside": ob.get("price_inside"),
        }
        if ob.get("ob_type") == "bearish_ob":
            if best_supply is None or dist < best_supply:
                best_supply = dist
                nearest_supply_ob = base
        if ob.get("ob_type") == "bullish_ob":
            if best_demand is None or dist < best_demand:
                best_demand = dist
                nearest_demand_ob = base

    for fvg in fvgs_by_tf:
        if fvg.get("status") != "active":
            continue
        fvg_mid = _mid(fvg.get("gap_low"), fvg.get("gap_high"))
        dist = _distance_pct(price, fvg_mid)
        base = {
            "tf": fvg.get("tf"),
            "fvg_type": fvg.get("fvg_type"),
            "gap_high": fvg.get("gap_high"),
            "gap_low": fvg.get("gap_low"),
            "price_inside": fvg.get("price_inside"),
        }
        if best_fvg is None or dist < best_fvg:
            best_fvg = dist
            nearest_active_fvg = base

    return nearest_supply_ob, nearest_demand_ob, nearest_active_fvg


def _liq_and_orderbook():
    liq = read_jsonl_tail1(SRC / "liquidation_clusters.jsonl")
    walls = read_jsonl_tail1(SRC / "orderbook_walls.jsonl")
    cascade_risk = liq.get("cascade_risk", "UNKNOWN")
    nearby_long = liq.get("nearby_long_clusters") or []
    nearby_short = liq.get("nearby_short_clusters") or []
    nearest_long = nearby_long[0] if nearby_long else None
    nearest_short = nearby_short[0] if nearby_short else None
    return liq, walls, cascade_risk, nearest_long, nearest_short


def build_zone_v2() -> None:
    price = _current_price()
    if price <= 0:
        return

    multi_tf_obs, multi_tf_fvgs, obs_by_tf, fvgs_by_tf = _collect_tf_levels(price)
    vp, poc, vah, val = _volume_profile()
    price_location = _price_location(price, obs_by_tf, fvgs_by_tf, poc, vah, val)
    premium_discount = _premium_discount(price_location)
    vp_shape = _vp_shape(poc, vah, val)
    nearest_supply_ob, nearest_demand_ob, nearest_active_fvg = _nearest_supply_demand_fvg(price, obs_by_tf, fvgs_by_tf)
    liq, walls, cascade_risk, nearest_long, nearest_short = _liq_and_orderbook()

    out = {
        "engine": "zone_engine_v2",
        "ts": int(time.time() * 1000),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "current_price": price,
        "price_location": price_location,
        "premium_discount": premium_discount,
        "vp_shape": vp_shape,
        "poc": poc if poc is not None else None,
        "vah": vah if vah is not None else None,
        "val": val if val is not None else None,
        "multi_tf_obs": multi_tf_obs,
        "multi_tf_fvgs": multi_tf_fvgs,
        "nearest_supply_ob": nearest_supply_ob,
        "nearest_demand_ob": nearest_demand_ob,
        "nearest_active_fvg": nearest_active_fvg,
        "liquidation": {
            "cascade_risk": cascade_risk,
            "nearest_long_liq": None if not isinstance(nearest_long, dict) else {
                "price": nearest_long.get("price"),
                "usd_at_risk": nearest_long.get("usd_at_risk"),
                "intensity_label": nearest_long.get("intensity_label"),
            },
            "nearest_short_liq": None if not isinstance(nearest_short, dict) else {
                "price": nearest_short.get("price"),
                "usd_at_risk": nearest_short.get("usd_at_risk"),
                "intensity_label": nearest_short.get("intensity_label"),
            },
        },
        "orderbook": {
            "nearest_bid_wall": walls.get("nearest_bid_wall"),
            "nearest_ask_wall": walls.get("nearest_ask_wall"),
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "zone_v2.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def run_live() -> None:
    while True:
        build_zone_v2()
        time.sleep(30)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["once", "live"], default="live")
    args = p.parse_args()
    if args.mode == "once":
        build_zone_v2()
    else:
        run_live()


if __name__ == "__main__":
    main()
