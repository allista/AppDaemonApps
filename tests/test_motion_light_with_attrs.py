import asyncio
import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


# Run the app logic without an AppDaemon server or Home Assistant connection.
fake_hass = types.ModuleType("appdaemon.plugins.hass.hassapi")
fake_hass.Hass = object
fake_modules = {
    name: types.ModuleType(name)
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass")
}
fake_modules[fake_hass.__name__] = fake_hass
spec = importlib.util.spec_from_file_location(
    "motion_light_under_test",
    Path(__file__).resolve().parents[1] / "config/apps/motion_light_with_attrs.py",
)
module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {**fake_modules, spec.name: module}):
    spec.loader.exec_module(module)


class MotionLightTests(unittest.IsolatedAsyncioTestCase):
    async def make_app(self, states=None):
        app = module.MotionLightWithAttrs()
        app.args = {
            "sensors": ["binary_sensor.a", "binary_sensor.b"],
            "light_entities": ["light.a"],
            "delay": 180,
            "max_delay": 1200,
            "delay_steps": 12,
        }
        app.states = {
            "binary_sensor.a": "off",
            "binary_sensor.b": "off",
            "light.a": "off",
            "sensor.period_of_day": "day",
            **(states or {}),
        }
        app.timers = {}
        app.services = []
        app.listeners = []
        next_handle = 0
        app.log = lambda *args, **kwargs: None

        def listen_state(callback, entity):
            app.listeners.append((callback, entity))
            future = asyncio.get_running_loop().create_future()
            future.set_result(str(len(app.listeners)))
            return future

        async def get_state(entity):
            return app.states.get(entity)

        async def run_in(callback, delay):
            nonlocal next_handle
            next_handle += 1
            handle = str(next_handle)
            app.timers[handle] = (callback, delay)
            return handle

        def cancel_timer(handle):
            app.timers.pop(handle, None)
            future = asyncio.get_running_loop().create_future()
            future.set_result(True)
            return future

        async def call_service(service, **attrs):
            app.services.append((service, attrs))

        app.listen_state = listen_state
        app.get_state = get_state
        app.run_in = run_in
        app.timer_running = lambda handle: handle in app.timers
        app.cancel_timer = cancel_timer
        app.call_service = call_service
        result = app.initialize()
        if inspect.isawaitable(result):
            await result
        return app

    async def test_continuous_occupancy_keeps_lights_on(self):
        app = await self.make_app()
        app.states["binary_sensor.b"] = "on"
        app.last_on_event = 0
        with patch.object(module.time, "time", return_value=1000):
            await app._turn_light_off({})
        self.assertEqual(app.services, [])
        self.assertEqual(len(app.timers), 1)
        self.assertEqual(app.timers[app.handle][1], 180)

        # Once occupancy clears, the next timeout may switch the light off.
        app.states["binary_sensor.b"] = "off"
        with patch.object(module.time, "time", return_value=1181):
            await app._turn_light_off({})
        self.assertEqual(app.services, [("light/turn_off", {"entity_id": "light.a"})])
        self.assertEqual(app.timers, {})


if __name__ == "__main__":
    unittest.main()
