"""M5 slice 3: durability of a write-ahead-log store (C-02-AC3).

C-02-AC3 is about deletion being real. This slice asks the harder half of the
same question: after a process dies in the middle of a write, does the store
still contain exactly what committed -- no more, no less, and no illusion?

Everything here is evaluator test infrastructure. The store is a small
file-backed log in a temporary directory, the records are invented, and the
"process death" is the harness raising at a byte offset it chose itself. None of
it is a storage engine, a product store, a real crash, or a measurement of a
system's reliability.

The tests check seven separable invariants (one counter and one gate each), the
fixture's own honesty (gold recomputed for every candidate crash stage), the
byte-level crash machinery, and the documentation bookkeeping.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from eval.harness import durability
from eval.harness import gates as gates_mod
from eval.harness import wal_store
from eval.harness.config import Thresholds
from eval.harness.runner import run_block, solver_view
from eval.harness.solvers.mutations import MUTATIONS
from eval.harness.taskset import (B4W_EXPECTED_FIELDS, B4W_ZERO_COUNTERS, TaskSetError,
                                  load_taskset, load_tasksets)
from eval.harness.write_state import COMMITTED, NOT_STARTED, PARTIALLY_APPLIED, ROLLED_BACK

REPO = Path(__file__).resolve().parents[2]
B4W_GATES = ("wal_integrity_zero", "durability_zero", "uncommitted_write_zero",
             "durability_idempotency_zero", "durability_resurrection_zero",
             "unrelated_record_zero", "recovery_complete_zero")


def _payload(block: str = "B4W") -> dict:
    return json.loads((REPO / "eval" / "tasks" / block.lower() / "tasks.json")
                      .read_text(encoding="utf-8"))


def _sets():
    return load_tasksets(REPO / "eval" / "tasks")


def _taskset():
    return _sets()["B4W"]


def _task(task_id: str) -> dict:
    return next(task for task in _taskset().tasks if task["id"] == task_id)


def _load_variant(payload: dict) -> str:
    """Write a modified fixture to a temp dir and load it (must raise or return)."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "tasks.json").write_text(json.dumps(payload), encoding="utf-8")
        load_taskset(tmp)
        return tmp


def _refuses(payload: dict) -> str:
    """Return the refusal message, or fail the test if the fixture loaded."""
    try:
        _load_variant(payload)
    except TaskSetError as exc:
        return str(exc)
    raise AssertionError("the loader accepted a fixture it should have refused")


def _gate_map(summary: dict, clean: dict | None = None) -> dict:
    return {result.gate: result for result in
            gates_mod.evaluate_block("B4W", summary, clean, Thresholds())}


def _run(mutations: tuple[str, ...] = (), task_id: str | None = None):
    taskset = _taskset()
    if task_id:
        import dataclasses
        taskset = dataclasses.replace(
            taskset, tasks=[t for t in taskset.tasks if t["id"] == task_id])
    return run_block(taskset, solver_name="stub", mutations=mutations,
                     git_sha="0" * 40, git_dirty=False)


def _record(task: dict, record_id: str) -> dict:
    return next(item for item in task["records"] if item["record_id"] == record_id)


def _empty_store() -> Path:
    """A path that does not exist yet and carries the harness prefix."""
    parent = Path(tempfile.mkdtemp(prefix="jarvis-b4w-parent-"))
    return parent / f"{wal_store.STORE_PREFIX}{os.urandom(4).hex()}"


def _drop(path: Path) -> None:
    import shutil
    shutil.rmtree(path.parent, ignore_errors=True)


def _recover_through_crashes(driver) -> dict:
    """Recover, retrying when the queued plan kills recovery itself."""
    for _ in range(durability.CRASH_LIMIT):
        try:
            return driver.recover()
        except wal_store.StoreCrashed:
            continue
    raise AssertionError("recovery kept dying")


