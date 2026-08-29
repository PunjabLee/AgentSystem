# AutoDL 实例配置规格要求

| 项 | 值 |
|---|---|
| 版本 | v1.1（显存实测修正） |
| 日期 | 2026-08-29 |
| 用途 | P0-7 / P1 出口判据 / M1·M2 全部评测 |
| 关联 | [P0 验证结果](P0-VERIFICATION.md) · [WBS 依赖 D1](WBS.md) · [设计文档](superpowers/specs/2026-08-28-enterprise-ai-poc-design.md) |

> 所有显存数字基于 **P0 实测**的 Qwen3.8-27B 架构参数（64 层 · kv_heads=4 · head_dim=256 · 原生 ctx 262144）与 safetensors 分片实际体积，非估算。

---

## 1. 卡型与量化的对应关系

**架构决定量化选择，不能混用。**

| 卡型 | 架构 | 计算能力 | 原生支持 | 本项目选定量化 |
|---|---|---|---|---|
| **RTX 5090 32G** | Blackwell | **sm120** | FP8 **+ NVFP4** | **NVFP4** |
| **A100 80G** | Ampere | sm80 | 均不支持 | **BF16（不量化）** |
| ~~RTX 4090 24G~~ | Ada | sm89 | FP8（无 FP4） | INT4 + FP8 KV |

**vLLM 0.28.0 对 Blackwell 的支持已核实**：量化注册表含 `modelopt_fp4` / `mxfp4` / `quark`；内核文件含 `nvfp4_scaled_mm_sm120_kernels.cu`（**专为 sm120 即消费级 Blackwell 编写**）与 `nvfp4_kv_cache_kernels.cu`。

### 为什么 5090 选 NVFP4 而不是 FP8

Qwen3.8-27B 的 FP8 权重是 **28.7 GiB**，32G 卡装得下但 KV cache 只剩 1 GiB 左右，无法服务。NVFP4 约 14 GiB，留出 12.4 GiB 给 KV。

**在 Blackwell 上 NVFP4 有硬件加速**，与 INT4 体积相同但更快、且 FP4 的动态范围通常优于同位宽 INT4。在 Ada（4090）上则相反——那里 FP4 只能反量化，应选 INT4。

### 为什么 A100 选 BF16 而不是量化

A100 是 Ampere，**没有 FP8/FP4 张量核心**。加载量化权重只能 weight-only 反量化回 BF16 计算——省显存但不省算力，还引入量化损失。而 M2 的定位是**准确率上限基准**，必须无量化损失。

---

## 2. 显存预算实算

**KV/token = 64 层 × 4 kv_heads × 256 head_dim × 2(K,V) × 2 B = 256 KiB**（BF16 KV；FP8 减半，NVFP4 再减半）

### ⚠️ v1.1 修正：量化权重实测体积远大于估算

**Qwen3.5 / 3.8 是多模态模型**（`vision_config`：27 层 / hidden 1152），而**视觉塔在所有量化版本中都被排除在量化之外**：

```
未量化模块: ['model.visual.blocks.0.attn.qkv', 'model.visual.blocks.0.attn.proj', ...]
```

v1.0 按纯文本模型估的「NVFP4 约 14 GiB」不成立。实测：

| 权重仓库 | 实测体积 | 32G 卡可行性 |
|---|---|---|
| `RedHatAI/Qwen3.8-27B-INT4` | **18.1 GiB** | ✅ **推荐** |
| `cyankiwi/Qwen3.8-27B-AWQ-INT4` | 19.6 GiB | ✅ 可行，余量更小 |
| `Qwen/Qwen3.5-27B-GPTQ-Int4`（官方） | **28.2 GiB** | ❌ **装不下** |
| `Qwen/Qwen3.8-27B-FP8`（官方） | 28.7 GiB | ❌ 装不下 |

> **官方量化在 32G 卡上都用不了。** v1.0 曾以「官方量化比社区量化成熟」为由倾向 Qwen3.5，该论据在 32G 卡上不适用——只有 18–20 GiB 的社区 INT4 装得下。

### 5090 32G + INT4（18.1 GiB）

```
可用 31.4 GiB × --gpu-memory-utilization 0.92 = 28.9 GiB
  − 权重 18.1  − 运行时/激活 2.5  =  KV 可用 8.3 GiB
```

| KV 精度 | KiB/token | 总容量 | 10 并发每路 | 达标 |
|---|---|---|---|---|
| BF16 | 256 | 34.0K token | 3.4K | ❌ **差一点** |
| **FP8** | 128 | **68.0K** | **6.8K** | ✅ |
| NVFP4 | 64 | 136.0K | 13.6K | ✅ |

🔴 **`--kv-cache-dtype fp8` 在 5090 上是必须项，不是可选项。** BF16 KV 的 34K 容量低于 10 并发所需的 35K。Blackwell 原生支持 FP8 KV，开启无损耗。

### A100 80G + BF16

