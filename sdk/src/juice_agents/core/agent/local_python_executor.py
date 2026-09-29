"""
本地 Python 代码执行器。

参考 hf-agents 的实现，使用 AST 安全执行模型：
- 内置安全的 Python tools，禁止危险内置函数与非法导入
- 支持 submit_output() 终止，add_image() 需显式注入工具后才能收集图片
- 支持注入工具与变量，维持状态、打印日志与输出截断
"""

from __future__ import annotations

import ast
import builtins
import difflib
import inspect
import logging
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from functools import wraps
from importlib import import_module
from importlib.util import find_spec
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Any

from juice_agents.core.agent.tools.builtin.code_execution.security import (
    guard_subprocess_call,
    is_blocked_python_call,
    is_blocked_python_import,
)
from juice_agents.core.agent.tools.runtime.base_tools import Tool

logger = logging.getLogger(__name__)


class InterpreterError(ValueError):
    """解释器执行失败时的异常，包含安全检查与语法错误等。"""

    pass


ERRORS = {
    name: getattr(builtins, name)
    for name in dir(builtins)
    if isinstance(getattr(builtins, name), type) and issubclass(getattr(builtins, name), BaseException)
}

MAX_OPERATIONS = 10_000_000
MAX_WHILE_ITERATIONS = 1_000_000
ALLOWED_DUNDER_METHODS = ["__init__", "__str__", "__repr__"]

# 与参考实现一致：允许导入的标准库白名单
BASE_BUILTIN_MODULES = [
    "collections",
    "datetime",
    "itertools",
    "math",
    "queue",
    "random",
    "re",
    "stat",
    "statistics",
    "time",
    "unicodedata",
]

def custom_print(*args):
    return None


def nodunder_getattr(obj, name, default=None):
    if name.startswith("__") and name.endswith("__"):
        raise InterpreterError(f"Forbidden access to dunder attribute: {name}")
    return getattr(obj, name, default)


BASE_PYTHON_TOOLS = {
    "print": custom_print,
    "isinstance": isinstance,
    "range": range,
    "float": float,
    "int": int,
    "bool": bool,
    "str": str,
    "set": set,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "degrees": math.degrees,
    "radians": math.radians,
    "pow": pow,
    "sqrt": math.sqrt,
    "len": len,
    "sum": sum,
    "max": max,
    "min": min,
    "abs": abs,
    "enumerate": enumerate,
    "zip": zip,
    "reversed": reversed,
    "sorted": sorted,
    "all": all,
    "any": any,
    "map": map,
    "filter": filter,
    "ord": ord,
    "chr": chr,
    "next": next,
    "iter": iter,
    "divmod": divmod,
    "callable": callable,
    "getattr": nodunder_getattr,
    "hasattr": hasattr,
    "setattr": setattr,
    "issubclass": issubclass,
    "type": type,
    "complex": complex,
    "open": open,
}

# 禁止直接访问的高危模块与函数名（防止逃逸沙箱）
DANGEROUS_MODULES = [
    "builtins",
    "io",
    "multiprocessing",
    "os",
    "pathlib",
    "pty",
    "shutil",
    "socket",
    "subprocess",
    "sys",
]

DANGEROUS_FUNCTIONS = [
    "builtins.compile",
    "builtins.eval",
    "builtins.exec",
    "builtins.globals",
    "builtins.locals",
    "builtins.__import__",
    "os.popen",
    "os.system",
    "posix.system",
]


def check_safer_result(result: Any, static_tools: dict[str, Callable] = None, authorized_imports: list[str] = None):
    """检查返回结果是否安全，主要针对模块与危险函数。"""
    if isinstance(result, ModuleType):
        if not check_import_authorized(result.__name__, authorized_imports):
            raise InterpreterError(f"Forbidden access to module: {result.__name__}")
    elif isinstance(result, dict) and result.get("__spec__"):
        if not check_import_authorized(result["__name__"], authorized_imports):
            raise InterpreterError(f"Forbidden access to module: {result['__name__']}")
    elif isinstance(result, (FunctionType, BuiltinFunctionType)):
        for qualified_function_name in DANGEROUS_FUNCTIONS:
            module_name, function_name = qualified_function_name.rsplit(".", 1)
            if (
                (static_tools is None or function_name not in static_tools)
                and result.__name__ == function_name
                and result.__module__ == module_name
            ):
                raise InterpreterError(f"Forbidden access to function: {function_name}")


def safer_eval(func: Callable):
    """封装 evaluate_ast，统一检查返回值安全性。"""

    @wraps(func)
    def _check_return(
        expression,
        state,
        static_tools,
        custom_tools,
        authorized_imports=BASE_BUILTIN_MODULES,
    ):
        result = func(expression, state, static_tools, custom_tools, authorized_imports=authorized_imports)
        check_safer_result(result, static_tools, authorized_imports)
        return result

    return _check_return


def safer_func(
    func: Callable,
    static_tools: dict[str, Callable] = BASE_PYTHON_TOOLS,
    authorized_imports: list[str] = BASE_BUILTIN_MODULES,
):
    """封装单个函数调用，保证返回值安全。"""
    if isinstance(func, type):
        return func

    @wraps(func)
    def _check_return(*args, **kwargs):
        result = func(*args, **kwargs)
        check_safer_result(result, static_tools, authorized_imports)
        return result

    return _check_return


class PrintContainer:
    """收集 print 输出的容器，便于截断与回传。"""

    def __init__(self):
        self.value = ""

    def append(self, text):
        self.value += text
        return self

    def __iadd__(self, other):
        self.value += str(other)
        return self

    def __str__(self):
        return self.value

    def __repr__(self):
        return f"PrintContainer({self.value})"

    def __len__(self):
        return len(self.value)


class BreakException(Exception):
    pass


class ContinueException(Exception):
    pass


class ReturnException(Exception):
    def __init__(self, value):
        self.value = value


