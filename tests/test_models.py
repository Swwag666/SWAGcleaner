import pytest

from core.models import AppInfo


class TestAppInfo:
    def test_app_info_from_dict(self):
        app = {"display_name": "TestApp", "install_location": r"C:\Test", "publisher": "Maker"}
        info = AppInfo(
            display_name=app["display_name"],
            install_location=app["install_location"],
            publisher=app["publisher"],
        )
        assert info.display_name == "TestApp"


class TestPlanSnapshots:
    def test_plan_uses_snapshot_default(self):
        # В модели snapshot по умолчанию None
        from core.models import Plan

        plan = Plan(snapshot=None)
        assert plan.snapshot is None
