---
globs: ["src/**/*.cpp", "src/**/*.h", "src/**/*.hpp", "include/**/*.h", "include/**/*.hpp"]
---
# C++ 代码规范

## 核心原则
- 优先使用 C++17/20 标准特性，避免使用过时的 C 风格宏和裸指针。
- 严格遵循 RAII 原则，资源管理必须通过智能指针或容器实现，禁止手动 `new/delete`。
- 保持函数短小精悍，单一职责，避免深层嵌套。

## 命名约定
- **变量与函数**：使用 `snake_case`（小写加下划线）。
- **类与结构体**：使用 `PascalCase`（大驼峰）。
- **类成员变量**：以 `m_` 为前缀，例如 `m_user_name`。
- **常量与宏**：使用 `UPPER_SNAKE_CASE`。
- **命名空间**：使用全小写 `snake_case`。

## 类型与内存安全
- 优先使用值语义（Value Semantics）和 `std::optional` 处理可能为空的返回值。
- 必须使用 `std::unique_ptr` 或 `std::shared_ptr` 管理堆内存。
- 优先使用 `std::string_view` 传递只读字符串，避免不必要的拷贝。
- 集合遍历时，如果不修改元素，必须使用 `const auto&`。

## 现代特性偏好
- 优先使用 `auto` 进行类型推导，尤其是当右侧类型冗长或显而易见时。
- 使用 `constexpr` 和 `consteval` 替代传统的 `#define` 宏常量。
- 使用 `<format>` (C++20) 或 `fmt` 库进行字符串格式化，禁用 `printf`。
- 使用 `<filesystem>` 处理文件路径，禁用 `os.path` 风格的字符串拼接。