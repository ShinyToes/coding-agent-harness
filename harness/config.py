"""配置：harness.toml + 运行时路径。

只用 pydantic BaseModel（不引 pydantic-settings，依赖白名单外）。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from harness.models import canonical_json, sha256_hex


class ModelConfig(BaseModel):
    name: str = "deepseek-chat"
    api_base: str = ""
    api_key_env: str = "DEEPSEEK_API_KEY"
    window: int = 128_000
    temperature: float = 0.0
    max_tokens: int = 8_192


class RunConfig(BaseModel):
    max_steps: int = 120
    max_nudges: int = 2
    compact_at: float = 0.7
    turn_keep: int = 8
    require_verification: bool = True
    read_cache: bool = True  # 消融开关：False 时每次 read 都全量
    compact_enabled: bool = True  # 消融开关：False 时不做自动压缩（溢出兜底仍会压）
    compact_cooldown: int = 2  # 两次自动压缩之间至少隔几个 turn，防止每轮都付一次摘要调用
    nudge_at: float = 0.75  # 步数用到这个比例时提醒模型收尾
    max_cost: float = 0.0  # 成本上限（美元），0 = 不限制


class SandboxConfig(BaseModel):
    kind: str = "local"
    timeout: int = 120
    network: bool = False
    shell: str = "auto"  # auto | cmd | pwsh | bash
    max_output: int = 1_000_000  # 单条命令输出上限（字符），超出留头尾并标记


class PolicyConfig(BaseModel):
    auto_allow_read: bool = True
    approval_mode: str = "interactive"
    allow_read_outside: bool = False


class Settings(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    workspace: Path = Path(".")
    state_dir: Path = Path(".harness")
    calibration: float = 3.0
    max_artifact_preview: int = 1_200
    # 每轮运行前由 loop 注入（S1 / S2）；不进 config_hash。
    task_brief: str = ""
    repo_map: str = ""
    drift_notice: str = ""

    @classmethod
    def load(cls, path: str | Path) -> Settings:
        cfg = cls()
        p = Path(path)
        if p.exists():
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
            cfg = cls.model_validate(raw)
        return cfg

    def with_workspace(self, repo: str | Path) -> Settings:
        repo_path = Path(repo).resolve()
        return self.model_copy(
            update={"workspace": repo_path, "state_dir": repo_path / ".harness"}
        )

    @property
    def db_path(self) -> Path:
        return Path(self.state_dir) / "harness.db"

    @property
    def artifacts_dir(self) -> Path:
        return Path(self.state_dir) / "artifacts"

    @property
    def tasks_dir(self) -> Path:
        return Path(self.state_dir) / "tasks"

    def budget_tokens(self) -> int:
        """组装预算必须大于压缩触发线，否则压缩永远不会触发。"""
        hard = min(self.model.window - self.model.max_tokens - 512, int(self.model.window * 0.9))
        return max(1_000, hard)

    def config_hash(self) -> str:
        payload = {
            "model": self.model.model_dump(),
            "run": self.run.model_dump(),
            "sandbox": self.sandbox.model_dump(),
            "policy": self.policy.model_dump(),
        }
        return sha256_hex(canonical_json(payload))[:12]
