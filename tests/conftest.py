import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.corpus import Corpus  # noqa: E402

TODAY = date(2026, 11, 6)


@pytest.fixture(scope="session")
def corpus():
    return Corpus.load(config.DOCUMENTS_DIR)
