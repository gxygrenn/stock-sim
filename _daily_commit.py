#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""每日自动提交：拉三账户快照 → git add/commit/push，一次调用完成。

用法:
  python _daily_commit.py              # 完整流程
  python _daily_commit.py --dry-run    # 只拉快照 + 看 diff，不提交不推送
"""
import argparse
import datetime
import json
import os
import ssl
import subprocess
import sys
import urllib.request

BASE = "https://stock-sim-us.app.workbuddy.host"
SHARECODE = "6R7GBTHXQI"  # 2026-11-12 平台改版：所有请求必须带 sharecode，否则网关 403
ACCOUNTS = ["us", "cn_conservative", "cn_aggressive"]
ROOT = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(ROOT, "multi-market", "snapshot", "data-multi.snapshot.json")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def git(*args, timeout=180):
    p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="ignore", timeout=timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def fetch_states():
    data, ok, fail = {}, [], []
    for acc in ACCOUNTS:
        try:
            req = urllib.request.Request("%s/api/state?account=%s&sharecode=%s" % (BASE, acc, SHARECODE),
                                         headers={"User-Agent": "daily-commit/2.0"})
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                data[acc] = json.loads(r.read().decode("utf-8"))
            ok.append(acc)
        except Exception as e:
            fail.append("%s(%s)" % (acc, str(e)[:40]))
    # 失败账户保留旧快照，不写空对象
    if os.path.exists(SNAP):
        try:
            old = json.load(open(SNAP, encoding="utf-8"))
            for acc in ACCOUNTS:
                if acc not in data and acc in old:
                    data[acc] = old[acc]
        except Exception:
            pass
    return data, ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    today = datetime.date.today().strftime("%Y-%m-%d")

    data, ok, fail = fetch_states()
    if data:
        os.makedirs(os.path.dirname(SNAP), exist_ok=True)
        with open(SNAP, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    print("快照：成功 %s｜失败 %s｜写入 %s" % (
        ",".join(ok) or "无", ",".join(fail) or "无",
        ("%d 个账户 %.0fKB" % (len(data), os.path.getsize(SNAP) / 1024)) if os.path.exists(SNAP) else "未写入"))

    code, status = git("status", "--short")
    if not status:
        print("工作区干净，无需提交")
        return 0
    lines = [l for l in status.splitlines() if l.strip()]
    print("待提交 %d 个文件：%s" % (len(lines), "、".join(l.split()[-1] for l in lines[:12])))
    if args.dry_run:
        print("dry-run，未提交")
        return 0

    git("add", "-A")
    code, out = git("commit", "-m", "chore: 每日自动提交 %s（含数据快照）" % today)
    if code != 0:
        print("提交失败：%s" % out[:300])
        return 1
    hash7 = ""
    _, h = git("rev-parse", "--short", "HEAD")
    hash7 = h.strip()
    print("提交成功 %s" % hash7)

    for i in range(1, 4):
        code, out = git("push", "origin", "main", timeout=180)
        if code == 0:
            print("推送成功（第 %d 次）" % i)
            return 0
        if "could not read Username" in out or "Authentication failed" in out:
            print("推送失败：凭据缺失（不是网络问题），需要配置 PAT / gh auth login")
            return 1
        print("推送第 %d 次失败：%s" % (i, out.splitlines()[-1][:120] if out else "-"))
    _, ahead = git("rev-list", "--count", "origin/main..HEAD")
    print("推送失败，本地领先 origin/main %s 个提交" % ahead.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())
