"""ffmpeg を呼んで PNG 連番 ⇄ mp4 を変換する。

工程の正本は PNG 連番のほうで、mp4 は目視確認用に出す。H.264 は色差を
間引くので、ドットの硬い輪郭は必ず少しにじむ。そのにじんだ画から次の工程
を進めないために、機能2 の入力も mp4 ではなく PNG 連番を読む。

機能3 は逆方向 — 編集後の mp4 を PNG 連番へ戻す。ここも一度で全フレーム
を PNG に落としてから Pillow 側で選ぶ。フレームごとに `-ss` で個別に
シークすると、コーデックによっては直前のキーフレームに丸められて狙った
フレームからずれることがあり、短い動画では通し読みのほうが確実で速い。

ffmpeg 本体は探して使う。PATH に無ければ imageio-ffmpeg が同梱している
実行ファイルへ落ちるので、利用者に別途インストールを求めずに済む。
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from . import config


class FfmpegMissing(RuntimeError):
    """ffmpeg が見つからない。PNG 連番までは出ているので、その旨を伝える。"""


class FfmpegFailed(RuntimeError):
    """ffmpeg が非ゼロ終了した。stderr の末尾を添えて投げる。"""


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 — 理由を問わず「無い」に畳む
        raise FfmpegMissing(
            "ffmpeg が見つかりません。PATH に通すか、"
            "`pip install imageio-ffmpeg` を入れてください。"
        ) from exc


def ffmpeg_version() -> str:
    """UI に出す 1 行。どの ffmpeg を掴んでいるかは効いてくるので見せる。"""
    try:
        binary = ffmpeg_path()
    except FfmpegMissing as exc:
        return str(exc)
    result = subprocess.run(
        [binary, "-version"], capture_output=True, text=True, check=False
    )
    head = (result.stdout or "").splitlines()
    return f"{head[0] if head else 'ffmpeg'}  [{binary}]"


def _run(argv: list[str]) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-12:])
        raise FfmpegFailed(f"ffmpeg が失敗しました (exit {result.returncode})\n{tail}")


def encode_sequence(
    sequence_dir: Path,
    destination: Path,
    *,
    fps: int,
    crf: int = config.DEFAULT_CRF,
    pix_fmt: str = config.DEFAULT_PIX_FMT,
    pattern: str = "%04d.png",
) -> Path:
    """``0000.png`` から始まる連番を mp4 にする。

    ``-start_number 0`` を明示するのは、image2 の既定が 1 始まりで、0 から
    並べた連番だと先頭 1 枚を黙って落とすため。落ちても再生はできてしまう
    ので、気づかないまま 1 フレームずれた動画で先へ進むことになる。
    """
    binary = ffmpeg_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            binary,
            "-y",
            "-loglevel", "error",
            "-framerate", str(fps),
            "-start_number", "0",
            "-i", str(sequence_dir / pattern),
            "-c:v", "libx264",
            "-preset", "veryslow",
            "-crf", str(crf),
            "-pix_fmt", pix_fmt,
            # 既定の補間は色をなめらかに混ぜる。ここでは拡大縮小をしない
            # 前提だが、encoder 側の都合で寸法が触られたときに輪郭が溶け
            # ないよう最近傍で固定しておく。
            "-sws_flags", "neighbor",
            "-movflags", "+faststart",
            str(destination),
        ]
    )
    return destination


def decode_sequence(source: Path, dest_dir: Path, pattern: str = "%05d.png") -> list[Path]:
    """mp4（や mov/webm）を連番 PNG に展開する。0 始まり。

    PNG はロスレスなので、ここでは画質を一切落とさない。縮小やキー抜きは
    あとで Pillow 側が担当する — ffmpeg にやらせると補間方式や色空間の
    変換が混ざり込み、どちらの工程が効いたのか切り分けられなくなる。
    """
    binary = ffmpeg_path()
    dest_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            binary,
            "-y",
            "-loglevel", "error",
            "-i", str(source),
            "-start_number", "0",
            str(dest_dir / pattern),
        ]
    )
    suffix = Path(pattern).suffix or ".png"
    frames = sorted(dest_dir.glob(f"*{suffix}"))
    if not frames:
        raise FfmpegFailed(f"{source} からフレームを取り出せませんでした。")
    return frames


def probe(path: Path) -> dict[str, str]:
    """出来上がった mp4 の寸法・fps・フレーム数を読み返す。

    書いたつもりの尺と実際の尺がずれるのは encoder 側でも起きるので、
    出力を信じずに確認して UI に出す。
    """
    binary = shutil.which("ffprobe")
    if binary is None:
        candidate = Path(ffmpeg_path()).with_name("ffprobe")
        binary = str(candidate) if candidate.exists() else None
    if binary is None:
        return {}

    result = subprocess.run(
        [
            binary,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,nb_frames,pix_fmt",
            "-of", "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def parse_fps(value: str | None) -> float | None:
    """``"24/1"`` のような ffprobe の分数表記を float にする。"""
    if not value:
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        den_f = float(den) if den else 0.0
        return float(num) / den_f if den_f else None
    try:
        return float(value)
    except ValueError:
        return None


def duration_seconds(path: Path) -> float | None:
    """コンテナのメタデータから秒数を読む。フレームを数え直しはしない。"""
    binary = shutil.which("ffprobe")
    if binary is None:
        candidate = Path(ffmpeg_path()).with_name("ffprobe")
        binary = str(candidate) if candidate.exists() else None
    if binary is None:
        return None
    result = subprocess.run(
        [
            binary,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def describe(path: Path) -> str:
    info = probe(path)
    if not info:
        return f"{path.name}  ({path.stat().st_size / 1024:.0f} KB)"
    fps = parse_fps(info.get("r_frame_rate"))
    fps_text = f"{fps:g}" if fps else "?"
    return (
        f"{path.name}  {info.get('width', '?')}x{info.get('height', '?')}  "
        f"{fps_text}fps  {info.get('nb_frames', '?')}フレーム  "
        f"{info.get('pix_fmt', '?')}  ({path.stat().st_size / 1024:.0f} KB)"
    )
