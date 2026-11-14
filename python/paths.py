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

# 情绪状态：自带引擎的实况文件（不是别人的插件——我们已经自己算心情了）
MOOD_STATE = os.environ.get("NEBULA_MOOD_STATE") or os.path.join(MIND, "mood-state.json")

# 向量模型目录：用户可覆盖；默认指向 DSH 缓存区
# 向量模型的实际落点：D 盘的模型缓存（和 embed_server.py 用同一个），
# 不是插件自己的 cache —— 92MB 的模型不该在 ~/.dsh 里再放一份。
BGE_DIR = (os.environ.get("NEBULA_BGE_DIR")
           or os.environ.get("YU_BGE_DIR")
           or r"D:\cache\modelscope\bge-small-zh")

for d in (MIND, LOGS, CACHE):
    os.makedirs(d, exist_ok=True)
