"""機能4: 画像生成（esora API）で衣装を差し替えた1枚を作る。

ローカルで画素をいじる機能1〜3 とは違い、ここは esora のサーバーに生成を
依頼するだけの薄い層になる。役割は2つの参照画像を正しい順で渡すことと、
結果をセッションに記録すること。

参照の順序がそのままプロンプトの意味になる。「1枚目の参照画像」「2枚目の
参照画像」という言い方は config.DEFAULT_IMAGE_PROMPT の前提でもあるので、
img_refs の並びを変えるときは呼び出し側でプロンプトも合わせて直すこと。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config, esora_cli, manifest
from .io_paths import Session

Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    return None


def _canonical_name(path: Path) -> Path:
    """esora-api が付けたファイル名を短い固定名に変える。

    esora-api はプロンプト全文をファイル名にすることがある。日本語の
    長いプロンプトだとパスが Windows の上限（260文字）に迫る／超えることが
    あり、他の工程（ダウンロード・Gradioのプレビュー）に渡すには不安定。
    同じフォルダ内で確定名にリネームするだけなので、生成結果そのものは
    変わらない。
    """
    canonical = path.with_name(f"generated{path.suffix or '.png'}")
    if canonical == path or not path.is_file():
        return path
    if canonical.exists():
        canonical.unlink()
    path.rename(canonical)
    return canonical


@dataclass
class Result:
    session: Session
    image_path: Path
    model: str
    generation_id: str
    record: dict

    @property
    def report(self) -> str:
        return "\n".join(
            [
                f"セッション: {self.session.name}",
                f"モデル: {self.model}",
                f"generation id: {self.generation_id}",
                f"出力: {self.image_path}",
            ]
        )


def run(
    session: Session,
    base_image: str | Path,
    design_image: str | Path,
    *,
    prompt: str,
    model: str,
    aspect_ratio: str = config.DEFAULT_IMAGE_ASPECT_RATIO,
    image_size: str = config.DEFAULT_IMAGE_SIZE,
    seed: int | None = None,
    progress: Progress = _noop,
) -> Result:
    if not prompt.strip():
        raise ValueError("プロンプトが空です。")
    if not model.strip():
        raise ValueError(
            "モデルが指定されていません。先に「モデルを確認」で id を選んでください。"
        )
    for label, path in (("ベース画像(画像1)", base_image), ("デザイン参照(画像2)", design_image)):
        if not path or not Path(path).is_file():
            raise ValueError(f"{label}が見つかりません: {path!r}")

    session.clear(session.generated_images)

    progress(0.05, "画像生成をリクエスト")
    record = esora_cli.generate_image(
        prompt,
        model=model,
        img_refs=[str(base_image), str(design_image)],
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        seed=seed,
        out_dir=session.generated_images,
        progress=lambda fraction, message: progress(0.05 + fraction * 0.85, message),
    )

    saved = record.get("saved") or []
    if not saved:
        raise esora_cli.EsoraCliError(
            "生成は完了(completed)しましたが、保存されたファイルがありません。"
        )
    image_path = _canonical_name(Path(saved[0]))

    progress(0.95, "manifest を保存")
    manifest.update(
        session.manifest_path,
        "feature4",
        {
            "prompt": prompt,
            "model": model,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "seed": seed,
            "references": {
                "base_image": str(base_image),
                "design_image": str(design_image),
            },
            "generation_id": record.get("id"),
            "saved": [str(p) for p in saved],
        },
    )

    progress(1.0, "完了")
    return Result(
        session=session,
        image_path=image_path,
        model=model,
        generation_id=str(record.get("id")),
        record=record,
    )
