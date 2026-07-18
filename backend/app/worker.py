"""Small injectable worker boundary for scheduled PrintVault library scans."""

from __future__ import annotations

from app.services.filesystem import LibraryLike
from app.services.indexer import LibraryIndexer, ScanResult


class IndexingWorker:
    """Schedule-facing façade that never accepts a caller-provided host path."""

    def __init__(self, indexer: LibraryIndexer) -> None:
        self.indexer = indexer

    def index_library(self, library: LibraryLike) -> ScanResult:
        return self.indexer.scan(library)


__all__ = ["IndexingWorker"]
