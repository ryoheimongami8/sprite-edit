"""セッションに何をしたかを 1 つの JSON に残す。

これは記録のためだけのものではない。あとで生成結果を元のコマへ戻すとき、
必要になるのは「どのセル座標から切ったか」「どのフレームがどのポーズか」
「元アルファはどのファイルか」の三つで、いずれもここにしか無い。工程を
またいで持ち回る値なので、後付けにすると全部やり直しになる。

節ごとに上書きする。機能2 を回し直しても機能1 の記録は残る。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def file_digest(path: Path) -> str:
    """内容で素材を同定する。同じ名前の別バージョンを取り違えないため。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 壊れた manifest で全体を止めない。工程はやり直せる。
        return {"schema_version": SCHEMA_VERSION, "recovered": True}


def update(path: Path, section: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = load(path)
    data["schema_version"] = SCHEMA_VERSION
    data[section] = payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def section(path: Path, name: str) -> dict[str, Any] | None:
    return load(path).get(name)


def require(path: Path, name: str, hint: str) -> dict[str, Any]:
    """無ければ、次に何をすればいいかを言って落ちる。"""
    found = section(path, name)
    if not found:
        raise ValueError(f"このセッションには {name} の記録がありません。{hint}")
    return found
