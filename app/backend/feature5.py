"""機能5: 動画生成（esora API）で衣装を差し替えた動画を作る。

**動画をそのまま参照に渡す経路は無い。実機で確認済み。** esora の CLI に
動画参照専用のオプション（--vid-ref）は無く未実装としてドキュメントに
残っているだけで、以前はクライアント側のチェックを迂回して動画由来の
asset_id を --img-ref に渡せないか試したが、サーバー側が

    "Asset ... is video; reference images must be images"

と明示的に拒否することを確認した。つまり esora は現状、動画そのものからの
動作・タイミングの取り込みに対応していない。

代わりに、参考動画から等間隔で代表フレームを複数枚抜き出し、**通常の画像
参照**として渡す。動画の正確なタイミング（何秒で切り替わるか）そのものは
渡せないので、静止画の枚数と順序でポーズの流れを伝え、保持時間や切り替え
方（補間しない・瞬時に切り替える、など）はプロンプトの文章に委ねる。
これは esora が普通にサポートしている画像参照の使い方なので、以前のような
「サーバーが拒否するかもしれない」という不確実性は無い。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config, esora_cli, manifest, video
from .io_paths import Session

Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    return None


def _canonical_name(path: Path) -> Path:
    """esora-api が付けたファイル名を短い固定名に変える（機能4と同じ理由）。"""
    canonical = path.with_name(f"generated{path.suffix or '.mp4'}")
    if canonical == path or not path.is_file():
        return path
    if canonical.exists():
        canonical.unlink()
    path.rename(canonical)
    return canonical


def extract_reference_frames(video_path: Path, dest_dir: Path, count: int) -> list[Path]:
    """参考動画から、全体に均等な間隔で ``count`` 枚を抜き出す。

    展開は 1 回だけ（機能3 の extract.py と同じ考え方）。取り出す枚数を
    動画の実フレーム数より多く指定したら、有るだけ全部を返す。
    """
    frames = video.decode_sequence(video_path, dest_dir)
    if count <= 0 or count >= len(frames):
        return frames
    if count == 1:
        return [frames[0]]
    step = (len(frames) - 1) / (count - 1)
    indices: list[int] = []
    for i in range(count):
        index = round(i * step)
        if not indices or index != indices[-1]:
            indices.append(index)
    return [frames[i] for i in indices]


@dataclass
class Result:
    session: Session
    video_path: Path
    model: str
    generation_id: str
    reference_frames: list[Path]
    record: dict

    @property
    def report(self) -> str:
        return "\n".join(
            [
                f"セッション: {self.session.name}",
                f"モデル: {self.model}",
                f"参照フレーム: {len(self.reference_frames)}枚 + 衣装参照画像1枚",
                f"generation id: {self.generation_id}",
                f"出力: {self.video_path}",
            ]
        )


def run(
    session: Session,
    video_path: str | Path,
    image_path: str | Path,
    *,
    prompt: str,
    model: str,
    aspect_ratio: str = config.DEFAULT_VIDEO_ASPECT_RATIO,
    resolution: str = config.DEFAULT_VIDEO_RESOLUTION,
    duration: int = config.DEFAULT_VIDEO_DURATION_S,
    audio: bool = False,
    frame_count: int = config.VIDEO_REFERENCE_FRAME_COUNT,
    seed: int | None = None,
    progress: Progress = _noop,
) -> Result:
    if not prompt.strip():
        raise ValueError("プロンプトが空です。")
    if not model.strip():
        raise ValueError(
            "モデルが指定されていません。先に「モデルを確認」で id を選んでください。"
        )
    if not video_path or not Path(video_path).is_file():
        raise ValueError(f"参考動画が見つかりません: {video_path!r}")
    if not image_path or not Path(image_path).is_file():
        raise ValueError(f"参考画像が見つかりません: {image_path!r}")
    if frame_count < 1:
        raise ValueError("代表フレーム枚数は1以上にしてください。")

    session.clear(session.generated_videos)
    frames_dir = session.generated_videos / "_reference_frames"

    progress(0.02, "参考動画から代表フレームを抽出")
    frames = extract_reference_frames(Path(video_path), frames_dir, frame_count)
    if not frames:
        raise ValueError("参考動画からフレームを抽出できませんでした。")

    # 画像参照の順序＝プロンプトが語る「1枚目〜N枚目＝ポーズ、最後＝衣装」の
    # 前提そのもの。ここを変えるなら config.DEFAULT_VIDEO_PROMPT も直すこと。
    refs = [str(path) for path in frames] + [str(image_path)]

    progress(0.15, "動画生成をリクエスト")
    record = esora_cli.generate_video(
        prompt,
        model=model,
        img_refs=refs,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration=duration,
        audio=audio,
        seed=seed,
        out_dir=session.generated_videos,
        progress=lambda fraction, message: progress(0.15 + fraction * 0.80, message),
    )

    saved = record.get("saved") or []
    if not saved:
        raise esora_cli.EsoraCliError(
            "生成は完了(completed)しましたが、保存されたファイルがありません。"
        )
    output_path = _canonical_name(Path(saved[0]))

    progress(0.95, "manifest を保存")
    manifest.update(
        session.manifest_path,
        "feature5",
        {
            "prompt": prompt,
            "model": model,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "duration": duration,
            "audio": audio,
            "frame_count": len(frames),
            "seed": seed,
            "references": {
                "video": str(video_path),
                "extracted_frames": [str(p) for p in frames],
                "image": str(image_path),
            },
            "generation_id": record.get("id"),
            "saved": [str(p) for p in saved],
        },
    )

    progress(1.0, "完了")
    return Result(
        session=session,
        video_path=output_path,
        model=model,
        generation_id=str(record.get("id")),
        reference_frames=frames,
        record=record,
    )
