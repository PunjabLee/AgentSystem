"""为 config/users.yaml 铸造演示用户令牌。

明文写入 .env，配置里只留 SHA-256（宪法第七条）。已存在的令牌不会被
重新生成 —— 重跑本脚本是幂等的，不会让别人手里的令牌突然失效。

用法：uv run python scripts/mint_user_tokens.py
"""

import hashlib
import pathlib
import secrets

#: 四个演示用户覆盖三事业部，并刻意留出宪法第十条的负测试主角
#: （u_yr_01 只有 BU-A，用它去查 BU-B 必须显式报错而非返回空）。
USERS = [
    ("u_yr_01", "印染销售", "DEMO_TOKEN_YR", ["BU-A"], ["华东", "华南"]),
    ("u_tile_01", "瓷砖销售", "DEMO_TOKEN_TILE", ["BU-B"], ["华东"]),
    ("u_tile_mgr", "瓷砖业务主管", "DEMO_TOKEN_TILE_MGR", ["BU-B"], ["*"]),
    ("u_group_01", "集团运营", "DEMO_TOKEN_GROUP", ["BU-A", "BU-B", "BU-C"], ["*"]),
]

HEADER = """\
# 静态 Bearer 身份表（P1 详细设计 §2.5.1）。
#
# 只存 SHA-256，绝不存明文 —— 明文令牌走 .env（宪法第七条）。
# 本文件与 P2 §5.2 的权限配置是同一份：身份与授权范围必须同源，
# 分成两份配置就会漂移，而漂移的那一次就是越权。
#
# bu_codes 不支持通配符：事业部边界必须逐个列举，避免一次配置疏忽
# 就打通三个事业部。regions 允许 "*" 表示不限。
#
# 本文件由 scripts/mint_user_tokens.py 生成，勿手工编辑。

users:
"""


def _quote(values: list[str]) -> str:
    """构造 YAML 行内序列。

    **必须逐项加引号** —— 裸的 ``*`` 是 YAML 的别名指示符，
    ``regions: [*]`` 会让整份配置解析失败（本项目踩过一次）。
    """
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def _read_env(path: pathlib.Path) -> dict[str, str]:
    """极简 .env 解析，只认 ``KEY=VALUE``，忽略注释与空行。"""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    """铸造缺失的令牌并重写 users.yaml。"""
    env_path = pathlib.Path(".env")
    env = _read_env(env_path)

    minted: list[str] = []
    rows = []
    for user_id, name, var, bu_codes, regions in USERS:
        token = env.get(var) or secrets.token_urlsafe(24)
        if var not in env:
            minted.append(f"{var}={token}")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        rows.append((user_id, name, digest, bu_codes, regions))

    if minted:
        block = "\n# ── PoC 演示用户令牌（明文只在此处）──\n" + "\n".join(minted) + "\n"
        env_path.write_text(
            env_path.read_text(encoding="utf-8").rstrip() + "\n" + block, encoding="utf-8"
        )
        print(f"  新铸 {len(minted)} 枚令牌 → .env")

    body = "".join(
        f"  - user_id: {uid}\n"
        f"    name: {name}\n"
        f'    token_sha256: "{digest}"\n'
        f"    bu_codes: {_quote(bus)}\n"
        f"    regions: {_quote(regions)}\n"
        for uid, name, digest, bus, regions in rows
    )
    pathlib.Path("config/users.yaml").write_text(HEADER + body, encoding="utf-8")
    print(f"  config/users.yaml 已写入 {len(rows)} 个用户（仅哈希）")


if __name__ == "__main__":
    main()
