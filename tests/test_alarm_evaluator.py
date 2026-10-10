# -*- coding: utf-8 -*-
"""AlarmEvaluator 单元测试

它钉住的是"检出事件 → 阈值判定 → 告警落库"这一跳。这条线在 `ui/` 归档时断过：
`check_amplitude` 在生产路径上零调用点，而全套测试仍然全绿——因为老用例都是直接调
`PDAlarmService.check_amplitude()`，绕过了"**谁去调它**"这个问题本身。
所以这里的断言全部从总线入口起步，不去直接戳服务方法。
"""

import pytest

from core.foundation.signal_bus import PDDataBus
from core.services.alarm_evaluator import AlarmEvaluator


class FakeAlarm:
    """记录调用而不做判定：用来断言订阅器"调了什么、调了几次、有没有多调"。"""

    def __init__(self):
        self.calls = []
        self.trigger_calls = []
        self.result = None

    def check_amplitude(self, device_id, channel_id, amplitude):
        self.calls.append((device_id, channel_id, amplitude))
        return self.result

    def trigger_alarm(self, alarm):
        self.trigger_calls.append(alarm)


@pytest.fixture
def bus():
    # 不能用 PDDataBus.instance()：那是进程级单例，别处的 shutdown() 会释放它的信号源，
    # 之后任何 emit 都抛 "Signal source has been deleted"
    return PDDataBus()


@pytest.fixture
def db():
    """每测试一个独立内存库。

    不用 conftest 里 session 作用域的 db_manager：告警表是整个套件共享的，
    断言"库里恰好一条"会被别的测试写的行污染（本仓库踩过"单跑绿、全套红"）。
    """
    from core.data import DatabaseManager

    mgr = DatabaseManager(":memory:")
    yield mgr
    mgr.close()


def emit(bus, device="dev1", channel=1, amplitude=85.0, **extra):
    event = {"amplitude": amplitude, "sensor_type": "uhf"}
    event.update(extra)
    bus.publish_pd_event(device, channel, event)


def test_event_amplitude_reaches_threshold_check(bus):
    alarm = FakeAlarm()
    evaluator = AlarmEvaluator(bus, alarm)
    evaluator.start()

    emit(bus, channel=3, amplitude=85.0)

    assert alarm.calls == [("dev1", 3, 85.0)], "阈值判定没收到总线上的事件"
    assert evaluator.stats == {"evaluated": 1, "over_threshold": 0}


def test_returned_alarm_is_not_triggered_a_second_time(bus):
    """check_amplitude 的返回值已经是"已构造+已落库+已发布"的告警，不能再触发第二次。

    旧 ui 的 signal_router 紧接着又调了一次 trigger_alarm，只是恰好被同级别去抖兜住。
    """
    alarm = FakeAlarm()
    alarm.result = {"level": "critical", "alarm_type": "pd_over_limit"}
    evaluator = AlarmEvaluator(bus, alarm)
    evaluator.start()

    emit(bus)

    assert evaluator.stats == {"evaluated": 1, "over_threshold": 1}
    assert alarm.trigger_calls == [], "重复 trigger_alarm 会发布两条告警帧、写两行库"


def test_stop_unsubscribes(bus):
    alarm = FakeAlarm()
    evaluator = AlarmEvaluator(bus, alarm)
    evaluator.start()
    evaluator.stop()

    emit(bus)

    assert alarm.calls == []
    assert evaluator.stats == {"evaluated": 0, "over_threshold": 0}


def test_double_start_registers_only_once(bus):
    alarm = FakeAlarm()
    evaluator = AlarmEvaluator(bus, alarm)
    evaluator.start()
    evaluator.start()

    emit(bus)

    assert len(alarm.calls) == 1, "重复 start 会重复订阅，同一条事件被判定两次"


def test_event_without_amplitude_is_skipped(bus):
    alarm = FakeAlarm()
    evaluator = AlarmEvaluator(bus, alarm)
    evaluator.start()

    bus.publish_pd_event("dev1", 1, {"phase": 90.0})

    assert alarm.calls == []
    assert evaluator.stats == {"evaluated": 0, "over_threshold": 0}


def test_none_alarm_does_not_crash_the_pipeline(bus):
    """--hardware 模式不构造 AlarmService：订阅器必须安静地不工作，而不是抛异常。"""
    evaluator = AlarmEvaluator(bus, None)
    evaluator.start()

    emit(bus)

    assert evaluator.stats == {"evaluated": 0, "over_threshold": 0}


def test_over_threshold_writes_exactly_one_row_per_level(bus, db):
    """端到端：连续越限 3 次（去抖）才落一条库行，之后同级别再越限不重复落库。

    用真 PDAlarmService + 真库，是因为这条链断掉的原因恰恰是"每段单看都对、
    拼起来没人调"——只测服务方法测不出接线问题。
    """
    from core.data.monitor_models import PDAlarmModel
    from core.services.alarm_service import PDAlarmService

    alarm_service = PDAlarmService(db_manager=db)
    evaluator = AlarmEvaluator(bus, alarm_service)
    evaluator.start()

    # 默认量程 100 mV、critical 从 80% 起：85 mV 属于 critical
    for _ in range(2):
        emit(bus, amplitude=85.0)
    assert evaluator.stats["over_threshold"] == 0, "去抖还没到 3 次就放行，报警会刷屏"

    emit(bus, amplitude=85.0)
    assert evaluator.stats == {"evaluated": 3, "over_threshold": 1}

    # 继续越限：每次都"放行"（过了去抖），但同级别被 trigger_alarm 去抖，不再新增库行
    for _ in range(3):
        emit(bus, amplitude=95.0)
    assert evaluator.stats == {"evaluated": 6, "over_threshold": 4}

    with db.session() as session:
        rows = session.query(PDAlarmModel).all()
    assert len(rows) == 1, "同级别重复越限应当只留一条告警，否则报警页会被刷屏"
    assert rows[0].level == "critical"
    assert rows[0].device_id == "dev1"
    # 口径要分清：去抖期间库里那一行**不更新**，被刷新的只是内存里的活跃告警。
    # 所以报警页的"当前幅值"是 95，而历史表那一条永远记首次触发的 85。
    assert rows[0].amplitude == pytest.approx(85.0)
    active = alarm_service.get_active_alarms("dev1")
    assert active[0]["amplitude"] == pytest.approx(95.0)
