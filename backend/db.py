"""SQLite persistence for detection history and scanner config."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Database file in project root
DB_PATH = Path(__file__).resolve().parent.parent / "detection_history.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create detection_history and scanner_config tables if not exists."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS detection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                tf TEXT NOT NULL,
                bar_time INTEGER NOT NULL,
                pattern TEXT NOT NULL,
                score REAL,
                last_price REAL,
                UNIQUE(symbol, tf, bar_time)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scanner_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                config_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def save_detection(signal: Dict[str, Any]) -> None:
    """
    Insert one detection. Uses INSERT OR IGNORE so the same candle
    (symbol, tf, bar_time) is only stored once.
    """
    detected_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    symbol = signal.get("symbol", "")
    tf = signal.get("tf", "")
    bar_time = signal.get("bar_time") or signal.get("timestamp") or 0
    if isinstance(bar_time, float):
        bar_time = int(bar_time)
    pattern = signal.get("pattern", "")
    score = signal.get("score")
    last_price = signal.get("last_price")

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO detection_history
            (detected_at, symbol, tf, bar_time, pattern, score, last_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (detected_at, symbol, tf, bar_time, pattern, score, last_price),
        )
        conn.commit()
    except Exception as e:
        logger.exception("save_detection failed: %s", e)
    finally:
        conn.close()


def save_detections(signals: List[Dict[str, Any]]) -> None:
    """Save each signal; duplicates (same symbol, tf, bar_time) are ignored."""
    for s in signals:
        save_detection(s)


def load_config_from_db() -> Optional[Dict[str, Any]]:
    """Load scanner config from DB. Returns None if no row or invalid JSON."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT config_json FROM scanner_config WHERE id = 1").fetchone()
        if row is None:
            return None
        return json.loads(row["config_json"])
    except Exception as e:
        logger.debug("load_config_from_db: %s", e)
        return None
    finally:
        conn.close()


def save_config_to_db(config: Dict[str, Any]) -> None:
    """Save full config to DB (overwrite)."""
    conn = get_connection()
    try:
        updated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR REPLACE INTO scanner_config (id, config_json, updated_at) VALUES (1, ?, ?)",
            (json.dumps(config), updated_at),
        )
        conn.commit()
    except Exception as e:
        logger.exception("save_config_to_db failed: %s", e)
        raise
    finally:
        conn.close()


def get_history(limit: int = 100, symbol: Optional[str] = None, tf: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return recent rows, optionally filtered by symbol and/or tf."""
    conn = get_connection()
    try:
        sql = "SELECT id, detected_at, symbol, tf, bar_time, pattern, score, last_price FROM detection_history WHERE 1=1"
        params: List[Any] = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if tf:
            sql += " AND tf = ?"
            params.append(tf)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
