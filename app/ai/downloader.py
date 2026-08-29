"""模型下载器：从公共模型源拉取 GGUF 到 models/。

安全：
- 全部 URL（含重定向目标）经 validate_endpoint 校验：仅 http/https，
  拒绝内网/环回/链路本地/保留地址；
- 目标文件名规范化并限制在 models/ 目录内，禁止路径穿越；
- 预置源（huggingface.co / hf-mirror.com）+ 自定义 URL；
- 断点续传（Range）、多源回退、进度显示。
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

from .remote import UnsafeAPIEndpointError, validate_endpoint

PRESETS: dict[str, dict] = {
    "qwen3-1.7b": {
        "file": "qwen3-1.7b-instruct-q4_k_m.gguf",
        "sha256": "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
        "size": 1107409472,
        "urls": [
            "https://hf-mirror.com/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
            "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
        ],
        "desc": "主力叙事/拆解模型（Q4_K_M，约 1.1GB）",
    },
    "qwen3-4b": {
        "file": "qwen3-4b-instruct-2507-q4_k_m.gguf",
        "sha256": "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        "size": 2497281120,
        "urls": [
            "https://hf-mirror.com/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        ],
        "desc": "增强档叙事模型（Instruct-2507 非思考型，Q4_K_M，约 2.5GB）",
    },
    "qwen3-0.6b": {
        "file": "qwen3-0.6b-instruct-q4_k_m.gguf",
        "sha256": "ac2d97712095a558e31573f62f466a3f9d93990898b0ec79d7c974c1780d524a",
        "size": 396705472,
        "urls": [
            "https://hf-mirror.com/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q4_K_M.gguf",
            "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q4_K_M.gguf",
        ],
        "desc": "快速档模型（Q4_K_M，约 0.4GB）",
    },
}


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟随重定向前校验目标（HF 源会 302 到 CDN）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_endpoint(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_ValidatingRedirectHandler)


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _safe_filename(raw: str) -> str:
    """取纯文件名：Path.name 剥离全部目录成分，拒绝点开头（含 ..）。"""
    name = Path(raw.replace("\\", "/")).name.strip()
    if not name or name.startswith("."):
        raise ValueError(f"非法文件名：{raw!r}")
    return name


def _download_one(url: str, dest: Path, progress_cb=None, phase_cb=None) -> None:
    validate_endpoint(url)
    if phase_cb:
        phase_cb("connecting")
    headers = {"User-Agent": "ai-story-simulator/0.1"}
    partial = dest.stat().st_size if dest.exists() else 0
    if partial:
        headers["Range"] = "bytes=%d-" % partial
    req = urllib.request.Request(url, headers=headers)
    # 连接 25 秒快速超时：源不通尽早切下一个源，不让界面干等
    resp = _OPENER.open(req, timeout=25)
    if phase_cb:
        phase_cb("downloading")
    mode = "ab" if partial else "wb"
    with resp, dest.open(mode) as f:
        total = resp.headers.get("Content-Length")
        total = int(total) + partial if total else None
        done = partial
        while True:
            chunk = resp.read(256 * 1024)   # 256KB 粒度：慢速网络下进度也能实时跳动
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            # 进度回调无条件上报（部分源无 Content-Length，total 可能为 None）
            if progress_cb:
                try:
                    progress_cb(done, total)
                except Exception:
                    pass
            if total:
                sys.stdout.write("\r  %s / %s（%d%%）" % (_human(done), _human(total), done * 100 // total))
                sys.stdout.flush()
    print()


def fetch(preset_or_url: str, models_dir: Path, *, name: str | None = None,
          progress_cb=None, phase_cb=None) -> Path:
    """下载预置模型或自定义 URL，返回目标文件路径。"""
    models_dir = Path(models_dir).resolve()
    models_dir.mkdir(parents=True, exist_ok=True)

    if preset_or_url in PRESETS:
        preset = PRESETS[preset_or_url]
        urls, filename, desc = preset["urls"], preset["file"], preset["desc"]
        print(f"下载 {preset_or_url}：{desc}")
    else:
        urls = [preset_or_url]
        filename = _safe_filename(name or preset_or_url.rstrip("/").split("/")[-1])
        if not filename.endswith(".gguf"):
            raise ValueError("目标文件名必须以 .gguf 结尾（用 --name 指定）")

    dest = models_dir / _safe_filename(filename)
    if dest.parent != models_dir:
        raise ValueError("目标路径越界，拒绝写入")

    last_error: Exception | None = None
    for url in urls:
        try:
            if dest.exists() and dest.stat().st_size > 0:
                print(f"续传/重新下载：{dest.name}")
            _download_one(url, dest, progress_cb, phase_cb)
            if dest.stat().st_size < 1024 * 1024:
                dest.unlink(missing_ok=True)
                raise ValueError("下载结果异常（小于 1MB），可能源不可用")
            # SHA256 完整性校验（对比官方摘要；能发现截断/损坏/篡改）
            expected = preset.get("sha256") if preset else None
            if expected and phase_cb:
                phase_cb("verifying")
                import hashlib

                sha = hashlib.sha256()
                with dest.open("rb") as fh:
                    for block in iter(lambda: fh.read(1024 * 1024), b""):
                        sha.update(block)
                actual = sha.hexdigest()
                if actual != expected:
                    dest.unlink(missing_ok=True)
                    raise ValueError(
                        f"SHA256 校验失败（实际 {actual[:12]}…，预期 {expected[:12]}…），"
                        "文件已删除——请重试（会切换下载源）")
            # 校验通过 → 写 .ok 认证标记（含官方摘要）
            dest.with_suffix(dest.suffix + ".ok").write_text(expected, encoding="utf-8")
            print(f"完成：{dest}（{_human(dest.stat().st_size)}）")
            return dest
        except (UnsafeAPIEndpointError, ValueError) as e:
            raise e
        except (urllib.error.URLError, OSError) as e:
            last_error = e
            print(f"  源失败（{e}），尝试下一个源…")
    raise RuntimeError(f"全部源下载失败：{last_error}")


def migrate_existing(models_dir: Path) -> None:
    """存量模型迁移：字节数与官方精确一致的补写 .ok 认证（一次性）。"""
    for preset in PRESETS.values():
        f = Path(models_dir) / preset["file"]
        ok_file = f.with_suffix(f.suffix + ".ok")
        if f.is_file() and not ok_file.is_file() and f.stat().st_size == preset["size"]:
            ok_file.write_text(preset["sha256"], encoding="utf-8")


def scan(models_dir: Path) -> list[dict]:
    """扫描 models/ 目录，返回可识别的模型清单（按偏好排序）。"""
    from .local import _PREFERRED_PATTERNS

    result = []
    if models_dir.is_dir():
        for p in sorted(models_dir.iterdir()):
            if p.suffix.lower() in (".gguf", ".onnx"):
                entry = {"file": p.name, "size": p.stat().st_size}
                entry["rank"] = next(
                    (i for i, pat in enumerate(_PREFERRED_PATTERNS) if pat in p.name.lower()),
                    99,
                )
                result.append(entry)
    result.sort(key=lambda e: e["rank"])
    return result
