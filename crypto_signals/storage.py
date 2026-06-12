"""Veri repository — SQLite (yerel/dev) veya PostgreSQL (`DATABASE_URL` varsa).

Render'ın diski ephemeral olduğundan üretimde SQLite dosyası her deploy'da
silinir; bu yüzden `DATABASE_URL` tanımlıysa kalıcı PostgreSQL kullanılır.
Arayüz (metotlar) iki backend için de aynıdır.

Thread-safe: tek bağlantı + kilit (scheduler ayrı thread'de çalışır). Düşük
trafikli bir bot için bu serileştirme yeterli; PostgreSQL'de bağlantı düşerse
otomatik yeniden bağlanılır.
"""
from __future__ import annotations

import os
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
    def __init__(self, db_path: str = "crypto_signals.db", database_url: Optional[str] = None):
        self._lock = threading.Lock()
        url = (database_url or os.getenv("DATABASE_URL") or "").strip()
        self.is_pg = url.startswith("postgres://") or url.startswith("postgresql://")
        self._url = url
        self._db_path = db_path
        if self.is_pg:
            import psycopg2
            import psycopg2.extras

            self._pg = psycopg2
            self._pg_extras = psycopg2.extras
        self._connect()
        self._init_schema()

    # --- bağlantı ---------------------------------------------------------
    def _connect(self) -> None:
        if self.is_pg:
            self._conn = self._pg.connect(
                self._url,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
            self._conn.autocommit = True
        else:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    def _cursor(self):
        if self.is_pg:
            return self._conn.cursor(cursor_factory=self._pg_extras.RealDictCursor)
        return self._conn.cursor()

    def _exec(self, sql: str, params=(), *, fetch: Optional[str] = None):
        """SQL çalıştır; `?` yer tutucularını PG için `%s`'e çevirir.

        PG bağlantısı düşerse bir kez yeniden bağlanıp tekrar dener.
        """
        query = sql.replace("?", "%s") if self.is_pg else sql
        with self._lock:
            try:
                cur = self._cursor()
                cur.execute(query, params)
            except Exception:
                if not self.is_pg:
                    raise
                self._connect()  # tek seferlik yeniden bağlanma
                cur = self._cursor()
                cur.execute(query, params)
            rows = None
            if fetch == "one":
                rows = cur.fetchone()
            elif fetch == "all":
                rows = cur.fetchall()
            rowcount = cur.rowcount
            if not self.is_pg:
                self._conn.commit()
            cur.close()
            return rows, rowcount

    def _init_schema(self) -> None:
        int_type = "BIGINT" if self.is_pg else "INTEGER"
        real = "DOUBLE PRECISION" if self.is_pg else "REAL"
        statements = [
            f"CREATE TABLE IF NOT EXISTS subscribers ("
            f"chat_id {int_type} PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1)",
            f"CREATE TABLE IF NOT EXISTS watchlist ("
            f"chat_id {int_type} NOT NULL, symbol TEXT NOT NULL, "
            f"UNIQUE(chat_id, symbol))",
            f"CREATE TABLE IF NOT EXISTS open_signals ("
            f"symbol TEXT PRIMARY KEY, entry_price {real} NOT NULL, "
            f"rating TEXT NOT NULL, score {real} NOT NULL, created_at {real} NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS snapshots ("
            f"symbol TEXT PRIMARY KEY, score {real} NOT NULL, rating TEXT NOT NULL, "
            f"price {real} NOT NULL, updated_at {real} NOT NULL)",
        ]
        for stmt in statements:
            self._exec(stmt)

    # --- aboneler ---------------------------------------------------------
    def add_subscriber(self, chat_id: int) -> None:
        self._exec(
            "INSERT INTO subscribers(chat_id, active) VALUES(?, 1) "
            "ON CONFLICT(chat_id) DO UPDATE SET active=1",
            (chat_id,),
        )

    def deactivate_subscriber(self, chat_id: int) -> None:
        self._exec("UPDATE subscribers SET active=0 WHERE chat_id=?", (chat_id,))

    def active_subscribers(self) -> List[int]:
        rows, _ = self._exec(
            "SELECT chat_id FROM subscribers WHERE active=1", fetch="all"
        )
        return [r["chat_id"] for r in rows]

    # --- watchlist --------------------------------------------------------
    def add_watch(self, chat_id: int, symbol: str) -> None:
        self._exec(
            "INSERT INTO watchlist(chat_id, symbol) VALUES(?, ?) "
            "ON CONFLICT(chat_id, symbol) DO NOTHING",
            (chat_id, symbol.upper()),
        )

    def remove_watch(self, chat_id: int, symbol: str) -> bool:
        _, rowcount = self._exec(
            "DELETE FROM watchlist WHERE chat_id=? AND symbol=?",
            (chat_id, symbol.upper()),
        )
        return rowcount > 0

    def list_watch(self, chat_id: int) -> List[str]:
        rows, _ = self._exec(
            "SELECT symbol FROM watchlist WHERE chat_id=? ORDER BY symbol",
            (chat_id,),
            fetch="all",
        )
        return [r["symbol"] for r in rows]

    def all_watched_symbols(self) -> List[str]:
        rows, _ = self._exec("SELECT DISTINCT symbol FROM watchlist", fetch="all")
        return [r["symbol"] for r in rows]

    def subscribers_watching(self, symbol: str) -> List[int]:
        rows, _ = self._exec(
            "SELECT chat_id FROM watchlist WHERE symbol=?",
            (symbol.upper(),),
            fetch="all",
        )
        return [r["chat_id"] for r in rows]

    # --- açık sinyaller ---------------------------------------------------
    def upsert_open_signal(
        self, symbol: str, entry_price: float, rating: str, score: float
    ) -> None:
        self._exec(
            "INSERT INTO open_signals(symbol, entry_price, rating, score, created_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
            "rating=excluded.rating, score=excluded.score",
            (symbol.upper(), entry_price, rating, score, time.time()),
        )

    def get_open_signal(self, symbol: str) -> Optional[OpenSignal]:
        row, _ = self._exec(
            "SELECT * FROM open_signals WHERE symbol=?",
            (symbol.upper(),),
            fetch="one",
        )
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
        self._exec("DELETE FROM open_signals WHERE symbol=?", (symbol.upper(),))

    def list_open_signals(self) -> List[OpenSignal]:
        rows, _ = self._exec(
            "SELECT * FROM open_signals ORDER BY score DESC", fetch="all"
        )
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
        self._exec(
            "INSERT INTO snapshots(symbol, score, rating, price, updated_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
            "score=excluded.score, rating=excluded.rating, price=excluded.price, "
            "updated_at=excluded.updated_at",
            (symbol.upper(), score, rating, price, time.time()),
        )

    def top_snapshots(self, limit: int = 10) -> List[Snapshot]:
        rows, _ = self._exec(
            "SELECT * FROM snapshots ORDER BY score DESC LIMIT ?", (limit,), fetch="all"
        )
        return [self._snapshot(r) for r in rows]

    def snapshots_for(self, symbols: List[str]) -> List[Snapshot]:
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        rows, _ = self._exec(
            f"SELECT * FROM snapshots WHERE symbol IN ({placeholders}) ORDER BY score DESC",
            [s.upper() for s in symbols],
            fetch="all",
        )
        return [self._snapshot(r) for r in rows]

    @staticmethod
    def _snapshot(r) -> Snapshot:
        return Snapshot(
            symbol=r["symbol"],
            score=r["score"],
            rating=r["rating"],
            price=r["price"],
            updated_at=r["updated_at"],
        )

    def close(self) -> None:  # pragma: no cover
        with self._lock:
            self._conn.close()
