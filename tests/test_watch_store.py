"""Хранилище расписания на временной БД: схема, операции, согласованность двух соединений."""

import sqlite3
import threading

import pytest

from mcp_moex.errors import WatchStoreUnavailable
from mcp_moex.watch.store import RETENTION_SECONDS, SCHEMA_VERSION, Sample, WatchStore

T0 = 1_800_000_000  # произвольный момент, секунды Unix
POLL = 900
REPORT = 3600


@pytest.fixture
def store(tmp_path) -> WatchStore:
    return WatchStore(tmp_path / "watch.db")


def ok(secid: str, price: float, at: int) -> Sample:
    return Sample(secid=secid, observed_at=at, ok=True, price=price, price_unit="RUB", price_source="last_trade", quote_at=at)


def failed(secid: str, at: int) -> Sample:
    return Sample(secid=secid, observed_at=at, ok=False)


def set_watch(store: WatchStore, chat_id: int = 1, secids=("SBER",), now: int = T0, poll: int = POLL, report: int = REPORT):
    return store.replace_watch(chat_id, secids, poll, report, now, [ok(secid, 100.0, now) for secid in secids])


def json_builder(watch, samples, start, end) -> str:
    return f'{{"chat": {watch.chat_id}, "n": {len(samples)}, "start": {start}, "end": {end}}}'


# --- 2.1 файл, схема, версия -------------------------------------------------------------------


def test_file_and_directories_are_created_on_first_use_not_on_construction(tmp_path):
    path = tmp_path / "nested" / "deeper" / "watch.db"
    store = WatchStore(path)
    assert not path.parent.exists()

    assert store.get_watch(1) is None

    assert path.exists()


