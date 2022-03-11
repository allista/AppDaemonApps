from typing import Tuple

import pytest
from appdaemontestframework import automation_fixture

from apps.motion_light_with_ct import MotionLightWithCT

InitializedApp = Tuple[MotionLightWithCT, dict]


@automation_fixture(MotionLightWithCT)
def motion_light_with_ct_ok(given_that):
    given_that.passed_arg("light_entity").is_set_to("light.test_light")
    given_that.passed_arg("sensor").is_set_to("binary_sensor.test_sensor_occupancy")


@automation_fixture(MotionLightWithCT)
def motion_light_with_ct_no_sensor(given_that):
    given_that.passed_arg("light_entity").is_set_to("light.test_light")


@automation_fixture(MotionLightWithCT)
def motion_light_with_ct_no_light():
    pass


def test_init_ok(motion_light_with_ct_ok: MotionLightWithCT, assert_that):
    assert_that(motion_light_with_ct_ok).listens_to.state(
        "binary_sensor.test_sensor_occupancy"
    ).with_callback(motion_light_with_ct_ok.on_motion)


def test_init_no_sensor(motion_light_with_ct_no_sensor: MotionLightWithCT, assert_that):
    assert motion_light_with_ct_no_sensor.light_entity == "light.test_light"
    with pytest.raises(AssertionError):
        assert_that(motion_light_with_ct_no_sensor).listens_to.state(
            "binary_sensor.test_sensor_occupancy"
        ).with_callback(motion_light_with_ct_no_sensor.on_motion)


def test_init_no_light(motion_light_with_ct_no_light: MotionLightWithCT, assert_that):
    assert motion_light_with_ct_no_light.light_entity is None
    with pytest.raises(AssertionError):
        assert_that(motion_light_with_ct_no_light).listens_to.state(
            "binary_sensor.test_sensor_occupancy"
        ).with_callback(motion_light_with_ct_no_light.on_motion)


def test_cancel(motion_light_with_ct_ok: MotionLightWithCT, assert_that):
    motion_light_with_ct_ok.handle = 1
    motion_light_with_ct_ok.cancel()
    assert motion_light_with_ct_ok.handle is None
    motion_light_with_ct_ok.cancel_timer.assert_any_call(1)
    motion_light_with_ct_ok.cancel_timer.reset_mock()
    motion_light_with_ct_ok.cancel()
    assert not motion_light_with_ct_ok.cancel_timer.called


def test_on_motion_off_to_on(motion_light_with_ct_ok: MotionLightWithCT, assert_that):
    motion_light_with_ct_ok.handle = 1
    motion_light_with_ct_ok.on_motion(None, None, "off", "on", None)
    motion_light_with_ct_ok.cancel_timer.assert_any_call(1)
    assert_that("light/turn_on").was.called_with(
        entity_id="light.test_light",
        brightness_pct=100,
        kelvin=2000,
    )


def test_on_motion_on_to_on(motion_light_with_ct_ok: MotionLightWithCT, assert_that):
    motion_light_with_ct_ok.handle = 1
    motion_light_with_ct_ok.on_motion(None, None, "on", "on", None)
    assert motion_light_with_ct_ok.handle == 1
    assert not motion_light_with_ct_ok.cancel_timer.called
    with pytest.raises(AssertionError):
        assert_that("light.turn_on").was.called_with(
            entity_id="light.test_light",
            brightness_pct=100,
            kelvin=2000,
        )


def test_on_motion_on_to_off(
    motion_light_with_ct_ok: MotionLightWithCT, assert_that, given_that
):
    def _test_run_in(delay):
        motion_light_with_ct_ok.handle = 1
        motion_light_with_ct_ok.on_motion(None, None, "on", "off", None)
        motion_light_with_ct_ok.cancel_timer.assert_any_call(1)
        motion_light_with_ct_ok.run_in.assert_any_call(
            motion_light_with_ct_ok._turn_light_off, delay
        )

    _test_run_in(60)
    given_that.passed_arg("delay").is_set_to(30)
    _test_run_in(30)
