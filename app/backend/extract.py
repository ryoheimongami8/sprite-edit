"""機能3: 編集後の動画から、ポーズごとの代表フレームを選ぶ。

編集後の動画は元の 6 ポーズと同じフレーム数・fps で返ってくるとは限らない
（検証動画は 120→121 フレーム、120→1026px の想定に対し 960px だった）。
だから frame index を直接対応させず、**タイムライン全体に対する 0-1 の
位置（時間比率）** に一度変換してから、新しい動画の実際のフレーム数へ
掛け直す。フレーム数がずれても、各ポーズの「だいたいここ」という時刻の
相対位置さえ合っていれば狙ったフレームに当たる。

新しい中間フレームは作らない。動画から 1 枚を選ぶだけで、合成も補間も
しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import video


@dataclass(frozen=True)
class Sampled:
    """1 ポーズぶんの選定結果。どこから選んだかを、選んだ絵と一緒に持つ。"""

    pose_index: int
    frame_index: int  # 展開した新動画の連番の中での位置（0始まり）
    path: Path
    time_s: float | None
    source_fraction: float  # 元タイムライン全体に対するこのポーズの代表点 (0-1)

    def to_dict(self) -> dict:
        return {
            "pose_index": self.pose_index,
            "frame_index": self.frame_index,
            "path": str(self.path),
            "time_s": self.time_s,
            "source_fraction": round(self.source_fraction, 6),
        }


def hold_windows(holds: list[int]) -> list[tuple[int, int]]:
    """``holds``（各ポーズが元タイムラインで占めたフレーム数）を区間にする。

    区間は ``[start, end)``。``sequence.build_timeline`` が作った並びと
    対応させるための下ごしらえで、ここだけ見れば元の何フレーム目から
    何フレーム目までが同じポーズだったか分かる。
    """
    windows: list[tuple[int, int]] = []
    cursor = 0
    for hold in holds:
        windows.append((cursor, cursor + hold))
        cursor += hold
    return windows


def pose_fractions(holds: list[int], *, pick: str = "middle") -> list[float]:
    """各ポーズの代表点を、元タイムライン全体に対する 0-1 の位置で返す。

    ``middle`` を既定にしてあるのは、切り替わりの前後は生成側が補間して
    形が不安定になりやすいから。窓の中でいちばん安定しているはずの中央を
    取る。``first`` / ``last`` は比較用に残してある。
    """
    if pick not in {"first", "middle", "last"}:
        raise ValueError(f"未知の pick です: {pick!r}（first / middle / last）")

    total = sum(holds)
    if total <= 1:
        return [0.0 for _ in holds]

    fractions = []
    for start, end in hold_windows(holds):
        if pick == "first":
            point = start
        elif pick == "last":
            point = end - 1
        else:
            point = start + (end - 1 - start) / 2
        fractions.append(point / (total - 1))
    return fractions


def map_to_sequence(fractions: list[float], frame_count: int) -> list[int]:
    """0-1 の位置を、実際の連番の中の index に変換する。"""
    if frame_count <= 1:
        return [0 for _ in fractions]
    return [
        min(frame_count - 1, max(0, round(fraction * (frame_count - 1))))
        for fraction in fractions
    ]


def duration_mismatch(original_duration_s: float, new_duration_s: float | None) -> float | None:
    """尺のずれを比率で返す。大きくずれているときの注意書きに使う。"""
    if not new_duration_s or original_duration_s <= 0:
        return None
    return abs(new_duration_s - original_duration_s) / original_duration_s


def sample_video(
    video_path: Path,
    dest_dir: Path,
    holds: list[int],
    *,
    pick: str = "middle",
) -> tuple[list[Sampled], list[Path]]:
    """動画を全展開し、各ポーズの代表フレームを選ぶ。

    展開そのものは 1 回だけ行う。6 回シークするより、短い動画では通し読み
    のほうが確実で、かつ ``dest_dir`` に全フレームが残るので後から見返せる。
    """
    frames = video.decode_sequence(video_path, dest_dir)
    fractions = pose_fractions(holds, pick=pick)
    indices = map_to_sequence(fractions, len(frames))

    fps = video.parse_fps(video.probe(video_path).get("r_frame_rate"))
    sampled = [
        Sampled(
            pose_index=pose_index,
            frame_index=frame_index,
            path=frames[frame_index],
            time_s=(frame_index / fps) if fps else None,
            source_fraction=fraction,
        )
        for pose_index, (frame_index, fraction) in enumerate(zip(indices, fractions))
    ]
    return sampled, frames
