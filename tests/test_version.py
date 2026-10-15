# -*- coding: utf-8 -*-
"""
版本信息单元测试
Unit Tests for Version Information
"""

import pytest

from core.version import VERSION_INFO, __version__, get_version, get_version_info


class TestVersionInfo:
    """版本信息测试"""

    def test_version_format(self):
        """测试版本号格式"""
        parts = __version__.split(".")

        assert len(parts) >= 2
        for part in parts:
            assert part.isdigit(), f"Version part '{part}' is not a number"

    def test_version_info_structure(self):
        """测试版本信息结构"""
        assert "major" in VERSION_INFO
        assert "minor" in VERSION_INFO
        assert "patch" in VERSION_INFO
        assert "releaselevel" in VERSION_INFO
        assert "serial" in VERSION_INFO

    def test_get_version_returns_string(self):
        """测试get_version返回字符串"""
        version = get_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_get_version_matches_module(self):
        """测试get_version与模块变量一致"""
        assert get_version() == __version__

    def test_get_version_info_returns_dict(self):
        """测试get_version_info返回字典"""
        info = get_version_info()
        assert isinstance(info, dict)
        assert len(info) > 0

    def test_get_version_info_copy(self):
        """测试get_version_info返回副本(非引用)"""
        info1 = get_version_info()
        info2 = get_version_info()

        assert info1 is not info2
        assert info1 == info2

    def test_version_consistency(self):
        """测试版本一致性"""
        major = VERSION_INFO["major"]
        minor = VERSION_INFO["minor"]
        patch = VERSION_INFO["patch"]

        expected = f"{major}.{minor}.{patch}"
        assert __version__ == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
