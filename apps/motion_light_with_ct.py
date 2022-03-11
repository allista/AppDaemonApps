from dataclasses import dataclass
from typing import cast

import appdaemon.plugins.hass.hassapi as hass


@dataclass
class LightAttrs:
    kelvin: int = 2000
    brightness_pct: int = 100

    @property
    def as_dict(self):
        return self.__dict__


class MotionLightWithCT(hass.Hass):
    handle = None
    light_entity = None

    default_light = LightAttrs()

    light_by_period = {
        "night": LightAttrs(brightness_pct=1, kelvin=2000),
        "morning_1": LightAttrs(brightness_pct=1, kelvin=2000),
        "morning_2": LightAttrs(brightness_pct=50, kelvin=2400),
        "morning_3": LightAttrs(brightness_pct=100, kelvin=2700),
        "day": LightAttrs(brightness_pct=100, kelvin=3000),
        "evening_1": LightAttrs(brightness_pct=100, kelvin=3000),
        "evening_2": LightAttrs(brightness_pct=100, kelvin=2700),
        "evening_3": LightAttrs(brightness_pct=50, kelvin=2400),
        "before_night": LightAttrs(brightness_pct=1, kelvin=2000),
    }

    def initialize(self):
        self.handle = None
        self.light_entity = self.args.get("light_entity")
        if not self.light_entity:
            self.log("No 'light_entity' in app config", level="ERROR")
            return
        sensor = self.args.get("sensor")
        if not sensor:
            self.log("No 'sensor' in app config", level="ERROR")
            return
        self.listen_state(self.on_motion, sensor)

    def on_motion(self, _entity, _attribute, old, new, _kwargs):
        if old == "off" and new == "on":
            self.cancel()
            self._turn_light_on()
        elif old == "on" and new == "off":
            self.cancel()
            self.handle = self.run_in(self._turn_light_off, self.args.get("delay", 60))

    def _turn_light_on(self):
        period = cast(str, self.get_state("sensor.period_of_day"))
        attrs = self.light_by_period.get(period, self.default_light).as_dict
        self.call_service(
            "light/turn_on",
            entity_id=self.light_entity,
            **attrs,
        )

    def _turn_light_off(self, _kwargs):
        self.call_service(
            "light/turn_off",
            entity_id=self.light_entity,
        )

    def cancel(self):
        if self.handle is not None:
            self.cancel_timer(self.handle)
        self.handle = None
