"""
Index internal knowledge documents into the local vector store.
Called from seed/seed.py after database initialisation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import rag
from backend.config import rag_reindex_on_seed


def main() -> dict:
    force = rag_reindex_on_seed()
    result = rag.index_corpus(force=force)
    if result.get("skipped"):
        print(f"  - RAG index up to date ({result['chunks']} chunks)")
    elif result.get("chunks", 0) > 0:
        print(
            f"  - RAG index built: {result['chunks']} chunks from "
            f"{result.get('documents', '?')} documents "
            f"(embed: {result.get('embed_mode', 'unknown')})"
        )
    else:
        reason = result.get("reason", "unknown")
        print(f"  - RAG indexing skipped ({reason})")
    return result


if __name__ == "__main__":
    main()