# --------------------------------------------------------------------------- #
# The fixture
# --------------------------------------------------------------------------- #
class B4WFixtureTests(unittest.TestCase):
    """The fixture has to be synthetic, honest and impossible to read from the view."""

    @classmethod
    def setUpClass(cls):
        cls.payload = _payload()
        cls.taskset = _taskset()

    def test_ten_synthetic_tasks_load(self):
        self.assertEqual(self.taskset.block, "B4W")
        self.assertEqual(self.taskset.version, "2026-Q4.1-m5.7")
        self.assertEqual(len(self.taskset.tasks), 10)
        self.assertTrue(self.payload["synthetic"])
        self.assertEqual(self.payload["data_class"], "synthetic")
        self.assertEqual(sorted(t["id"] for t in self.taskset.tasks),
                         sorted(f"b4w-{n:03d}" for n in range(1, 11)))

    def test_every_task_declares_the_full_expected_shape(self):
        for task in self.taskset.tasks:
            with self.subTest(task=task["id"]):
                for field in B4W_EXPECTED_FIELDS:
                    self.assertIn(field, task["expected"])
                for counter in B4W_ZERO_COUNTERS:
                    self.assertEqual(task["expected"].get(counter, 0), 0)
                self.assertEqual(sorted(task["expected"]["final_lines"]),
                                 sorted(task["expected"]["durable"]))
                self.assertFalse(set(task["expected"]["durable"])
                                 & set(task["expected"]["absent"]))
                self.assertFalse(set(task["expected"]["durable"])
                                 & set(task["expected"]["deleted"]))
                self.assertTrue(set(task["expected"]["unrelated"])
                                <= set(task["expected"]["durable"]))

    def test_gold_does_not_depend_on_which_crash_stage_is_chosen(self):
        """The fixture cannot encode the answer if every candidate gives the same one."""
        for task in self.taskset.tasks:
            spec = task["durability"]
            fixed = list(spec.get("crash_stages") or [])
            candidates = list(spec.get("crash_candidates") or [])
            # Mirror the loader: a fixed schedule crashes the same way every time,
            # a candidate list has to give the same gold whichever stage is picked.
            stages = candidates if candidates else (fixed[:1] or [None])
            with self.subTest(task=task["id"], stages=stages):
                for stage in stages:
                    outcome = durability.expected_outcome(task, stage=stage)
                    for key in ("durable", "absent", "deleted", "unrelated"):
                        self.assertEqual(sorted(outcome[key]),
                                         sorted(task["expected"][key]),
                                         f"{task['id']} under {stage}: {key}")

    def test_every_crash_stage_the_store_supports_is_exercised(self):
        declared = {stage for task in self.taskset.tasks
                    for stage in (list(task["durability"].get("crash_candidates") or [])
                                  + list(task["durability"].get("crash_stages") or []))}
        self.assertEqual(declared, set(wal_store.CRASH_STAGES))

    def test_crash_offsets_land_inside_the_interrupted_operation(self):
        for task in self.taskset.tasks:
            for plan in durability.plans(task):
                with self.subTest(task=task["id"], stage=plan.stage):
                    self.assertGreaterEqual(plan.offset, 1)
                    self.assertLessEqual(plan.offset, plan.target_bytes)
                    self.assertEqual(plan.is_truncating, plan.offset < plan.target_bytes)
                    if plan.stage == "crash_after_commit":
                        self.assertEqual(plan.offset, plan.target_bytes)
                    if plan.corrupt:
                        self.assertEqual(plan.offset, plan.target_bytes)

    def test_plans_are_deterministic_and_bounded(self):
        for task in self.taskset.tasks:
            first = [plan.describe() for plan in durability.plans(task)]
            second = [plan.describe() for plan in durability.plans(task)]
            self.assertEqual(first, second, f"{task['id']} crash plan is not deterministic")
            self.assertLessEqual(len(first), durability.CRASH_LIMIT)

    def test_a_different_salt_moves_the_crash_inside_the_declared_candidates(self):
        moved = 0
        for task in self.taskset.tasks:
            candidates = list(task["durability"].get("crash_candidates") or [])
            if not candidates:
                continue
            for extra in range(1, 12):
                variant = json.loads(json.dumps(task))
                variant["durability"]["salt"] = f"{task['durability']['salt']}-{extra}"
                plan = durability.plans(variant)[0]
                with self.subTest(task=task["id"], salt=variant["durability"]["salt"]):
                    self.assertIn(plan.stage, candidates)
                if plan.describe() != durability.plans(task)[0].describe():
                    moved += 1
                    break
        self.assertGreater(moved, 0, "no candidate task reacted to a changed salt")

    def test_the_solver_view_hides_the_schedule_and_the_gold(self):
        for task in self.taskset.tasks:
            view = solver_view(json.loads(json.dumps(task)), "B4W")
            with self.subTest(task=task["id"]):
                self.assertNotIn("expected", view)
                self.assertNotIn("durability", view)
                self.assertNotIn("planted_corruption", view)
                blob = json.dumps(view)
                for needle in ("crash_candidates", "crash_op", "crash_record", "seq_base",
                               "planted_corruption"):
                    self.assertNotIn(needle, blob)
                for plan in durability.plans(task):
                    self.assertNotIn(f'"offset": {plan.offset}', blob)
                self.assertIn("records", view)
                self.assertIn("operations", view)

    def test_preregistration_counts_match_the_fixture(self):
        prereg = self.payload["preregistration"]
        counts = prereg["sample_counts"]
        tasks = self.payload["tasks"]
        self.assertEqual(prereg["task_counts"]["total"], len(tasks))
        self.assertEqual(counts["tasks"], len(tasks))
        self.assertEqual(counts["operations"], sum(len(t["operations"]) for t in tasks))
        self.assertEqual(counts["crash_scenarios"],
                         sum(1 for t in tasks if t["durability"]["crashes"]))
        self.assertEqual(counts["simulated_crashes"],
                         sum(t["durability"]["crashes"] for t in tasks))
        self.assertEqual(counts["declared_recovery_scenarios"],
                         sum(len(durability.plans(t)) for t in tasks))
        self.assertEqual(counts["fail_closed_scenarios"],
                         sum(1 for t in tasks if t["expected"].get("fail_closed")))
        self.assertIn("not independent samples", counts["note"])
        self.assertIn("no confidence interval", counts["note"])
        self.assertEqual(sorted(counts["crash_stages_exercised"]),
                         sorted(wal_store.CRASH_STAGES))
        self.assertEqual(prereg["provenance"]["model_calls"], 0)
        self.assertFalse(prereg["provenance"]["network"])

    def test_the_fixture_says_what_it_is_not(self):
        text = json.dumps(self.payload["preregistration"]).lower()
        self.assertIn("evaluator code", text)
        self.assertIn("temporary directory", text)
        self.assertIn("no file outside", text)


