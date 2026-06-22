from __future__ import annotations

import atexit
import importlib
import multiprocessing as mp
import threading
import traceback

from tqdm import tqdm
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from logger import logger

# ---- 可调参数 ----
DEFAULT_PROCESSES = 8
ORDERED = True
CHUNKSIZE: Optional[int] = None
SMALL_TASK_THRESHOLD = 2
MAX_FAILURE_LOG = 3

# ---- 模块级状态 ----
_worker_context: dict[str, Any] = {}
_pool: Optional[Any] = None  # multiprocessing.pool.Pool
_pool_size: Optional[int] = None
_pool_lock = threading.Lock()  # 保护进程池的创建 / 销毁
_run_lock = threading.Lock()  # 保证同一时刻只有一个并行任务在跑
_FUNC_REGISTRY: dict[str, Callable[..., Any]] = {}


def get_context() -> dict[str, Any]:
    """暴露给 worker 内业务代码存放每进程状态 (如已建立的连接)."""
    return _worker_context


# ---------------------------------------------------------------------------
# worker 初始化 / 进程池管理
# ---------------------------------------------------------------------------
def _init_worker(config_path: Optional[str]) -> None:
    """每个 worker 进程启动时执行. config_path 为 None 时跳过配置初始化 (便于测试)."""
    _worker_context.clear()
    if config_path is None:
        return
    from config import load_config

    load_config(config_path)


def _resolve_config_path() -> Optional[str]:
    """取当前配置文件路径; 未初始化配置 (如单测环境) 时返回 None."""
    try:
        from config import get_config_path

        return get_config_path()
    except Exception:  # noqa: BLE001 - 配置未初始化属正常降级路径
        return None


def _get_pool(n: int) -> Any:
    """惰性创建并复用进程池. 请求进程数变化时重建, 避免旧池大小被永久锁死."""
    global _pool, _pool_size
    with _pool_lock:
        if _pool is not None and _pool_size != n:
            _pool.close()
            _pool.join()
            _pool = None
            _pool_size = None
        if _pool is None:
            _pool = mp.Pool(
                processes=n, initializer=_init_worker, initargs=(_resolve_config_path(),)
            )
            _pool_size = n
        return _pool


@atexit.register
def _cleanup() -> None:
    global _pool, _pool_size
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool.join()
            _pool = None
            _pool_size = None


# ---------------------------------------------------------------------------
# 任务结果 / 函数注册
# ---------------------------------------------------------------------------
@dataclass
class TaskResult:
    success: bool
    value: Any = None
    error: Optional[str] = None  # 存字符串而非异常对象, 避免跨进程 pickle 失败
    traceback: Optional[str] = None


def _register_func(name: str, func: Callable[..., Any]) -> None:
    _FUNC_REGISTRY[name] = func


def _resolve_func(module: str, name: str) -> Callable[..., Any]:
    """按全限定名取函数. spawn 下 worker 不继承注册表, 首次未命中时导入定义模块,
    触发 @parallel 装饰器在本 worker 内重新注册."""
    if name not in _FUNC_REGISTRY:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法导入定义 '{name}' 的模块 '{module}': {exc!r}") from exc
    if name not in _FUNC_REGISTRY:
        raise RuntimeError(
            f"函数 '{name}' 未注册 (module={module})。已注册: {list(_FUNC_REGISTRY)}"
        )
    return _FUNC_REGISTRY[name]


def _wrap_call(payload: tuple[str, str, Any, bool]) -> TaskResult:
    module, name, args, is_star = payload
    try:
        func = _resolve_func(module, name)
        value = func(*args) if is_star else func(args)
        return TaskResult(success=True, value=value)
    except Exception as exc:  # noqa: BLE001 - 需捕获任意业务异常并回传
        return TaskResult(
            success=False, error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()
        )


