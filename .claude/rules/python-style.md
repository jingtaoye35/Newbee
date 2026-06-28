---
globs: ["alpha_backend/**/*.py", "tests/**/*.py", "scripts/**/*.py"]
---
# Python 代码规范

## 核心原则
- 遵循 PEP 8 规范，行宽限制为 150 字符。
- 强制使用类型提示（Type Hints），所有公开函数必须有入参和返回值注解。
- 在文件顶部统一添加 `from __future__ import annotations`。

## 命名约定
- **变量与函数**：使用 `snake_case`。
- **类**：使用 `PascalCase`。
- **常量**：使用 `UPPER_SNAKE_CASE`，并在模块顶部定义。
- **私有成员**：使用单下划线前缀 `_private_var`，避免使用双下划线（名称改写）。

## 类型系统与数据结构
- 优先使用内置泛型（如 `list[str]`, `dict[str, int]`），而非 `typing` 模块中的旧别名。
- 优先使用 `dataclasses` 或 `pydantic.BaseModel` 定义数据结构，避免使用裸字典传递复杂状态。
- 使用 `Enum` 代替魔术字符串或魔法数字。

## 异步与并发
- 涉及 I/O 操作（网络请求、数据库、文件读写）的函数必须声明为 `async def`。
- 禁止在异步上下文中调用阻塞的同步 I/O 函数。
- 优先使用 `asyncio` 标准库，除非有明确的性能瓶颈再考虑多线程/多进程。

## 异常与日志
- 禁止使用 `print` 进行调试或输出，统一使用内部 `logger` 模块。
- 捕获异常时必须指定具体异常类型，禁止使用裸 `except:`。
- 记录日志时，使用 f-string 或 `%s` 占位符，不要在日志函数外进行字符串拼接。