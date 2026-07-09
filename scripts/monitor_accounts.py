#!/usr/bin/env python3
"""监控 chatgpt2api 号池数量，低于阈值自动注册补号。

用法:
  python3 monitor_accounts.py [--url http://localhost:8000] [--key agent2026] [--threshold 5] [--register-count 5] [--proxy http://127.0.0.1:7890]

环境变量（优先级低于命令行参数）:
  C2A_URL, C2A_KEY, C2A_PROXY

退出码: 0=正常, 1=出错
"""
import sys
import time
import argparse
import requests


def check_pool(base, auth_key, proxy=None, timeout=10):
    """返回 (可用账号数, 总账号数)，失败返回 None。

    通过 /api/accounts 获取实时账号列表，只统计状态为"正常"的账号。
    注意：/api/register 的 stats.current_available 是上次注册时的快照，不是实时数据。
    """
    proxies = {"http": proxy, "https": proxy} if proxy else None
    h = {"Authorization": f"Bearer {auth_key}"}
    try:
        r = requests.get(f"{base}/api/accounts", headers=h, proxies=proxies, timeout=timeout)
        r.raise_for_status()
        items = r.json().get("items", [])
        total = len(items)
        # 只统计状态正常的账号
        available = sum(1 for a in items if a.get("status") not in {"禁用", "限流", "异常"})
        return available, total
    except Exception as e:
        print(f"❌ 查询号池失败: {e}", file=sys.stderr)
        return None


def do_register(base, auth_key, count, proxy=None, max_wait=180):
    """启动注册机，轮询等待完成，返回成功数。"""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    h = {"Authorization": f"Bearer {auth_key}", "Content-Type": "application/json"}

    print(f"🔄 号池不足，开始注册 {count} 个账号...", file=sys.stderr)

    # 1. 设置注册参数
    try:
        requests.post(
            f"{base}/api/register",
            headers=h,
            json={"total": count, "mode": "total"},
            proxies=proxies,
            timeout=10,
        )
    except Exception as e:
        print(f"❌ 设置注册参数失败: {e}", file=sys.stderr)
        return 0

    # 2. 启动注册
    try:
        requests.post(
            f"{base}/api/register/start", headers=h, proxies=proxies, timeout=10
        )
    except Exception as e:
        print(f"❌ 启动注册失败: {e}", file=sys.stderr)
        return 0

    # 3. 轮询等待完成
    poll_interval = 3
    max_polls = max_wait // poll_interval
    for i in range(max_polls):
        time.sleep(poll_interval)
        try:
            r = (
                requests.get(
                    f"{base}/api/register",
                    headers=h,
                    proxies=proxies,
                    timeout=5,
                )
                .json()["register"]
            )
            s = r["stats"]
            # 注册机不在运行状态且没有运行中的任务 → 完成
            if not r["enabled"] and s.get("running", 0) == 0:
                success = s.get("success", 0)
                fail = s.get("fail", 0)
                print(f"✅ 注册完成: 成功 {success}, 失败 {fail}", file=sys.stderr)
                return success
            # 还在跑，打印进度
            running = s.get("running", 0)
            success = s.get("success", 0)
            print(
                f"⏳ 注册中... 运行中 {running}, 已成功 {success}",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"⚠️ 轮询失败: {e}", file=sys.stderr)

    print("⚠️ 注册超时（180s），请手动检查", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(description="监控 chatgpt2api 号池，自动补号")
    parser.add_argument(
        "--url",
        default=None,
        help="服务地址 (默认: http://localhost:8000，或环境变量 C2A_URL)",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="Auth Key (默认: agent2026，或环境变量 C2A_KEY)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="号池低于此数量触发注册 (默认: 10)",
    )
    parser.add_argument(
        "--register-count",
        type=int,
        default=5,
        help="每次注册账号数 (默认: 5)",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="HTTP 代理地址 (或环境变量 C2A_PROXY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查不注册，打印当前号池状态",
    )
    args = parser.parse_args()

    base = args.url or __import__("os").environ.get("C2A_URL", "http://localhost:8000")
    auth_key = args.key or __import__("os").environ.get("C2A_KEY", "agent2026")
    proxy = args.proxy or __import__("os").environ.get("C2A_PROXY")

    # 查询当前号池（实时数据）
    result = check_pool(base, auth_key, proxy)
    if result is None:
        sys.exit(1)

    available, total = result
    print(f"📊 号池状态: 可用 {available}/{total}")

    if args.dry_run:
        return

    if available >= args.threshold:
        print(f"✅ 号池充足（{available} >= {args.threshold}），无需补号")
        return

    print(f"⚠️ 号池不足（{available} < {args.threshold}），触发自动补号")
    success = do_register(base, auth_key, args.register_count, proxy)

    # 注册后复查
    time.sleep(2)
    new_result = check_pool(base, auth_key, proxy)
    if new_result:
        new_avail, new_total = new_result
        print(f"📊 补号后号池: 可用 {new_avail}/{new_total}")
        if new_avail >= args.threshold:
            print("✅ 补号成功，号池已恢复")
        else:
            print(f"⚠️ 补号后仍不足（{new_avail} < {args.threshold}），请检查")


if __name__ == "__main__":
    main()
