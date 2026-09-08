"""起動用。

    python app.py

処理は一切書かない。ここに条件分岐が入り始めると、UI からしか叩けない
ロジックができて、バックエンドだけを動かす検証ができなくなる。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# `python app.py` を app/ の外から叩かれても動くようにする。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend import config  # noqa: E402
from frontend.ui_main import build  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprite Edit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument(
        "--share", action="store_true", help="Gradio の共有リンクを作る"
    )
    parser.add_argument(
        "--open", action="store_true", help="起動時にブラウザを開く"
    )
    args = parser.parse_args()

    config.WORK_ROOT.mkdir(parents=True, exist_ok=True)
    build().launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=args.open,
        show_error=True,
        # 出力は work/ の下にある実ファイルを直接返す。ここを許可しないと
        # 動画も First Frame も UI に出せない。
        allowed_paths=[str(config.WORK_ROOT)],
    )


if __name__ == "__main__":
    main()
