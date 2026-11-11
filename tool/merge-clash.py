#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# 脚本名称: merge-clash.py
# 功能描述: 将多台服务器生成的 Clash/Mihomo 配置合并为一个订阅文件。
#           1. 汇总所有输入文件中的 proxies，节点重名时自动加文件标签前缀。
#           2. 生成 AUTO（url-test 自动测速）、FALLBACK（按顺序故障转移）
#              和 PROXY（手动选择）三个策略组。
#           3. 默认使用国内直连规则，可用 --proxy-all 改为全部代理。
# 依赖: python3, PyYAML
#   Debian/Ubuntu: apt install -y python3-yaml
#   其他环境:      python3 -m pip install pyyaml
# 用法:
#   python3 merge-clash.py -o clash-merged.yaml server-a.yaml server-b.yaml
#   python3 merge-clash.py -o clash-merged.yaml --label A --label B a.yaml b.yaml
# =============================================================================

import argparse
import os
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "缺少 PyYAML 依赖。\n"
        "  Debian/Ubuntu: apt install -y python3-yaml\n"
        "  其他环境:      python3 -m pip install pyyaml\n"
    )
    sys.exit(1)

# 与 share.sh 保持一致的分流规则
DEFAULT_RULES = [
    "GEOIP,LAN,DIRECT",
    "GEOSITE,cn,DIRECT",
    "GEOIP,CN,DIRECT",
    "MATCH,PROXY",
]
PROXY_ALL_RULES = ["MATCH,PROXY"]
DEFAULT_TEST_URL = "https://www.gstatic.com/generate_204"


def file_label(path):
    """用文件名（不含扩展名）作为节点重名时的前缀。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem or "node"


def load_config(path):
    """读取并校验单个 Clash 配置文件。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ValueError(f"无法读取 {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} 不是合法的 YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path} 的顶层不是 YAML 对象")
    proxies = data.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise ValueError(f"{path} 中没有 proxies 节点")
    return data


def unique_name(name, used, label):
    """节点重名时加上文件标签前缀，仍冲突则追加序号。"""
    name = str(name)
    if name not in used:
        used.add(name)
        return name

    candidate = f"{label}-{name}"
    index = 2
    while candidate in used:
        candidate = f"{label}-{name}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def merge(paths, labels, proxy_all):
    used = set()
    proxies = []
    first = None
    for index, path in enumerate(paths):
        data = load_config(path)
        if first is None:
            first = data
        label = labels[index] if index < len(labels) else file_label(path)
        for proxy in data["proxies"]:
            if not isinstance(proxy, dict) or not proxy.get("name"):
                raise ValueError(f"{path} 中存在缺少 name 的代理节点")
            proxy = dict(proxy)
            proxy["name"] = unique_name(proxy["name"], used, label)
            proxies.append(proxy)

    names = [proxy["name"] for proxy in proxies]
    groups = [
        {
            "name": "AUTO",
            "type": "url-test",
            "url": DEFAULT_TEST_URL,
            "interval": 300,
            "tolerance": 50,
            "proxies": list(names),
        },
        {
            "name": "FALLBACK",
            "type": "fallback",
            "url": DEFAULT_TEST_URL,
            "interval": 300,
            "proxies": list(names),
        },
        {
            # 第一个成员是 AUTO，因此默认自动选节点；也可手动指定节点
            "name": "PROXY",
            "type": "select",
            "proxies": ["AUTO", "FALLBACK"] + list(names) + ["DIRECT"],
        },
    ]

    result = {}
    # 保留第一个文件里的通用设置（端口、日志级别、DNS 等）
    for key, value in (first or {}).items():
        if key in ("proxies", "proxy-groups", "rules"):
            continue
        result[key] = value
    result.setdefault("mixed-port", 7890)
    result.setdefault("allow-lan", False)
    result.setdefault("mode", "rule")
    result.setdefault("log-level", "info")
    result.setdefault("unified-delay", True)
    result.setdefault("tcp-concurrent", True)
    result["proxies"] = proxies
    result["proxy-groups"] = groups
    result["rules"] = PROXY_ALL_RULES if proxy_all else DEFAULT_RULES
    return result


def main():
    parser = argparse.ArgumentParser(
        description="合并多台服务器的 Clash/Mihomo 配置，生成自动 + 手动选择节点。"
    )
    parser.add_argument("inputs", nargs="+", metavar="CLASH_YAML", help="待合并的 clash.yaml")
    parser.add_argument("-o", "--output", default="clash-merged.yaml", help="输出文件（默认 clash-merged.yaml）")
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="NAME",
        help="为输入文件指定标签（按顺序对应），用于节点重名时加前缀，可重复",
    )
    parser.add_argument(
        "--proxy-all",
        action="store_true",
        help="只生成 MATCH,PROXY（默认国内直连）",
    )
    args = parser.parse_args()

    if args.label and len(args.label) > len(args.inputs):
        parser.error("--label 的数量不能多于输入文件数量")

    try:
        result = merge(args.inputs, args.label, args.proxy_all)
    except ValueError as exc:
        sys.stderr.write(f"错误: {exc}\n")
        sys.exit(1)

    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(result, handle, allow_unicode=True, sort_keys=False, default_flow_style=False)

    node_count = len(result["proxies"])
    print(f"已合并 {len(args.inputs)} 个配置文件，共 {node_count} 个节点")
    print(f"输出文件: {os.path.abspath(args.output)}")
    print("策略组: AUTO（自动测速）、FALLBACK（故障转移）、PROXY（手动选择）")


if __name__ == "__main__":
    main()