```
可用 79.2 GiB × --gpu-memory-utilization 0.90 = 71.3 GiB
  − 权重 51.7  − 运行时/激活 3.0  =  KV 可用 16.6 GiB
```

| KV 精度 | KiB/token | 总容量 | 10 并发每路 |
|---|---|---|---|
| **BF16** | 256 | **67.9K token** | **6.8K** |
| FP8 | 128 | 135.8K | 13.6K |

**需求基线：10 并发 × 3.5K token（RAG prompt + 输出）= 35K token。**

**A100 在默认 BF16 KV 下已满足；5090 必须开 FP8 KV**（见上，BF16 KV 的 34K 低于 35K 需求）。

---

## 3. 系统内存

**要求：64 GB 满足两种卡型。**

一个常见误解是"内存要 ≥ 权重大小"。实际不需要：safetensors 是 **mmap** 加载，vLLM 逐分片读取并拷贝到 GPU，页缓存可被系统回收，峰值 RSS 远低于模型总体积。

| 场景 | 最低 | 推荐 | 说明 |
|---|---|---|---|
| 5090 + NVFP4（14 GiB 权重） | 32 GB | **64 GB** | 宽裕 |
| A100 + BF16（51.7 GiB 权重） | 48 GB | **64 GB** | 满足；若同时下载模型 + 加载会略紧，96 GB 更从容 |

**你提的 64 GB+ 是合适的。** 需要更多内存的唯一场景是 CPU offload（本项目不使用）。

---

## 4. 硬盘空间

**这是 AutoDL 上最容易踩的坑**——系统盘通常只有 30 GB，**放不下模型**，必须用数据盘。

### 分项核算

| 项 | 大小 | 备注 |
|---|---|---|
| CUDA + PyTorch + vLLM 环境 | 20–25 GB | torch 与 CUDA 库本身很大 |
| pip / uv 缓存 | 5–8 GB | 可清理 |
| **Qwen3.8-27B BF16** | **51.7 GiB** | M2 用 |
| **Qwen3.8-27B INT4**（含未量化视觉塔） | **18.1 GiB** | M1 用 |
| vLLM 编译缓存（torch.compile / CUDA graph） | 2–5 GB | 首次启动生成 |
| 日志与杂项 | 5 GB | |

### 建议容量

| 实例用途 | 实需 | **建议数据盘** |
|---|---|---|
| 只跑 M1（5090 + INT4） | ~65 GB | **100 GB** |
| 只跑 M2（A100 + BF16） | ~95 GB | **150 GB** |
| **同一实例上两个都要** | ~115 GB | **200 GB** |

> ⚠️ **下载缓存双份风险**：若 HuggingFace 缓存目录与实际加载路径未用软链接，同一模型会存两份。BF16 模型双份就是 103 GiB。务必确认 `HF_HOME` / `MODELSCOPE_CACHE` 指向数据盘，且不做二次拷贝。

---

## 5. 镜像与 CUDA 要求

| 卡型 | CUDA | 驱动 | PyTorch |
|---|---|---|---|
| **5090（Blackwell sm120）** | **≥ 12.8** | **≥ 570** | cu128 构建 |
| A100（Ampere sm80） | ≥ 12.1 | ≥ 530 | cu121+ 均可 |

> 🔴 **5090 实例选镜像时必须确认 CUDA ≥ 12.8。** Blackwell 是新架构，CUDA 12.4 及更早的镜像**编译不出 sm120 内核**，vLLM 会启动失败或退回极慢的兼容路径。这是选错镜像后最耗时的排查之一。

选好镜像后第一件事验证：

```bash
nvidia-smi                              # 驱动版本与卡型
python -c "import torch; print(torch.__version__, torch.version.cuda)"
python -c "import torch; print(torch.cuda.get_device_capability())"   # 5090 应为 (12, 0)
```

---

## 6. 模型下载策略

### 用无卡模式下载，别烧 GPU 钱

AutoDL 支持**无卡模式开机**（价格极低，无 GPU）。51.7 GiB 的 BF16 权重下载可能需要 30–60 分钟，**用无卡模式做这件事**，下完再切 GPU 模式启动 vLLM。这一条能显著降低 PoC 的 GPU 计费。

### 国内走 ModelScope，不要走 HuggingFace

HuggingFace 在国内访问慢且不稳定。Qwen 官方同步发布在 **ModelScope（魔搭）**：

```bash
pip install modelscope
# 数据盘路径，避免占系统盘
export MODELSCOPE_CACHE=/root/autodl-tmp/modelscope
modelscope download --model Qwen/Qwen3.8-27B --local_dir /root/autodl-tmp/models/Qwen3.8-27B
```

备选：`HF_ENDPOINT=https://hf-mirror.com`

### 模型仓库对照

| 档位 | 仓库 | 体积 |
|---|---|---|
| **M2** | `Qwen/Qwen3.8-27B` | 51.7 GiB |
| **M1**（5090 或 4090） | **`RedHatAI/Qwen3.8-27B-INT4`** | **18.1 GiB** |

