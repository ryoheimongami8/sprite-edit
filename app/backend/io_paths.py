"""1 回の作業に 1 つのディレクトリを与える。

機能1 と機能2 は別のタブで別々に走るが、扱っているのは同じ素材の同じ工程
なので、出力が混ざると「どの動画がどの分割から来たのか」が分からなくなる。
セッションディレクトリを作業単位にして、そこに manifest ごと閉じ込める。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(name: str) -> str:
    cleaned = _UNSAFE.sub("_", name).strip("_.")
    return cleaned or "sprite"


@dataclass(frozen=True)
class Session:
    """1 素材 1 回分の作業ディレクトリ。

    サブディレクトリは作られた時点で存在する。「あとで必要になったら掘る」
    にすると、失敗した工程と、まだ走っていない工程の区別がつかなくなる。
    """

    root: Path

    @property
    def name(self) -> str:
        return self.root.name

    # 機能1
    @property
    def source(self) -> Path:
        """アップロードされたシートの原本。上書き前の姿を必ず残す。"""
        return self.root / "source"

    @property
    def frames_rgba(self) -> Path:
        """切り出した各コマ。透過を保ったまま。最終復元の材料になる。"""
        return self.root / "01_frames_rgba"

    @property
    def shadow_masks(self) -> Path:
        """地面の影だけを指すマスク。機能3 で影の RGB を戻すのに使う。"""
        return self.root / "01b_shadow_masks"

    @property
    def sequence(self) -> Path:
        """タイムライン展開後の連番 PNG。背景合成済み・不透明。"""
        return self.root / "02_sequence_png"

    @property
    def video(self) -> Path:
        return self.root / "03_video"

    # 機能2
    @property
    def upscaled(self) -> Path:
        return self.root / "04_upscaled_png"

    @property
    def upscaled_video(self) -> Path:
        return self.root / "05_upscaled_video"

    @property
    def first_frame(self) -> Path:
        return self.root / "06_first_frame"

    # 機能3
    @property
    def incoming_video(self) -> Path:
        """アップロードされた編集後の動画の原本。"""
        return self.root / "07_incoming_video"

    @property
    def extracted_frames(self) -> Path:
        """編集後の動画を全展開した連番。選ばなかったフレームも残す。"""
        return self.root / "08_extracted_frames"

    @property
    def rebuilt_cells(self) -> Path:
        """ポーズごとに作り直したセル。透過方式ごとにサブフォルダを分ける。"""
        return self.root / "09_rebuilt_cells"

    @property
    def rebuilt_sheets(self) -> Path:
        """最終的なスプライトシート。透過方式ごとに 1 枚ずつ。"""
        return self.root / "10_rebuilt_sheets"

    # 機能4
    @property
    def generated_images(self) -> Path:
        """esora で生成した画像（衣装差し替え後）。"""
        return self.root / "11_generated_image"

    # 機能5
    @property
    def generated_videos(self) -> Path:
        """esora で生成した動画（衣装差し替え後）。"""
        return self.root / "12_generated_video"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def subdirs(self) -> list[Path]:
        return [
            self.source,
            self.frames_rgba,
            self.shadow_masks,
            self.sequence,
            self.video,
            self.upscaled,
            self.upscaled_video,
            self.first_frame,
            self.incoming_video,
            self.extracted_frames,
            self.rebuilt_cells,
            self.rebuilt_sheets,
            self.generated_images,
            self.generated_videos,
        ]

    def ensure(self) -> "Session":
        for path in [self.root, *self.subdirs()]:
            path.mkdir(parents=True, exist_ok=True)
        return self

    def clear(self, *dirs: Path) -> None:
        """再実行の前に、その工程の出力だけを消す。

        セッションごと作り直さないのは、機能2 をやり直すたびに機能1 の分割
        結果まで消えるのでは、切り分けができなくなるため。
        """
        for path in dirs:
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)


def new_session(source_name: str, work_root: Path | None = None) -> Session:
    root = (work_root or config.WORK_ROOT) / (
        f"{datetime.now():%Y%m%d-%H%M%S}_{_slug(Path(source_name).stem)}"
    )
    return Session(root).ensure()


def open_session(root: str | Path) -> Session:
    return Session(Path(root))


def list_sessions(work_root: Path | None = None) -> list[Session]:
    """新しい順。manifest を持つものだけを、選べる作業として扱う。"""
    base = work_root or config.WORK_ROOT
    if not base.is_dir():
        return []
    found = [
        Session(child)
        for child in base.iterdir()
        if child.is_dir() and (child / "manifest.json").is_file()
    ]
    return sorted(found, key=lambda s: s.name, reverse=True)


def numbered(directory: Path, index: int, suffix: str = ".png") -> Path:
    """``0000.png`` 形式。連番は ffmpeg にそのまま食わせられる桁数で固定する。"""
    return directory / f"{index:04d}{suffix}"


def read_sequence(directory: Path, suffix: str = ".png") -> list[Path]:
    return sorted(directory.glob(f"*{suffix}"))


def latest_file(directory: Path, pattern: str = "*") -> Path | None:
    """機能4・5 が「前の工程の出力」を初期値として拾うための、ただの最新1件。

    無ければ None を返すだけで、呼び出し側はアップロード必須として扱う。
    """
    if not directory.is_dir():
        return None
    matches = sorted(
        (p for p in directory.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return matches[-1] if matches else None
