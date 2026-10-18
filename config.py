"""
配置层
provider 可插拔，Key 与参数从环境变量读取
"""
import  os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT=Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT/".env")
@dataclass(frozen=True)
class ProviderConfig:
    name:str
    base_url:str
    default_model:str
    supports_thinking:bool=False
    thinking_extra_body:dict|None=None
PROVIDERS={
    "deepseek":ProviderConfig(
        name="deepseek",
        base_url="https://api.deepseek.com",
        default_model="deepseek-flash",
        supports_thinking=True,
        thinking_extra_body={"thinking":{"type":"enabled"}},
    ),
}
DEFAULT_PROVIDER="deepseek"

@dataclass
class Settings:
    provider:ProviderConfig
    model:str
    api_key:str
    workspace:Path
    timeout:int
    max_retries:int
    thinking:bool
    dotenv_loaded:bool
    run_python_timeout: int = 10
    review_max_steps:int = 14
    fix_max_steps: int = 10
    max_fix_rounds: int = 2
    node_retries: int = 2

    @property
    def has_api_key(self)->bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-请")

def _env_str(key:str,default:str="")->str:
    value=os.environ.get(key)
    return default if value is None or value.strip()=="" else value.strip()

def _env_int(key:str,default:int)->int:
    try:
        return int(_env_str(key) or default)
    except ValueError:
        return default

def _env_bool(key:str,default:bool)->bool:
    raw=_env_str(key).lower()
    if raw in {"1","true","yes","on"}:
        return True
    if raw in {"0","false","no","off"}:
        return False
    return default

def load_settings(workspace:str|None=None)->Settings:
    name=_env_str("LLM_PROVIDER",DEFAULT_PROVIDER).lower()
    if name not in PROVIDERS:
        print(f"[config] 未知 provider {name!r},回退到 {DEFAULT_PROVIDER}")
        name=DEFAULT_PROVIDER
    provider=PROVIDERS[name]
    api_key=_env_str("LLM_API_KEY") or _env_str(f"{name.upper()}_API_KEY")
    ws=Path(workspace).resolve() if workspace else Path.cwd()

    return Settings(
        provider=provider,
        model=_env_str("LLM_MODEL") or provider.default_model,
        api_key=api_key,
        workspace=ws,
        timeout=_env_int("LLM_TIMEOUT", 120),
        max_retries=_env_int("LLM_MAX_RETRIES", 4),
        thinking=_env_bool("LLM_THINKING", provider.supports_thinking),
        dotenv_loaded=True,
        run_python_timeout=_env_int("RUN_PYTHON_TIMEOUT", 10),
    )

def describe(s: Settings) -> str:
    return (
        f"provider={s.provider.name} | model={s.model} | "
        f"thinking={'on' if s.thinking else 'off'} | "
        f"api_key={'已配置' if s.has_api_key else '缺失'} | "
        f"workspace={s.workspace}"
    )