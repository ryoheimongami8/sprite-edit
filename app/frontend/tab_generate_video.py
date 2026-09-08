"""機能5 のタブ: esora API で衣装を差し替えた動画を生成する。

動画をそのまま参照に渡す経路は無い（実機で確認済み — サーバーは
"reference images must be images" と明示的に拒否する）。ここでは参考動画
から等間隔で代表フレームを複数枚抜き出し、通常の画像参照として渡す
（backend/feature5.py 参照）。動画の正確なタイミングは渡せないので、
静止画の枚数と順序でポーズの流れを伝える設計になっている。
"""

from __future__ import annotations

import gradio as gr

from backend import config, esora_cli, feature5, io_paths

from . import common


def _upload_path(uploaded) -> str:
    if uploaded is None:
        return ""
    if isinstance(uploaded, (list, tuple)):
        uploaded = uploaded[0]
    return str(getattr(uploaded, "name", uploaded))


def build(session_state: gr.State) -> dict:
    with gr.Tab("機能5: 動画生成（衣装差し替え）") as tab:
        gr.Markdown(
            "**参考動画（動作・タイミングの基準）** と **参考画像（衣装デザインの"
            "基準）** から、衣装を差し替えた動画を esora に生成させます。"
            "esora は動画をそのまま参照として受け付けないため（実機で確認済み）、"
            "参考動画から等間隔で代表フレームを複数枚抜き出し、通常の画像参照"
            "として渡します。動画の正確なタイミングまでは渡せないので、"
            "ポーズの保持・切り替え方はプロンプトの文章で指定しています。"
        )

        with gr.Row():
            with gr.Column(scale=1):
                with gr.Row():
                    session = gr.Dropdown(
                        label="対象セッション（任意 — 記録の保存先）",
                        choices=common.session_choices(),
                        value=None,
                        allow_custom_value=True,
                        scale=5,
                    )
                    refresh = gr.Button("↻", scale=1, min_width=48)

                check = gr.Button("① 接続確認 (esora-api me)", variant="secondary")
                connection_note = gr.Markdown("")

                with gr.Group():
                    gr.Markdown(
                        "**② 参照素材** — セッションを選ぶと、機能2の拡大動画を"
                        "参考動画に、機能4の生成結果があれば参考画像の初期値に"
                        "します。どちらも手動で差し替えられます。"
                    )
                    ref_video = gr.File(
                        label="参考動画（ポーズ・タイミングの基準）",
                        file_types=[".mp4", ".mov", ".webm", ".mkv"],
                        type="filepath",
                    )
                    ref_image = gr.File(
                        label="参考画像（衣装・配色・描画スタイルの基準）",
                        file_types=["image"],
                        type="filepath",
                    )
                    frame_count = gr.Number(
                        label="参考動画から抜く代表フレーム枚数",
                        value=config.VIDEO_REFERENCE_FRAME_COUNT,
                        precision=0,
                        info="--img-ref は「代表フレーム(この枚数) → 参考画像"
                        "(最後の1枚)」の順で渡ります。プロンプトの"
                        "「1枚目からN枚目」「最後の参照画像」という言い方は"
                        "この順序が前提なので、変更しないでください。",
                    )

                with gr.Group():
                    gr.Markdown("**③ モデル**")
                    with gr.Row():
                        model_hint = gr.Textbox(
                            label="モデルを探すキーワード（空白区切り）",
                            value=" ".join(config.ESORA_VIDEO_MODEL_HINTS),
                            scale=3,
                        )
                        find_model = gr.Button("モデルを確認", scale=1)
                    model = gr.Dropdown(
                        label="使用モデル (id)",
                        choices=[],
                        value=None,
                        allow_custom_value=True,
                        info="表示名(Seedance 2.0 Fastなど)はesora側のidと"
                        "一致するとは限らないため、実行時に一覧から探しています。",
                    )
                    model_note = gr.Markdown("")

                with gr.Group():
                    gr.Markdown("**④ サイズとプロンプト**")
                    with gr.Row():
                        aspect_ratio = gr.Textbox(
                            label="アスペクト比", value=config.DEFAULT_VIDEO_ASPECT_RATIO
                        )
                        resolution = gr.Textbox(
                            label="解像度（モデルにより表記が違う）",
                            value=config.DEFAULT_VIDEO_RESOLUTION,
                        )
                        duration = gr.Number(
                            label="秒数", value=config.DEFAULT_VIDEO_DURATION_S, precision=0
                        )
                        seed = gr.Number(label="seed（空欄可）", value=None, precision=0)
                    audio = gr.Checkbox(label="音声を生成する", value=False)
                    prompt = gr.Textbox(
                        label="プロンプト（編集可）",
                        value=config.DEFAULT_VIDEO_PROMPT,
                        lines=18,
                        max_lines=40,
                    )

                run = gr.Button("⑤ 実行（esoraに生成依頼）", variant="primary")

            with gr.Column(scale=1):
                report = gr.Textbox(label="結果", lines=14, max_lines=28)
                result_video = gr.Video(label="生成結果", height=360)
                downloads = gr.File(label="書き出したファイル", file_count="multiple")

    # ─── 配線 ───

    refresh.click(
        lambda: gr.update(choices=common.session_choices()), outputs=session
    )

    def adopt(path):
        if not path:
            return gr.update(), gr.update(), gr.update()
        opened = io_paths.open_session(path)
        default_video = io_paths.latest_file(opened.upscaled_video, "*.mp4")
        default_image = io_paths.latest_file(opened.generated_images, "*.png")
        return (
            gr.update(choices=common.session_choices(), value=str(path)),
            str(default_video) if default_video else gr.update(),
            str(default_image) if default_image else gr.update(),
        )

    session_state.change(adopt, inputs=session_state, outputs=[session, ref_video, ref_image])

    def on_check():
        return common.connection_report()

    check.click(on_check, outputs=connection_note)

    def on_find_model(hint_value):
        choices, note = common.resolve_models("video", str(hint_value))
        value = choices[0][1] if choices else None
        return gr.update(choices=choices, value=value), note

    find_model.click(on_find_model, inputs=model_hint, outputs=[model, model_note])

    def on_run(
        path, video_value, image_value, frame_count_value, model_value,
        aspect_value, resolution_value, duration_value, seed_value,
        audio_value, prompt_value,
        progress=gr.Progress(),
    ):
        try:
            video_path = _upload_path(video_value)
            image_path = _upload_path(image_value)
            if not video_path or not image_path:
                return "参考動画・参考画像の両方を用意してください。", None, []
            session = (
                io_paths.open_session(path) if path else io_paths.new_session("feature5")
            )
            result = feature5.run(
                session,
                video_path,
                image_path,
                prompt=str(prompt_value),
                model=str(model_value or ""),
                aspect_ratio=str(aspect_value),
                resolution=str(resolution_value),
                duration=common.as_int(duration_value, config.DEFAULT_VIDEO_DURATION_S),
                audio=bool(audio_value),
                frame_count=common.as_int(frame_count_value, config.VIDEO_REFERENCE_FRAME_COUNT),
                seed=common.as_int(seed_value, 0) or None,
                progress=common.bridge(progress),
            )
        except (esora_cli.EsoraCliError, ValueError) as exc:
            return f"[失敗] {exc}", None, []
        except Exception as exc:  # noqa: BLE001
            return common.failure(exc), None, []

        return (
            result.report,
            common.existing(result.video_path),
            [found for found in (common.existing(result.video_path),) if found],
        )

    run.click(
        on_run,
        inputs=[
            session, ref_video, ref_image, frame_count, model,
            aspect_ratio, resolution, duration, seed, audio, prompt,
        ],
        outputs=[report, result_video, downloads],
    )

    return {"tab": tab, "session": session}
