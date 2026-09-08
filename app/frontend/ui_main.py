"""画面全体の組み立て。

画面は 2 つある。

  かんたん（既定） … 1 画面。機能1→2→4 を1ボタンで通し、手作業の動画
                     生成を挟んで、機能3 を同じ画面で実行する。
  詳細（旧UI）     … 機能ごとのタブ。工程を1つずつ確かめたいとき用。

切り替えは上部のスイッチで、どちらも同じ `session_state` を見ている。
かんたん側で作ったセッションは、そのまま旧UI のタブにも引き継がれる。
"""

from __future__ import annotations

import gradio as gr

from backend import config, video

from . import (
    tab_extract,
    tab_generate_image,
    tab_generate_video,
    tab_slice,
    tab_upscale,
    ui_simple,
)

TITLE = "Sprite Edit — 分解 / アップスケール / 再構成"

SIMPLE = "かんたん"
ADVANCED = "詳細（旧UI）"

#: Gradio 6 は ``Blocks(css=...)`` を launch() へ移したので、どのバージョン
#: でも同じに効く形（素の <style>）で入れている。見た目のためだけの指定なので、
#: 効かない環境があっても動作には響かない。
CSS = """
<style>
.gradio-container { max-width: 1500px !important; }
</style>
"""


def _environment_note() -> str:
    """起動時に、掴んでいる ffmpeg と作業ディレクトリを見せる。

    出力がどこへ行ったか分からない、mp4 だけできない、という詰まり方が
    いちばん多いので、その 2 つを最初から画面に出しておく。
    """
    return (
        f"作業ディレクトリ: `{config.WORK_ROOT}`  \n"
        f"{video.ffmpeg_version()}  \n"
        f"esora CLI: `{config.ESORA_CLI}`"
    )


def build() -> gr.Blocks:
    with gr.Blocks(title=TITLE) as demo:
        gr.HTML(CSS)
        with gr.Row():
            gr.Markdown(f"## {TITLE}")
            mode = gr.Radio(
                choices=[SIMPLE, ADVANCED],
                value=SIMPLE,
                show_label=False,
                container=False,
                scale=0,
                min_width=280,
            )

        # タブ間で持ち回る「いま作業中のセッション」。
        #
        # gr.Tabs() の中で作らないこと。Tabs は直下の子をタブのペインとして
        # 割り当てるので、gr.Tab 以外の要素（State は画面に出ないが、要素と
        # しては存在する）が混ざるとペインの対応が 1 つずれる。実際、機能1 の
        # 実行で State が更新された瞬間に機能3 のタブが消えていた。
        session_state = gr.State(value=None)

        with gr.Column(visible=True) as simple_pane:
            ui_simple.build(session_state)

        with gr.Column(visible=False) as advanced_pane:
            with gr.Tabs():
                tab_slice.build(session_state)
                tab_upscale.build(session_state)
                tab_extract.build(session_state)
                tab_generate_image.build(session_state)
                tab_generate_video.build(session_state)

        def switch(choice: str):
            simple = choice != ADVANCED
            return gr.update(visible=simple), gr.update(visible=not simple)

        mode.change(switch, inputs=mode, outputs=[simple_pane, advanced_pane])

        with gr.Accordion("環境", open=False):
            gr.Markdown(_environment_note())

    return demo
