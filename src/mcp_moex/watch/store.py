"""Хранилище расписания в SQLite: схема, транзакции, запросы.

Модуль синхронный: вызовы короткие, сервис оборачивает их в `asyncio.to_thread`. Соединение открывается на
каждую операцию и закрывается: процесс сервера короткоживущий, а несколько процессов работают с одним файлом.
Времена в БД это целые секунды Unix (UTC). Файл создаётся при первой операции, а не при создании объекта.
"""

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..errors import WatchStoreUnavailable
from .intervals import format_seconds

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 10_000
RETENTION_SECONDS = 30 * 24 * 60 * 60
"""Замеры и сводки старше этого срока удаляются."""
REPORTS_PER_CALL = 100

_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    """CREATE TABLE watches (
        chat_id INTEGER PRIMARY KEY, secids TEXT NOT NULL, poll_seconds INTEGER NOT NULL,
        report_seconds INTEGER NOT NULL, poll_interval TEXT NOT NULL, report_interval TEXT NOT NULL, version INTEGER NOT NULL, started_at INTEGER NOT NULL,
        window_start INTEGER NOT NULL, next_poll_at INTEGER NOT NULL, next_report_at INTEGER NOT NULL)""",
    """CREATE TABLE observations (
        id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES watches(chat_id) ON DELETE CASCADE,
        version INTEGER NOT NULL, secid TEXT NOT NULL, observed_at INTEGER NOT NULL, ok INTEGER NOT NULL,
        price REAL, price_unit TEXT, price_source TEXT, quote_at INTEGER)""",
    """CREATE TABLE reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, period_start INTEGER NOT NULL,
        period_end INTEGER NOT NULL, created_at INTEGER NOT NULL, payload TEXT NOT NULL, delivered_at INTEGER)""",
    "CREATE INDEX observations_chat_time ON observations (chat_id, observed_at)",
    "CREATE INDEX reports_delivery ON reports (delivered_at, id)",
)


@dataclass(frozen=True)
class WatchRow:
    chat_id: int
    secids: tuple[str, ...]
    poll_seconds: int
    report_seconds: int
    poll_interval: str
    """Интервал в том виде, в каком принят (`15m`), для показа клиенту."""
    report_interval: str
    version: int
    started_at: int
    window_start: int
    """Нижняя граница текущего периода сводки, не включительно."""
    next_poll_at: int
    next_report_at: int

    @property
    def period_start(self) -> int:
        """Начало периода для показа: у первого периода это момент постановки, у следующих конец прошлой сводки."""
        return max(self.started_at, self.window_start)


@dataclass(frozen=True)
class Sample:
    secid: str
    observed_at: int
    ok: bool
    price: float | None = None
    price_unit: str | None = None
    price_source: str | None = None
    quote_at: int | None = None


@dataclass(frozen=True)
class Claim:
    """Заявленный опрос: тикеры и версия, с которой замеры будут записаны."""

    chat_id: int
    version: int
    secids: tuple[str, ...]


@dataclass(frozen=True)
class StoredReport:
    report_id: int
    chat_id: int
    payload: str


ReportBuilder = Callable[[WatchRow, list[Sample], int, int], str]
"""(опрос, замеры периода, начало, конец) -> JSON-текст сводки для хранения."""


def _watch(row: sqlite3.Row) -> WatchRow:
    return WatchRow(
        chat_id=row["chat_id"],
        secids=tuple(json.loads(row["secids"])),
        poll_seconds=row["poll_seconds"],
        report_seconds=row["report_seconds"],
        poll_interval=row["poll_interval"],
        report_interval=row["report_interval"],
        version=row["version"],
        started_at=row["started_at"],
        window_start=row["window_start"],
        next_poll_at=row["next_poll_at"],
        next_report_at=row["next_report_at"],
    )


def _sample(row: sqlite3.Row) -> Sample:
    return Sample(
        secid=row["secid"],
        observed_at=row["observed_at"],
        ok=bool(row["ok"]),
        price=row["price"],
        price_unit=row["price_unit"],
        price_source=row["price_source"],
        quote_at=row["quote_at"],
    )


def _insert_sample(conn: sqlite3.Connection, chat_id: int, version: int, sample: Sample) -> None:
    conn.execute(
        "INSERT INTO observations (chat_id, version, secid, observed_at, ok, price, price_unit, price_source, quote_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            chat_id, version, sample.secid, sample.observed_at, int(sample.ok),
            sample.price, sample.price_unit, sample.price_source, sample.quote_at,
        ),
    )  # fmt: skip


class WatchStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    # --- соединение и транзакции ----------------------------------------------------------------

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        except (OSError, sqlite3.Error) as error:
            raise WatchStoreUnavailable(f"{type(error).__name__}: {error}") from error
        try:
            conn.row_factory = sqlite3.Row
            self._prepare(conn)
            yield conn
        except sqlite3.Error as error:
            raise WatchStoreUnavailable(f"{type(error).__name__}: {error}") from error
        finally:
            conn.close()

    @staticmethod
    @contextmanager
    def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
        """`BEGIN IMMEDIATE`: пишущая транзакция берёт блокировку сразу, поэтому проверки внутри не устаревают."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _prepare(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        if self._schema_version(conn) == SCHEMA_VERSION:
            return
        with self._transaction(conn):
            # другой процесс мог создать схему, пока мы ждали блокировку
            if self._schema_version(conn) < SCHEMA_VERSION:
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise WatchStoreUnavailable(
                f"схема хранилища версии {version} новее поддерживаемой ({SCHEMA_VERSION}): обновите mcp-moex"
            )
        return version

    # --- опросы ---------------------------------------------------------------------------------

    def get_watch(self, chat_id: int) -> WatchRow | None:
        with self._connection() as conn:
            return self._read_watch(conn, chat_id)

    @staticmethod
    def _read_watch(conn: sqlite3.Connection, chat_id: int) -> WatchRow | None:
        row = conn.execute("SELECT * FROM watches WHERE chat_id = ?", (chat_id,)).fetchone()
        return _watch(row) if row else None

    def replace_watch(
        self,
        chat_id: int,
        secids: Sequence[str],
        poll_seconds: int,
        report_seconds: int,
        now: int,
        first_samples: Sequence[Sample],
        *,
        poll_interval: str | None = None,
        report_interval: str | None = None,
    ) -> tuple[WatchRow, bool]:
        """Задаёт опрос чата (заменяя прежний) и сохраняет первые замеры: всё в одной транзакции.

        Неподтверждённые сводки прежнего опроса не трогаются. Возвращает опрос и признак замены.
        """
        with self._connection() as conn, self._transaction(conn):
            replaced = self._read_watch(conn, chat_id) is not None
            # Версия берётся из общего счётчика, а не из `версия + 1`: после остановки и новой постановки
            # она не должна совпасть с версией, которую заявил ещё идущий вызов `watch_run_due`.
            conn.execute("INSERT INTO meta (key, value) VALUES ('watch_version', 1) "
                         "ON CONFLICT (key) DO UPDATE SET value = value + 1")  # fmt: skip
            version = conn.execute("SELECT value FROM meta WHERE key = 'watch_version'").fetchone()[0]
            conn.execute("DELETE FROM observations WHERE chat_id = ?", (chat_id,))
            conn.execute(
                "INSERT INTO watches (chat_id, secids, poll_seconds, report_seconds, poll_interval, report_interval, "
                "version, started_at, window_start, next_poll_at, next_report_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET secids = excluded.secids, poll_seconds = excluded.poll_seconds, "
                "report_seconds = excluded.report_seconds, poll_interval = excluded.poll_interval, "
                "report_interval = excluded.report_interval, version = excluded.version, "
                "started_at = excluded.started_at, window_start = excluded.window_start, "
                "next_poll_at = excluded.next_poll_at, next_report_at = excluded.next_report_at",
                (
                    chat_id, json.dumps(list(secids)), poll_seconds, report_seconds,
                    poll_interval or format_seconds(poll_seconds), report_interval or format_seconds(report_seconds),
                    version, now,
                    now - 1,  # граница не включительно: первый замер, сделанный в момент `now`, входит в период
                    now + poll_seconds, now + report_seconds,
                ),
            )  # fmt: skip
            for sample in first_samples:
                _insert_sample(conn, chat_id, version, sample)
            watch = self._read_watch(conn, chat_id)
        assert watch is not None
        return watch, replaced

    def delete_watch(self, chat_id: int) -> bool:
        """Удаляет опрос чата вместе с замерами и сводками. Возвращает, был ли опрос."""
        with self._connection() as conn, self._transaction(conn):
            existed = conn.execute("DELETE FROM watches WHERE chat_id = ?", (chat_id,)).rowcount > 0
            conn.execute("DELETE FROM reports WHERE chat_id = ?", (chat_id,))
        return existed

    # --- опрос по срокам ------------------------------------------------------------------------

    def claim_due_polls(self, now: int) -> list[Claim]:
        """Заявляет опросы, срок которых наступил, и сразу сдвигает их срок: параллельный вызов их уже не увидит."""
        with self._connection() as conn, self._transaction(conn):
            rows = conn.execute("SELECT * FROM watches WHERE next_poll_at <= ? ORDER BY chat_id", (now,)).fetchall()
            claims = []
            for row in rows:
                watch = _watch(row)
                conn.execute(
                    "UPDATE watches SET next_poll_at = ? WHERE chat_id = ?", (now + watch.poll_seconds, watch.chat_id)
                )
                claims.append(Claim(watch.chat_id, watch.version, watch.secids))
        return claims

    def record_samples(self, batches: Sequence[tuple[Claim, Sequence[Sample]]]) -> int:
        """Пишет замеры заявленных опросов, но только тех, чья версия не изменилась (опрос не заменён и не остановлен).

        Возвращает число записанных замеров.
        """
        written = 0
        with self._connection() as conn, self._transaction(conn):
            for claim, samples in batches:
                current = conn.execute("SELECT version FROM watches WHERE chat_id = ?", (claim.chat_id,)).fetchone()
                if current is None or current["version"] != claim.version:
                    continue
                for sample in samples:
                    _insert_sample(conn, claim.chat_id, claim.version, sample)
                    written += 1
        return written

    def create_due_reports(self, now: int, builder: ReportBuilder) -> int:
        """Создаёт сводки чатов, срок которых наступил. Условие проверяется внутри транзакции: дубля не будет."""
        created = 0
        with self._connection() as conn, self._transaction(conn):
            rows = conn.execute("SELECT * FROM watches WHERE next_report_at <= ? ORDER BY chat_id", (now,)).fetchall()
            for row in rows:
                watch = _watch(row)
                samples = self._period_samples(conn, watch.chat_id, watch.window_start, now)
                payload = builder(watch, samples, watch.period_start, now)
                conn.execute(
                    "INSERT INTO reports (chat_id, period_start, period_end, created_at, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (watch.chat_id, watch.period_start, now, now, payload),
                )
                conn.execute(
                    "UPDATE watches SET window_start = ?, next_report_at = ? WHERE chat_id = ?",
                    (now, now + watch.report_seconds, watch.chat_id),
                )
                created += 1
        return created

    @staticmethod
    def _retained_after(after: int, until: int) -> int:
        """Замеры старше срока хранения в период не входят, даже если `purge` до них ещё не дошёл."""
        return max(after, until - RETENTION_SECONDS - 1)

    @classmethod
    def _period_samples(cls, conn: sqlite3.Connection, chat_id: int, after: int, until: int) -> list[Sample]:
        after = cls._retained_after(after, until)
        rows = conn.execute(
            "SELECT * FROM observations WHERE chat_id = ? AND observed_at > ? AND observed_at <= ? "
            "ORDER BY observed_at, id",
            (chat_id, after, until),
        ).fetchall()
        return [_sample(row) for row in rows]

    def period_samples(self, chat_id: int, after: int, until: int) -> list[Sample]:
        """Замеры чата с `after` (не включительно) по `until` (включительно), по времени."""
        with self._connection() as conn:
            return self._period_samples(conn, chat_id, after, until)

    def count_period_samples(self, chat_id: int, after: int, until: int) -> int:
        """Число удавшихся замеров периода (неудавшиеся в счёт не идут, как и в сводке)."""
        after = self._retained_after(after, until)
        with self._connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM observations WHERE chat_id = ? AND ok = 1 AND observed_at > ? AND observed_at <= ?",
                (chat_id, after, until),
            ).fetchone()[0]

    def purge(self, now: int) -> None:
        """Удаляет замеры и сводки старше срока хранения."""
        cutoff = now - RETENTION_SECONDS
        with self._connection() as conn, self._transaction(conn):
            conn.execute("DELETE FROM observations WHERE observed_at < ?", (cutoff,))
            conn.execute("DELETE FROM reports WHERE created_at < ?", (cutoff,))

    def next_due(self) -> int | None:
        """Ближайший срок опроса или сводки среди всех чатов; `None`, если опросов нет."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT MIN(MIN(next_poll_at), MIN(next_report_at)) FROM watches"
            ).fetchone()
        return row[0]

    # --- доставка сводок ------------------------------------------------------------------------

    def pending_reports(self, limit: int = REPORTS_PER_CALL) -> tuple[list[StoredReport], bool]:
        """Неподтверждённые сводки всех чатов, старые первыми; второй элемент: есть ли ещё сверх `limit`."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, chat_id, payload FROM reports WHERE delivered_at IS NULL ORDER BY id LIMIT ?",
                (limit + 1,),
            ).fetchall()
        reports = [StoredReport(row["id"], row["chat_id"], row["payload"]) for row in rows[:limit]]
        return reports, len(rows) > limit

    def count_pending(self, chat_id: int) -> int:
        with self._connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM reports WHERE chat_id = ? AND delivered_at IS NULL", (chat_id,)
            ).fetchone()[0]

    def acknowledge(self, report_ids: Sequence[int], now: int) -> int:
        """Отмечает сводки доставленными; повторные и неизвестные идентификаторы не считаются."""
        acknowledged = 0
        with self._connection() as conn, self._transaction(conn):
            for report_id in dict.fromkeys(report_ids):
                acknowledged += conn.execute(
                    "UPDATE reports SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL", (now, report_id)
                ).rowcount
        return acknowledged
