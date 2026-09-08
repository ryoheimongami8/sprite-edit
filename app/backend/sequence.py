"""コマ列を、指定した fps と尺のフレーム列に展開する。

fps とポーズの表示時間は別のものだという前提で書いてある。6 枚をそのまま
24fps で並べれば全体が 0.25 秒で終わる。ここでやるのは同じ絵を必要な数だけ
複製して尺を作ることで、新しい中間ポーズは 1 枚も作らない。

「ゆっくりパラパラ動く」の中身がこれ。滑らかさを足しているのではなく、
1 ポーズあたりの滞在時間を伸ばしている。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Timeline:
    """出力フレーム 1 枚ごとに、元コマの番号を並べたもの。

    fps と尺から機械的に決まるので、この配列が「どのフレームがどのポーズ
    か」の唯一の正解になる。生成後にフレームを選び直すときも、まずここへ
    戻って照合する。
    """

    indices: list[int]
    fps: int
    frame_count: int
    source_count: int
    holds: list[int]
    loop: bool

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps

    def to_dict(self) -> dict:
        return {
            "fps": self.fps,
            "frame_count": self.frame_count,
            "source_count": self.source_count,
            "holds": self.holds,
            "loop": self.loop,
            "duration_s": round(self.duration_s, 4),
            "indices": self.indices,
        }

    def summary(self) -> str:
        low, high = min(self.holds), max(self.holds)
        hold = f"{low}" if low == high else f"{low}-{high}"
        return (
            f"{self.fps}fps x {self.duration_s:.2f}秒 = {self.frame_count}フレーム / "
            f"元コマ {self.source_count}枚 / 1ポーズ {hold}フレーム "
            f"({low / self.fps:.3f}-{high / self.fps:.3f}秒) / "
            f"{'巡回あり' if self.loop else '1周のみ'}"
        )


def build_timeline(
    source_count: int,
    *,
    fps: int,
    duration_s: float,
    loop: bool = False,
) -> Timeline:
    """尺を各コマへ割り振る。

    端数は先頭のコマから 1 フレームずつ配る。捨てて尺を縮めることはしない
    — 指定した秒数がそのまま出ないと、あとで別の設定と比べるときに何が
    効いたのか分からなくなる。
    """
    if source_count <= 0:
        raise ValueError("コマが 1 枚もありません。")
    if fps <= 0:
        raise ValueError("fps は 1 以上にしてください。")

    total = max(source_count, round(fps * duration_s))

    if loop:
        # 尺を埋めるまで 0,1,2,…,n-1,0,1,… と巡回する。保持は 1 フレーム。
        indices = [i % source_count for i in range(total)]
        holds = [1] * source_count
        return Timeline(indices, fps, total, source_count, holds, True)

    base, remainder = divmod(total, source_count)
    holds = [base + (1 if i < remainder else 0) for i in range(source_count)]

    indices: list[int] = []
    for source_index, hold in enumerate(holds):
        indices.extend([source_index] * hold)
    return Timeline(indices, fps, len(indices), source_count, holds, False)


def expand(frames: list, timeline: Timeline) -> list:
    """タイムラインどおりに参照を並べ替える。画像そのものは複製しない。

    同じ ``Image`` オブジェクトが何度も並ぶ。書き出し側が読むだけなので
    問題にならず、120 枚ぶんのコピーを持たずに済む。
    """
    if timeline.source_count != len(frames):
        raise ValueError(
            f"タイムラインは元コマ {timeline.source_count} 枚を前提にしていますが、"
            f"渡されたのは {len(frames)} 枚です。"
        )
    return [frames[i] for i in timeline.indices]
