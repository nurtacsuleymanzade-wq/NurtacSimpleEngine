#!/usr/bin/env python3
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path("/root/NurtacSimpleEngine")
DATA_DIR = BASE_DIR / "data"
ENV_FILE = BASE_DIR / ".env"
TRADES_FILE = DATA_DIR / "trades_all.jsonl"
BALANCE_FILE = DATA_DIR / "balance.json"
STRUCTURE_1M_FILE = Path("/root/NurtacCoreEngineClaude/data/structure_1m.jsonl")

UTC4 = timezone(timedelta(hours=4))
SIGNALS_TOKEN = None
SIGNALS_CHAT_ID = None
SUMMARY_TOKEN = None
SUMMARY_CHAT_ID = None


def _load_env():
    global SIGNALS_TOKEN, SIGNALS_CHAT_ID, SUMMARY_TOKEN, SUMMARY_CHAT_ID
    try:
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("TELEGRAM_SIGNALS_TOKEN="):
                SIGNALS_TOKEN = line.split("=", 1)[1].strip() or None
            elif line.startswith("TELEGRAM_SIGNALS_CHAT_ID="):
                SIGNALS_CHAT_ID = line.split("=", 1)[1].strip() or None
            elif line.startswith("TELEGRAM_SUMMARY_TOKEN="):
                SUMMARY_TOKEN = line.split("=", 1)[1].strip() or None
            elif line.startswith("TELEGRAM_SUMMARY_CHAT_ID="):
                SUMMARY_CHAT_ID = line.split("=", 1)[1].strip() or None
    except Exception:
        pass


def _send(token: str | None, chat_id: str | None, text: str) -> bool:
    if not token or not chat_id:
        print(text, flush=True)
        return False
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TELEGRAM] {e}", flush=True)
        return False


def send_signal(text: str) -> bool:
    _load_env()
    return _send(SIGNALS_TOKEN, SIGNALS_CHAT_ID, text)


def send_summary(text: str) -> bool:
    _load_env()
    return _send(SUMMARY_TOKEN, SUMMARY_CHAT_ID, text)


def _tail_jsonl(path: Path, n: int = 3000) -> list[dict]:
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


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _now_utc4() -> str:
    return datetime.now(UTC4).strftime("%H:%M")


def _trend_1m() -> str:
    rec = _tail_jsonl(STRUCTURE_1M_FILE, 1)
    if not rec:
        return "unknown"
    trend = rec[-1].get("trend") or {}
    return trend.get("direction") or "unknown"


def _pnl_summary(trades: list[dict]) -> tuple[int, int, int, int, float, float]:
    opened = 0
    closed = 0
    tp_count = 0
    sl_count = 0
    tp_r = 0.0
    sl_r = 0.0
    for trade in trades:
        if trade.get("open_ts") and int(trade.get("open_ts") or 0) >= int(time.time() * 1000) - 900000:
            opened += 1
        if trade.get("close_ts") and int(trade.get("close_ts") or 0) >= int(time.time() * 1000) - 900000:
            closed += 1
            pnl_r = float(trade.get("pnl_r") or 0)
            if trade.get("outcome") == "win":
                tp_count += 1
                tp_r += pnl_r
            elif trade.get("outcome") == "loss":
                sl_count += 1
                sl_r += pnl_r
    return opened, closed, tp_count, sl_count, tp_r, sl_r


def notify_trade_open(trade: dict) -> bool:
    side = "LONG" if trade.get("direction") == "long" else "SHORT"
    emoji = "🟢" if trade.get("direction") == "long" else "🔴"
    tp2 = trade.get("tp2")
    now = _now_utc4()
    text = (
        f"{emoji} {side} AÇILDI\n"
        f"🆔 ID: {trade.get('trade_id')}\n"
        f"📡 Sinyal: {trade.get('signal_type')} ({trade.get('signal_label')})\n"
        f"⏰ Saat: {now} UTC+4\n"
        f"📊 TF: {trade.get('timeframe')}\n"
        f"💰 Entry: {trade.get('entry')}\n"
        f"🛑 SL: {trade.get('sl')}\n"
        f"🎯 TP1: {trade.get('tp1')}\n"
        f"🎯 TP2: {tp2 if tp2 is not None else 'n/a'}\n"
        f"⚖️ Risk: ${float(trade.get('risk_usd') or 0):.2f}\n"
        f"🏆 RR: 1:{trade.get('rr')}\n"
        f"📈 Kaldıraç: {float(trade.get('leverage') or 0):.1f}x\n"
        f"📍 Lokasyon: {trade.get('price_location')} ({trade.get('premium_discount')})"
    )
    return send_signal(text)