def test_pragmas_and_schema_version(store):
    store.get_watch(1)

    conn = sqlite3.connect(store.path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert {"watches", "observations", "reports"} <= tables


def test_connection_pragmas_are_applied(store, monkeypatch):
    seen = {}
    real_prepare = WatchStore._prepare

    def spy(self, conn):
        real_prepare(self, conn)
        seen["busy_timeout"] = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        seen["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    monkeypatch.setattr(WatchStore, "_prepare", spy)
    store.get_watch(1)

    assert seen == {"busy_timeout": 10_000, "foreign_keys": 1}


def test_reopening_keeps_the_data(store, tmp_path):
    set_watch(store, chat_id=7, secids=("SBER", "GAZP"))

    reopened = WatchStore(tmp_path / "watch.db").get_watch(7)

    assert reopened is not None
    assert reopened.secids == ("SBER", "GAZP")
    assert reopened.next_poll_at == T0 + POLL


def test_newer_schema_version_is_an_error_not_corruption(store):
    store.get_watch(1)
    conn = sqlite3.connect(store.path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(WatchStoreUnavailable, match="новее"):
        store.get_watch(1)


def test_corrupted_file_is_a_store_unavailable_error(tmp_path):
    path = tmp_path / "watch.db"
    path.write_bytes(b"this is definitely not an sqlite database" * 100)

    with pytest.raises(WatchStoreUnavailable) as error:
        WatchStore(path).get_watch(1)

    assert "--watch-db" in str(error.value)
    assert "Traceback" not in str(error.value)


def test_path_below_a_regular_file_is_a_store_unavailable_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")

    with pytest.raises(WatchStoreUnavailable):
        WatchStore(blocker / "sub" / "watch.db").get_watch(1)


# --- 2.2 операции ---------------------------------------------------------------------------------


def test_replace_watch_creates_watch_with_deadlines_and_first_samples(store):
    watch, replaced = set_watch(store, secids=("SBER", "GAZP"))

    assert replaced is False
    assert watch.secids == ("SBER", "GAZP")
    assert (watch.next_poll_at, watch.next_report_at) == (T0 + POLL, T0 + REPORT)
    assert watch.period_start == T0
    assert store.count_period_samples(1, watch.window_start, T0) == 2


def test_replace_watch_bumps_version_drops_old_samples_and_keeps_pending_reports(store):
    first, _ = set_watch(store)
    store.create_due_reports(T0 + REPORT, json_builder)

    second, replaced = set_watch(store, secids=("GAZP",), now=T0 + 10)

    assert replaced is True
    assert second.version > first.version
    assert [s.secid for s in store.period_samples(1, second.window_start, T0 + 10)] == ["GAZP"]
    assert store.count_pending(1) == 1  # неподтверждённая сводка прежнего опроса осталась


def test_version_does_not_repeat_after_stop_and_new_watch(store):
    first, _ = set_watch(store)
    store.delete_watch(1)

    second, replaced = set_watch(store)

    assert replaced is False
    assert second.version != first.version


def test_delete_watch_removes_samples_and_reports(store):
    set_watch(store)
    store.create_due_reports(T0 + REPORT, json_builder)

    assert store.delete_watch(1) is True

    assert store.get_watch(1) is None
    assert store.pending_reports() == ([], False)
    conn = sqlite3.connect(store.path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        conn.close()
    assert store.delete_watch(1) is False


def test_claim_shifts_poll_deadline_from_the_call_moment(store):
    set_watch(store, secids=("SBER", "GAZP"))

    claims = store.claim_due_polls(T0 + 5 * POLL)  # пропущено несколько периодов

    assert [(c.chat_id, c.secids) for c in claims] == [(1, ("SBER", "GAZP"))]
    assert store.get_watch(1).next_poll_at == T0 + 6 * POLL
    assert store.claim_due_polls(T0 + 5 * POLL) == []


def test_claim_ignores_watches_that_are_not_due(store):
    set_watch(store)

    assert store.claim_due_polls(T0 + POLL - 1) == []
    assert store.get_watch(1).next_poll_at == T0 + POLL


def test_record_samples_writes_only_for_unchanged_version(store):
    set_watch(store, chat_id=1)
    set_watch(store, chat_id=2)
    claims = store.claim_due_polls(T0 + POLL)
    by_chat = {c.chat_id: c for c in claims}
    set_watch(store, chat_id=2, secids=("GAZP",), now=T0 + POLL)  # чат 2 заменён между заявкой и записью

    written = store.record_samples(
        [(by_chat[1], [ok("SBER", 101.0, T0 + POLL)]), (by_chat[2], [ok("SBER", 999.0, T0 + POLL)])]
    )

    assert written == 1
    assert [s.price for s in store.period_samples(1, T0, T0 + POLL)] == [101.0]
    assert [s.secid for s in store.period_samples(2, T0, T0 + POLL)] == ["GAZP"]


def test_record_samples_after_stop_is_discarded(store):
    set_watch(store)
    (claim,) = store.claim_due_polls(T0 + POLL)
    store.delete_watch(1)

    assert store.record_samples([(claim, [ok("SBER", 101.0, T0 + POLL)])]) == 0


def test_failed_samples_are_stored_with_ok_false(store):
    set_watch(store)
    (claim,) = store.claim_due_polls(T0 + POLL)

    store.record_samples([(claim, [failed("SBER", T0 + POLL)])])

    last = store.period_samples(1, T0, T0 + POLL)[-1]
    assert (last.ok, last.price, last.price_unit) == (False, None, None)


def test_create_due_reports_moves_the_window_and_the_deadline(store):
    set_watch(store)
    (claim,) = store.claim_due_polls(T0 + POLL)
    store.record_samples([(claim, [ok("SBER", 105.0, T0 + POLL)])])

    assert store.create_due_reports(T0 + REPORT - 1, json_builder) == 0  # срок не наступил
    assert store.create_due_reports(T0 + REPORT, json_builder) == 1

    (report,), has_more = store.pending_reports()
    assert has_more is False
    assert '"n": 2' in report.payload  # первый замер и замер спустя период
    assert f'"start": {T0}' in report.payload and f'"end": {T0 + REPORT}' in report.payload
    watch = store.get_watch(1)
    assert watch.next_report_at == T0 + 2 * REPORT
    assert watch.period_start == T0 + REPORT


def test_boundary_sample_belongs_to_exactly_one_report(store):
    set_watch(store)
    (claim,) = store.claim_due_polls(T0 + REPORT)  # опрос и сводка наступили в один момент
    store.record_samples([(claim, [ok("SBER", 105.0, T0 + REPORT)])])
    store.create_due_reports(T0 + REPORT, json_builder)
    (claim,) = store.claim_due_polls(T0 + REPORT + POLL)
    store.record_samples([(claim, [ok("SBER", 106.0, T0 + REPORT + POLL)])])
    store.create_due_reports(T0 + 2 * REPORT, json_builder)

    first, second = store.pending_reports()[0]

    assert '"n": 2' in first.payload  # замер при постановке и замер на границе
    assert '"n": 1' in second.payload  # замер на границе не считается второй раз


def test_report_of_one_chat_does_not_touch_the_other(store):
    set_watch(store, chat_id=1)
    set_watch(store, chat_id=2, now=T0 + 100)

    store.create_due_reports(T0 + REPORT, json_builder)

    assert [r.chat_id for r in store.pending_reports()[0]] == [1]
    assert store.get_watch(2).next_report_at == T0 + 100 + REPORT


def test_pending_reports_are_limited_oldest_first_with_has_more(store):
    set_watch(store)
    for i in range(1, 106):
        store.create_due_reports(T0 + i * REPORT, json_builder)

    reports, has_more = store.pending_reports(100)

    assert len(reports) == 100 and has_more is True
    assert [r.report_id for r in reports] == sorted(r.report_id for r in reports)
    assert store.count_pending(1) == 105
    rest, has_more = store.pending_reports(200)
    assert len(rest) == 105 and has_more is False


def test_acknowledge_is_idempotent_and_counts_only_marked_reports(store):
    set_watch(store)
    store.create_due_reports(T0 + REPORT, json_builder)
    (report,), _ = store.pending_reports()

    assert store.acknowledge([report.report_id, report.report_id, 9999], T0 + REPORT) == 1
    assert store.acknowledge([report.report_id], T0 + REPORT) == 0
    assert store.acknowledge([], T0 + REPORT) == 0
    assert store.pending_reports() == ([], False)
    assert store.count_pending(1) == 0


def test_report_ids_are_not_reused_after_purge(store):
    set_watch(store)
    store.create_due_reports(T0 + REPORT, json_builder)
    (first,), _ = store.pending_reports()
    store.purge(T0 + REPORT + RETENTION_SECONDS + 1)

    store.create_due_reports(T0 + REPORT + RETENTION_SECONDS + 2, json_builder)

    (second,), _ = store.pending_reports()
    assert second.report_id > first.report_id


def test_purge_removes_samples_and_reports_older_than_retention(store):
    set_watch(store)
    store.create_due_reports(T0 + REPORT, json_builder)
    later = T0 + REPORT + RETENTION_SECONDS + 1
    (claim,) = store.claim_due_polls(later)
    store.record_samples([(claim, [ok("SBER", 200.0, later)])])

    store.purge(later)

    samples = store.period_samples(1, 0, later)
    assert [s.price for s in samples] == [200.0]  # первый замер старше 30 суток удалён
    assert store.pending_reports() == ([], False)  # и сводка тоже


def test_purge_keeps_fresh_data(store):
    set_watch(store)
    store.create_due_reports(T0 + REPORT, json_builder)

    store.purge(T0 + REPORT)

    assert store.count_period_samples(1, 0, T0 + REPORT) == 1
    assert store.count_pending(1) == 1


def test_next_due_is_the_nearest_deadline_or_none(store):
    assert store.next_due() is None

    set_watch(store, chat_id=1)
    set_watch(store, chat_id=2, now=T0 + 60, poll=300)

    assert store.next_due() == T0 + 60 + 300
    store.delete_watch(2)
    assert store.next_due() == T0 + POLL
    store.delete_watch(1)
    assert store.next_due() is None


# --- 2.3 два соединения к одному файлу ---------------------------------------------------------


def race(*functions):
    """Запускает функции в потоках одновременно (барьер) и возвращает их результаты."""
    barrier = threading.Barrier(len(functions))
    results = [None] * len(functions)
    errors = []

    def runner(index, function):
        try:
            barrier.wait()
            results[index] = function()
        except BaseException as error:  # noqa: BLE001 - пробрасываем в основной поток
            errors.append(error)

    threads = [threading.Thread(target=runner, args=(i, f)) for i, f in enumerate(functions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    return results


def test_two_concurrent_claim_passes_claim_a_poll_once(tmp_path):
    first, second = WatchStore(tmp_path / "w.db"), WatchStore(tmp_path / "w.db")
    set_watch(first)

    results = race(lambda: first.claim_due_polls(T0 + POLL), lambda: second.claim_due_polls(T0 + POLL))

    assert sorted(len(r) for r in results) == [0, 1]


def test_two_concurrent_report_passes_create_the_report_once(tmp_path):
    first, second = WatchStore(tmp_path / "w.db"), WatchStore(tmp_path / "w.db")
    set_watch(first)

    results = race(
        lambda: first.create_due_reports(T0 + REPORT, json_builder),
        lambda: second.create_due_reports(T0 + REPORT, json_builder),
    )

    assert sorted(results) == [0, 1]
    assert len(first.pending_reports()[0]) == 1


def test_sequential_passes_from_two_connections_do_not_duplicate(tmp_path):
    first, second = WatchStore(tmp_path / "w.db"), WatchStore(tmp_path / "w.db")
    set_watch(first)

    assert len(first.claim_due_polls(T0 + POLL)) == 1
    assert second.claim_due_polls(T0 + POLL) == []
    assert first.create_due_reports(T0 + REPORT, json_builder) == 1
    assert second.create_due_reports(T0 + REPORT, json_builder) == 0
