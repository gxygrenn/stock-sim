#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在禁用系统代理的情况下委托 _premarket.py 执行（本机 127.0.0.1:7890 代理未运行，
导致 urllib 全部返回 WinError 10061；此包装器改为直连，参数原样透传）。

用法: python _premarket_noproxy.py --push _ctx.json
"""
import sys
import urllib.request

urllib.request.getproxies = lambda: {}

sys.argv = ["_premarket.py"] + sys.argv[1:]
import _premarket  # noqa: E402

sys.exit(_premarket.main())
