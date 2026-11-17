"""The cache key of a query result (LD-04 3.3 · DD-50 · DD-81 · DD-99 review item seven).

No cache backend in the first version (DD-99): this is the key a cache will use, written and tested now so that the
cache, when performance data shows it is needed, cannot be added without it. Three parts, none optional:
  what was asked      metrics with their versions, dimensions, filters, range, granularity, data version
  which data          the publication sequence read: a correction published later changes it, so a cached result
                      of the corrected days is never served again (DD-81)
  who may see what    the role, the fingerprint of the sensitive fields the role may see, and the row scope that
                      applied -- the department, or the agent for "only my own rows". Leaving the row scope out lets
                      one department's rows (DD-50), or one call taker's, reach another through the cache.
"""

from __future__ import annotations

import hashlib
import json


def cache_key(*, request_digest: str, metric_versions: dict[str, list[int]], publication_seq: int | None,
              role: str, visible_sensitive: list[str], row_scope: str) -> str:
    material = {
        "request": request_digest,
        "metric_versions": {k: sorted(v) for k, v in sorted(metric_versions.items())},
        "publication_seq": publication_seq,
        "role": role,
        "fields": hashlib.sha256(json.dumps(sorted(visible_sensitive)).encode()).hexdigest(),
        "row_scope": row_scope,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
