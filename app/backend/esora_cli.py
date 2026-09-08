"""esora API CLI (`esora-api`) を呼び出す薄いラッパー。

機能4・5 はここまでの機能1〜3 と性格が違う。ローカルで画素を操作するのでは
なく、esora のサーバーに生成を依頼するだけなので、このモジュールは
subprocess で `esora-api --json ...` を呼び、JSON を読むことに徹する。

**このアプリの Python と esora_api_cli の Python は別でよい。** 実行ファイルの
パスさえ config.ESORA_CLI で合っていれば、esora_api_cli 側は専用の venv
（Python 3.12 以上が必須）に入っていて構わない。

**認証はここでは扱わない。** `esora-api auth login` はどこかの端末で事前に
済ませておく前提で、未サインインならそのままエラーとして返す。UI 側は
「① 接続確認」で `whoami()` を呼んで先に確かめさせる。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable

from . import config

Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    return None


class EsoraCliError(RuntimeError):
    """esora-api の呼び出しが失敗した。メッセージはユーザーにそのまま見せる。"""


def _child_env() -> dict[str, str]:
    """子プロセス(esora-api)自身に UTF-8 で書かせる。

    こちら側で ``encoding="utf-8"`` を指定しても直るのは読む側だけ。
    esora-api は Python プロセスなので、パイプに繋がれた stdout の
    エンコーディングは**子プロセス自身の**ロケール既定（Windows の日本語
    環境では大抵 cp932）で決まる。プロンプトに日本語が入っていると、
    子が cp932 で書いたバイト列をこちらが UTF-8 として読むことになり、
    JSON として壊れる（実際に status=completed で生成自体は成功して
    いるのに、行が JSON として読めず「応答を読み取れませんでした」に
    なった不具合はこれが原因）。PYTHONUTF8=1 で子の標準入出力を
    ロケールに関係なく UTF-8 に固定する。
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _popen(args: list[str]) -> subprocess.Popen:
    try:
        return subprocess.Popen(
            [config.ESORA_CLI, "--json", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
        )
    except FileNotFoundError as exc:
        raise EsoraCliError(
            f"esora-api が見つかりません（{config.ESORA_CLI!r}）。"
            "D:\\40_Esora\\01_CLI で専用の venv を作って esora_api_cli を"
            "インストールしたうえで、環境変数 ESORA_CLI_PATH に "
            "esora-api(.exe) の完全パスを指定してください。"
        ) from exc


def run_single_json(args: list[str], *, timeout: float | None = 120.0) -> Any:
    """1個の JSON ドキュメントを返すコマンド用（model list, asset upload, me など）。"""
    process = _popen(args)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise EsoraCliError(f"esora-api の応答がありません（{timeout:g}秒待機）。") from exc

    if process.returncode != 0:
        raise EsoraCliError((stderr or stdout or f"exit code {process.returncode}").strip())

    text = (stdout or "").strip()
    if not text:
        raise EsoraCliError("esora-api から応答がありませんでした。")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise EsoraCliError(f"応答をJSONとして読めません: {text[:500]}") from exc


def run_streaming_json(
    args: list[str],
    *,
    progress: Progress = _noop,
    timeout: float | None = 900.0,
) -> dict[str, Any]:
    """`generate image` / `generate video` を JSON Lines で追い、最終結果を返す。

    途中経過は ``{"event": "status", "status": ...}`` で流れる。最後の1行が
    ``{"event": "result", ...}``（成功でも失敗でも）で、これを戻り値にする。
    途中経過の割合はサーバーが教えてくれないので、段階が変わるたびに
    メッセージだけ更新する（進捗バーの動き自体は粗い）。
    """
    process = _popen(args)
    assert process.stdout is not None

    terminal: dict[str, Any] | None = None
    tail: list[str] = []
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        tail.append(line)
        del tail[:-5]
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "status":
            progress(0.5, f"生成中: {event.get('status')}")
        elif event.get("event") == "result":
            terminal = event

    process.stdout.close()
    stderr_text = ""
    if process.stderr is not None:
        stderr_text = process.stderr.read()
        process.stderr.close()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        raise EsoraCliError(f"esora-api の終了を待てませんでした（{timeout:g}秒）。") from exc

    if terminal is None:
        detail = stderr_text.strip() or "\n".join(tail) or f"exit code {returncode}"
        raise EsoraCliError(f"生成の応答を読み取れませんでした: {detail}")
    if terminal.get("status") != "completed":
        message = terminal.get("error") or stderr_text.strip() or "不明なエラー"
        raise EsoraCliError(
            f"生成に失敗しました（status={terminal.get('status')}）: {message}"
        )
    return terminal


# ── モデル ──


def list_models(task: str | None = None) -> list[dict[str, Any]]:
    args = ["model", "list"]
    if task:
        args += ["--task", task]
    return run_single_json(args)


def find_models(task: str | None, keywords: Iterable[str]) -> list[dict[str, Any]]:
    """id と label の両方に、与えたキーワードを**すべて**含むモデルだけ返す。

    「GPT image2」「Seedance 2.0 Fast」のような表示名は esora の実際の
    model id と一致するとは限らない。ハードコードで賭けるより、そのとき
    サーバーが返す一覧から探すほうが確実。
    """
    keys = [k.lower() for k in keywords if k]
    models = list_models(task)
    if not keys:
        return models
    return [
        model
        for model in models
        if all(key in f"{model.get('id', '')} {model.get('label', '')}".lower() for key in keys)
    ]


def show_model(model_id: str) -> dict[str, Any]:
    matches = [m for m in list_models() if m.get("id") == model_id]
    if not matches:
        raise EsoraCliError(f"モデル {model_id!r} が見つかりません。")
    return matches[0]


def resolve_model_id(task: str, keywords: Iterable[str]) -> str:
    """キーワードから model id を 1 つ決める。新UI が自動で呼ぶ。

    候補が複数あるときは先頭を採る。0 件のときは、選べる id を並べて
    落とす — 「モデルが見つかりません」だけでは、次に何を打てばいいかが
    分からないため。
    """
    hits = find_models(task, keywords)
    if hits:
        return str(hits[0]["id"])
    available = ", ".join(str(m.get("id")) for m in list_models(task)) or "（0件）"
    raise EsoraCliError(
        f"キーワード {list(keywords)} に一致する{task}モデルがありません。"
        f"使える id: {available}"
    )


# ── アセット ──


def upload_asset(path: str | Path, *, progress: Progress = _noop) -> dict[str, Any]:
    """画像・動画・音声をアセットとしてアップロードし、asset_id を含む記録を返す。

    機能5 が参照動画をアップロードするのに使う。**サーバー側が動画種別の
    アセットを画像参照（--img-ref）として実際に受理するかどうかは、この先の
    generate video 呼び出しで初めて分かる** — CLIのクライアント側チェックは
    asset_id を直接渡す経路には掛からないため、ここまでは必ず通る。
    """
    progress(0.0, f"{Path(path).name} をアップロード")
    record = run_single_json(["asset", "upload", str(path)], timeout=600.0)
    progress(1.0, "アップロード完了")
    return record


# ── 生成 ──


def generate_image(
    prompt: str,
    *,
    model: str,
    img_refs: list[str],
    aspect_ratio: str = "",
    image_size: str = "",
    seed: int | None = None,
    out_dir: str | Path,
    progress: Progress = _noop,
) -> dict[str, Any]:
    args = ["generate", "image", "-p", prompt, "-m", model]
    for ref in img_refs:
        args += ["-r", ref]
    if aspect_ratio:
        args += ["--aspect-ratio", aspect_ratio]
    if image_size:
        args += ["--image-size", image_size]
    if seed is not None:
        args += ["--seed", str(seed)]
    args += ["-o", str(out_dir)]
    progress(0.0, "画像生成をリクエスト")
    return run_streaming_json(args, progress=progress)


def generate_video(
    prompt: str,
    *,
    model: str,
    img_refs: list[str],
    aspect_ratio: str = "",
    resolution: str = "",
    duration: int | None = None,
    audio: bool = False,
    seed: int | None = None,
    out_dir: str | Path,
    progress: Progress = _noop,
) -> dict[str, Any]:
    args = ["generate", "video", "-p", prompt, "-m", model]
    for ref in img_refs:
        args += ["-r", ref]
    if aspect_ratio:
        args += ["--aspect-ratio", aspect_ratio]
    if resolution:
        args += ["--resolution", resolution]
    if duration:
        args += ["--duration", str(duration)]
    args.append("--audio" if audio else "--no-audio")
    if seed is not None:
        args += ["--seed", str(seed)]
    args += ["-o", str(out_dir)]
    progress(0.0, "動画生成をリクエスト")
    return run_streaming_json(args, progress=progress)


# ── 接続確認 ──


def whoami() -> dict[str, Any]:
    return run_single_json(["me"], timeout=30.0)
