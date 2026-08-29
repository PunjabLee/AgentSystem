# AutoDL 实例配置规格要求

| 项 | 值 |
|---|---|
| 版本 | v1.2（混合注意力架构修正） |
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

### ⚠️ v1.2 修正：Qwen3.8 是混合注意力架构，KV/token 此前高估 4 倍

`config.json` 的 `layer_types` 实测：

```
layer_types: 48 × linear_attention  +  16 × full_attention
full_attention_interval: 4        ← 每 4 层才有 1 层全注意力
linear_conv_kernel_dim: 4, mamba_ssm_dtype    ← Mamba 式线性注意力
```

**64 层中只有 16 层产生随 token 增长的 KV cache**，其余 48 层是常数大小的循环状态（每序列固定开销，不随上下文增长）。

```
v1.0/v1.1 按 64 层全注意力算： 256 KiB/token   ← 错
真实值（16 个全注意力层）：     64 KiB/token   ← 正确
```

**根因**：未查 `layer_types` 就按常规稠密模型套 KV 公式。凡混合/线性注意力模型（Qwen3-Next、Mamba 系、Jamba 等）都必须先看层类型分布。

**KV/token = 16 层 × 4 kv_heads × 256 head_dim × 2(K,V) × 2 B = 64 KiB**（BF16 KV；FP8 减半为 32 KiB）

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

### 5090 32G（预算 28.9 GiB，运行时/激活 2.5 GiB）

| 权重仓库 | 体积 | KV 余 | BF16 KV 容量 | FP8 KV 容量 | Blackwell 加速 | 下载 |
|---|---|---|---|---|---|---|
| **`unsloth/Qwen3.8-27B-NVFP4`** | 21.8 GiB | 4.6 GiB | **75K** ✅ | 150K | ✅ **原生 FP4** | **1.80M** |
| `RedHatAI/Qwen3.8-27B-INT4` | 18.1 GiB | 8.3 GiB | 136K ✅ | 272K | ❌ | 115K |
| `cyankiwi/Qwen3.8-27B-AWQ-INT4` | 19.6 GiB | 6.8 GiB | 111K ✅ | 222K | ❌ | 793K |

**需求基线 35K，三者均大幅超标——容量不再是选型依据**，判据转为速度与成熟度。

**5090 选定 `unsloth/Qwen3.8-27B-NVFP4`**：

- `quant_method: compressed-tensors` —— **vLLM 原生量化格式**，无需额外适配
- `kv_cache_scheme: num_bits=8` —— 仓库自带 FP8 KV 方案
- 180 万下载、2026-08-25 更新，成熟度远超其它 NVFP4 候选
- **Blackwell 原生 FP4 张量核心加速**，这是 INT4 在该卡上拿不到的

**`--kv-cache-dtype fp8` 在 5090 上是可选项**（v1.1 曾据错误的 256 KiB/token 判为必须，此处更正）。开启可把容量从 75K 提到 150K，代价接近零，建议开但非必需。

**4090 24G 若作为备选卡**：无 FP4 硬件支持，应选 `RedHatAI/Qwen3.8-27B-INT4`。

### A100 80G + BF16

```
可用 79.2 GiB × --gpu-memory-utilization 0.90 = 71.3 GiB
  − 权重 51.7  − 运行时/激活 3.0  =  KV 可用 16.6 GiB
```

| KV 精度 | KiB/token | 总容量 | 10 并发每路 |
|---|---|---|---|
| **BF16** | 64 | **272K token** | **27.2K** |
| FP8 | 32 | 544K | 54.4K |

A100 余量极大，无需任何 KV 量化。

**需求基线：10 并发 × 3.5K token（RAG prompt + 输出）= 35K token。**

**两种卡在默认 BF16 KV 下均大幅超标**（5090 75K / A100 272K）。5090 建议开 FP8 KV 取额外余量，但非必需。

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
| **Qwen3.8-27B NVFP4**（含未量化视觉塔与线性注意力层） | **21.8 GiB** | M1 用（5090） |
| vLLM 编译缓存（torch.compile / CUDA graph） | 2–5 GB | 首次启动生成 |
| 日志与杂项 | 5 GB | |

### 建议容量

| 实例用途 | 实需 | **建议数据盘** |
|---|---|---|
| 只跑 M1（5090 + NVFP4） | ~70 GB | **100 GB** |
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
| **M1**（5090，推荐） | **`unsloth/Qwen3.8-27B-NVFP4`** | **21.8 GiB** |
| M1（4090 备选） | `RedHatAI/Qwen3.8-27B-INT4` | 18.1 GiB |

> **v1.1 曾称「NVFP4 候选下载量仅 100–700，成熟度不足」，该判断有误**——当时按 `AWQ/GPTQ/Int4/4bit/GGUF` 几个模式搜索，遗漏了 `unsloth/Qwen3.8-27B-NVFP4`（↓180 万）。它是 NVFP4 路线的主流选择，成熟度充分。

---

## 7. vLLM 启动参数

### M1 · 5090 32G · NVFP4

```bash
vllm serve /root/autodl-tmp/models/Qwen3.8-27B-NVFP4 \
  --served-model-name qwen3.8-27b \
  --host 127.0.0.1 --port 8000 \
  --gpu-memory-utilization 0.92 \
  --max-model-len 32768 \
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
| `--kv-cache-dtype fp8`（5090，**可选**） | 不开也够（75K），开了 150K。Blackwell 原生支持，代价接近零 |
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
- [ ] 模型下载完成，体积与预期相符（BF16 **51.7 GiB** / NVFP4 **21.8 GiB**）
- [ ] vLLM 日志中的 KV cache 块数与 §2 测算相符（按 **16 个全注意力层**、64 KiB/token 核对，不是 64 层）
- [ ] `vllm serve` 启动无 OOM，日志中 KV cache 块数符合 §2 测算
- [ ] `curl 127.0.0.1:8000/v1/models` 返回模型
- [ ] **`tool_calls` 验证**：发一个带 `tools` 的请求，响应必须返回**结构化 `tool_calls` 字段**，而不是把工具调用写在 `content` 里
- [ ] SSH 隧道建立，本机 `curl 127.0.0.1:18001/v1/models` 通
- [ ] **关机 → 开机 → 确认 SSH 地址与端口是否变化**
- [ ] `curl 127.0.0.1:8000/metrics` 可取到 vLLM Prometheus 指标（P5 压测归因需要）
