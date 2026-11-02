"""Veiligheidsregressies met echte coordinatorlogica en gesimuleerde hardware.

Uitvoeren: python tests/safety_regression_tests.py
Geen Home Assistant-installatie of fysieke accu nodig.
"""
import asyncio
import importlib.util
import threading
import types
import unittest
from datetime import timedelta
from unittest.mock import patch

import coordinator_tests as H


class SafetyRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_setup_reads_parameters_outside_the_event_loop(self):
        spec = importlib.util.spec_from_file_location(
            "wattson_ems.entry_setup_test", H.PKG_DIR / "__init__.py",
            submodule_search_locations=None)
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        main_thread = threading.get_ident()
        read_threads = []
        forwarded = []

        def load():
            read_threads.append(threading.get_ident())
            return H.C.load_params()

        async def executor(func, *args):
            return await asyncio.to_thread(func, *args)

        async def forward(entry, platforms):
            forwarded.extend(platforms)

        class Coordinator:
            def __init__(self, hass, entry, params):
                self.params = params

            async def async_start(self):
                pass

        hass = H.FakeHass()
        hass.data = {}
        hass.async_add_executor_job = executor
        hass.config_entries = types.SimpleNamespace(async_forward_entry_setups=forward)
        entry = types.SimpleNamespace(
            entry_id="setup-test", async_on_unload=lambda remove: None,
            add_update_listener=lambda callback: lambda: None)
        with patch.object(setup, "load_params", load), patch.object(setup, "WattsonCoordinator", Coordinator):
            self.assertTrue(await setup.async_setup_entry(hass, entry))
        self.assertEqual(len(read_threads), 1)
        self.assertNotEqual(read_threads[0], main_thread)
        self.assertIn("battery", hass.data[H.K.DOMAIN][entry.entry_id].params)
        self.assertEqual(forwarded, list(H.K.PLATFORMS))

    def coordinator(self):
        c = H.make_coordinator(caps=H.CAPS_FIXED)
        c.control_enabled = True
        H.basis(c, soc_pct=25)
        H.set_prices(c, 0.05, [0.40] * 6)
        return c

    async def stale_stop(self, c):
        H.set_state(c, c.ent_soc, 25, age_s=3600)
        c.safety._data_ok_at = H._dt.utcnow() - timedelta(seconds=1200)
        await c.safety.stale_guard()

    async def test_stale_stop_blocks_planner_and_all_active_commands(self):
        c = self.coordinator()
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        await self.stale_stop(c)
        stopped_calls = list(c.adapter_impl.calls)
        self.assertEqual(stopped_calls[-1][0], "noodstop")
        await c._tick(None)
        for action in ("laden", "laden_overschot", "ontladen", "verkopen"):
            for source in (H.CTRL.CommandSource.PLANNER, H.CTRL.CommandSource.REALTIME):
                self.assertIsNone(await c.set_battery(action, 800, source=source))
        self.assertIsNone(await c.set_discharge_limit(800))
        await c.safety.stale_guard()
        self.assertEqual(c.adapter_impl.calls, stopped_calls)
        self.assertEqual(c._last_action, "rust")
        self.assertIn("telemetrie stil", c.last_error)

    async def test_stale_stop_recovers_on_fresh_telemetry(self):
        c = self.coordinator()
        await self.stale_stop(c)
        H.set_state(c, c.ent_soc, 25)
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        self.assertGreater(c._last_charge_w, 0)
        self.assertIsNone(c.last_error)

    async def test_watchdog_ignores_unstabilized_plan_decision(self):
        """17-09-2026: het ruwe DP-besluit 'rust' stond in c.mode terwijl de
        wisseldemping (await executor) het nog terugdraaide naar 'houdt laden
        vast'. Een watchdog-run in dat gat zag 'rust' bij een ladende accu ->
        trip -> 22 s off + relaisklik op elke prijsgrens."""
        c = self.coordinator()
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        ent_chg, _ = c.bat_flow_entities()
        H.set_state(c, ent_chg, 500.0)
        H.CLOCK.t += H.K.STARTUP_GRACE_S + 1
        calls_before = list(c.adapter_impl.calls)
        hold = c.decision
        seen = {}

        async def stabilize(previous, context, evaluation):
            # ruw tussenbesluit 'rust', gelijktijdige bewakingstick, dan demping
            c.set_decision(H.CTRL.Decision(H.CTRL.AdviceMode.IDLE, reason="ruw"))
            seen["pending"] = c._decision_pending
            await c.safety.watchdog()
            c.assist.check(None)
            c.set_decision(hold)

        with patch.object(c, "_stabilize_decision", stabilize):
            await c._tick(None)
        self.assertTrue(seen["pending"])
        self.assertFalse(c._decision_pending)
        self.assertIsNone(c.safety.tripped)
        self.assertNotIn("noodstop", [call[0] for call in c.adapter_impl.calls])
        self.assertNotIn("rust", [call[0] for call in c.adapter_impl.calls[len(calls_before):]])
        self.assertEqual(c._last_action, "laden")
        # buiten de plan-tick geldt de bewaking onverkort
        c.set_decision(H.CTRL.Decision(H.CTRL.AdviceMode.IDLE, reason="rust"))
        await c.safety.watchdog()
        self.assertEqual(c.safety.tripped, "laden")

    async def test_idle_stale_stop_does_not_deadlock_next_command(self):
        """15-09-2026: accu in rust -> write-on-change-telemetrie staat stil ->
        stiltestop -> elk nieuw commando geweigerd -> nooit meer verse data.
        Een stop in rust mag limieten dichtzetten, maar niet blokkeren."""
        c = self.coordinator()
        c._last_action = "rust"
        await self.stale_stop(c)
        self.assertEqual(c.adapter_impl.calls[-1][0], "noodstop")
        self.assertFalse(c.safety.telemetry_blocked)
        self.assertIsNone(c.last_error)
        # telemetrie nog steeds oud, maar het plan mag nu gewoon sturen
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        self.assertGreater(c._last_charge_w, 0)
        # direct daarna geen herhaalde stop: het meetvenster loopt vanaf het commando
        calls_after_cmd = list(c.adapter_impl.calls)
        await c.safety.stale_guard()
        self.assertEqual(c.adapter_impl.calls, calls_after_cmd)
        self.assertFalse(c.safety.telemetry_blocked)

    async def test_active_command_after_idle_stop_must_yield_fresh_data(self):
        """Blijft de telemetrie ook ná het doorgelaten commando stil, dan is
        dat een echte actieve stilte: opnieuw stoppen, nu wél blokkerend."""
        c = self.coordinator()
        c._last_action = "rust"
        await self.stale_stop(c)
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        H.CLOCK.t += 1200
        H.set_state(c, c.ent_soc, 25, age_s=4800)
        await c.safety.stale_guard()
        self.assertEqual(c.adapter_impl.calls[-1][0], "noodstop")
        self.assertTrue(c.safety.telemetry_blocked)
        self.assertIn("telemetrie stil", c.last_error)
        self.assertIsNone(await c.set_battery("laden", 800))
        # verse data heft ook deze stop weer op (prijzen opnieuw op het
        # verschoven klokuur zetten, anders plant hij terecht rust)
        H.set_state(c, c.ent_soc, 26)
        H.set_prices(c, 0.05, [0.40] * 6)
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        self.assertIsNone(c.last_error)

    async def test_alive_device_with_constant_values_is_not_stale(self):
        """16-09-2026: laadcommando bij 89% niet uitgevoerd -> SoC en 0 W
        bleven constant, maar rssi van hetzelfde apparaat meldde elke 10 s.
        Een levend apparaat is geen stilte, ook niet tijdens actieve sturing."""
        c = self.coordinator()
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        c.t.device_alive = lambda entity, max_age_s: True
        H.set_state(c, c.ent_soc, 25, age_s=3600)
        c.safety._data_ok_at = H._dt.utcnow() - timedelta(seconds=1200)
        calls = list(c.adapter_impl.calls)
        await c.safety.stale_guard()
        self.assertEqual(c.adapter_impl.calls, calls)
        self.assertFalse(c.safety.telemetry_blocked)
        self.assertIsNone(c.last_error)
        # apparaat écht dood -> wel stoppen en blokkeren
        c.t.device_alive = lambda entity, max_age_s: False
        await self.stale_stop(c)
        self.assertEqual(c.adapter_impl.calls[-1][0], "noodstop")
        self.assertTrue(c.safety.telemetry_blocked)

    async def test_missing_soc_or_prices_stops_and_recovers(self):
        for entity_kind in ("soc", "price"):
            with self.subTest(source=entity_kind):
                c = self.coordinator()
                await c._tick(None)
                entity = c.ent_soc if entity_kind == "soc" else c.ent_price
                H.set_state(c, entity, "unavailable")
                await c._tick(None)
                self.assertEqual(c.advies, "geen data")
                self.assertEqual(c._last_action, "rust")
                self.assertIn("plandata ontbreekt", c.last_error)
                self.assertTrue(any(call[0] == "rust" for call in c.adapter_impl.calls))
                stopped_calls = list(c.adapter_impl.calls)
                self.assertIsNone(await c.set_battery("laden", 800))
                self.assertIsNone(await c.set_discharge_limit(800))
                self.assertEqual(c.adapter_impl.calls, stopped_calls)
                H.basis(c, soc_pct=25)
                H.set_prices(c, 0.05, [0.40] * 6)
                await c._tick(None)
                self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
                self.assertIsNone(c.last_error)

    async def test_shadow_mode_never_sends_active_commands(self):
        c = self.coordinator()
        c.control_enabled = False
        H.set_state(c, c.ent_soc, "unavailable")
        await c._tick(None)
        self.assertIsNone(await c.set_battery("laden", 800))
        self.assertIsNone(await c.set_discharge_limit(800))
        self.assertEqual(c.adapter_impl.calls, [])

    async def test_unload_discards_inflight_plan_and_late_callbacks(self):
        c = self.coordinator()
        started, release = asyncio.Event(), asyncio.Event()

        async def executor(func, *args):
            started.set()
            await release.wait()
            return func(*args)

        c.hass.async_add_executor_job = executor
        planning = asyncio.create_task(c._tick(None))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await c.async_stop()
        finally:
            release.set()
            await asyncio.wait_for(planning, 2)
        self.assertFalse(c.control_enabled)
        self.assertEqual([call[0] for call in c.adapter_impl.calls], ["rust"])
        await c._tick(None)
        await c.set_battery("laden", 800)
        await c.set_discharge_limit(800)
        await c.emergency_stop(None)
        self.assertEqual([call[0] for call in c.adapter_impl.calls], ["rust"])
        self.assertIsNone(c._retry_cancel)

    async def test_stop_invalidates_plan_even_if_telemetry_recovers_before_result(self):
        c = self.coordinator()
        started, release = asyncio.Event(), asyncio.Event()

        async def executor(func, *args):
            started.set()
            await release.wait()
            return func(*args)

        c.hass.async_add_executor_job = executor
        planning = asyncio.create_task(c._plan_and_apply())
        try:
            await asyncio.wait_for(started.wait(), 2)
            await self.stale_stop(c)
            H.set_state(c, c.ent_soc, 25)
            await c.safety.stale_guard()
            stopped_calls = list(c.adapter_impl.calls)
        finally:
            release.set()
            await asyncio.wait_for(planning, 2)
        self.assertEqual(c.adapter_impl.calls, stopped_calls)
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")

    async def test_command_permission_is_rechecked_after_waiting_for_adapter(self):
        for write_limit in (False, True):
            with self.subTest(limit=write_limit):
                c = self.coordinator()
                started, release = asyncio.Event(), asyncio.Event()
                original = c.adapter_impl.apply

                async def blocked(action, power_w, *, p1_cap=True):
                    started.set()
                    await release.wait()
                    return await original(action, power_w, p1_cap=p1_cap)

                c.adapter_impl.apply = blocked
                first = asyncio.create_task(c.set_battery("laden", 500))
                await asyncio.wait_for(started.wait(), 2)
                waiting = asyncio.create_task(
                    c.set_discharge_limit(800) if write_limit
                    else c.set_battery("ontladen", 800))
                await asyncio.sleep(0)
                # Geen generatieverandering: de actuele toestemming moet
                # binnen het adapter-lock opnieuw worden beoordeeld.
                c.control_enabled = False
                release.set()
                await asyncio.wait_for(first, 2)
                self.assertIsNone(await asyncio.wait_for(waiting, 2))
                self.assertEqual([call[0] for call in c.adapter_impl.calls], ["laden"])