class B4WFixtureRefusalTests(unittest.TestCase):
    """A malformed or dishonest fixture has to be refused, not scored."""

    def setUp(self):
        self.payload = _payload()

    def _task(self) -> dict:
        return self.payload["tasks"][0]

    def test_refuses_gold_that_the_operations_do_not_imply(self):
        task = self._task()
        task["expected"]["durable"] = task["expected"]["durable"][:-1]
        task["expected"]["final_lines"] = list(task["expected"]["durable"])
        task["expected"]["unrelated"] = []
        self.assertIn("but the operations and the crash schedule imply",
                      _refuses(self.payload))

    def test_refuses_gold_that_depends_on_the_crash_stage(self):
        payload = _payload()
        task = next(t for t in payload["tasks"] if t["durability"]["crashes"] == 0)
        task["durability"]["crashes"] = 1
        task["durability"]["crash_candidates"] = ["wal_append", "crash_after_commit"]
        task["durability"]["crash_op"] = 0
        task["durability"]["crash_record"] = task["operations"][0]["record_id"]
        self.assertIn("depends on the crash stage", _refuses(payload))

    def test_refuses_an_unknown_crash_stage(self):
        task = self._task()
        task["durability"].update({"crashes": 1, "crash_candidates": ["power_cut"],
                                   "crash_op": 0,
                                   "crash_record": task["operations"][0]["record_id"]})
        self.assertIn("crash stage", _refuses(self.payload))

    def test_refuses_a_crash_that_names_the_wrong_record(self):
        task = self._task()
        task["durability"].update({"crashes": 1, "crash_candidates": ["wal_append"],
                                   "crash_op": 0,
                                   "crash_record": task["operations"][1]["record_id"]})
        self.assertIn("interrupts", _refuses(self.payload))

    def test_refuses_a_crash_op_that_is_not_an_operation(self):
        task = self._task()
        task["durability"].update({"crashes": 1, "crash_candidates": ["wal_append"],
                                   "crash_op": 99, "crash_record": "r1"})
        self.assertIn("crash_op", _refuses(self.payload))

    def test_refuses_fewer_stages_than_crashes(self):
        task = self._task()
        task["durability"].update({"crashes": 2, "crash_stages": ["wal_append"],
                                   "crash_op": 0, "crash_record": "r1"})
        self.assertIn("crash", _refuses(self.payload))

    def test_refuses_an_invitation_to_a_nonzero_counter(self):
        task = self._task()
        task["expected"]["durability_lost"] = 1
        self.assertIn("may not invite a nonzero counter", _refuses(self.payload))

    def test_refuses_final_lines_that_are_not_the_durable_set(self):
        task = self._task()
        task["expected"]["final_lines"] = list(task["expected"]["final_lines"])[:-1]
        self.assertIn("final_lines", _refuses(self.payload))

    def test_refuses_an_unknown_damage_class(self):
        task = self._task()
        task["expected"]["damage"] = "probably fine"
        self.assertIn("damage", _refuses(self.payload))

    def test_refuses_a_record_both_durable_and_deleted(self):
        task = self._task()
        task["expected"]["deleted"] = list(task["expected"]["durable"])[:1]
        self.assertIn("both durable", _refuses(self.payload))

    def test_refuses_a_planted_sequence_that_is_not_a_wal(self):
        task = self._task()
        task["planted_entries"] = [{"kind": "commit", "txn": "p1", "seq": 1},
                                   {"kind": "put", "txn": "p1", "seq": 2,
                                    "record": _record(task, "r1")}]
        self.assertIn("malformed", _refuses(self.payload))

    def test_refuses_an_operation_without_commit_metadata(self):
        task = self._task()
        task["operations"][0].pop("txn")
        self.assertIn("transaction id", _refuses(self.payload))

    def test_refuses_a_record_without_metadata(self):
        task = self._task()
        task["records"][0].pop("metadata")
        self.assertIn("metadata", _refuses(self.payload))

    def test_refuses_a_non_synthetic_fixture(self):
        self.payload["data_class"] = "real"
        self.assertIn("synthetic", _refuses(self.payload))