# ---------------------------------------------------------------------------
# 装饰器
# ---------------------------------------------------------------------------
class ParallelFunc:
    def __init__(self, func: Callable[..., Any]) -> None:
        self._func = func
        self._module = func.__module__
        self._name = f"{func.__module__}.{func.__qualname__}"
        self.__name__ = func.__name__
        self.__qualname__ = func.__qualname__
        self.__doc__ = func.__doc__
        _register_func(self._name, func)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """透明代理: 装饰后仍可像普通函数一样直接调用."""
        return self._func(*args, **kwargs)

    def run(
        self, iterable: Iterable[Any], desc: Optional[str] = None, disable_progress: bool = False
    ) -> list[TaskResult]:
        """单参数映射: 每个元素作为 func 的唯一入参."""
        payloads = [(self._module, self._name, x, False) for x in iterable]
        return self._dispatch(payloads, desc, disable_progress)

    def run_star(
        self, iterable: Iterable[Any], desc: Optional[str] = None, disable_progress: bool = False
    ) -> list[TaskResult]:
        """多参数映射: 每个元素为参数元组, 以 func(*args) 展开调用."""
        payloads = [(self._module, self._name, tuple(args), True) for args in iterable]
        return self._dispatch(payloads, desc, disable_progress)

    # -- 内部 --
    @staticmethod
    def _max_procs() -> int:
        try:
            from config import get_config

            max_core = get_config().runtime.max_core
        except Exception:  # noqa: BLE001 - 无配置时退回 CPU 数
            max_core = mp.cpu_count()
        return max(1, min(DEFAULT_PROCESSES, max_core))

    @staticmethod
    def _calc_chunksize(total: int, procs: int) -> int:
        if CHUNKSIZE is not None:
            return CHUNKSIZE
        return max(1, total // (procs * 4))

    def _dispatch(
        self,
        payloads: list[tuple[str, str, Any, bool]],
        desc: Optional[str],
        disable_progress: bool,
    ) -> list[TaskResult]:
        if not _run_lock.acquire(blocking=False):
            raise RuntimeError("已有并行任务正在执行, 不支持并发调用")
        try:
            total = len(payloads)
            label = desc or f"{self.__qualname__}()"

            if total <= SMALL_TASK_THRESHOLD:
                logger.info(f"任务数 {total} <= {SMALL_TASK_THRESHOLD}, 串行执行: {label}")
                return self._run_serial(payloads, label, disable_progress)

            max_procs = self._max_procs()
            chunksize = self._calc_chunksize(total, max_procs)
            logger.info(
                f"并行执行 {label}: {max_procs} 进程, chunksize={chunksize}, 任务数={total}"
            )

            pool = _get_pool(max_procs)
            imap_fn = pool.imap if ORDERED else pool.imap_unordered
            iterator = imap_fn(_wrap_call, payloads, chunksize)
            return self._consume(iterator, label, total, disable_progress)
        finally:
            _run_lock.release()

    def _run_serial(
        self, payloads: list[tuple[str, str, Any, bool]], label: str, disable_progress: bool
    ) -> list[TaskResult]:
        return self._consume(
            (_wrap_call(p) for p in payloads), label, len(payloads), disable_progress
        )

    def _consume(
        self, iterator: Iterable[TaskResult], label: str, total: int, disable_progress: bool
    ) -> list[TaskResult]:
        results: list[TaskResult] = []
        stats = {"ok": 0, "fail": 0}
        with tqdm(iterator, desc=label, total=total, disable=disable_progress) as pbar:
            for r in pbar:
                results.append(r)
                stats["ok" if r.success else "fail"] += 1
                pbar.set_postfix(stats)
        if not disable_progress:
            self._summary(label, stats, results)
        return results

    @staticmethod
    def _summary(label: str, stats: dict[str, int], results: list[TaskResult]) -> None:
        total = stats["ok"] + stats["fail"]
        logger.info(f"[{label}] 完成: success={stats['ok']} failed={stats['fail']} total={total}")
        if stats["fail"] > 0:
            shown = 0
            for r in results:
                if r.success:
                    continue
                logger.warning(f"[{label}] 失败: {r.error}")
                shown += 1
                if shown >= MAX_FAILURE_LOG:
                    break


def parallel(func: Callable[..., Any]) -> ParallelFunc:
    return ParallelFunc(func)


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------
def get_success_values(results: list[TaskResult]) -> list[Any]:
    return [r.value for r in results if r.success]


def get_failures(results: list[TaskResult]) -> list[TaskResult]:
    return [r for r in results if not r.success]
