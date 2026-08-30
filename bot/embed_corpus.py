"""ساخت وکتورهای معنایی پایگاه دانش با bge-m3 روی Ollama (یک‌بار اجرا).

خروجی: data/vectors_semantic.npz — ۱۰۰۵ قطعه × ۱۰۲۴ بُعد، نرمال‌شده.
بعد از ساخت، فایل به مخزن خصوصی داده سینک می‌شود تا در چرخه‌های بعدی بازیابی شود.
"""
import json
import os
import urllib.request

import numpy as np

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS_PATH = os.path.join(BOT_DIR, "knowledge.json")
OUT_PATH = os.path.join(BOT_DIR, "..", "data", "vectors_semantic.npz")
OLLAMA_EMBED = "http://127.0.0.1:11434/api/embed"
MODEL = "bge-m3"
BATCH = 32


def main() -> None:
    with open(CORPUS_PATH, encoding="utf-8") as f:
        corpus = json.load(f)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    vecs = []
    for i in range(0, len(corpus), BATCH):
        batch = [(c.get("text") or "")[:1500] for c in corpus[i:i + BATCH]]
        body = json.dumps({"model": MODEL, "input": batch}).encode()
        req = urllib.request.Request(OLLAMA_EMBED, data=body, method="POST",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            emb = json.loads(r.read().decode())["embeddings"]
        vecs.extend(emb)
        print(f"embedded {min(i + BATCH, len(corpus))}/{len(corpus)}", flush=True)
    v = np.array(vecs, dtype="float32")
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    np.savez_compressed(OUT_PATH, vectors=v.astype("float16"))
    print("saved:", os.path.abspath(OUT_PATH), v.shape)


if __name__ == "__main__":
    main()
