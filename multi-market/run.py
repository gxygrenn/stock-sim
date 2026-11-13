#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产入口（沙箱 startCmd = python run.py）。

server.py 自 2026-11-13 拆分后只作为可导入的核心模块与 facade，
直接运行 server.py 不再启动服务；必须经由本入口启动，
并在导入 server 之前设置 SIM_PRODUCTION=1 以使用真实存档
（测试与调试导入不设置该变量，自动落到 PID 临时文件，防止污染真实数据）。
"""
import os
import sys

os.environ.setdefault("SIM_PRODUCTION", "1")
import server  # noqa: E402

sys.exit(server.main())
