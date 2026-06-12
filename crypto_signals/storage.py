"""SQLite repository — abone, watchlist, snapshot ve açık sinyal state.

İnce bir arayüz; thread-safe (scheduler ayrı thread'de çalışır). Arayüz
sayesinde Postgres'e geçiş tek dosyalık iştir.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class OpenSignal:
    symbol: str
    entry_price: float
    rating: str
    score: float
    created_at: float


@dataclass
class Snapshot:
    symbol: str
    score: float
    rating: str
    price: float
    updated_at: float


class Storage:
    def __init__(self, path: str = "crypto_signals.db"):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id INTEGER PRIMARY KEY,
                    active  INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS watchlist (
                    chat_id INTEGER NOT NULL,
                    symbol  TEXT NOT NULL,
                    UNIQUE(chat_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS open_signals (
                    symbol      TEXT PRIMARY KEY,
                    entry_price REAL NOT NULL,
                    rating      TEXT NOT NULL,
                    score       REAL NOT NULL,
                    created_at  REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    symbol     TEXT PRIMARY KEY,
                    score      REAL NOT NULL,
                    rating     TEXT NOT NULL,
                    price      REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    # --- aboneler ---------------------------------------------------------
    def add_subscriber(self, chat_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO subscribers(chat_id, active) VALUES(?, 1) "
                "ON CONFLICT(chat_id) DO UPDATE SET active=1",
                (chat_id,),
            )

    def deactivate_subscriber(self, chat_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE subscribers SET active=0 WHERE chat_id=?", (chat_id,)
            )

    def active_subscribers(self) -> List[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id FROM subscribers WHERE active=1"
            ).fetchall()
        return [r["chat_id"] for r in rows]

    # --- watchlist --------------------------------------------------------
    def add_watch(self, chat_id: int, symbol: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO watchlist(chat_id, symbol) VALUES(?, ?)",
                (chat_id, symbol.upper()),
            )

    def remove_watch(self, chat_id: int, symbol: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM watchlist WHERE chat_id=? AND symbol=?",
                (chat_id, symbol.upper()),
            )
            return cur.rowcount > 0

    def list_watch(self, chat_id: int) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol FROM watchlist WHERE chat_id=? ORDER BY symbol",
                (chat_id,),
            ).fetchall()
        return [r["symbol"] for r in rows]

    def all_watched_symbols(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT symbol FROM watchlist"
            ).fetchall()
        return [r["symbol"] for r in rows]

    def subscribers_watching(self, symbol: str) -> List[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id FROM watchlist WHERE symbol=?", (symbol.upper(),)
            ).fetchall()
        return [r["chat_id"] for r in rows]

    # --- açık sinyaller ---------------------------------------------------
    def upsert_open_signal(
        self, symbol: str, entry_price: float, rating: str, score: float
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO open_signals(symbol, entry_price, rating, score, created_at) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                "rating=excluded.rating, score=excluded.score",
                (symbol.upper(), entry_price, rating, score, time.time()),
            )

    def get_open_signal(self, symbol: str) -> Optional[OpenSignal]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM open_signals WHERE symbol=?", (symbol.upper(),)
            ).fetchone()
        if not row:
            return None
        return OpenSignal(
            symbol=row["symbol"],
            entry_price=row["entry_price"],
            rating=row["rating"],
            score=row["score"],
            created_at=row["created_at"],
        )

    def delete_open_signal(self, symbol: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM open_signals WHERE symbol=?", (symbol.upper(),)
            )

    def list_open_signals(self) -> List[OpenSignal]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM open_signals ORDER BY score DESC"
            ).fetchall()
        return [
            OpenSignal(
                symbol=r["symbol"],
                entry_price=r["entry_price"],
                rating=r["rating"],
                score=r["score"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- snapshot'lar -----------------------------------------------------
    def upsert_snapshot(
        self, symbol: str, score: float, rating: str, price: float
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO snapshots(symbol, score, rating, price, updated_at) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                "score=excluded.score, rating=excluded.rating, price=excluded.price, "
                "updated_at=excluded.updated_at",
                (symbol.upper(), score, rating, price, time.time()),
            )

    def top_snapshots(self, limit: int = 10) -> List[Snapshot]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM snapshots ORDER BY score DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Snapshot(
                symbol=r["symbol"],
                score=r["score"],
                rating=r["rating"],
                price=r["price"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def snapshots_for(self, symbols: List[str]) -> List[Snapshot]:
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM snapshots WHERE symbol IN ({placeholders}) "
                "ORDER BY score DESC",
                [s.upper() for s in symbols],
            ).fetchall()
        return [
            Snapshot(
                symbol=r["symbol"],
                score=r["score"],
                rating=r["rating"],
                price=r["price"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def close(self) -> None:  # pragma: no cover
        with self._lock:
            self._conn.close()
