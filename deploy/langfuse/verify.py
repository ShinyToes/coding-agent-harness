"""查询本地 Langfuse：列出 trace、观察 span 树、确认 session 归组。

用法：python deploy/langfuse/verify.py [--wait 90]
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

BASE = "http://127.0.0.1:3000/api/public"
PUBLIC_KEY = "pk-lf-harness-local"
SECRET_KEY = "sk-lf-harness-local"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=60, help="最多等多少秒让 worker 入库")
    parser.add_argument("--trace", default=None, help="指定 trace id，否则取最新")
    args = parser.parse_args()

    auth = httpx.BasicAuth(PUBLIC_KEY, SECRET_KEY)
    client = httpx.Client(base_url=BASE, auth=auth, timeout=20)

    deadline = time.time() + args.wait
    traces: list[dict] = []
    while time.time() < deadline:
        response = client.get("/traces", params={"limit": 10})
        if response.status_code == 200:
            traces = response.json().get("data", [])
            if traces:
                break
        time.sleep(3)

    if not traces:
        print("no traces yet (check worker logs: docker compose -f deploy/langfuse/docker-compose.yml logs worker)")
        sys.exit(1)

    print(f"traces={len(traces)}")
    for trace in traces[:5]:
        print(
            f"- {trace['id'][:16]} name={trace.get('name')} session={trace.get('sessionId')} "
            f"latency={trace.get('latency')} observations={len(trace.get('observations') or [])}"
        )

    target = args.trace or traces[0]["id"]
    detail = client.get(f"/traces/{target}")
    if detail.status_code != 200:
        print("trace detail failed:", detail.status_code, detail.text[:200])
        sys.exit(1)

    data = detail.json()
    observations = data.get("observations") or []
    print(f"\ntrace {target}: observations={len(observations)}")
    names = {item["id"]: item.get("name") for item in observations}
    for item in observations:
        parent = item.get("parentObservationId")
        print(
            f"  {item.get('name'):14s} parent={names.get(parent, '<root>'):14s} "
            f"type={item.get('type')} latency={item.get('latency')}"
        )

    root = next((item for item in observations if item.get("name") == "harness.turn"), None)
    if root and root.get("usage"):
        print("\nusage:", root["usage"])
    print("\nUI:", f"http://127.0.0.1:3000/traces/{target}")


if __name__ == "__main__":
    main()