def notify_trade_close(trade: dict) -> bool:
    emoji = "✅" if trade.get("outcome") == "win" else "❌"
    label = "TP HIT" if trade.get("outcome") == "win" else "SL HIT"
    close_ts = int(trade.get("close_ts") or 0)
    close_time = datetime.fromtimestamp(close_ts / 1000, UTC4).strftime("%H:%M") if close_ts else _now_utc4()
    duration_s = int(trade.get("duration_s") or 0)
    text = (
        f"{emoji} {label}\n"
        f"🆔 ID: {trade.get('trade_id')}\n"
        f"📡 Sinyal: {trade.get('signal_type')}\n"
        f"⏰ Kapanış: {close_time} UTC+4\n"
        f"💵 PnL: {float(trade.get('pnl_r') or 0):+.2f}R / ${float(trade.get('pnl_usd') or 0):+.2f}\n"
        f"⏱️ Süre: {duration_s//60}dk {duration_s%60}s\n"
        f"💰 Bakiye: ${float(trade.get('balance_after') or 0):.2f}\n"
        f"📝 Sebep: {trade.get('why_closed')}"
    )
    return send_signal(text)


def _summary_text() -> str:
    trades = _tail_jsonl(TRADES_FILE, 5000)
    recent = [t for t in trades if int(t.get("open_ts") or 0) >= int(time.time() * 1000) - 900000 or int(t.get("close_ts") or 0) >= int(time.time() * 1000) - 900000]
    balance = _read_json(BALANCE_FILE)
    opened, closed, tp_count, sl_count, tp_r, sl_r = _pnl_summary(recent)
    total_trades = int(balance.get("closed_trades") or 0)
    wr = float(balance.get("win_rate") or 0)
    pf = float(balance.get("profit_factor") or 0)
    avg_r = float(balance.get("avg_pnl_r") or 0)
    current_balance = float(balance.get("current_balance") or 0)
    initial_balance = float(balance.get("initial_balance") or 500.0)
    pnl_pct = ((current_balance - initial_balance) / initial_balance * 100.0) if initial_balance > 0 else 0.0
    trend = _trend_1m()
    if not recent:
        return (
            f"📊 15 DAKİKA RAPORU\n"
            f"⏰ {_now_utc4()} UTC+4\n"
            f"📉 Trend: {trend}\n"
            f"Son 15dk: Trade yok\n"
            f"Genel: {total_trades} trade | WR:{wr:.0f}% | ${current_balance:.2f}"
        )
    by_engine = balance.get("by_engine") or {}
    def engine_wr(name: str) -> float:
        e = by_engine.get(name) or {}
        trades_n = int(e.get("trades") or 0)
        wins_n = int(e.get("wins") or 0)
        return (wins_n / trades_n * 100.0) if trades_n > 0 else 0.0
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 15 DAKİKA RAPORU\n"
        f"⏰ {_now_utc4()} UTC+4\n"
        f"📉 Trend: {trend}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Son 15 Dakika:\n"
        f"🟢 Açılan:   {opened}\n"
        f"🔴 Kapanan:  {closed}\n"
        f"✅ TP:       {tp_count}  ({tp_r:+.2f}R)\n"
        f"❌ SL:       {sl_count}  ({sl_r:+.2f}R)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Engine Performansı:\n"
        f"ICT:     {int((by_engine.get('ict') or {}).get('trades') or 0)} trade | WR:{engine_wr('ict'):.0f}%\n"
        f"LIQ:     {int((by_engine.get('liq') or {}).get('trades') or 0)} trade | WR:{engine_wr('liq'):.0f}%\n"
        f"AUCTION: {int((by_engine.get('auction') or {}).get('trades') or 0)} trade | WR:{engine_wr('auction'):.0f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Genel (Başlangıçtan):\n"
        f"Toplam: {total_trades} | WR: {wr:.0f}%\n"
        f"PF: {pf:.2f} | AvgR: {avg_r:+.2f}R\n"
        f"Bakiye: ${initial_balance:.0f} → ${current_balance:.2f} ({pnl_pct:+.1f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def summary_loop():
    last_sent_slot = None
    while True:
        slot = int(time.time() // 900)
        if slot != last_sent_slot:
            send_summary(_summary_text())
            last_sent_slot = slot
        time.sleep(5)


if __name__ == "__main__":
    summary_loop()