def build_import_tree(authorized_imports: list[str]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for import_path in authorized_imports:
        parts = import_path.split(".")
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
    return tree


def check_import_authorized(import_to_check: str, authorized_imports: list[str]) -> bool:
    current_node = build_import_tree(authorized_imports)
    for part in import_to_check.split("."):
        if "*" in current_node:
            return True
        if part not in current_node:
            return False
        current_node = current_node[part]
    return True


def _get_blocked_imports(state: dict[str, Any]) -> list[str]:
    value = state.get("__blocked_imports__", [])
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _get_blocked_call_prefixes(state: dict[str, Any]) -> list[str]:
    value = state.get("__blocked_call_prefixes__", [])
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _get_blocked_shell_patterns(state: dict[str, Any]) -> list[Any]:
    value = state.get("__blocked_shell_patterns__", [])
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _get_attribute_path(expression: ast.AST) -> str:
    """尽量从 AST 里还原属性链，保留调用时使用的模块名前缀。"""

    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        prefix = _get_attribute_path(expression.value)
        return f"{prefix}.{expression.attr}".strip(".") if prefix else expression.attr
    return ""


def evaluate_attribute(
    expression: ast.Attribute,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    if expression.attr.startswith("__") and expression.attr.endswith("__"):
        raise InterpreterError(f"Forbidden access to dunder attribute: {expression.attr}")
    value = evaluate_ast(expression.value, state, static_tools, custom_tools, authorized_imports)
    return getattr(value, expression.attr)


def evaluate_unaryop(
    expression: ast.UnaryOp,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    operand = evaluate_ast(expression.operand, state, static_tools, custom_tools, authorized_imports)
    if isinstance(expression.op, ast.USub):
        return -operand
    if isinstance(expression.op, ast.UAdd):
        return operand
    if isinstance(expression.op, ast.Not):
        return not operand
    if isinstance(expression.op, ast.Invert):
        return ~operand
    raise InterpreterError(f"Unary operation {expression.op.__class__.__name__} is not supported.")


def evaluate_lambda(
    lambda_expression: ast.Lambda,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Callable:
    args = [arg.arg for arg in lambda_expression.args.args]

    def lambda_func(*values: Any) -> Any:
        new_state = state.copy()
        for arg, value in zip(args, values):
            new_state[arg] = value
        return evaluate_ast(
            lambda_expression.body,
            new_state,
            static_tools,
            custom_tools,
            authorized_imports,
        )

    return lambda_func


def evaluate_while(
    while_loop: ast.While,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> None:
    iterations = 0
    while evaluate_ast(while_loop.test, state, static_tools, custom_tools, authorized_imports):
        for node in while_loop.body:
            try:
                evaluate_ast(node, state, static_tools, custom_tools, authorized_imports)
            except BreakException:
                return None
            except ContinueException:
                break
        iterations += 1
        if iterations > MAX_WHILE_ITERATIONS:
            raise InterpreterError(f"Maximum number of {MAX_WHILE_ITERATIONS} iterations in While loop exceeded")
    return None


def create_function(
    func_def: ast.FunctionDef,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Callable:
    source_code = ast.unparse(func_def)

    def new_func(*args: Any, **kwargs: Any) -> Any:
        func_state = state.copy()
        arg_names = [arg.arg for arg in func_def.args.args]
        default_values = [
            evaluate_ast(d, state, static_tools, custom_tools, authorized_imports) for d in func_def.args.defaults
        ]
        defaults = dict(zip(arg_names[-len(default_values) :], default_values))

        for name, value in zip(arg_names, args):
            func_state[name] = value

        for name, value in kwargs.items():
            func_state[name] = value

        if func_def.args.vararg:
            vararg_name = func_def.args.vararg.arg
            func_state[vararg_name] = args

        if func_def.args.kwarg:
            kwarg_name = func_def.args.kwarg.arg
            func_state[kwarg_name] = kwargs

        for name, value in defaults.items():
            if name not in func_state:
                func_state[name] = value

        if func_def.args.args and func_def.args.args[0].arg == "self":
            if args:
                func_state["self"] = args[0]
                func_state["__class__"] = args[0].__class__

        result = None
        try:
            for stmt in func_def.body:
                result = evaluate_ast(stmt, func_state, static_tools, custom_tools, authorized_imports)
        except ReturnException as e:
            result = e.value

        if func_def.name == "__init__":
            return None
        return result

    new_func.__ast__ = func_def
    new_func.__source__ = source_code
    new_func.__name__ = func_def.name
    return new_func


def evaluate_function_def(
    func_def: ast.FunctionDef,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Callable:
    custom_tools[func_def.name] = create_function(func_def, state, static_tools, custom_tools, authorized_imports)
    return custom_tools[func_def.name]


def evaluate_class_def(
    class_def: ast.ClassDef,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> type:
    class_name = class_def.name
    bases = [evaluate_ast(base, state, static_tools, custom_tools, authorized_imports) for base in class_def.bases]

    metaclass = type
    for base in bases:
        base_metaclass = type(base)
        if base_metaclass is not type:
            metaclass = base_metaclass
            break

    if hasattr(metaclass, "__prepare__"):
        class_dict = metaclass.__prepare__(class_name, bases)
    else:
        class_dict = {}

    for stmt in class_def.body:
        if isinstance(stmt, ast.FunctionDef):
            class_dict[stmt.name] = evaluate_ast(stmt, state, static_tools, custom_tools, authorized_imports)
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value:
                value = evaluate_ast(stmt.value, state, static_tools, custom_tools, authorized_imports)
            target = stmt.target
            if isinstance(target, ast.Name):
                annotation = evaluate_ast(stmt.annotation, state, static_tools, custom_tools, authorized_imports)
                class_dict.setdefault("__annotations__", {})[target.id] = annotation
                if stmt.value:
                    class_dict[target.id] = value
            elif isinstance(target, ast.Attribute):
                obj = evaluate_ast(target.value, class_dict, static_tools, custom_tools, authorized_imports)
                if stmt.value:
                    setattr(obj, target.attr, value)
            elif isinstance(target, ast.Subscript):
                container = evaluate_ast(target.value, class_dict, static_tools, custom_tools, authorized_imports)
                index = evaluate_ast(target.slice, state, static_tools, custom_tools, authorized_imports)
                if stmt.value:
                    container[index] = value
            else:
                raise InterpreterError(f"Unsupported AnnAssign target in class body: {type(target).__name__}")
        elif isinstance(stmt, ast.Assign):
            value = evaluate_ast(stmt.value, state, static_tools, custom_tools, authorized_imports)
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    class_dict[target.id] = value
                elif isinstance(target, ast.Attribute):
                    obj = evaluate_ast(target.value, class_dict, static_tools, custom_tools, authorized_imports)
                    setattr(obj, target.attr, value)
        elif isinstance(stmt, ast.Pass):
            pass
        elif (
            isinstance(stmt, ast.Expr)
            and stmt == class_def.body[0]
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            class_dict["__doc__"] = stmt.value.value
        else:
            raise InterpreterError(f"Unsupported statement in class body: {stmt.__class__.__name__}")

    new_class = metaclass(class_name, tuple(bases), class_dict)
    state[class_name] = new_class
    return new_class


def evaluate_annassign(
    annassign: ast.AnnAssign,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    if annassign.value:
        value = evaluate_ast(annassign.value, state, static_tools, custom_tools, authorized_imports)
        set_value(annassign.target, value, state, static_tools, custom_tools, authorized_imports)
        return value
    return None


def evaluate_augassign(
    expression: ast.AugAssign,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    def get_current_value(target: ast.AST) -> Any:
        if isinstance(target, ast.Name):
            return state.get(target.id, 0)
        if isinstance(target, ast.Subscript):
            obj = evaluate_ast(target.value, state, static_tools, custom_tools, authorized_imports)
            key = evaluate_ast(target.slice, state, static_tools, custom_tools, authorized_imports)
            return obj[key]
        if isinstance(target, ast.Attribute):
            obj = evaluate_ast(target.value, state, static_tools, custom_tools, authorized_imports)
            return getattr(obj, target.attr)
        if isinstance(target, ast.Tuple):
            return tuple(get_current_value(elt) for elt in target.elts)
        if isinstance(target, ast.List):
            return [get_current_value(elt) for elt in target.elts]
        raise InterpreterError("AugAssign not supported for {type(target)} targets.")

    current_value = get_current_value(expression.target)
    value_to_add = evaluate_ast(expression.value, state, static_tools, custom_tools, authorized_imports)

    if isinstance(expression.op, ast.Add):
        if isinstance(current_value, list):
            if not isinstance(value_to_add, list):
                raise InterpreterError(f"Cannot add non-list value {value_to_add} to a list.")
            current_value += value_to_add
        else:
            current_value += value_to_add
    elif isinstance(expression.op, ast.Sub):
        current_value -= value_to_add
    elif isinstance(expression.op, ast.Mult):
        current_value *= value_to_add
    elif isinstance(expression.op, ast.Div):
        current_value /= value_to_add
    elif isinstance(expression.op, ast.Mod):
        current_value %= value_to_add
    elif isinstance(expression.op, ast.Pow):
        current_value **= value_to_add
    elif isinstance(expression.op, ast.FloorDiv):
        current_value //= value_to_add
    elif isinstance(expression.op, ast.BitAnd):
        current_value &= value_to_add
    elif isinstance(expression.op, ast.BitOr):
        current_value |= value_to_add
    elif isinstance(expression.op, ast.BitXor):
        current_value ^= value_to_add
    elif isinstance(expression.op, ast.LShift):
        current_value <<= value_to_add
    elif isinstance(expression.op, ast.RShift):
        current_value >>= value_to_add
    else:
        raise InterpreterError(f"Operation {type(expression.op).__name__} is not supported.")

    set_value(
        expression.target,
        current_value,
        state,
        static_tools,
        custom_tools,
        authorized_imports,
    )
    return current_value


def evaluate_boolop(
    node: ast.BoolOp,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    is_short_circuit_value = (lambda x: not x) if isinstance(node.op, ast.And) else (lambda x: bool(x))
    for value in node.values:
        result = evaluate_ast(value, state, static_tools, custom_tools, authorized_imports)
        if is_short_circuit_value(result):
            return result
    return result


def evaluate_binop(
    binop: ast.BinOp,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    left_val = evaluate_ast(binop.left, state, static_tools, custom_tools, authorized_imports)
    right_val = evaluate_ast(binop.right, state, static_tools, custom_tools, authorized_imports)

    if isinstance(binop.op, ast.Add):
        return left_val + right_val
    if isinstance(binop.op, ast.Sub):
        return left_val - right_val
    if isinstance(binop.op, ast.Mult):
        return left_val * right_val
    if isinstance(binop.op, ast.Div):
        return left_val / right_val
    if isinstance(binop.op, ast.Mod):
        return left_val % right_val
    if isinstance(binop.op, ast.Pow):
        return left_val**right_val
    if isinstance(binop.op, ast.FloorDiv):
        return left_val // right_val
    if isinstance(binop.op, ast.BitAnd):
        return left_val & right_val
    if isinstance(binop.op, ast.BitOr):
        return left_val | right_val
    if isinstance(binop.op, ast.BitXor):
        return left_val ^ right_val
    if isinstance(binop.op, ast.LShift):
        return left_val << right_val
    if isinstance(binop.op, ast.RShift):
        return left_val >> right_val
    raise NotImplementedError(f"Binary operation {type(binop.op).__name__} is not implemented.")


def evaluate_assign(
    assign: ast.Assign,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    result = evaluate_ast(assign.value, state, static_tools, custom_tools, authorized_imports)
    if len(assign.targets) == 1:
        target = assign.targets[0]
        set_value(target, result, state, static_tools, custom_tools, authorized_imports)
    else:
        expanded_values = []
        for tgt in assign.targets:
            if isinstance(tgt, ast.Starred):
                expanded_values.extend(result)
            else:
                expanded_values.append(result)
        for tgt, val in zip(assign.targets, expanded_values):
            set_value(tgt, val, state, static_tools, custom_tools, authorized_imports)
    return result


def set_value(
    target: ast.AST,
    value: Any,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> None:
    if isinstance(target, ast.Name):
        if target.id in static_tools:
            raise InterpreterError(f"Cannot assign to name '{target.id}': doing this would erase the existing tool!")
        state[target.id] = value
    elif isinstance(target, ast.Tuple):
        if not isinstance(value, tuple):
            if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
                value = tuple(value)
            else:
                raise InterpreterError("Cannot unpack non-tuple value")
        if len(target.elts) != len(value):
            raise InterpreterError("Cannot unpack tuple of wrong size")
        for i, elem in enumerate(target.elts):
            set_value(elem, value[i], state, static_tools, custom_tools, authorized_imports)
    elif isinstance(target, ast.Subscript):
        obj = evaluate_ast(target.value, state, static_tools, custom_tools, authorized_imports)
        key = evaluate_ast(target.slice, state, static_tools, custom_tools, authorized_imports)
        obj[key] = value
    elif isinstance(target, ast.Attribute):
        obj = evaluate_ast(target.value, state, static_tools, custom_tools, authorized_imports)
        setattr(obj, target.attr, value)


def evaluate_call(
    call: ast.Call,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    if not isinstance(call.func, (ast.Call, ast.Lambda, ast.Attribute, ast.Name, ast.Subscript)):
        raise InterpreterError(f"This is not a correct function: {call.func}).")

    func, func_name = None, None
    call_module_name = ""

    if isinstance(call.func, ast.Call):
        func = evaluate_ast(call.func, state, static_tools, custom_tools, authorized_imports)
    elif isinstance(call.func, ast.Lambda):
        func = evaluate_ast(call.func, state, static_tools, custom_tools, authorized_imports)
    elif isinstance(call.func, ast.Attribute):
        obj = evaluate_ast(call.func.value, state, static_tools, custom_tools, authorized_imports)
        func_name = call.func.attr
        if not hasattr(obj, func_name):
            raise InterpreterError(f"Object {obj} has no attribute {func_name}")
        if isinstance(obj, ModuleType):
            call_module_name = str(getattr(obj, "__name__", "") or _get_attribute_path(call.func.value))
        else:
            call_module_name = _get_attribute_path(call.func.value)
        func = getattr(obj, func_name)
    elif isinstance(call.func, ast.Name):
        func_name = call.func.id
        if func_name in state:
            func = state[func_name]
        elif func_name in static_tools:
            func = static_tools[func_name]
        elif func_name in custom_tools:
            func = custom_tools[func_name]
        elif func_name in ERRORS:
            func = ERRORS[func_name]
        else:
            raise InterpreterError(
                f"Forbidden function evaluation: '{call.func.id}' is not among the explicitly allowed tools or defined/imported in the preceding code"
            )
    elif isinstance(call.func, ast.Subscript):
        func = evaluate_ast(call.func, state, static_tools, custom_tools, authorized_imports)
        if not callable(func):
            raise InterpreterError(f"This is not a correct function: {call.func}).")
        func_name = None

    args = []
    for arg in call.args:
        if isinstance(arg, ast.Starred):
            args.extend(evaluate_ast(arg.value, state, static_tools, custom_tools, authorized_imports))
        else:
            args.append(evaluate_ast(arg, state, static_tools, custom_tools, authorized_imports))

    kwargs = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            starred_dict = evaluate_ast(keyword.value, state, static_tools, custom_tools, authorized_imports)
            if not isinstance(starred_dict, dict):
                raise InterpreterError(f"Cannot unpack non-dict value in **kwargs: {type(starred_dict).__name__}")
            kwargs.update(starred_dict)
        else:
            kwargs[keyword.arg] = evaluate_ast(keyword.value, state, static_tools, custom_tools, authorized_imports)

    if func_name == "super":
        if not args:
            if "__class__" in state and "self" in state:
                return super(state["__class__"], state["self"])
            raise InterpreterError("super() needs at least one argument")
        cls = args[0]
        if not isinstance(cls, type):
            raise InterpreterError("super() argument 1 must be type")
        if len(args) == 1:
            return super(cls)
        if len(args) == 2:
            instance = args[1]
            return super(cls, instance)
        raise InterpreterError("super() takes at most 2 arguments")
    elif func_name == "print":
        state["_print_outputs"] += " ".join(map(str, args)) + "\n"
        return None
    else:
        if (inspect.getmodule(func) == builtins) and inspect.isbuiltin(func) and (func not in static_tools.values()):
            raise InterpreterError(
                f"Invoking a builtin function that has not been explicitly added as a tool is not allowed ({func_name})."
            )
        if (
            hasattr(func, "__name__")
            and func.__name__.startswith("__")
            and func.__name__.endswith("__")
            and (func.__name__ not in static_tools)
            and (func.__name__ not in ALLOWED_DUNDER_METHODS)
        ):
            raise InterpreterError(f"Forbidden call to dunder function: {func.__name__}")
        module_name = str(call_module_name or getattr(func, "__module__", "") or "")
        callable_name = str(getattr(func, "__name__", func_name or "") or func_name or "")
        if is_blocked_python_call(
            module_name=module_name,
            func_name=callable_name,
            blocked_call_prefixes=_get_blocked_call_prefixes(state),
        ):
            raise InterpreterError(f"Blocked Python call: {module_name}.{callable_name}")
        if module_name == "subprocess":
            guard_subprocess_call(
                func_name=callable_name,
                args=args,
                kwargs=kwargs,
                blocked_shell_patterns=_get_blocked_shell_patterns(state),
            )
        result = func(*args, **kwargs)
        if func_name == "add_image" and result is not None:
            try:
                from juice_agents.core.agent.agent_type import ObservationImage  # type: ignore
            except Exception:
                ObservationImage = None  # type: ignore
            if "_collected_observation_images" not in state:
                state["_collected_observation_images"] = []
            image_obj = result
            if ObservationImage is not None and not isinstance(image_obj, ObservationImage):
                image_obj = ObservationImage.from_image(image=image_obj, description="")
            state["_collected_observation_images"].append(image_obj)
        return result


def evaluate_subscript(
    subscript: ast.Subscript,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    index = evaluate_ast(subscript.slice, state, static_tools, custom_tools, authorized_imports)
    value = evaluate_ast(subscript.value, state, static_tools, custom_tools, authorized_imports)
    try:
        return value[index]
    except (KeyError, IndexError, TypeError) as e:
        error_message = f"Could not index {value} with '{index}': {type(e).__name__}: {e}"
        if isinstance(index, str) and isinstance(value, Mapping):
            close_matches = difflib.get_close_matches(index, list(value.keys()))
            if len(close_matches) > 0:
                error_message += f". Maybe you meant one of these indexes instead: {str(close_matches)}"
        raise InterpreterError(error_message) from e


def evaluate_name(
    name: ast.Name,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    if name.id in state:
        return state[name.id]
    if name.id in static_tools:
        return safer_func(static_tools[name.id], static_tools=static_tools, authorized_imports=authorized_imports)
    if name.id in custom_tools:
        return custom_tools[name.id]
    if name.id in ERRORS:
        return ERRORS[name.id]
    close_matches = difflib.get_close_matches(name.id, list(state.keys()))
    if len(close_matches) > 0:
        return state[close_matches[0]]
    raise InterpreterError(f"The variable `{name.id}` is not defined.")


def evaluate_condition(
    condition: ast.Compare,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> bool | object:
    result = True
    left = evaluate_ast(condition.left, state, static_tools, custom_tools, authorized_imports)
    for i, (op, comparator) in enumerate(zip(condition.ops, condition.comparators)):
        op = type(op)
        right = evaluate_ast(comparator, state, static_tools, custom_tools, authorized_imports)
        if op == ast.Eq:
            current_result = left == right
        elif op == ast.NotEq:
            current_result = left != right
        elif op == ast.Lt:
            current_result = left < right
        elif op == ast.LtE:
            current_result = left <= right
        elif op == ast.Gt:
            current_result = left > right
        elif op == ast.GtE:
            current_result = left >= right
        elif op == ast.Is:
            current_result = left is right
        elif op == ast.IsNot:
            current_result = left is not right
        elif op == ast.In:
            current_result = left in right
        elif op == ast.NotIn:
            current_result = left not in right
        else:
            raise InterpreterError(f"Unsupported comparison operator: {op}")

        if current_result is False:
            return False
        result = current_result if i == 0 else (result and current_result)
        left = right
    return result


def evaluate_if(
    if_statement: ast.If,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    result = None
    test_result = evaluate_ast(if_statement.test, state, static_tools, custom_tools, authorized_imports)
    if test_result:
        for line in if_statement.body:
            line_result = evaluate_ast(line, state, static_tools, custom_tools, authorized_imports)
            if line_result is not None:
                result = line_result
    else:
        for line in if_statement.orelse:
            line_result = evaluate_ast(line, state, static_tools, custom_tools, authorized_imports)
            if line_result is not None:
                result = line_result
    return result


def evaluate_for(
    for_loop: ast.For,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Any:
    result = None
    iterator = evaluate_ast(for_loop.iter, state, static_tools, custom_tools, authorized_imports)
    for counter in iterator:
        set_value(
            for_loop.target,
            counter,
            state,
            static_tools,
            custom_tools,
            authorized_imports,
        )
        for node in for_loop.body:
            try:
                line_result = evaluate_ast(node, state, static_tools, custom_tools, authorized_imports)
                if line_result is not None:
                    result = line_result
            except BreakException:
                return result
            except ContinueException:
                break
    return result


def _evaluate_comprehensions(
    comprehensions: list[ast.comprehension],
    evaluate_element: Callable[[dict[str, Any]], Any],
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Generator[Any, None, None]:
    if not comprehensions:
        yield evaluate_element(state)
        return
    comprehension = comprehensions[0]
    iter_value = evaluate_ast(comprehension.iter, state, static_tools, custom_tools, authorized_imports)
    for value in iter_value:
        new_state = state.copy()
        set_value(comprehension.target, value, new_state, static_tools, custom_tools, authorized_imports)
        if all(
            evaluate_ast(if_clause, new_state, static_tools, custom_tools, authorized_imports)
            for if_clause in comprehension.ifs
        ):
            yield from _evaluate_comprehensions(
                comprehensions[1:], evaluate_element, new_state, static_tools, custom_tools, authorized_imports
            )


def evaluate_listcomp(
    listcomp: ast.ListComp,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> list[Any]:
    return list(
        _evaluate_comprehensions(
            listcomp.generators,
            lambda comp_state: evaluate_ast(listcomp.elt, comp_state, static_tools, custom_tools, authorized_imports),
            state,
            static_tools,
            custom_tools,
            authorized_imports,
        )
    )


def evaluate_setcomp(
    setcomp: ast.SetComp,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> set[Any]:
    return set(
        _evaluate_comprehensions(
            setcomp.generators,
            lambda comp_state: evaluate_ast(setcomp.elt, comp_state, static_tools, custom_tools, authorized_imports),
            state,
            static_tools,
            custom_tools,
            authorized_imports,
        )
    )


def evaluate_dictcomp(
    dictcomp: ast.DictComp,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> dict[Any, Any]:
    return dict(
        _evaluate_comprehensions(
            dictcomp.generators,
            lambda comp_state: (
                evaluate_ast(dictcomp.key, comp_state, static_tools, custom_tools, authorized_imports),
                evaluate_ast(dictcomp.value, comp_state, static_tools, custom_tools, authorized_imports),
            ),
            state,
            static_tools,
            custom_tools,
            authorized_imports,
        )
    )


def evaluate_try(
    try_node: ast.Try,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> None:
    try:
        for stmt in try_node.body:
            evaluate_ast(stmt, state, static_tools, custom_tools, authorized_imports)
    except Exception as e:
        matched = False
        for handler in try_node.handlers:
            if handler.type is None or isinstance(
                e,
                evaluate_ast(handler.type, state, static_tools, custom_tools, authorized_imports),
            ):
                matched = True
                if handler.name:
                    state[handler.name] = e
                for stmt in handler.body:
                    evaluate_ast(stmt, state, static_tools, custom_tools, authorized_imports)
                break
        if not matched:
            raise e
    else:
        if try_node.orelse:
            for stmt in try_node.orelse:
                evaluate_ast(stmt, state, static_tools, custom_tools, authorized_imports)
    finally:
        if try_node.finalbody:
            for stmt in try_node.finalbody:
                evaluate_ast(stmt, state, static_tools, custom_tools, authorized_imports)


def evaluate_raise(
    raise_node: ast.Raise,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> None:
    if raise_node.exc is not None:
        exc = evaluate_ast(raise_node.exc, state, static_tools, custom_tools, authorized_imports)
    else:
        exc = None
    if raise_node.cause is not None:
        cause = evaluate_ast(raise_node.cause, state, static_tools, custom_tools, authorized_imports)
    else:
        cause = None
    if exc is not None:
        if cause is not None:
            raise exc from cause
        raise exc
    raise InterpreterError("Re-raise is not supported without an active exception")


def evaluate_assert(
    assert_node: ast.Assert,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> None:
    test_result = evaluate_ast(assert_node.test, state, static_tools, custom_tools, authorized_imports)
    if not test_result:
        if assert_node.msg:
            msg = evaluate_ast(assert_node.msg, state, static_tools, custom_tools, authorized_imports)
            raise AssertionError(msg)
        test_code = ast.unparse(assert_node.test)
        raise AssertionError(f"Assertion failed: {test_code}")


def evaluate_with(
    with_node: ast.With,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> None:
    contexts = []
    for item in with_node.items:
        context_expr = evaluate_ast(item.context_expr, state, static_tools, custom_tools, authorized_imports)
        if item.optional_vars:
            state[item.optional_vars.id] = context_expr.__enter__()
            contexts.append(state[item.optional_vars.id])
        else:
            context_var = context_expr.__enter__()
            contexts.append(context_var)

    try:
        for stmt in with_node.body:
            evaluate_ast(stmt, state, static_tools, custom_tools, authorized_imports)
    except Exception as e:
        for context in reversed(contexts):
            context.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        for context in reversed(contexts):
            context.__exit__(None, None, None)


def get_safe_module(raw_module, authorized_imports, visited=None):
    """为模块创建浅安全拷贝，避免访问危险子模块。"""
    if not isinstance(raw_module, ModuleType):
        return raw_module
    if visited is None:
        visited = set()
    module_id = id(raw_module)
    if module_id in visited:
        return raw_module
    visited.add(module_id)

    safe_module = ModuleType(raw_module.__name__)
    for attr_name in dir(raw_module):
        try:
            attr_value = getattr(raw_module, attr_name)
        except (ImportError, AttributeError) as e:
            logger.info(
                "Skipping import error while copying %s.%s: %s - %s",
                raw_module.__name__,
                attr_name,
                type(e).__name__,
                e,
            )
            continue
        if isinstance(attr_value, ModuleType):
            attr_value = get_safe_module(attr_value, authorized_imports, visited=visited)
        setattr(safe_module, attr_name, attr_value)
    return safe_module


def evaluate_import(expression, state, authorized_imports):
    if isinstance(expression, ast.Import):
        for alias in expression.names:
            if is_blocked_python_import(alias.name, _get_blocked_imports(state)):
                raise InterpreterError(f"Import of {alias.name} is blocked by PythonTool blacklist")
            if check_import_authorized(alias.name, authorized_imports):
                raw_module = import_module(alias.name)
                state[alias.asname or alias.name] = get_safe_module(raw_module, authorized_imports)
            else:
                raise InterpreterError(
                    f"Import of {alias.name} is not allowed. Authorized imports are: {str(authorized_imports)}"
                )
        return None
    elif isinstance(expression, ast.ImportFrom):
        if is_blocked_python_import(str(expression.module or ""), _get_blocked_imports(state)):
            raise InterpreterError(f"Import from {expression.module} is blocked by PythonTool blacklist")
        if check_import_authorized(expression.module, authorized_imports):
            raw_module = __import__(expression.module, fromlist=[alias.name for alias in expression.names])
            module = get_safe_module(raw_module, authorized_imports)
            if expression.names[0].name == "*":
                if hasattr(module, "__all__"):
                    for name in module.__all__:
                        state[name] = getattr(module, name)
                else:
                    for name in dir(module):
                        if not name.startswith("_"):
                            state[name] = getattr(module, name)
            else:
                for alias in expression.names:
                    if hasattr(module, alias.name):
                        state[alias.asname or alias.name] = getattr(module, alias.name)
                    else:
                        raise InterpreterError(f"Module {expression.module} has no attribute {alias.name}")
        else:
            raise InterpreterError(
                f"Import from {expression.module} is not allowed. Authorized imports are: {str(authorized_imports)}"
            )
        return None


def evaluate_generatorexp(
    genexp: ast.GeneratorExp,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> Generator[Any]:
    def generator():
        for gen in genexp.generators:
            iter_value = evaluate_ast(gen.iter, state, static_tools, custom_tools, authorized_imports)
            for value in iter_value:
                new_state = state.copy()
                set_value(
                    gen.target,
                    value,
                    new_state,
                    static_tools,
                    custom_tools,
                    authorized_imports,
                )
                if all(
                    evaluate_ast(if_clause, new_state, static_tools, custom_tools, authorized_imports)
                    for if_clause in gen.ifs
                ):
                    yield evaluate_ast(
                        genexp.elt,
                        new_state,
                        static_tools,
                        custom_tools,
                        authorized_imports,
                    )

    return generator()


def evaluate_delete(
    delete_node: ast.Delete,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str],
) -> None:
    for target in delete_node.targets:
        if isinstance(target, ast.Name):
            if target.id in state:
                del state[target.id]
            else:
                raise InterpreterError(f"Cannot delete name '{target.id}': name is not defined")
        elif isinstance(target, ast.Subscript):
            obj = evaluate_ast(target.value, state, static_tools, custom_tools, authorized_imports)
            index = evaluate_ast(target.slice, state, static_tools, custom_tools, authorized_imports)
            try:
                del obj[index]
            except (TypeError, KeyError, IndexError) as e:
                raise InterpreterError(f"Cannot delete index/key: {str(e)}")
        else:
            raise InterpreterError(f"Deletion of {type(target).__name__} targets is not supported")


@safer_eval
def evaluate_ast(
    expression: ast.AST,
    state: dict[str, Any],
    static_tools: dict[str, Callable],
    custom_tools: dict[str, Callable],
    authorized_imports: list[str] = BASE_BUILTIN_MODULES,
):
    if state.setdefault("_operations_count", {"counter": 0})["counter"] >= MAX_OPERATIONS:
        raise InterpreterError(
            f"Reached the max number of operations of {MAX_OPERATIONS}. Maybe there is an infinite loop somewhere in the code, or you're just asking too many calculations."
        )
    state["_operations_count"]["counter"] += 1
    common_params = (state, static_tools, custom_tools, authorized_imports)
    if isinstance(expression, ast.Assign):
        return evaluate_assign(expression, *common_params)
    elif isinstance(expression, ast.AnnAssign):
        return evaluate_annassign(expression, *common_params)
    elif isinstance(expression, ast.AugAssign):
        return evaluate_augassign(expression, *common_params)
    elif isinstance(expression, ast.Call):
        return evaluate_call(expression, *common_params)
    elif isinstance(expression, ast.Constant):
        return expression.value
    elif isinstance(expression, ast.Tuple):
        return tuple((evaluate_ast(elt, *common_params) for elt in expression.elts))
    elif isinstance(expression, ast.GeneratorExp):
        return evaluate_generatorexp(expression, *common_params)
    elif isinstance(expression, ast.ListComp):
        return evaluate_listcomp(expression, *common_params)
    elif isinstance(expression, ast.DictComp):
        return evaluate_dictcomp(expression, *common_params)
    elif isinstance(expression, ast.SetComp):
        return evaluate_setcomp(expression, *common_params)
    elif isinstance(expression, ast.UnaryOp):
        return evaluate_unaryop(expression, *common_params)
    elif isinstance(expression, ast.Starred):
        return evaluate_ast(expression.value, *common_params)
    elif isinstance(expression, ast.BoolOp):
        return evaluate_boolop(expression, *common_params)
    elif isinstance(expression, ast.Break):
        raise BreakException()
    elif isinstance(expression, ast.Continue):
        raise ContinueException()
    elif isinstance(expression, ast.BinOp):
        return evaluate_binop(expression, *common_params)
    elif isinstance(expression, ast.Compare):
        return evaluate_condition(expression, *common_params)
    elif isinstance(expression, ast.Lambda):
        return evaluate_lambda(expression, *common_params)
    elif isinstance(expression, ast.FunctionDef):
        return evaluate_function_def(expression, *common_params)
    elif isinstance(expression, ast.Dict):
        keys = (evaluate_ast(k, *common_params) for k in expression.keys)
        values = (evaluate_ast(v, *common_params) for v in expression.values)
        return dict(zip(keys, values))
    elif isinstance(expression, ast.Expr):
        return evaluate_ast(expression.value, *common_params)
    elif isinstance(expression, ast.For):
        return evaluate_for(expression, *common_params)
    elif isinstance(expression, ast.FormattedValue):
        value = evaluate_ast(expression.value, *common_params)
        if not expression.format_spec:
            return value
        format_spec = evaluate_ast(expression.format_spec, *common_params)
        return format(value, format_spec)
    elif isinstance(expression, ast.If):
        return evaluate_if(expression, *common_params)
    elif hasattr(ast, "Index") and isinstance(expression, ast.Index):
        return evaluate_ast(expression.value, *common_params)
    elif isinstance(expression, ast.JoinedStr):
        return "".join([str(evaluate_ast(v, *common_params)) for v in expression.values])
    elif isinstance(expression, ast.List):
        return [evaluate_ast(elt, *common_params) for elt in expression.elts]
    elif isinstance(expression, ast.Name):
        return evaluate_name(expression, *common_params)
    elif isinstance(expression, ast.Subscript):
        return evaluate_subscript(expression, *common_params)
    elif isinstance(expression, ast.IfExp):
        test_val = evaluate_ast(expression.test, *common_params)
        if test_val:
            return evaluate_ast(expression.body, *common_params)
        return evaluate_ast(expression.orelse, *common_params)
    elif isinstance(expression, ast.Attribute):
        return evaluate_attribute(expression, *common_params)
    elif isinstance(expression, ast.Slice):
        return slice(
            evaluate_ast(expression.lower, *common_params) if expression.lower is not None else None,
            evaluate_ast(expression.upper, *common_params) if expression.upper is not None else None,
            evaluate_ast(expression.step, *common_params) if expression.step is not None else None,
        )
    elif isinstance(expression, ast.While):
        return evaluate_while(expression, *common_params)
    elif isinstance(expression, (ast.Import, ast.ImportFrom)):
        return evaluate_import(expression, state, authorized_imports)
    elif isinstance(expression, ast.ClassDef):
        return evaluate_class_def(expression, *common_params)
    elif isinstance(expression, ast.Try):
        return evaluate_try(expression, *common_params)
    elif isinstance(expression, ast.Raise):
        return evaluate_raise(expression, *common_params)
    elif isinstance(expression, ast.Assert):
        return evaluate_assert(expression, *common_params)
    elif isinstance(expression, ast.With):
        return evaluate_with(expression, *common_params)
    elif isinstance(expression, ast.Set):
        return set((evaluate_ast(elt, *common_params) for elt in expression.elts))
    elif isinstance(expression, ast.Return):
        raise ReturnException(evaluate_ast(expression.value, *common_params) if expression.value else None)
    elif isinstance(expression, ast.Pass):
        return None
    elif isinstance(expression, ast.Delete):
        return evaluate_delete(expression, *common_params)
    raise InterpreterError(f"{expression.__class__.__name__} is not supported.")


class SubmitOutputException(Exception):
    def __init__(self, value):
        self.value = value


def evaluate_python_code(
    code: str,
    static_tools: dict[str, Callable] | None = None,
    custom_tools: dict[str, Callable] | None = None,
    state: dict[str, Any] | None = None,
    authorized_imports: list[str] = BASE_BUILTIN_MODULES,
):
    """
    安全执行 Python 代码，返回 (结果, 是否触发终止输出)。
    """
    try:
        expression = ast.parse(code)
    except SyntaxError as e:
        raise InterpreterError(
            f"Code parsing failed on line {e.lineno} due to: {type(e).__name__}: {str(e)}\n"
            f"{e.text}"
            f"{' ' * (e.offset or 0)}^"
        )

    if state is None:
        state = {}
    static_tools = static_tools.copy() if static_tools is not None else {}
    custom_tools = custom_tools if custom_tools is not None else {}
    result = None
    state["_print_outputs"] = PrintContainer()
    state["_operations_count"] = {"counter": 0}
    if "_collected_observation_images" not in state:
        state["_collected_observation_images"] = []

    if "submit_output" in static_tools:
        previous_submit_output = static_tools["submit_output"]

        def submit_output(*args, **kwargs):
            raise SubmitOutputException(previous_submit_output(*args, **kwargs))

        static_tools["submit_output"] = submit_output

    try:
        for node in expression.body:
            result = evaluate_ast(node, state, static_tools, custom_tools, authorized_imports)
        submitted = False
        return result, submitted
    except SubmitOutputException as e:
        submitted = True
        return e.value, submitted
    except Exception as e:
        raise InterpreterError(
            f"Code execution failed at line '{ast.get_source_segment(code, node)}' due to: {type(e).__name__}: {e}"
        )


@dataclass
class CodeOutput:
    output: Any
    logs: str
    error: str | None
    submitted: bool
    observation_images: list[Any]


class PythonExecutor(ABC):
    @abstractmethod
    def send_tools(self, tools: dict[str, Tool]) -> None: ...

    @abstractmethod
    def send_variables(self, variables: dict[str, Any]) -> None: ...

    @abstractmethod
    def __call__(self, code_action: str) -> CodeOutput: ...


class LocalPythonExecutor(PythonExecutor):
    """
    本地 Python 执行器。

    - 白名单导入，阻止危险模块
    - 自定义工具注入、额外函数注入
    - 捕获 print 输出、submit_output 终止；add_image 需显式注入后收集图片
    """

    def __init__(
        self,
        additional_authorized_imports: list[str],
        additional_functions: dict[str, Callable] | None = None,
        allow_all_imports: bool = False,
        blocked_imports: list[str] | None = None,
        blocked_call_prefixes: list[str] | None = None,
        blocked_shell_patterns: list[Any] | None = None,
    ):
        self.custom_tools: dict[str, Callable] = {}
        self.state: dict[str, Any] = {"__name__": "__main__"}
        self.additional_authorized_imports = additional_authorized_imports
        self.allow_all_imports = bool(allow_all_imports)
        self.authorized_imports = (
            ["*"]
            if self.allow_all_imports
            else list(set(BASE_BUILTIN_MODULES) | set(self.additional_authorized_imports))
        )
        self.blocked_imports = list(blocked_imports or [])
        self.blocked_call_prefixes = list(blocked_call_prefixes or [])
        self.blocked_shell_patterns = list(blocked_shell_patterns or [])
        self._check_authorized_imports_are_installed()
        self.static_tools: dict[str, Callable] | None = None
        self.additional_functions = additional_functions or {}
        self.state["__blocked_imports__"] = list(self.blocked_imports)
        self.state["__blocked_call_prefixes__"] = list(self.blocked_call_prefixes)
        self.state["__blocked_shell_patterns__"] = list(self.blocked_shell_patterns)
        self._tool_execution_records: list[dict[str, Any]] = []

    def _check_authorized_imports_are_installed(self):
        missing_modules = [
            base_module
            for imp in self.authorized_imports
            if imp != "*" and find_spec(base_module := imp.split(".")[0]) is None
        ]
        if missing_modules:
            raise InterpreterError(
                f"Non-installed authorized modules: {', '.join(missing_modules)}. "
                f"Please install these modules or remove them from the authorized imports list."
            )

    def __call__(self, code_action: str) -> CodeOutput:
        logger.info("开始执行代码块")
        self.state["_collected_observation_images"] = []
        self._tool_execution_records = []
        error: str | None = None
        output = None
        submitted = False

        try:
            output, submitted = evaluate_python_code(
                code_action,
                static_tools=self.static_tools,
                custom_tools=self.custom_tools,
                state=self.state,
                authorized_imports=self.authorized_imports,
            )
        except InterpreterError as exc:
            error = str(exc)
            logger.exception("代码执行失败")

        logs = str(self.state.get("_print_outputs", ""))
        observation_images = self.state.get("_collected_observation_images", []).copy()
        return CodeOutput(
            output=output,
            logs=logs,
            error=error,
            submitted=submitted,
            observation_images=observation_images,
        )

    def get_collected_observation_images(self) -> list:
        """获取执行过程中收集的 ObservationImage 列表。"""
        return self.state.get("_collected_observation_images", []).copy()

    def send_variables(self, variables: dict[str, Any]):
        logger.debug("注入变量: %s", list(variables.keys()))
        self.state.update(variables)

    def _classify_tool_mode(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        """判定工具调用是否属于后台启动类动作。"""

        if name == "agent_tool":
            return "background_launch"
        if name != "shell":
            return "sync"
        background = kwargs.get("background")
        if background is None and len(args) >= 4:
            background = args[3]
        return "background_launch" if bool(background) else "sync"

    def _tool_call_succeeded(self, name: str, mode: str, result: Any) -> bool:
        """根据工具调用结果判定这次调用是否成功。"""

        if mode != "background_launch":
            return True
        if name in {"agent_tool", "shell"}:
            return isinstance(result, dict) and str(result.get("status") or "").strip() == "launched"
        return True

    def _wrap_runtime_tool(self, name: str, tool: Callable) -> Callable:
        """给注入到代码执行器的工具加一层轻量记录，供 Runner 判定是否应等待后台 async task。"""

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            mode = self._classify_tool_mode(name, args, kwargs)
            try:
                result = tool(*args, **kwargs)
            except Exception:
                self._tool_execution_records.append(
                    {"name": name, "mode": mode, "succeeded": False}
                )
                raise
            self._tool_execution_records.append(
                {
                    "name": name,
                    "mode": mode,
                    "succeeded": self._tool_call_succeeded(name, mode, result),
                }
            )
            return result

        _wrapped.__name__ = str(name)
        return _wrapped

    def consume_tool_execution_records(self) -> list[dict[str, Any]]:
        """消费本次代码块执行期间记录的工具调用摘要。"""

        records = [dict(item) for item in self._tool_execution_records]
        self._tool_execution_records = []
        return records

    def send_tools(self, tools: dict[str, Tool]):
        logger.debug("注册工具: %s", list(tools.keys()))
        def _builtin_submit_output(value: Any = None, /, **kwargs: Any):
            return kwargs.get("output", value)

        wrapped_tools = {
            name: self._wrap_runtime_tool(name, tool)
            for name, tool in dict(tools or {}).items()
        }
        merged_tools = {**wrapped_tools, **BASE_PYTHON_TOOLS.copy(), **self.additional_functions}
        merged_tools.setdefault("submit_output", _builtin_submit_output)
        self.static_tools = merged_tools


__all__ = [
    "evaluate_python_code",
    "LocalPythonExecutor",
    "CodeOutput",
    "InterpreterError",
]
