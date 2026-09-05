"""原始响应快照 —— 溯源链的最后一环。

事实 → source_id → 快照文件 → 当时接口返回的原始内容。
没有这一层，「昨天的研报今天复现不出来」是必然而非偶然。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Snapshot:
    source_id: str
    source: str
    url: str
    fetched_at: datetime
    payload: Any
    params: dict[str, Any] | None = None


class SnapshotStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, source_id: str) -> Path:
        return self.root / f"{source_id}.json"

    def save(
        self,
        source: str,
        payload: Any,
        url: str,
        fetched_at: datetime,
        params: dict[str, Any] | None = None,
    ) -> str:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(
            f"{url}|{fetched_at.isoformat()}|{body}".encode("utf-8")
        ).hexdigest()[:12]
        source_id = f"{source}_{fetched_at:%Y%m%d%H%M%S}_{digest}"

        path = self.path_for(source_id)
        if not path.exists():
            path.write_text(json.dumps({
                "source_id": source_id,
                "source": source,
                "url": url,
                "fetched_at": fetched_at.isoformat(),
                "params": params,
                "payload": payload,
            }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return source_id

    def load(self, source_id: str) -> Snapshot:
        path = self.path_for(source_id)
        if not path.exists():
            raise KeyError(f"没有快照 {source_id}")
        d = json.loads(path.read_text(encoding="utf-8"))
        return Snapshot(
            source_id=d["source_id"],
            source=d["source"],
            url=d["url"],
            fetched_at=datetime.fromisoformat(d["fetched_at"]),
            payload=d["payload"],
            params=d.get("params"),
        )
