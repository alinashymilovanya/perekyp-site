import tempfile
import unittest
from pathlib import Path

from iphone_monitor.instance_lock import InstanceLock


class InstanceLockTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.path = self.directory / "bot.lock"

    def test_second_instance_is_rejected(self):
        first = InstanceLock(self.path)
        second = InstanceLock(self.path)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_lock_file_records_pid(self):
        lock = InstanceLock(self.path)
        self.assertTrue(lock.acquire())
        try:
            self.assertTrue(self.path.exists())
            self.assertEqual(lock.pid, __import__("os").getpid())
        finally:
            lock.release()

    def test_release_is_idempotent(self):
        lock = InstanceLock(self.path)
        self.assertTrue(lock.acquire())
        lock.release()
        lock.release()


if __name__ == "__main__":
    unittest.main()