> **M1 统一用 `RedHatAI/Qwen3.8-27B-INT4`（18.1 GiB，↓115K，出自 vLLM 维护方 Neural Magic）**，5090 与 4090 通用。
>
> NVFP4 候选（`TelperionAI/...-NVFP4-AWQ-AutoRound` 等）下载量仅 100–700，成熟度远不及。**在 5090 上可作为 P5 的额外数据点尝试**（Blackwell 原生加速 FP4，理论更快），但不作为主路径——PoC 的关键路径不应押在下载量三位数的权重上。

---

## 7. vLLM 启动参数

### M1 · 5090 32G · INT4

```bash
vllm serve /root/autodl-tmp/models/Qwen3.8-27B-INT4 \
  --served-model-name qwen3.8-27b \
  --host 127.0.0.1 --port 8000 \
  --gpu-memory-utilization 0.92 \
  --max-model-len 16384 \
  --max-num-seqs 16 \
  --kv-cache-dtype fp8 \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  --enable-auto-tool-choice --tool-call-parser hermes
```

### M2 · A100 80G · BF16

```bash
vllm serve /root/autodl-tmp/models/Qwen3.8-27B \
  --served-model-name qwen3.8-27b \
  --host 127.0.0.1 --port 8000 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 16384 \
  --max-num-seqs 16 \
  --enable-auto-tool-choice --tool-call-parser hermes
```

### 三个参数必须显式给，缺一个都会出问题

| 参数 | 不给的后果 |
|---|---|
| `--enable-auto-tool-choice --tool-call-parser hermes` | **工具调用以纯文本留在 `content` 里，`tool_calls` 为空**，表现为"模型不会用工具"，极易误判成模型能力问题，浪费半天到一天 |
| `--max-model-len 16384` | 模型原生 ctx 是 **262144**，不限制则 vLLM 按 256K 预留 KV，显存瞬间不够 |
| `--host 127.0.0.1` | 默认 `0.0.0.0` 会把推理端口暴露在实例网络上，SSH 隧道给的安全感是假的 |
| `--kv-cache-dtype fp8`（**仅 5090**） | BF16 KV 只有 34K 容量，低于 10 并发所需的 35K |
| `--limit-mm-per-prompt`（可选） | 本项目只用文本。禁用多模态输入可省下视觉相关的预分配 |

---

## 8. 网络与访问

AutoDL 实例**不提供公网 IP**，须经 SSH 端口转发接入：

```bash
ssh -N -L 18001:127.0.0.1:8000 -p <SSH端口> root@<实例地址>
```

生产形态用 `autossh` 保活：

```bash
autossh -M 0 -N \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -L 18001:127.0.0.1:8000 -p <SSH端口> root@<实例地址>
```

**用 SSH 公钥认证，不要用密码**——`autossh` 无人值守若用密码就得上 `sshpass`，那会把口令放进进程 argv（`ps` 可见），违反宪法第七条。

端口分配：M1 → 本地 `18001`，M2 → 本地 `18002`。

> ⚠️ **P0-7 必须验证的假设（15 分钟）**：AutoDL 实例**关机再开机后，SSH 地址与端口是否变化**。设计当前默认它不变（`.env` 里存一份即可）。若会变，隧道脚本必须参数化、每次开机重读。**这个假设错了会在演示当天爆炸。**

---

## 9. 需要你提供的信息

| # | 项 | 用途 |
|---|---|---|
| 1 | 实例 SSH 地址与端口 | 建隧道 |
| 2 | SSH 公钥是否已上传（推荐）或口令 | 免密登录 |
| 3 | 实际卡型（5090 32G / A100 80G / 两者都有） | 定量化方案 |
| 4 | 镜像的 CUDA 与驱动版本 | 5090 须 ≥ 12.8 / 570 |
| 5 | 数据盘挂载路径与容量 | 通常 `/root/autodl-tmp` |

**凭据按宪法第七条由你自行注入 `.env`，不要贴进对话。**

---

## 10. 开机后的验收清单（P0-7）

- [ ] `nvidia-smi` 卡型与显存正确；`torch.cuda.get_device_capability()` 5090 应为 `(12, 0)`
- [ ] 数据盘容量足够且 `MODELSCOPE_CACHE` / `HF_HOME` 指向数据盘
- [ ] 模型下载完成，体积与预期相符（BF16 **51.7 GiB** / INT4 **18.1 GiB**）
- [ ] `vllm serve` 启动无 OOM，日志中 KV cache 块数符合 §2 测算
- [ ] `curl 127.0.0.1:8000/v1/models` 返回模型
- [ ] **`tool_calls` 验证**：发一个带 `tools` 的请求，响应必须返回**结构化 `tool_calls` 字段**，而不是把工具调用写在 `content` 里
- [ ] SSH 隧道建立，本机 `curl 127.0.0.1:18001/v1/models` 通
- [ ] **关机 → 开机 → 确认 SSH 地址与端口是否变化**
- [ ] `curl 127.0.0.1:8000/metrics` 可取到 vLLM Prometheus 指标（P5 压测归因需要）
