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

        async def cancel_timer(handle, silent=False):
            await asyncio.sleep(0)
            return app.timers.pop(handle, None) is not None

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

    async def test_overlapping_motion_creates_one_timer(self):
        app = await self.make_app()
        entered = asyncio.Event()
        release = asyncio.Event()
        sequences = []

        async def waves(service, attrs):
            sequences.append(service)
            entered.set()
            await release.wait()

        app._call_light_service_waves = waves
        with patch.object(module.time, "time", return_value=1000) as clock:
            first = asyncio.create_task(app.on_motion("a", "state", "off", "on", {}))
            await asyncio.wait_for(entered.wait(), 1)
            clock.return_value = 1001
            second = asyncio.create_task(app.on_motion("b", "state", "off", "on", {}))
            await asyncio.sleep(0)
            self.assertEqual(app.last_on_event, 1001)
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 1)
        self.assertEqual(sequences, ["turn_on"])
        self.assertEqual(len(app.timers), 1)
        self.assertIn(app.handle, app.timers)
        self.assertEqual(app.last_on_event, 1001)

    async def test_cancel_waits_for_appdaemon_task(self):
        app = await self.make_app()
        app.handle = "pending"
        entered = asyncio.Event()
        release = asyncio.Event()

        async def cancel_timer(handle, silent=False):
            self.assertEqual(handle, "pending")
            self.assertTrue(silent)
            entered.set()
            await release.wait()
            return True

        # AppDaemon's async API wrapper returns a Task in the event loop.
        app.cancel_timer = lambda *args, **kwargs: asyncio.create_task(
            cancel_timer(*args, **kwargs)
        )
        cancellation = asyncio.create_task(app.cancel())
        await asyncio.wait_for(entered.wait(), 1)
        self.assertFalse(cancellation.done())
        self.assertEqual(app.handle, "pending")
        release.set()
        await asyncio.wait_for(cancellation, 1)
        self.assertIsNone(app.handle)

    async def test_cancel_accepts_expired_timer(self):
        app = await self.make_app()
        app.handle = "expired"
        await app.cancel()
        self.assertIsNone(app.handle)

    async def test_timeout_waits_for_motion_sequence(self):
        app = await self.make_app()
        entered = asyncio.Event()
        release = asyncio.Event()
        sequences = []

        async def waves(service, attrs):
            sequences.append(service)
            entered.set()
            await release.wait()

        app._call_light_service_waves = waves
        with patch.object(module.time, "time", return_value=1000):
            motion = asyncio.create_task(app.on_motion("a", "state", "off", "on", {}))
            await asyncio.wait_for(entered.wait(), 1)
            timeout = asyncio.create_task(app._turn_light_off({}))
            await asyncio.sleep(0)
            self.assertFalse(timeout.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(motion, timeout), 1)
        self.assertEqual(sequences, ["turn_on"])
        self.assertEqual(len(app.timers), 1)
        self.assertIn(app.handle, app.timers)


if __name__ == "__main__":
    unittest.main()