# --------------------------------------------------------------------------- #
# The bytes
# --------------------------------------------------------------------------- #
class B4WWALEncodingTests(unittest.TestCase):
    """Frame encoding, checksums and commit markers, with no crash involved."""

    def _entry(self, kind="put", seq=1, txn="t1", record_id="r1"):
        return wal_store.frame_entry(kind, seq, txn,
                                     _record(_task("b4w-001"), record_id)
                                     if kind != "commit" else None)

    def test_a_frame_round_trips(self):
        entry = self._entry()
        self.assertIsNone(wal_store.frame_problem(entry))
        data = wal_store.MAGIC + wal_store.encode_frame(entry)
        entries, tail = wal_store.decode_frames(data)
        self.assertEqual(tail["status"], wal_store.DAMAGE_NONE)
        self.assertEqual(entries, [entry])

    def test_a_flipped_payload_byte_is_corrupt_not_torn(self):
        entry = self._entry()
        frame = bytearray(wal_store.encode_frame(entry))
        frame[wal_store.HEADER_HEX + 3] ^= 0x01
        entries, tail = wal_store.decode_frames(wal_store.MAGIC + bytes(frame))
        self.assertEqual(entries, [])
        self.assertEqual(tail["status"], wal_store.DAMAGE_CORRUPT)
        self.assertIn("checksum", tail["detail"])

    def test_a_truncated_frame_is_torn_and_keeps_the_verified_prefix(self):
        first = self._entry(seq=1, txn="t1", record_id="r1")
        second = self._entry(seq=3, txn="t2", record_id="r2")
        prefix = wal_store.encode_frame(first)
        tail_frame = wal_store.encode_frame(second)
        self.assertEqual(wal_store.decode_frames(wal_store.MAGIC + prefix)[1]["status"],
                         wal_store.DAMAGE_NONE)
        for cut in (1, 4, wal_store.HEADER_HEX, len(tail_frame) // 2, len(tail_frame) - 1):
            with self.subTest(cut=cut):
                entries, tail = wal_store.decode_frames(
                    wal_store.MAGIC + prefix + tail_frame[:cut])
                self.assertEqual(tail["status"], wal_store.DAMAGE_TORN)
                self.assertEqual(entries, [first])
        # A cut inside the *first* frame loses everything, and says so.
        entries, tail = wal_store.decode_frames(wal_store.MAGIC + prefix[:4])
        self.assertEqual(entries, [])
        self.assertEqual(tail["status"], wal_store.DAMAGE_TORN)

    def test_a_log_without_the_header_is_refused(self):
        entries, tail = wal_store.decode_frames(b"not-a-wal\n" + b"0" * 40)
        self.assertEqual(entries, [])
        self.assertEqual(tail["status"], wal_store.DAMAGE_MISSING_HEADER)

    def test_an_unknown_entry_kind_is_a_problem(self):
        entry = {"kind": "steal", "seq": 1, "txn": "t1"}
        self.assertIsNotNone(wal_store.frame_problem(entry))
        self.assertTrue(wal_store.validate_sequence([entry]))

    def test_a_commit_before_its_entry_is_refused_by_sequence_validation(self):
        """Replay matches commits by transaction, so ordering is caught before a run."""
        put = self._entry(seq=1, txn="t1")
        commit = wal_store.frame_entry("commit", 2, "t1")
        problems = wal_store.validate_sequence([commit, put])
        self.assertTrue(problems)
        self.assertIn("commit", problems[0].lower())

    def test_an_entry_without_a_commit_marker_is_discarded(self):
        put = self._entry(seq=1, txn="t1")
        applied, discarded = wal_store.committed_entries([put])
        self.assertEqual(applied, [])
        self.assertEqual([entry["txn"] for entry in discarded], ["t1"])

    def test_a_repeated_entry_commits_once(self):
        put = self._entry(seq=1, txn="t1")
        commit = wal_store.frame_entry("commit", 2, "t1")
        applied, _ = wal_store.committed_entries([put, commit, put, commit])
        self.assertEqual(len(applied), 1)
        records, deleted = wal_store.apply_entries(applied)
        self.assertEqual(sorted(records), ["r1"])
        self.assertEqual(deleted, set())

    def test_a_tombstone_is_authoritative_over_later_puts(self):
        root = _empty_store()
        store = wal_store.create_store(root)
        try:
            record = _record(_task("b4w-001"), "r1")
            store.write_operation({"kind": "put", "txn": "t1", "record": record}, 10)
            store.write_operation({"kind": "delete", "txn": "t2", "record": record}, 12)
            store.write_operation({"kind": "put", "txn": "t3", "record": record}, 14)
            report = store.recover()
            self.assertEqual(report.to_dict()["committed"], [])
            audit = wal_store.store_audit(store.root)
            self.assertIn("r1", audit["tombstoned"])
            self.assertNotIn("r1", audit["committed"])
            # The committed fact is the deletion, so the record reads as committed/deleted
            # rather than rolled back -- rolled_back is reserved for a discarded write.
            self.assertEqual(wal_store.record_state_in_store("r1", audit), COMMITTED)
        finally:
            store.close()
            wal_store.remove_store(store.root)
            _drop(root)

    def test_the_four_write_states_are_distinguishable(self):
        root = _empty_store()
        store = wal_store.create_store(root, plan=wal_store.CrashPlan(
            stage="record_write", record="r2", offset=5, target_bytes=500))
        try:
            first = _record(_task("b4w-001"), "r1")
            store.write_operation({"kind": "put", "txn": "t1", "record": first}, 10)
            with self.assertRaises(wal_store.StoreCrashed):
                store.write_operation(
                    {"kind": "put", "txn": "t2",
                     "record": _record(_task("b4w-001"), "r2")}, 12)
        finally:
            store.close()
        audit = wal_store.store_audit(root)
        self.assertEqual(wal_store.record_state_in_store("r1", audit), COMMITTED)
        self.assertEqual(wal_store.record_state_in_store("r2", audit), NOT_STARTED)
        self.assertEqual(wal_store.record_state_in_store("r5", audit), NOT_STARTED)
        wal_store.remove_store(root)
        _drop(root)

    def test_a_record_line_without_a_commit_is_partially_applied(self):
        root = _empty_store()
        store = wal_store.create_store(root)
        try:
            store.write_operation({"kind": "put", "txn": "t1",
                                   "record": _record(_task("b4w-001"), "r1")}, 10)
            # Drop the commit frame: the record line stays, no transaction vouches for it.
            data = store.read_wal()
            entries, _ = wal_store.decode_frames(data)
            kept = [e for e in entries if e["kind"] != "commit"]
            root.joinpath(wal_store.WAL_NAME).write_bytes(
                wal_store.MAGIC + b"".join(wal_store.encode_frame(e) for e in kept))
        finally:
            store.close()
        audit = wal_store.store_audit(root)
        self.assertEqual(wal_store.record_state_in_store("r1", audit), PARTIALLY_APPLIED)
        handle = wal_store.WalStore(root)
        try:
            handle.recover()
        finally:
            handle.close()
        audit = wal_store.store_audit(root)
        self.assertEqual(wal_store.record_state_in_store("r1", audit), NOT_STARTED)
        self.assertEqual(audit["line_ids"], [])
        wal_store.remove_store(root)
        _drop(root)


# --------------------------------------------------------------------------- #
# The crash
# --------------------------------------------------------------------------- #
class B4WCrashInjectionTests(unittest.TestCase):
    """Crash points, dead handles, and the directory the store may touch."""

    def test_a_crash_leaves_only_the_bytes_that_reached_the_file(self):
        task = _task("b4w-005")
        driver = durability.DurabilityDriver(task)
        driver.open()
        try:
            driver.write(0)
            before = wal_store.store_audit(driver.root)["wal_bytes"]
            with self.assertRaises(wal_store.StoreCrashed) as caught:
                driver.write(1)
            after = wal_store.store_audit(driver.root)["wal_bytes"]
            self.assertGreater(after, before)
            self.assertLessEqual(caught.exception.offset, after)
            audit = wal_store.store_audit(driver.root)
            self.assertEqual(audit["damage"], wal_store.DAMAGE_TORN)
        finally:
            driver.close()

    def test_a_crashed_handle_is_dead(self):
        task = _task("b4w-005")
        driver = durability.DurabilityDriver(task)
        driver.open()
        try:
            driver.write(0)
            with self.assertRaises(wal_store.StoreCrashed):
                driver.write(1)
            dead = driver.store
            with self.assertRaises(wal_store.StoreDead):
                dead.write_record(_record(task, "r4"))
        finally:
            driver.close()

    def test_a_restart_reads_the_disk_and_reaches_the_same_state(self):
        task = _task("b4w-005")
        driver = durability.DurabilityDriver(task)
        driver.open()
        root = driver.root
        try:
            driver.write(0)
            with self.assertRaises(wal_store.StoreCrashed):
                driver.write(1)
            report = _recover_through_crashes(driver)
            # A fresh handle -- no memory of the crashed process -- reads the bytes.
            handle = wal_store.WalStore(root)
            try:
                replay = handle.recover()
            finally:
                handle.close()
            self.assertEqual(replay.committed, report["committed"])
            # Only the operations before the crash can be there; r4 was never attempted.
            self.assertEqual(replay.committed, ["r1", "r3", "r5"])
        finally:
            driver.close()

    def test_the_temporary_directory_is_removed_and_only_it_is_touched(self):
        outside = Path(tempfile.mkdtemp(prefix="jarvis-b4w-outside-"))
        marker = outside / "do-not-touch.txt"
        marker.write_text("untouched", encoding="utf-8")
        driver = durability.DurabilityDriver(_task("b4w-002"))
        driver.open()
        root = driver.root
        self.assertTrue(root.is_dir())
        self.assertTrue(root.name.startswith(wal_store.STORE_PREFIX))
        for index in range(1, 3):
            try:
                driver.write(index)
            except wal_store.StoreCrashed:
                driver.recover()
        driver.close()
        self.assertFalse(root.exists(), "the store directory survived the run")
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")
        wal_store.remove_store(outside)

    def test_the_store_refuses_directories_it_did_not_create(self):
        stray = Path(tempfile.mkdtemp(prefix="not-a-store-"))
        try:
            with self.assertRaises(wal_store.WalError):
                wal_store.remove_store(stray)
            with self.assertRaises(wal_store.WalError):
                wal_store.WalStore(stray)
            with self.assertRaises(wal_store.WalError):
                wal_store._safe_root(Path(str(stray.parent), "..", stray.name))
        finally:
            import shutil
            shutil.rmtree(stray, ignore_errors=True)

    def test_a_symlinked_store_root_is_refused(self):
        real = Path(tempfile.mkdtemp(prefix=wal_store.STORE_PREFIX))
        link = Path(tempfile.mkdtemp(prefix="jarvis-b4w-link-"))
        alias = link / "store"
        os.symlink(real, alias)
        try:
            with self.assertRaises(wal_store.WalError):
                wal_store.WalStore(alias)
            with self.assertRaises(wal_store.WalError):
                wal_store.remove_store(alias)
        finally:
            import shutil
            shutil.rmtree(link, ignore_errors=True)
            shutil.rmtree(real, ignore_errors=True)

    def test_create_store_refuses_to_initialise_an_existing_directory(self):
        root = _empty_store()
        wal_store.create_store(root).close()
        try:
            with self.assertRaises(wal_store.WalError):
                wal_store.create_store(root)
        finally:
            wal_store.remove_store(root)
            _drop(root)


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #
class B4WRecoveryTests(unittest.TestCase):
    """What recovery may trust, repair or refuse."""

    def _written_store(self, task_id: str = "b4w-001"):
        task = _task(task_id)
        driver = durability.DurabilityDriver(task)
        driver.open()
        for index in range(len(task["operations"])):
            driver.write(index)
        return driver

    def test_a_clean_store_recovers_its_committed_set(self):
        driver = self._written_store()
        try:
            report = driver.recover()
            self.assertIsNone(report["damage"])
            self.assertFalse(report["repaired"])
            self.assertEqual(report["committed"], sorted(driver.task["expected"]["durable"]))
            self.assertEqual(driver.audit()["line_ids"], report["committed"])
        finally:
            driver.close()

    def test_a_torn_tail_is_cut_back_and_the_log_becomes_clean(self):
        task = _task("b4w-001")
        driver = durability.DurabilityDriver(task)
        # One crash, in the log append of the second operation: the tail is torn.
        driver.plan_queue = [wal_store.CrashPlan(stage="wal_append", record="r2",
                                                 offset=8, target_bytes=400)]
        driver.open()
        try:
            driver.write(0)
            with self.assertRaises(wal_store.StoreCrashed):
                driver.write(1)
            report = driver.recover()
            self.assertEqual(report["damage"], wal_store.DAMAGE_TORN)
            self.assertTrue(report["repaired"])
            self.assertGreater(report["truncated_bytes"], 0)
            after = driver.audit()
            self.assertIsNone(after["damage"])
            self.assertEqual(driver.recover()["committed"], report["committed"])
        finally:
            driver.close()

    def test_a_corrupt_tail_is_classified_separately_from_a_torn_one(self):
        task = _task("b4w-003")
        driver = durability.DurabilityDriver(task)
        driver.open()
        try:
            for index in range(len(task["operations"])):
                try:
                    driver.write(index)
                except wal_store.StoreCrashed:
                    break
            report = driver.recover()
            self.assertEqual(report["damage"], wal_store.DAMAGE_CORRUPT)
            self.assertTrue(report["repaired"])
        finally:
            driver.close()

    def test_a_crash_during_recovery_is_survivable(self):
        task = _task("b4w-004")
        driver = durability.DurabilityDriver(task)
        driver.open()
        try:
            for index in range(len(task["operations"])):
                driver.write(index)
            with self.assertRaises(wal_store.StoreCrashed):
                driver.recover()
            second = driver.recover()
            self.assertEqual(second["committed"], sorted(task["expected"]["durable"]))
            self.assertEqual(len(driver.crashes), 1)
        finally:
            driver.close()

    def test_a_log_without_its_header_is_refused_rather_than_repaired(self):
        root = _empty_store()
        store = wal_store.create_store(root)
        try:
            store.write_operation({"kind": "put", "txn": "t1",
                                   "record": _record(_task("b4w-001"), "r1")}, 10)
        finally:
            store.close()
        root.joinpath(wal_store.WAL_NAME).write_bytes(b"XXXXXXXXXXXX")
        handle = wal_store.WalStore(root)
        try:
            report = handle.recover()
            self.assertTrue(report.refused)
            self.assertEqual(report.committed, [])
            self.assertEqual(report.damage, wal_store.DAMAGE_MISSING_HEADER)
            self.assertEqual(report.truncated_bytes, 0)
        finally:
            handle.close()
        self.assertEqual(wal_store.store_audit(root)["committed"], [])
        wal_store.remove_store(root)
        _drop(root)

    def test_damage_before_the_frames_this_process_wrote_stops_the_replay(self):
        task = _task("b4w-008")
        driver = durability.DurabilityDriver(task)
        driver.open()
        try:
            for index in range(len(task["operations"])):
                driver.write(index)
            report = driver.recover()
            self.assertEqual(report["damage"], wal_store.DAMAGE_TORN)
            # The frames written after the damage are verifiable and still refused.
            self.assertEqual(report["committed"], sorted(task["expected"]["durable"]))
            self.assertEqual(driver.audit()["line_ids"], report["committed"])
        finally:
            driver.close()

    def test_a_committed_deletion_survives_a_restart_and_a_replay(self):
        driver = self._written_store()
        try:
            driver.recover()
            audit = driver.audit()
            self.assertEqual(sorted(audit["tombstoned"]), ["r3"])
            self.assertNotIn("r3", audit["committed"])
            self.assertEqual(audit["committed"], sorted(driver.task["expected"]["durable"]))
        finally:
            driver.close()

    def test_a_second_replay_of_the_same_log_doubles_the_lines(self):
        """Why ``replay_committed_twice`` is a regression: the committed set is stable,
        the record file is not."""
        driver = self._written_store()
        try:
            first = driver.recover()
            doubled = driver.recover(replay_entries=2)
            self.assertEqual(first["committed"], doubled["committed"])
            audit = driver.audit()
            self.assertEqual(len(audit["line_ids"]), 2 * len(audit["committed"]))
            self.assertEqual(len(set(audit["line_ids"])), len(audit["committed"]))
            clean = driver.recover()
            self.assertEqual(len(driver.audit()["line_ids"]),
                             len(clean["committed"]))
        finally:
            driver.close()


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
class B4WScoringTests(unittest.TestCase):
    """One counter per failure direction, and a clean run that passes."""

    @classmethod
    def setUpClass(cls):
        cls.clean = _run()

    def test_a_clean_run_passes_every_task_and_every_counter(self):
        self.assertTrue(all(record.success for record in self.clean.records))
        for key in ("wal_integrity_errors", "durability_lost", "uncommitted_applied",
                    "durability_duplicate_effects", "durability_resurrections",
                    "unrelated_lost", "recovery_unresolved"):
            self.assertEqual(self.clean.summary[key], 0, key)

    def test_the_report_matches_the_bytes_it_claims_to_describe(self):
        for record in self.clean.records:
            audit = record.detail["audit"]
            report = record.detail["report"]
            with self.subTest(task=record.task_id):
                self.assertEqual(sorted(report["committed"]), sorted(audit["committed"]))
                self.assertEqual(sorted(audit["line_ids"]), sorted(audit["committed"]))
                observed = record.detail["recoveries"][-1]["damage"]
                self.assertEqual(report["damage"], observed)

    def test_each_planted_regression_fires_its_own_counter_and_gate(self):
        clean_gates = _gate_map(self.clean.summary)
        for name, mutation in sorted(MUTATIONS.items()):
            if mutation.block != "B4W":
                continue
            with self.subTest(mutation=name):
                mutant = _run((name,))
                gates = _gate_map(mutant.summary, self.clean.summary)
                self.assertEqual(gates[mutation.expected_gate].status, "fail",
                                 f"{name} did not fail {mutation.expected_gate}")
                self.assertEqual(clean_gates[mutation.expected_gate].status, "pass")
                self.assertLess(mutant.summary["success_rate"], 1.0)

    def test_the_counters_are_not_one_generic_failure_number(self):
        counters = set()
        for name, mutation in MUTATIONS.items():
            if mutation.block != "B4W":
                continue
            mutant = _run((name,))
            fired = {key for key in ("wal_integrity_errors", "durability_lost",
                                     "uncommitted_applied", "durability_duplicate_effects",
                                     "durability_resurrections", "unrelated_lost",
                                     "recovery_unresolved")
                     if mutant.summary[key]}
            self.assertIn(_counter_for(mutation.expected_gate), fired,
                          f"{name} fired {sorted(fired)}")
            counters |= fired
        self.assertGreaterEqual(len(counters), 6, "the counters collapse into one number")

    def test_a_silent_solver_cannot_zero_the_counters(self):
        from eval.harness.scorers import score_b4w
        from eval.harness.solvers.base import SolveResult

        class Silent:
            def durability_result(self):
                return {"enabled": True, "report": {}, "audit": {},
                        "recoveries": [], "crashes": [], "injection": {}}

        result = score_b4w(_task("b4w-001"), SolveResult(status="done", message=""),
                           Silent())
        self.assertFalse(result["success"])
        self.assertGreater(result["metrics"]["wal_integrity_errors"], 0)

    def test_a_store_that_was_never_created_fails_closed(self):
        from eval.harness.scorers import score_b4w
        from eval.harness.solvers.base import SolveResult

        class Missing:
            def durability_result(self):
                return {"enabled": False}

        result = score_b4w(_task("b4w-001"), SolveResult(status="done", message=""),
                           Missing())
        self.assertFalse(result["success"])


def _counter_for(gate: str) -> str:
    return {
        "wal_integrity_zero": "wal_integrity_errors",
        "durability_zero": "durability_lost",
        "uncommitted_write_zero": "uncommitted_applied",
        "durability_idempotency_zero": "durability_duplicate_effects",
        "durability_resurrection_zero": "durability_resurrections",
        "unrelated_record_zero": "unrelated_lost",
        "recovery_complete_zero": "recovery_unresolved",
    }[gate]


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
class B4WGateTests(unittest.TestCase):
    """The seven gates exist, are zero-tolerance, and stay inside their block."""

    @classmethod
    def setUpClass(cls):
        cls.clean = _run()
        cls.gates = _gate_map(cls.clean.summary)

    def test_every_gate_is_defined_once_and_reads_one_counter(self):
        mapping = gates_mod.BLOCK_METRIC_GATES["B4W"]
        names = [gate for _, gate in mapping]
        self.assertEqual(names, list(B4W_GATES))
        self.assertEqual(len(set(names)), len(names))

    def test_the_gates_are_zero_tolerance(self):
        for name in B4W_GATES:
            with self.subTest(gate=name):
                self.assertEqual(self.gates[name].limit, 0)

    def test_a_clean_run_passes_all_seven(self):
        for name in B4W_GATES:
            with self.subTest(gate=name):
                self.assertEqual(self.gates[name].status, "pass")

    def test_a_broken_run_fails_exactly_the_gate_that_owns_the_breakage(self):
        mutants = {"lose_post_crash_record": "durability_zero",
                   "accept_torn_entry": "wal_integrity_zero",
                   "lose_unrelated_record": "unrelated_record_zero",
                   "claim_recovery_complete_with_partial_write": "recovery_complete_zero",
                   "resurrect_deleted_record": "durability_resurrection_zero",
                   "replay_committed_twice": "durability_idempotency_zero"}
        for name, gate in mutants.items():
            with self.subTest(mutation=name):
                mutant = _run((name,))
                gates = _gate_map(mutant.summary, self.clean.summary)
                self.assertEqual(gates[gate].status, "fail")

    def test_durability_gates_do_not_leak_into_another_family(self):
        b4d = run_block(_sets()["B4D"], solver_name="stub",
                        git_sha="0" * 40, git_dirty=False)
        leaked = [result.gate for result in
                  gates_mod.evaluate_block("B4D", b4d.summary, b4d.summary, Thresholds())
                  if result.gate in B4W_GATES]
        self.assertEqual(leaked, [])


# --------------------------------------------------------------------------- #
# Mutations and the CLI
# --------------------------------------------------------------------------- #
class B4WMutationTests(unittest.TestCase):
    """The planted regressions named by the task and by the slice note."""

    REQUIRED = {"accept_torn_entry", "replay_committed_twice", "lose_unrelated_record",
                "claim_recovery_complete_with_partial_write", "resurrect_deleted_record",
                "lose_post_crash_record"}

    def test_every_required_regression_is_planted(self):
        planted = {name for name, mutation in MUTATIONS.items() if mutation.block == "B4W"}
        self.assertTrue(self.REQUIRED <= planted, sorted(self.REQUIRED - planted))

    def test_each_mutation_names_a_live_gate_and_the_requirement(self):
        for name, mutation in MUTATIONS.items():
            if mutation.block != "B4W":
                continue
            with self.subTest(mutation=name):
                self.assertIn(mutation.expected_gate, B4W_GATES)
                self.assertIn("C-02-AC3", mutation.phase_requirement)
                self.assertTrue(mutation.description.strip())

    def test_the_preregistration_lists_the_same_mutations(self):
        payload = _payload()
        declared = payload["preregistration"]["mutations"]
        planted = {name: mutation.expected_gate for name, mutation in MUTATIONS.items()
                   if mutation.block == "B4W"}
        self.assertEqual(declared, planted)

    def test_the_x10_command_passes_for_the_block(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "x10.json"
            completed = subprocess.run(
                [sys.executable, "-m", "eval.harness", "x10", "--block", "B4W",
                 "--report", str(report)],
                cwd=REPO, capture_output=True, text=True, timeout=300,
                env={**os.environ, "PYTHONPATH": str(REPO)})
            self.assertEqual(completed.returncode, 0,
                             completed.stdout[-2000:] + completed.stderr[-2000:])
            self.assertIn("X-10 PASSED for B4W", completed.stdout)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["rows"]), len(
                [m for m in MUTATIONS.values() if m.block == "B4W"]))
            self.assertTrue(all(row["detected"] for row in payload["rows"]))

    def test_the_cli_lists_the_block_and_runs_it(self):
        import subprocess
        import sys
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        listed = subprocess.run([sys.executable, "-m", "eval.harness", "list"],
                                cwd=REPO, capture_output=True, text=True, timeout=120,
                                env=env)
        self.assertEqual(listed.returncode, 0)
        self.assertIn("B4W: 10 tasks", listed.stdout)
        self.assertIn("2026-Q4.1-m5.7", listed.stdout)