class CommandVerification(unittest.IsolatedAsyncioTestCase):
    """25-09-2026: 'laden 2000 W' 4,5 u op papier, accu bleef op 29 %. Het
    apparaat leefde (rssi), dus de stale-guard zweeg; de watchdog kijkt alleen
    naar de verkeerde richting. command_guard escaleert: opnieuw sturen ->
    integratie herladen -> veilig stoppen + melding."""

    def setUp(self):
        H.CLOCK.t = 10_000.0

    def coordinator(self):
        c = H.make_coordinator(caps=H.CAPS_FIXED)
        c.control_enabled = True
        H.basis(c, soc_pct=25)
        H.set_prices(c, 0.05, [0.40] * 6)
        c.hass.calls = []
        async def record(domain, service, data=None, **kw):
            c.hass.calls.append((domain, service, data or {}))
        c.hass.services.async_call = record
        return c

    async def charging(self, c):
        await c._tick(None)
        self.assertEqual(c.adapter_impl.calls[-1][0], "laden")
        self.assertGreaterEqual(c._last_charge_w, H.SAF.CMD_ACK_MIN_W)

    def advance(self, c, s, *, bat_chg_w):
        H.CLOCK.t += s
        # verse (net gerapporteerde) telemetrie in de laadrichting
        H.set_state(c, "sensor.bat_chg", bat_chg_w)

    async def test_executed_command_never_escalates(self):
        c = self.coordinator()
        await self.charging(c)
        calls = list(c.adapter_impl.calls)
        for _ in range(5):
            self.advance(c, 200, bat_chg_w=c._last_charge_w)
            await c.safety.command_guard()
        self.assertEqual(c.adapter_impl.calls, calls)
        self.assertEqual(c.safety._ack_stage, 0)
        self.assertIsNone(c.last_error)

    async def test_unexecuted_command_escalates_reassert_reload_stop(self):
        c = self.coordinator()
        await self.charging(c)
        setpoint = round(c._last_charge_w)
        # binnen CMD_ACK_S nog geen oordeel
        self.advance(c, 60, bat_chg_w=0)
        await c.safety.command_guard()
        self.assertEqual(c.safety._ack_stage, 0)
        # trap 1: opnieuw sturen (FakeAdapter heeft geen reassert -> apply zonder P1-cap)
        self.advance(c, 150, bat_chg_w=0)
        await c.safety.command_guard()
        self.assertEqual(c.safety._ack_stage, 1)
        self.assertEqual(c.adapter_impl.calls[-1], ("laden", setpoint, False))
        self.assertIsNone(c.last_error)
        # trap 2: accu-integratie herladen
        self.advance(c, 200, bat_chg_w=0)
        await c.safety.command_guard()
        self.assertEqual(c.safety._ack_stage, 2)
        self.assertIn(("homeassistant", "reload_config_entry", {"entity_id": c.ent_soc}), c.hass.calls)
        # trap 3: veilig stoppen + persistente melding, daarna gedempt
        self.advance(c, 200, bat_chg_w=0)
        await c.safety.command_guard()
        self.assertEqual(c.adapter_impl.calls[-1][0], "noodstop")
        self.assertIn("voert commando niet uit", c.last_error)
        self.assertTrue(any(d == "persistent_notification" for d, _, _ in c.hass.calls))
        self.assertIsNotNone(c.safety._ack_muted_until)
        # nieuw commando binnen de backoff: geen nieuwe escalatie
        await c._tick(None)
        calls = list(c.adapter_impl.calls)
        self.advance(c, 200, bat_chg_w=0)
        await c.safety.command_guard()
        self.assertEqual(c.adapter_impl.calls, calls)

    async def test_recovery_after_reassert_resets(self):
        c = self.coordinator()
        await self.charging(c)
        self.advance(c, 200, bat_chg_w=0)
        await c.safety.command_guard()
        self.assertEqual(c.safety._ack_stage, 1)
        self.advance(c, 30, bat_chg_w=c._last_charge_w)
        await c.safety.command_guard()
        self.assertEqual(c.safety._ack_stage, 0)
        self.assertIsNone(c.safety.ack_error)

    async def test_small_or_idle_setpoint_is_ignored(self):
        c = self.coordinator()
        c._last_action = "rust"
        c._last_command_at = H._dt.utcnow()
        self.advance(c, 400, bat_chg_w=0)
        await c.safety.command_guard()
        self.assertEqual(c.safety._ack_stage, 0)




