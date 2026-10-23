import math
import time
from asyncio import Lock, gather, sleep
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import appdaemon.plugins.hass.hassapi as hass


@dataclass
class LightAttrs:
    brightness_pct: Optional[int] = 100
    color_temp_kelvin: Optional[int] = None
    xy_color: Optional[Tuple[float, float]] = None
    transition: Optional[int] = None

    @property
    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if v is not None}


# noinspection PyAttributeOutsideInit
class MotionLightWithAttrs(hass.Hass):
    handle: Optional[str] = None
    light_entity: Optional[str] = None
    last_action_time: float = 0
    last_on_event: float = 0

    default_light = LightAttrs()

    light_by_period = {
        "night": LightAttrs(brightness_pct=1, xy_color=(0.598, 0.383)),
        "morning_1": LightAttrs(brightness_pct=1, color_temp_kelvin=2000),
        "morning_2": LightAttrs(brightness_pct=50, color_temp_kelvin=2400),
        "morning_3": LightAttrs(brightness_pct=100, color_temp_kelvin=2700),
        "day": LightAttrs(brightness_pct=100, color_temp_kelvin=3000),
        "evening_1": LightAttrs(brightness_pct=100, color_temp_kelvin=3000),
        "evening_2": LightAttrs(brightness_pct=100, color_temp_kelvin=2700),
        "evening_3": LightAttrs(brightness_pct=50, color_temp_kelvin=2400),
        "before_night": LightAttrs(brightness_pct=1, color_temp_kelvin=2000),
    }

    def _get_mandatory_arg(self, name: str, default: Any = None) -> Any:
        arg = self.args.get(name)
        if arg is None:
            self.log(f"No '{name}' in app config", level="ERROR")
            return default
        return arg

    def initialize(self) -> None:
        self.handle = None
        self._action_lock = Lock()
        self.light_entities: List[str] = self._get_mandatory_arg("light_entities", [])
        self.delay: int = self.args.get("delay", 60)
        self.max_delay: int = max(
            self.args.get("max_delay", self.delay * 2), self.delay
        )
        self.delay_steps: int = max(self.args.get("delay_steps", 1), 1)
        self.exp_base: float = (
            1
            if self.delay_steps == 1
            else math.exp(math.log(self.max_delay / self.delay) / self.delay_steps)
        )
        self._current_delay: int = self.delay
        self.sensors: List[str] = self._get_mandatory_arg("sensors", [])
        for sensor in self.sensors:
            self.listen_state(self.on_motion, sensor)

    def update_delay(self) -> None:
        if self.exp_base > 1 and self._current_delay < self.max_delay:
            delay = self._current_delay
            self._current_delay = min(
                self.max_delay, int(self._current_delay * self.exp_base)
            )
            self.log(f"Delay: {delay}, next: {self._current_delay}", level="INFO")

    def reset_delay(self):
        self._current_delay = self.delay
        self.log(f"Delay is reset: {self._current_delay}", level="INFO")

    def update_last_action_time(self):
        self.last_action_time = time.time()

    async def on_motion(self, _entity, _attribute, _old, new, _kwargs) -> None:
        if new != "on":
            return
        # Record activity before waiting for an in-flight service sequence.
        self.last_on_event = time.time()
        async with self._action_lock:
            now = time.time()
            time_since_last_action = now - self.last_action_time
            if (
                self.handle is not None
                and time_since_last_action < self._current_delay / 2
            ):
                self.log(
                    f"Ignoring ON event: {time_since_last_action} < {self._current_delay / 2}",
                    level="INFO",
                )
                return
            if self.handle is None and time_since_last_action > self.delay:
                self.reset_delay()
            else:
                self.update_delay()
            await self._turn_light_on()

    async def _turn_light_on(self) -> None:
        self.cancel()
        self.update_last_action_time()
        period = await self.get_state("sensor.period_of_day")
        service_attrs = self.light_by_period.get(period, self.default_light).as_dict
        await self._call_light_service_waves("turn_on", service_attrs)
        self.handle = await self.run_in(self._turn_light_off, self._current_delay)
        self.log("Turned ON", level="INFO")

    async def _call_light_service_waves(self, service: str, attrs: dict) -> None:
        """
        Call a light service in "waves". For each key/value pair in attrs, send a parallel
        service call for that single key to all lights. If attrs is empty, perform a single
        wave with no extra data (useful for turn_off).
        - service: service name without domain prefix, e.g. 'turn_on' or 'turn_off'
        - attrs: dict of attributes to send; each wave sends exactly one kv
        """
        # If there are no attributes, send one wave without extra kwargs
        if not attrs:
            tasks = [
                self.call_service(f"light/{service}", entity_id=entity)
                for entity in self.light_entities
            ]
            results = await gather(*tasks, return_exceptions=True)
            for entity, res in zip(self.light_entities, results):
                if isinstance(res, Exception):
                    self.log(
                        f"Turn {service.upper()} service for {entity} raised: {res}",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"Turn {service.upper()} service for {entity} result: {res}",
                        level="DEBUG",
                    )
            return

        # Iterate over attribute items and send each as a separate wave
        for k, v in attrs.items():
            if v is None:
                continue
            wave_attrs = {k: v}
            tasks = [
                self.call_service(f"light/{service}", entity_id=entity, **wave_attrs)
                for entity in self.light_entities
            ]
            results = await gather(*tasks, return_exceptions=True)
            for entity, res in zip(self.light_entities, results):
                if isinstance(res, Exception):
                    self.log(
                        f"Turn {service.upper()} service for {entity} raised when sending {k}={v}: {res}",
                        level="WARNING",
                    )
                else:
                    self.log(
                        f"Turn {service.upper()} service for {entity} result for {k}={v}: {res}",
                        level="DEBUG",
                    )
            # brief pause between waves to let HA process state updates
            await sleep(1)

    async def _turn_light_off(self, _kwargs) -> None:
        async with self._action_lock:
            await self._turn_light_off_if_idle()

    async def _turn_light_off_if_idle(self) -> None:
        self.cancel()
        sensor_states = await gather(*(self.get_state(sensor) for sensor in self.sensors))
        if "on" in sensor_states:
            self.last_on_event = time.time()
            self.handle = await self.run_in(self._turn_light_off, self._current_delay)
            return
        time_since_last_on_event = time.time() - self.last_on_event
        if time_since_last_on_event < self._current_delay:
            still_wait_for = int(self._current_delay - time_since_last_on_event) + 1
            self.log(
                f"Last ON even was {time_since_last_on_event} seconds ago, "
                f"continue waiting for {still_wait_for}",
                level="INFO",
            )
            self.handle = await self.run_in(self._turn_light_off, still_wait_for)
            return
        self.update_last_action_time()
        # Use the wave-based caller to perform a turn_off (no attrs needed)
        await self._call_light_service_waves("turn_off", {})
        self.log("Turned OFF", level="INFO")

    def cancel(self) -> None:
        if self.handle is not None and self.timer_running(self.handle):
            self.cancel_timer(self.handle)
        self.handle = None