# --------------------------------------------------------------------------- #
# Documentation bookkeeping
# --------------------------------------------------------------------------- #
class B4WBookkeepingTests(unittest.TestCase):
    """The documents say the same thing as the code, in both directions."""

    @classmethod
    def setUpClass(cls):
        cls.caps = json.loads((REPO / "docs" / "product" / "capabilities.json")
                              .read_text(encoding="utf-8"))
        cls.backlog = json.loads((REPO / "docs" / "product" / "family-backlog.json")
                                 .read_text(encoding="utf-8"))
        cls.coverage = json.loads((REPO / "docs" / "product" / "ac-coverage.json")
                                  .read_text(encoding="utf-8"))

    def test_the_index_declares_the_suite(self):
        suites = self.caps["evaluation_backlog"]["slice_suites"]
        self.assertIn("B4W", suites)
        self.assertEqual(suites["B4W"]["suite"], "eval/tasks/b4w")
        self.assertEqual(suites["B4W"]["family"], "B4")
        self.assertIn("B4", self.caps["partial_slice_families"])

    def test_the_slice_list_and_its_counters_agree(self):
        slices = self.caps["evaluation_backlog"]["slices"]
        families = self.caps["evaluation_backlog"]["families"]
        self.assertEqual(families["slices_built"], len(slices))
        self.assertEqual(families["slices_total"], len(self.caps["evaluation_backlog"]
                                                        ["slice_suites"]))
        entry = next(item for item in slices
                     if (item.get("suite_block") or item.get("suite_id")
                         or item.get("slice_id")) == "B4W"
                     or item.get("slice_id") == "M5-B4-slice-3")
        self.assertEqual(entry["slice_id"], "M5-B4-slice-3")
        self.assertFalse(entry["family_complete"])
        self.assertEqual(sorted(entry["gates"]), sorted(B4W_GATES))
        self.assertTrue(entry["status"].startswith("implemented evaluator slice"))
        self.assertEqual(entry["sample_counts"]["tasks"], 10)
        self.assertIn("not independent samples", entry["sample_counts"]["note"])

    def test_the_backlog_carries_the_slice_without_completing_the_family(self):
        family = next(f for f in self.backlog["families"] if f["id"] == "B4")
        self.assertEqual(family["status"], "planned")
        third = family["implemented_slice_3"]
        self.assertEqual(third["suite"], "eval/tasks/b4w")
        self.assertEqual(third["taskset_version"], "2026-Q4.1-m5.7")
        self.assertEqual(third["tasks"], 10)
        self.assertFalse(third["family_complete"])
        self.assertTrue(third["not_covered"])
        self.assertIn("evaluator test infrastructure", third["environment"])
        self.assertTrue(set(third["gates"]) <= set(family["scoring"]["gates"]))
        self.assertEqual(self.backlog["slices_built"]["count"],
                         len(self.backlog["slices_built"]["detail"]))
        self.assertEqual(self.backlog["slices_built"]["count"], 4)

    def test_the_backlog_sample_counts_match_the_fixture(self):
        family = next(f for f in self.backlog["families"] if f["id"] == "B4")
        published = family["implemented_slice_3"]["sample_counts"]
        fixture = _payload()["preregistration"]["sample_counts"]
        for key, value in published.items():
            self.assertEqual(value, fixture[key], key)
        self.assertIn("deterministic executions", published["note"])

    def test_gate_names_are_synced_in_both_directions(self):
        source = (REPO / "eval" / "harness" / "gates.py").read_text(encoding="utf-8")
        harness = {gate for _, gate in gates_mod.BLOCK_METRIC_GATES["B4W"]}
        published = set(next(f for f in self.backlog["families"] if f["id"] == "B4")
                        ["implemented_slice_3"]["gates"])
        planned = set(next(f for f in self.backlog["families"] if f["id"] == "B4")
                      ["scoring"]["gates"])
        self.assertEqual(harness, published)
        self.assertTrue(harness <= planned)
        for gate in harness:
            self.assertIn(gate, source)

    def test_the_criterion_stays_a_built_slice_and_no_capability_is_validated(self):
        entry = next(e for e in self.coverage["entries"] if e["ac"] == "C-02-AC3")
        self.assertEqual(entry["status"], "slice_built")
        self.assertEqual(entry["slice_2"]["suite"], "eval/tasks/b4w")
        self.assertEqual(entry["slice_2"]["tasks"], 10)
        self.assertEqual(entry["slice_2"]["status"], "slice_built")
        self.assertTrue(entry["slice_2"]["not_covered"].strip())
        self.assertIn("b4w", entry["note"])
        for capability in self.caps["capabilities"]:
            self.assertNotEqual(capability.get("status"), "implemented")
            self.assertNotEqual(capability.get("status"), "validated")
        c02 = next(c for c in self.caps["capabilities"] if c["id"] == "C-02")
        ac3 = next(c for c in c02["acceptance_criteria"] if c["id"] == "C-02-AC3")
        self.assertEqual(ac3["result"], "not measured")

    def test_the_gates_that_block_model_work_are_untouched(self):
        status = {gate["id"]: gate["status"] for gate in self.caps["gates"]}
        self.assertEqual(status["G-O1"], "blocked")
        self.assertEqual(status["G-W"], "open")
        self.assertEqual(status["G-C"], "open")
        self.assertIn("owner approval", " ".join(
            next(g for g in self.caps["gates"] if g["id"] == "G-O1")["requires"]).lower())

    def test_the_documents_describe_the_slice(self):
        matrix = (REPO / "docs" / "product" / "coverage-matrix.md").read_text(encoding="utf-8")
        backlog = (REPO / "docs" / "product" / "family-backlog.md").read_text(encoding="utf-8")
        self.assertIn("b4w", matrix.lower())
        self.assertIn("durability", matrix.lower())
        self.assertIn("durability", backlog.lower())


if __name__ == "__main__":
    unittest.main()