class FloorGuard(unittest.IsolatedAsyncioTestCase):
    """29-10-2026 (na 18 u HA-uitval, piek €0,80): 'verkopen 2000 W' bij 16 %
    liep in drie kwartieren door tot 7,4 %; de Zendure laadde zich daarna op
    €0,69 zelf terug naar 10 % en de watchdog trok dat als runaway af.
    02-11 01:45: zelfde BMS-bijlading op precies 10 % -> valse trip."""

    def setUp(self):
        H.CLOCK.t = 20_000.0

    def coordinator(self, soc_pct):
        c = H.make_coordinator(caps=H.CAPS_FIXED)
        c.control_enabled = True
        H.basis(c, soc_pct=soc_pct)
        H.set_prices(c, 0.50, [0.40] * 6)
        c.hass.calls = []
        async def record(domain, service, data=None, **kw):
            c.hass.calls.append((domain, service, data or {}))
        c.hass.services.async_call = record
        return c

    async def discharge(self, c, watts):
        command = c.command_arbiter.command(
            H.CTRL.BatteryAction.SELL, watts, p1_cap=False,
            source=H.CTRL.CommandSource.PLANNER)
        return await c.command_arbiter.execute(
            command, c._apply_command, allowed=c._command_allowed)

    async def test_full_power_far_above_floor(self):
        c = self.coordinator(soc_pct=40)
        await self.discharge(c, 2000)
        self.assertEqual(c.adapter_impl.calls[-1][:2], ("verkopen", 2000))

    async def test_power_tapers_towards_floor(self):
        c = self.coordinator(soc_pct=14)
        floor = c._min_soc_pct
        H.basis(c, soc_pct=floor + 4)
        await self.discharge(c, 2000)
        action, watts, _ = c.adapter_impl.calls[-1]
        self.assertEqual(action, "verkopen")
        # 4 % van 5,76 kWh = 0,23 kWh; in 10 min leeg = ~1,3 kW
        self.assertLess(watts, 1500)
        self.assertGreater(watts, 1000)
        H.basis(c, soc_pct=floor + 0.5)
        await self.discharge(c, 2000)
        action, watts, _ = c.adapter_impl.calls[-1]
        self.assertEqual(action, "verkopen")
        self.assertLess(watts, 200)

    async def test_at_floor_becomes_idle_without_ack_escalation(self):
        c = self.coordinator(soc_pct=10)
        H.basis(c, soc_pct=c._min_soc_pct)
        result = await self.discharge(c, 2000)
        self.assertEqual(c.adapter_impl.calls[-1][0], "rust")
        self.assertEqual(result.applied_w, 0.0)
        # realtime-volglus volgt dezelfde grens
        await c.set_discharge_limit(1500)
        self.assertEqual(c.adapter_impl.calls[-1], ("ontlaadlimiet", 0, False))

    async def test_watchdog_ignores_bms_recharge_at_floor(self):
        c = self.coordinator(soc_pct=10)
        c.advies = "rust"
        H.basis(c, soc_pct=c._min_soc_pct)
        ent_chg, _ = c.bat_flow_entities()
        H.set_state(c, ent_chg, 2400.0)
        H.CLOCK.t += H.K.STARTUP_GRACE_S + 1
        await c.safety.watchdog()
        self.assertIsNone(c.safety.tripped)
        self.assertNotIn("noodstop", [call[0] for call in c.adapter_impl.calls])
        # boven de rand is laden zonder opdracht wél een runaway
        H.basis(c, soc_pct=c._min_soc_pct + H.K.SOC_EDGE_PCT + 2)
        H.set_state(c, ent_chg, 2400.0)
        await c.safety.watchdog()
        self.assertEqual(c.safety.tripped, "laden")


if __name__ == "__main__":
    unittest.main()
