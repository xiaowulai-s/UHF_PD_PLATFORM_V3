# -*- coding: utf-8 -*-
"""core/utils/logger.py 的数据库日志路径专项测试。

写这组测试的起因：logger 在模块顶层 `from sqlalchemy.exc import ...` 和
`from core.data.models import ...`，而 core.utils.logger 被 core/ 到处 import，
于是"打个日志"就把 ORM 拖进常驻——实测 setup_logging 一次 +60.2 MB，其中
sqlalchemy 25.3 + core.data.models 12.1，连 `--no-db` 的纯推流模式也要付。

改惰性导入之前，`_write_batch` 是**零测试覆盖**的（tests/ 里原本没有 logger 测试），
也就是说把导入挪错位置、符号没解析到，全套测试仍然全绿。所以先补行为覆盖，
再动结构。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

LOGGER_NAME = "logger_db_probe"


def _rows(db_manager, logger_name: str):
    from core.data.models import SystemLogModel

    session = db_manager.get_session()
    try:
        return (
            session.query(SystemLogModel).filter(SystemLogModel.logger == logger_name).order_by(SystemLogModel.id).all()
        )
    finally:
        session.close()


class TestLoggerDbWritePath:
    """惰性导入必须仍然解析得到 SQLAlchemyError / SystemLogModel。"""

    def test_write_batch_persists_rows(self, db_manager):
        from core.utils.logger import AsyncDatabaseLogHandler

        handler = AsyncDatabaseLogHandler(db_manager, batch_size=99, flush_interval=3600.0)
        try:
            handler._write_batch(
                [
                    {
                        "level": "WARNING",
                        "logger": LOGGER_NAME,
                        "message": "第一条",
                        "module": "m",
                        "function": "f",
                        "line": 1,
                        "exception": None,
                    },
                    {
                        "level": "ERROR",
                        "logger": LOGGER_NAME,
                        "message": "第二条",
                        "module": "m",
                        "function": "f",
                        "line": 2,
                        "exception": "Traceback ...",
                    },
                ]
            )
        finally:
            handler.close()

        got = _rows(db_manager, LOGGER_NAME)
        assert [r.message for r in got] == ["第一条", "第二条"]
        assert [r.level for r in got] == ["WARNING", "ERROR"]
        assert got[1].exception == "Traceback ..."

    def test_empty_batch_is_noop(self, db_manager):
        """空批必须直接返回——它连导入都不该触发。"""
        from core.utils.logger import AsyncDatabaseLogHandler

        handler = AsyncDatabaseLogHandler(db_manager, batch_size=99, flush_interval=3600.0)
        try:
            assert handler._write_batch([]) is None
        finally:
            handler.close()

    def test_emit_through_flush_thread_reaches_db(self, db_manager):
        """走真实链路：emit → 队列 → 后台刷写线程 → _write_batch → 库。

        只测 _write_batch 会漏掉一件事：它是**在后台线程里**被调的，函数内导入
        在别的线程里必须仍然解析得到，这种问题只有真跑线程才暴露。

        这里刻意不在刷写进行中轮询读库：`get_session()` 返回的是共享 session
        （:memory: 走 StaticPool，跨线程同一份），边写边读会撞上 SQLAlchemy 的
        identity map（实测报 SAWarning "Identity map already had an identity…"），
        并且让用例时好时坏。所以先 close()（`_flush_loop` 的条件是
        `self._running or not self._queue.empty()`，会把队列排空并做末次刷写），
        线程真退了之后再单线程读一次。

        发 3 条、batch_size=2：前 2 条走"攒够就刷"，第 3 条走"退出前排空"，两条路径都覆盖。
        """
        from core.utils.logger import AsyncDatabaseLogHandler

        name = LOGGER_NAME + "_thread"
        handler = AsyncDatabaseLogHandler(db_manager, batch_size=2, flush_interval=0.2)
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.propagate = False
        lg.addHandler(handler)
        try:
            for i in (1, 2, 3):
                lg.warning("线程刷写-%d", i)
        finally:
            lg.removeHandler(handler)
            handler.close()

        got = _rows(db_manager, name)
        assert [r.message for r in got] == [
            "线程刷写-1",
            "线程刷写-2",
            "线程刷写-3",
        ], "后台刷写线程没把日志全落库：拿到 %s" % [r.message for r in got]


class TestLoggerDoesNotDragOrm:
    """这组才是要省的那 37 MB——必须是子进程，当前进程里 sqlalchemy 早就被别处加载了。"""

    def _probe(self, code: str) -> str:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert result.returncode == 0, result.stderr[-400:]
        # structlog 的 ConsoleRenderer 会把日志打到 stdout，所以取最后一行，
        # 不能断言整个 stdout 等于答案（踩过：'…[info] hi\nFalse' != 'False'）。
        return result.stdout.strip().splitlines()[-1].strip()

    def test_importing_logger_leaves_sqlalchemy_out(self):
        out = self._probe(
            "import sys; import core.utils.logger;"
            "print('sqlalchemy' in sys.modules or 'core.data.models' in sys.modules)"
        )
        assert out == "False", "import 日志模块就连带加载 ORM：--no-db 的纯推流模式也要常驻约 37 MB"

    def test_get_logger_still_leaves_sqlalchemy_out(self):
        """get_logger 是全内核最常用的入口，它同样不能拖上 ORM。"""
        out = self._probe(
            "import sys; from core.utils.logger import get_logger;"
            "get_logger('x').info('hi'); print('sqlalchemy' in sys.modules)"
        )
        assert out == "False"

    def test_db_handler_still_importable_when_needed(self):
        """惰性不等于不能用：真去写库时符号必须解析得到。"""
        out = self._probe(
            "from core.utils.logger import AsyncDatabaseLogHandler, DatabaseLogHandler;"
            "print(issubclass(DatabaseLogHandler, AsyncDatabaseLogHandler))"
        )
        assert out == "True"

    def test_no_module_scope_orm_import(self):
        """模块顶层（TYPE_CHECKING 之外）不得再导入 ORM，否则 37 MB 又回来了。"""
        import ast

        src = (REPO / "core" / "utils" / "logger.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        # 先把 `if TYPE_CHECKING:` 块里的内容剔掉——那是有意为之的
        guarded: set[int] = set()
        for node in tree.body:
            if isinstance(node, ast.If) and getattr(node.test, "id", "") == "TYPE_CHECKING":
                for sub in node.body:
                    guarded.update(id(x) for x in ast.walk(sub))

        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or id(node) in guarded:
                continue
            mod = node.module or ""
            if mod.split(".")[0] not in ("sqlalchemy", "core"):
                continue
            for alias in node.names:
                offenders.append("%s.%s" % (mod, alias.name))
        assert not offenders, "顶层又导入 ORM 了: %s" % offenders

    def test_future_annotations_still_present(self):
        """签名里的 `db_manager: DatabaseManager` 只在注解是字符串时才不需要运行时符号。

        这条只能静态查，不能靠"跑一下会不会炸"来验：开发机是 Python 3.14，PEP 649
        已经把注解默认延迟求值，删掉 `from __future__ import annotations` 也照样跑；
        但 `pyproject.toml` 的 requires-python 是 >=3.10，3.10~3.13 会在类定义时就
        NameError。变异验证实测过：注掉那行，3.14 上 7 个用例仍全绿——所以断言必须
        写在 AST 上，而不是指望某个用例失败。
        """
        import ast

        src = (REPO / "core" / "utils" / "logger.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        futures = [
            a.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "__future__"
            for a in node.names
        ]
        assert "annotations" in futures
