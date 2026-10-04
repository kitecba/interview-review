"""Prompt 的版本化注册表。

每个 prompt 带一个显式的版本号，**版本号参与阶段的幂等键计算**。

这条设计的价值在于消除一类极难排查的问题：改了提示词，但跑出来的结果没变，
因为幂等键没变、阶段被判定为「已完成」直接跳过了。把版本号写进 key，
这种不一致在结构上就不可能发生。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    """一个提示词模板。"""

    name: str
    #: 修改提示词内容时必须同步升这个版本号，否则旧结果会被错误复用
    version: str
    system: str
    user_template: str

    def render(self, **kwargs: object) -> str:
        return self.user_template.format(**kwargs)


_REGISTRY: dict[str, Prompt] = {}


def register(prompt: Prompt) -> Prompt:
    if prompt.name in _REGISTRY:
        raise ValueError(f"提示词重复注册：{prompt.name}")
    _REGISTRY[prompt.name] = prompt
    return prompt


def get(name: str) -> Prompt:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"未注册的提示词：{name}。已注册的有 {sorted(_REGISTRY)}") from exc


def all_prompts() -> dict[str, Prompt]:
    return dict(_REGISTRY)
