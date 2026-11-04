"""Feasibility spike: serve cached Glimmer bf16 weights via the official vLLM recipe.

NOT the FIELD audit implementation. Short-lived by design:
deploy -> one inference -> stop. See scripts/smoke/glimmer_infer.py.

Deploy:  modal deploy scripts/smoke/modal_glimmer.py
Stop:    modal app stop field-glimmer-spike

Design (all verified in official docs before writing):
- Image: vllm/vllm-openai:muse-glimmer (Meta's pinned recipe image, CUDA 13.0).
- Weights: account's frameteller-glimmer-weights volume, complete bf16
  checkpoint meta-models/Muse-Glimmer-30B (~60 GB, verified present).
- GPU: 1x H100 (recipe budgets 72 GB VRAM; H100 proven in this account).
- Hard container cap: timeout=1800s -> worst-case ~$1.97 at $0.001097/s.
- Auth: intentionally unauthenticated for this minutes-long spike; the real
  audit endpoint must use Modal proxy tokens (documented at
  modal.com/docs/guide/webhook-proxy-auth). Stopped immediately after use.
"""

import modal

WEIGHTS_DIR = (
    "/weights/meta-models/Muse-Glimmer-30B/cec94fcfd6512f2cfe708138c32533b346ea4f69"
)

# add_python: the recipe image's Python is not Modal-compatible on PATH, so
# Modal installs a standalone interpreter for its own wrapper (documented at
# modal.com/docs/guide/existing-images). The `vllm` server keeps working via
# its own console-script shebang.
# ENTRYPOINT []: the recipe image sets ENTRYPOINT ["vllm", "serve"], which
# swallows Modal's container entrypoint and crash-loops. serve() below starts
# `vllm serve` explicitly, so clearing it is safe.
image = modal.Image.from_registry(
    "vllm/vllm-openai:muse-glimmer", add_python="3.11"
).dockerfile_commands("ENTRYPOINT []")
app = modal.App("field-glimmer-spike")
weights = modal.Volume.from_name(
    "frameteller-glimmer-weights", create_if_missing=False
)


@app.function(
    image=image,
    gpu="H100",
    volumes={"/weights": weights},
    timeout=1800,
)
@modal.web_server(port=8000, startup_timeout=1500)
def serve() -> None:
    import subprocess

    cmd = [
        "vllm",
        "serve",
        WEIGHTS_DIR,
        "--served-model-name",
        "muse-glimmer",
        "--tensor-parallel-size",
        "1",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "muse_glimmer",
        "--reasoning-parser",
        "muse_glimmer",
        "--generation-config",
        "auto",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    proc = subprocess.Popen(cmd)
    proc.wait()
