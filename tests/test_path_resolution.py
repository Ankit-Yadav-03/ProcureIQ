import core.db as db
import core.logger as logger
import main


def test_project_paths_are_resolved_from_repo_root():
    assert logger.LOG_FILE.is_absolute()
    assert db.DB_PATH.is_absolute()
    assert main.FRONTEND_DIR.is_absolute()
    assert main.INDEX_FILE.is_absolute()
    assert main.INDEX_FILE.exists()
