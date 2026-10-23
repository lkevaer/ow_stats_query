from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api_server.db import task_db


class FreshInstallTests(unittest.TestCase):
    def test_task_database_creates_missing_runtime_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "new-data" / "tasks.sqlite3"
            with patch.object(task_db, "_TASK_DB_PATH", str(path)):
                task_db.init_task_db()
                task_db.create_task("fresh", "/api/example", {})
            self.assertTrue(path.is_file())
