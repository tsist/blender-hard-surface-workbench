"""Neutral HOST regressions for live disk accounting; fixtures are retained."""
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hardsurface.budgets import BudgetExceeded, tree_file_bytes


ROOT = Path(__file__).resolve().parents[1] / "evidence" / "disk-accounting-tests"


@unittest.skipUnless(hasattr(os, "O_NOFOLLOW") and os.scandir in os.supports_fd,
                     "Requires no-follow directory handles and fd scandir")
class DiskAccountingTests(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.base = Path(tempfile.mkdtemp(prefix="scan-", dir=ROOT))
        self.root = self.base / "tree"
        self.root.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "unrelated.bin").write_bytes(b"outside" * 100)
        self.real_stat, self.real_open, self.real_scandir = os.stat, os.open, os.scandir

    def test_exact_stable_nested_totals_and_empty_tree(self):
        self.assertEqual(tree_file_bytes(self.root, max_files=0, max_entries=0), 0)
        (self.root / "one.bin").write_bytes(b"abc")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "unicode-資料.bin").write_bytes(bytes(range(256)))
        (nested / "empty.bin").touch()
        (nested / "empty-directory").mkdir()
        self.assertEqual(tree_file_bytes(self.root), 259)

    def test_atomic_temp_rename_after_enumeration_is_tolerated(self):
        temp = self.root / "report.json.unique.tmp"
        temp.write_bytes(b"replacement")
        moved = []

        def race(name, *args, **kwargs):
            if name == temp.name and not moved:
                os.replace(temp, self.root / "report.json")
                moved.append(True)
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=race):
            self.assertEqual(tree_file_bytes(self.root), 0)
        self.assertEqual(moved, [True])
        self.assertEqual(tree_file_bytes(self.root), 11)

    def test_regular_file_disappears_after_enumeration(self):
        victim = self.root / "temporary.bin"
        victim.write_bytes(b"temporary")

        def race(name, *args, **kwargs):
            if name == victim.name:
                victim.unlink()
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=race):
            self.assertEqual(tree_file_bytes(self.root), 0)

    def test_same_name_atomic_regular_replacement_uses_current_size(self):
        target = self.root / "report.json"
        target.write_bytes(b"old")
        replacement = self.base / "new-report"
        replacement.write_bytes(b"new contents")
        replaced = []

        def race(name, *args, **kwargs):
            if name == target.name and not replaced:
                os.replace(replacement, target)
                replaced.append(True)
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=race):
            self.assertEqual(tree_file_bytes(self.root), 12)

    def test_leaf_symlink_and_dangling_symlink_rejected(self):
        link = self.root / "link"
        for target in (self.outside / "unrelated.bin", self.base / "missing"):
            with self.subTest(target=target.name):
                link.symlink_to(target)
                with self.assertRaises(ValueError):
                    tree_file_bytes(self.root)
                link.unlink()

    def test_root_and_ancestor_symlinks_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.outside, target_is_directory=True)
        (self.outside / "nested").mkdir()
        for root in (alias, alias / "nested"):
            with self.subTest(root=root.name), self.assertRaises(ValueError):
                tree_file_bytes(root)

    def test_directory_symlink_rejected(self):
        (self.root / "linked-directory").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            tree_file_bytes(self.root)

    def test_special_file_rejected_without_opening_it(self):
        fifo = self.root / "named-pipe"
        os.mkfifo(fifo)
        opened = []

        def audit(name, *args, **kwargs):
            opened.append(name)
            return self.real_open(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.open", side_effect=audit):
            with self.assertRaises(ValueError):
                tree_file_bytes(self.root)
        self.assertNotIn(fifo.name, opened)

    def test_regular_leaf_swapped_to_symlink_before_stat_is_rejected(self):
        target = self.root / "leaf"
        target.write_bytes(b"owned")

        def race(name, *args, **kwargs):
            if name == target.name:
                target.unlink()
                target.symlink_to(self.outside / "unrelated.bin")
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=race):
            with self.assertRaises(ValueError):
                tree_file_bytes(self.root)

    def test_directory_replaced_before_stat_is_rejected(self):
        child = self.root / "child"
        child.mkdir()

        def race(name, *args, **kwargs):
            if name == child.name:
                child.rename(self.base / "original-child")
                child.mkdir()
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=race):
            with self.assertRaisesRegex(ValueError, "Directory changed"):
                tree_file_bytes(self.root)

    def test_directory_replaced_with_regular_file_before_stat_is_rejected(self):
        child = self.root / "child"
        child.mkdir()

        def race(name, *args, **kwargs):
            if name == child.name:
                child.rename(self.base / "original-child")
                child.write_bytes(b"replacement")
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=race):
            with self.assertRaisesRegex(ValueError, "Directory changed"):
                tree_file_bytes(self.root)

    def test_vanished_special_file_does_not_become_regular_exception(self):
        fifo = self.root / "named-pipe"
        os.mkfifo(fifo)

        def vanish_before_classification(fd):
            with self.real_scandir(fd) as iterator:
                for entry in iterator:
                    if entry.name == fifo.name:
                        fifo.unlink()
                    yield entry

        with mock.patch("hardsurface.budgets.os.scandir", side_effect=vanish_before_classification):
            with self.assertRaises(ValueError):
                tree_file_bytes(self.root)

    def test_unclassified_vanished_entry_fails_closed(self):
        entry = mock.Mock(name="unknown-directory-entry")
        entry.name = "unknown-entry"
        entry.is_file.return_value = False
        entry.is_dir.return_value = False
        iterator = (item for item in [entry])
        with mock.patch("hardsurface.budgets.os.scandir", return_value=iterator):
            with self.assertRaises(ValueError):
                tree_file_bytes(self.root)

    def test_directory_swapped_to_symlink_before_open_is_not_followed(self):
        child = self.root / "child"
        child.mkdir()

        def race(name, *args, **kwargs):
            if name == child.name:
                child.rename(self.base / "original-child")
                child.symlink_to(self.outside, target_is_directory=True)
            return self.real_open(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.open", side_effect=race):
            with self.assertRaises(OSError):
                tree_file_bytes(self.root)

    def test_directory_swapped_to_different_directory_before_open_rejected(self):
        child = self.root / "child"
        child.mkdir()

        def race(name, *args, **kwargs):
            if name == child.name:
                child.rename(self.base / "original-child")
                child.mkdir()
            return self.real_open(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.open", side_effect=race):
            with self.assertRaisesRegex(ValueError, "Directory changed"):
                tree_file_bytes(self.root)

    def test_directory_swapped_after_open_detected_at_completion(self):
        child = self.root / "child"
        child.mkdir()
        initial = child.stat()
        swapped = []

        def race(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (initial.st_dev, initial.st_ino) and not swapped:
                child.rename(self.base / "original-child")
                child.symlink_to(self.outside, target_is_directory=True)
                swapped.append(True)
            return self.real_scandir(fd)

        with mock.patch("hardsurface.budgets.os.scandir", side_effect=race):
            with self.assertRaises(ValueError):
                tree_file_bytes(self.root)
        self.assertEqual(swapped, [True])

    def test_root_swapped_after_open_detected_at_completion(self):
        initial = self.root.stat()

        def race(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (initial.st_dev, initial.st_ino):
                self.root.rename(self.base / "original-tree")
                self.root.symlink_to(self.outside, target_is_directory=True)
            return self.real_scandir(fd)

        with mock.patch("hardsurface.budgets.os.scandir", side_effect=race):
            with self.assertRaises(ValueError):
                tree_file_bytes(self.root)

    def test_directory_disappearance_is_not_silently_skipped(self):
        child = self.root / "child"
        child.mkdir()

        def race(name, *args, **kwargs):
            if name == child.name:
                child.rename(self.base / "moved-child")
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=race):
            with self.assertRaises(FileNotFoundError):
                tree_file_bytes(self.root)

    def test_permission_and_unrelated_io_errors_from_stat_propagate(self):
        (self.root / "leaf").touch()
        for error in (PermissionError(errno.EACCES, "fixture denied"),
                      OSError(errno.EIO, "fixture I/O failure")):
            def fail(name, *args, **kwargs):
                if name == "leaf":
                    raise error
                return self.real_stat(name, *args, **kwargs)
            with self.subTest(error=type(error).__name__):
                with mock.patch("hardsurface.budgets.os.stat", side_effect=fail):
                    with self.assertRaises(type(error)):
                        tree_file_bytes(self.root)

    def test_traversal_permission_error_propagates(self):
        with mock.patch("hardsurface.budgets.os.scandir", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                tree_file_bytes(self.root)

    def test_traversal_iteration_error_propagates_and_closes_iterator(self):
        iterator = mock.Mock()
        iterator.__next__ = mock.Mock(side_effect=PermissionError("iteration denied"))
        with mock.patch("hardsurface.budgets.os.scandir", return_value=iterator):
            with self.assertRaises(PermissionError):
                tree_file_bytes(self.root)
        iterator.close.assert_called_once()

    def test_files_and_vanished_names_still_consume_file_budget(self):
        for name in ("one", "two"):
            (self.root / name).touch()
        with self.assertRaises(BudgetExceeded) as caught:
            tree_file_bytes(self.root, max_files=1)
        self.assertEqual(caught.exception.resource, "disk_accounting_files")
        self.assertEqual(caught.exception.observed, 2)

        def vanish(name, *args, **kwargs):
            if name in ("one", "two"):
                (self.root / name).unlink()
            return self.real_stat(name, *args, **kwargs)

        with mock.patch("hardsurface.budgets.os.stat", side_effect=vanish):
            with self.assertRaises(BudgetExceeded) as caught:
                tree_file_bytes(self.root, max_files=1)
        self.assertEqual(caught.exception.resource, "disk_accounting_files")

    def test_empty_directories_consume_entry_budget(self):
        for name in ("one", "two"):
            (self.root / name).mkdir()
        with self.assertRaises(BudgetExceeded) as caught:
            tree_file_bytes(self.root, max_entries=1)
        self.assertEqual(caught.exception.resource, "disk_accounting_entries")
        self.assertEqual(caught.exception.observed, 2)

    def test_invalid_limits_rejected(self):
        for name in ("max_files", "max_entries"):
            for value in (True, -1, 1.5, None):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    tree_file_bytes(self.root, **{name: value})

    def test_missing_root_and_non_directory_root_fail_closed(self):
        with self.assertRaises(FileNotFoundError):
            tree_file_bytes(self.base / "missing")
        with self.assertRaises(ValueError):
            tree_file_bytes(self.outside / "unrelated.bin")

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "Linux fd inspection")
    def test_nested_success_and_failure_release_all_handles(self):
        child = self.root / "child"
        child.mkdir()
        (child / "leaf").touch()
        before = len(os.listdir("/proc/self/fd"))
        self.assertEqual(tree_file_bytes(self.root), 0)
        (child / "link").symlink_to(self.outside)
        with self.assertRaises(ValueError):
            tree_file_bytes(self.root)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)


if __name__ == "__main__":
    unittest.main()
