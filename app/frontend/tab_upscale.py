"""機能2 のタブ: CPU で整数倍に拡大し、開始画像と mp4 を出す。

入力は機能1 の連番 PNG です。同じ工程で作った mp4 ではありません。一度
H.264 を通した画を拡大すると、圧縮でにじんだ色をそのまま 9 倍に引き伸ばす
ことになるので、正本のほうを読みます。

倍率は素材ごとに変わるので、実行の前に「何倍で何 px になるか」を出して
から走らせます。1024 に届かない・超えるぶんはここで見えます。
"""

from __future__ import annotations

import gradio as gr

from backend import config, feature2, io_paths, upscale

from . import common


def build(session_state: gr.State) -> dict:
    with gr.Tab("機能2: アップスケール") as tab:
        gr.Markdown(
            "機能1 の連番 PNG を **整数倍・最近傍** で拡大します。"
            "非整数倍にすると元の 1 画素が 8px になったり 9px になったりして"
            "輪郭が不均一に崩れるため、目標寸法のほうを譲ります。"
        )

        with gr.Row():
            with gr.Column(scale=1):
                with gr.Row():
                    session = gr.Dropdown(
                        label="対象セッション",
                        choices=common.session_choices(),
                        value=None,
                        allow_custom_value=True,
                        scale=5,
                    )
                    refresh = gr.Button("↻", scale=1, min_width=48)

                with gr.Group():
                    gr.Markdown("**① 倍率**")
                    target = gr.Number(
                        label="目標の長辺 (px) — 整数倍で最も近い値に丸めます",
                        value=config.DEFAULT_TARGET_SIZE,
                        precision=0,
                    )
                    scale_override = gr.Number(
                        label="倍率を手で指定（0 = 自動）", value=0, precision=0
                    )
                    resample = gr.Dropdown(
                        label="補間",
                        choices=list(upscale.RESAMPLE),
                        value=config.UPSCALE_RESAMPLE,
                    )
                    plan_button = gr.Button("倍率を計算", variant="secondary")
                    plan_note = gr.Markdown("")

                with gr.Accordion("mp4 の詳細設定", open=False):
                    fps_override = gr.Number(
                        label="fps を上書き（0 = 機能1 と同じ）", value=0, precision=0
                    )
                    with gr.Row():
                        crf = gr.Slider(
                            label="CRF", minimum=0, maximum=32, step=1,
                            value=config.DEFAULT_CRF,
                        )
                        pix_fmt = gr.Dropdown(
                            label="pix_fmt",
                            choices=["yuv420p", "yuv444p"],
                            value=config.DEFAULT_PIX_FMT,
                        )

                run = gr.Button("② 実行", variant="primary")

            with gr.Column(scale=1):
                report = gr.Textbox(
                    label="結果", lines=12, max_lines=24
                )
                first_frame = gr.Image(
                    label="First Frame", height=320, type="filepath"
                )
                result_video = gr.Video(label="拡大後の動画", height=320)
                downloads = gr.File(label="書き出したファイル", file_count="multiple")

    # ─── 配線 ───

    refresh.click(
        lambda: gr.update(choices=common.session_choices()), outputs=session
    )

    def adopt(path):
        """機能1 が終わったら、その作業をそのまま選んだ状態にする。

        毎回ドロップダウンを開き直させない。連続して回すのが普通の使い方で、
        そこで別のセッションを選び間違えると気づきにくい。
        """
        if not path:
            return gr.update()
        return gr.update(choices=common.session_choices(), value=str(path))

    session_state.change(adopt, inputs=session_state, outputs=session)

    def on_plan(path, target_value, scale_value, resample_value):
        if not path:
            return "対象セッションを選んでください。"
        try:
            plan = feature2.plan_for(
                io_paths.open_session(path),
                target=max(1, common.as_int(target_value, config.DEFAULT_TARGET_SIZE)),
                scale=common.as_int(scale_value, 0) or None,
                resample=str(resample_value),
            )
        except Exception as exc:  # noqa: BLE001
            return f"[失敗] {exc}"
        return f"**{plan.summary()}**"

    plan_inputs = [session, target, scale_override, resample]
    plan_button.click(on_plan, inputs=plan_inputs, outputs=plan_note)
    for component in plan_inputs:
        component.change(on_plan, inputs=plan_inputs, outputs=plan_note)

    def on_run(
        path, target_value, scale_value, resample_value,
        fps_value, crf_value, pix,
        progress=gr.Progress(),
    ):
        if not path:
            return "対象セッションを選んでください。", None, None, None
        try:
            result = feature2.run(
                io_paths.open_session(path),
                target=max(1, common.as_int(target_value, config.DEFAULT_TARGET_SIZE)),
                scale=common.as_int(scale_value, 0) or None,
                resample=str(resample_value),
                fps=common.as_int(fps_value, 0) or None,
                crf=common.as_int(crf_value, config.DEFAULT_CRF),
                pix_fmt=str(pix),
                progress=common.bridge(progress),
            )
        except Exception as exc:  # noqa: BLE001
            return common.failure(exc), None, None, None

        files = [
            found
            for found in (
                common.existing(result.first_frame),
                common.existing(result.first_frame_rgba),
                common.existing(result.video_path),
            )
            if found
        ]
        return (
            result.report,
            common.existing(result.first_frame),
            common.existing(result.video_path),
            files,
        )

    run.click(
        on_run,
        inputs=[
            session, target, scale_override, resample,
            fps_override, crf, pix_fmt,
        ],
        outputs=[report, first_frame, result_video, downloads],
    )

    return {"tab": tab, "session": session}
