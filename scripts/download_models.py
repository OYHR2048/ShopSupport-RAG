"""仅下载用户批准的两个模型；固定 revision，拒绝远程自定义代码。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

MODELS = ("BAAI/bge-small-zh-v1.5", "BAAI/bge-reranker-base")


def download(repo):
    api = HfApi()
    info = api.model_info(repo)
    names = {file.rfilename for file in info.siblings}
    if "model.safetensors" not in names:
        raise RuntimeError(repo + ": 未找到 safetensors，本脚本不下载 pickle 权重")
    selected = [
        n
        for n in names
        if "/" not in n
        and (n.endswith((".json", ".txt", ".model")) or n in {"model.safetensors", "README.md"})
    ]
    selected += [n for n in names if n.startswith("1_Pooling/") and n.endswith(".json")]
    target = Path("data/models") / repo.split("/")[-1]
    print("Downloading " + repo + " revision " + info.sha, flush=True)
    snapshot_download(
        repo_id=repo,
        revision=info.sha,
        local_dir=str(target),
        allow_patterns=selected,
        max_workers=2,
        token=False,
    )
    manifest = {"repository": repo, "revision": info.sha, "files": selected, "remote_code": False}
    target.joinpath("download_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print("Complete " + repo, flush=True)
    return manifest


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as pool:
        manifests = list(pool.map(download, MODELS))
    Path("reports/model_manifest.json").write_text(
        json.dumps(manifests, indent=2), encoding="utf-8"
    )
