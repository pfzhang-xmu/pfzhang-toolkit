#!/usr/bin/env python3
"""
预处理 Elsevier URL：通过 doi.org 重定向找到正确的 PII。
生成 elsevier_pii_map.tsv。

用法: python3 preprocess_elsevier.py
"""

import csv
import time
import requests
from pathlib import Path

PROXY = "http://127.0.0.1:3456"
TSV_FILE = Path(__file__).parent / "manual_download.tsv"
MAP_FILE = Path(__file__).parent / "elsevier_pii_map.tsv"


def main():
    # 读取所有 Elsevier 论文
    papers = []
    with open(TSV_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if "sciencedirect" in row["url"]:
                papers.append(row)

    print(f"共 {len(papers)} 篇 Elsevier 论文")
    print(f"通过 doi.org 重定向获取正确 PII...")
    print()

    # 检查已有映射
    existing = {}
    if MAP_FILE.exists():
        with open(MAP_FILE, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                existing[row["doi"]] = row["pii"]

    results = dict(existing)
    batch_size = 10
    processed = 0

    for i, p in enumerate(papers):
        doi = p["doi"]
        if doi in results:
            continue

        # 打开新 tab
        try:
            r = requests.get(f"{PROXY}/new", params={"url": "about:blank"}, timeout=15)
            tab = r.json().get("targetId")

            # 通过 doi.org 重定向
            r = requests.get(f"{PROXY}/navigate",
                           params={"target": tab, "url": f"https://doi.org/{doi}"},
                           timeout=30)

            # 等待重定向完成
            pii = None
            for _ in range(15):
                time.sleep(2)
                try:
                    r = requests.post(f"{PROXY}/eval", params={"target": tab},
                                    data="location.href", timeout=10)
                    url = r.json().get("value", "")
                    if url and "sciencedirect.com" in url and "/pii/" in url:
                        pii = url.split("/pii/")[1].split("?")[0].split("#")[0]
                        break
                except Exception:
                    pass

            requests.get(f"{PROXY}/close", params={"target": tab}, timeout=10)

            if pii:
                results[doi] = pii
                processed += 1
                print(f"  [{processed}] {doi} → {pii}")
            else:
                print(f"  [{i+1}/{len(papers)}] ✗ {doi}: 获取 PII 失败")

        except Exception as e:
            print(f"  [{i+1}/{len(papers)}] ✗ {doi}: {e}")

        # 定期保存
        if len(results) % batch_size == 0:
            with open(MAP_FILE, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["doi", "pii"], delimiter="\t")
                writer.writeheader()
                for d, p_val in results.items():
                    writer.writerow({"doi": d, "pii": p_val})

        # 间隔
        time.sleep(0.5)

    # 最终保存
    with open(MAP_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["doi", "pii"], delimiter="\t")
        writer.writeheader()
        for d, p_val in results.items():
            writer.writerow({"doi": d, "pii": p_val})

    print(f"\n完成！共解析 {len(results)} 个 PII，保存到 {MAP_FILE}")


if __name__ == "__main__":
    main()
