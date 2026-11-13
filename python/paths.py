#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件自己的路径约定：数据不放在安装目录里。

NEBULA_HOME 默认 ~/.dsh/nebula-mind（和别的 DSH 插件一致），里面：
    memory/     五本账（notes/summaries/thoughts/promises/tacts/profile/vectors/星图）
    logs/       日志
    cache/      向量模型等可再生的东西
安装目录只放代码，升级覆盖不会碰数据。
"""
import os

HOME = os.path.abspath(os.environ.get("NEBULA_HOME") or os.path.join(os.path.expanduser("~"), ".dsh", "nebula-mind"))
MIND = os.path.abspath(os.environ.get("NEBULA_MIND") or os.path.join(HOME, "memory"))
LOGS = os.path.join(HOME, "logs")
CACHE = os.path.join(HOME, "cache")

# 情绪状态：默认读别的插件（feiyu-mood）的状态文件；没有就当作中性
MOOD_STATE = os.environ.get("NEBULA_MOOD_STATE") or os.path.join(os.path.expanduser("~"), ".dsh", "feiyu-mood", "state.json")

# 向量模型目录：用户可覆盖；默认指向 DSH 缓存区
BGE_DIR = os.environ.get("NEBULA_BGE_DIR") or os.path.join(CACHE, "bge-small-zh")

for d in (MIND, LOGS, CACHE):
    os.makedirs(d, exist_ok=True)
