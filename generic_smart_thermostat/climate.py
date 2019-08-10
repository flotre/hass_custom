"""Adds support for generic smart thermostat units."""
import asyncio
import logging
import json
from datetime import datetime, timedelta
import voluptuous as vol

from homeassistant.const import (
    ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_NAME, PRECISION_HALVES,
    PRECISION_TENTHS, PRECISION_WHOLE, SERVICE_TURN_OFF, SERVICE_TURN_ON,
    STATE_OFF, STATE_ON, STATE_UNKNOWN)
from homeassistant.core import DOMAIN as HA_DOMAIN, callback
from homeassistant.helpers import condition
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change, async_track_time_interval)
from homeassistant.helpers.restore_state import RestoreEntity

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateDevice
from homeassistant.components.climate.const import (
    ATTR_AWAY_MODE, ATTR_OPERATION_MODE, STATE_AUTO, STATE_COOL, STATE_HEAT,
    STATE_IDLE, SUPPORT_AWAY_MODE, SUPPORT_OPERATION_MODE,
    SUPPORT_TARGET_TEMPERATURE)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 0.3
DEFAULT_NAME = 'Generic Smart Thermostat'
DEFAULT_CONFORT_TEMP = 19.0
DEFAULT_ECO_TEMP = 17.0
DEFAULT_MIN_POWER = 5
DEFAULT_CALCULATE_PERIOD = 30

CONF_HEATER = 'heater'
CONF_SENSOR_IN = 'in_temp_sensor'
CONF_SENSOR_OUT = 'out_temp_sensor'
CONF_MIN_TEMP = 'min_temp'
CONF_MAX_TEMP = 'max_temp'
CONF_TARGET_TEMP = 'target_temp'
CONF_AC_MODE = 'ac_mode'
CONF_MIN_POWER = 'min_cycle_power'
CONF_COLD_TOLERANCE = 'cold_tolerance'
CONF_HOT_TOLERANCE = 'hot_tolerance'
CONF_KEEP_ALIVE = 'keep_alive'
CONF_INITIAL_OPERATION_MODE = 'initial_operation_mode'
CONF_AWAY_TEMP = 'away_temp'
CONF_PRECISION = 'precision'
CONF_PLANNING = 'planning'
CONF_CONFORT_TEMP = 'confort_temp'
CONF_ECO_TEMP = 'eco_temp'
CONF_CALCULATE_PERIOD = 'calculate_period'
SUPPORT_FLAGS = (SUPPORT_TARGET_TEMPERATURE |
                 SUPPORT_OPERATION_MODE)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HEATER): cv.entity_id,
    vol.Required(CONF_SENSOR_IN): cv.entity_id,
    vol.Required(CONF_SENSOR_OUT): cv.entity_id,
    vol.Optional(CONF_AC_MODE): cv.boolean,
    vol.Optional(CONF_MAX_TEMP): vol.Coerce(float),
    vol.Optional(CONF_MIN_POWER, default=DEFAULT_MIN_POWER): vol.All(int, vol.Range(min=5, max=100)),
    vol.Optional(CONF_MIN_TEMP): vol.Coerce(float),
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_COLD_TOLERANCE, default=DEFAULT_TOLERANCE): vol.Coerce(
        float),
    vol.Optional(CONF_HOT_TOLERANCE, default=DEFAULT_TOLERANCE): vol.Coerce(
        float),
    vol.Optional(CONF_TARGET_TEMP): vol.Coerce(float),
    vol.Optional(CONF_KEEP_ALIVE): vol.All(
        cv.time_period, cv.positive_timedelta),
    vol.Optional(CONF_INITIAL_OPERATION_MODE):
        vol.In([STATE_AUTO, STATE_OFF]),
    vol.Optional(CONF_AWAY_TEMP): vol.Coerce(float),
    vol.Optional(CONF_PRECISION): vol.In(
        [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]),
    vol.Optional(CONF_PLANNING): cv.string,
    vol.Optional(CONF_CONFORT_TEMP, default=DEFAULT_CONFORT_TEMP): vol.Coerce(float),
    vol.Optional(CONF_ECO_TEMP, default=DEFAULT_ECO_TEMP): vol.Coerce(float),
    vol.Optional(CONF_CALCULATE_PERIOD, default=DEFAULT_CALCULATE_PERIOD): vol.All(
        int, vol.Range(min=1)),
})


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Set up the generic thermostat platform."""
    name = config.get(CONF_NAME)
    heater_entity_id = config.get(CONF_HEATER)
    in_temp_sensor_entity_id = config.get(CONF_SENSOR_IN)
    out_temp_sensor_entity_id = config.get(CONF_SENSOR_OUT)
    min_temp = config.get(CONF_MIN_TEMP)
    max_temp = config.get(CONF_MAX_TEMP)
    target_temp = config.get(CONF_TARGET_TEMP)
    ac_mode = config.get(CONF_AC_MODE)
    min_cycle_power = config.get(CONF_MIN_POWER)
    cold_tolerance = config.get(CONF_COLD_TOLERANCE)
    hot_tolerance = config.get(CONF_HOT_TOLERANCE)
    keep_alive = config.get(CONF_KEEP_ALIVE)
    initial_operation_mode = config.get(CONF_INITIAL_OPERATION_MODE)
    away_temp = config.get(CONF_AWAY_TEMP)
    precision = config.get(CONF_PRECISION)
    calculate_period = config.get(CONF_CALCULATE_PERIOD)

    async_add_entities([GenericSmartThermostat(
        hass, name, heater_entity_id, in_temp_sensor_entity_id, out_temp_sensor_entity_id, min_temp, max_temp,
        target_temp, ac_mode, min_cycle_power, cold_tolerance,
        hot_tolerance, keep_alive, initial_operation_mode, away_temp,
        precision, calculate_period)])


class GenericSmartThermostat(ClimateDevice, RestoreEntity):
    """Representation of a Generic Thermostat device."""

    def __init__(self, hass, name, heater_entity_id, in_temp_sensor_entity_id,
                 out_temp_sensor_entity_id,
                 min_temp, max_temp, target_temp, ac_mode, min_cycle_power,
                 cold_tolerance, hot_tolerance, keep_alive,
                 initial_operation_mode, away_temp, precision, calculate_period):
        """Initialize the thermostat."""
        self.hass = hass
        self._name = name
        self.heater_entity_id = heater_entity_id
        self.InternalsDefaults = {
            'ConstC': float(60),  # inside heating coeff, depends on room size & power of your heater (60 by default)
            'ConstT': float(1),  # external heating coeff,depends on the insulation relative to the outside (1 by default)
            'nbCC': 0,  # number of learnings for ConstC
            'nbCT': 0,  # number of learnings for ConstT
            'LastPwr': 0,  # % power from last calculation
            'LastInT': float(0),  # inside temperature at last calculation
            'LastOutT': float(0),  # outside temprature at last calculation
            'LastSetPoint': float(20),  # setpoint at time of last calculation
            'ALStatus': 0}  # AutoLearning status (0 = uninitialized, 1 = initialized, 2 = disabled)
        
        self.ac_mode = ac_mode
        self.min_cycle_power = min_cycle_power
        self._cold_tolerance = cold_tolerance
        self._hot_tolerance = hot_tolerance
        self._keep_alive = keep_alive
        self._initial_operation_mode = initial_operation_mode
        self._saved_target_temp = target_temp if target_temp is not None \
            else away_temp
        self._temp_precision = precision
        if self.ac_mode:
            self._current_operation = STATE_COOL
            self._operation_list = [STATE_COOL, STATE_OFF]
        else:
            self._current_operation = STATE_HEAT
            self._operation_list = [STATE_HEAT, STATE_OFF]
        if initial_operation_mode == STATE_OFF:
            self._enabled = False
            self._current_operation = STATE_OFF
        else:
            self._enabled = True
        self._active = False
        self._in_temp = None
        self._out_temp = None
        self._temp_lock = asyncio.Lock()
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._target_temp = target_temp
        self._unit = hass.config.units.temperature_unit
        self._support_flags = SUPPORT_FLAGS
        if away_temp is not None:
            self._support_flags = SUPPORT_FLAGS | SUPPORT_AWAY_MODE
        self._away_temp = away_temp
        self._is_away = False
        self._learn = True
        self._calculate_period = calculate_period # in minutes
        self._data_file = hass.config.path("{}.json".format(HA_DOMAIN))
        self.endheat = datetime.now()
        self.nextcalc = self.endheat
        self.lastcalc = self.endheat
        self.nextupdate = self.endheat
        self.nexttemps = self.endheat
        self.forced = False
        self.forcedduration = 60  # time in minutes for the forced mode
        self.heat = False
        # pause
        self.pauseondelay = 2  # time between pause sensor actuation and actual pause
        self.pauseoffdelay = 1  # time between end of pause sensor actuation and end of actual pause
        self.pause = False
        self.pauserequested = False
        self.pauserequestchangedtime = datetime.now()

        self.read_user_var()

        async_track_state_change(
            hass, in_temp_sensor_entity_id, self._async_in_temp_changed)
        async_track_state_change(
            hass, out_temp_sensor_entity_id, self._async_out_temp_changed)
        async_track_state_change(
            hass, heater_entity_id, self._async_switch_changed)

        if self._keep_alive:
            async_track_time_interval(
                hass, self._async_control_heating, self._keep_alive)

        # get in temperature
        sensor_state = hass.states.get(in_temp_sensor_entity_id)
        if sensor_state and sensor_state.state != STATE_UNKNOWN:
            self._async_update_in_temp(sensor_state)
        # get out temperature
        sensor_state = hass.states.get(out_temp_sensor_entity_id)
        if sensor_state and sensor_state.state != STATE_UNKNOWN:
            self._async_update_out_temp(sensor_state)
        
        # add heatbeat
        async_track_time_interval(
                hass, self._async_control_heating, timedelta(seconds=10))

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        # Check If we have an old state
        old_state = await self.async_get_last_state()
        if old_state is not None:
            # If we have no initial temperature, restore
            if self._target_temp is None:
                # If we have a previously saved temperature
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    if self.ac_mode:
                        self._target_temp = self.max_temp
                    else:
                        self._target_temp = self.min_temp
                    _LOGGER.warning("Undefined target temperature,"
                                    "falling back to %s", self._target_temp)
                else:
                    self._target_temp = float(
                        old_state.attributes[ATTR_TEMPERATURE])
            if old_state.attributes.get(ATTR_AWAY_MODE) is not None:
                self._is_away = str(
                    old_state.attributes[ATTR_AWAY_MODE]) == STATE_ON
            if (self._initial_operation_mode is None and
                    old_state.attributes[ATTR_OPERATION_MODE] is not None):
                self._current_operation = \
                    old_state.attributes[ATTR_OPERATION_MODE]
                self._enabled = self._current_operation != STATE_OFF

        else:
            # No previous state, try and restore defaults
            if self._target_temp is None:
                if self.ac_mode:
                    self._target_temp = self.max_temp
                else:
                    self._target_temp = self.min_temp
            _LOGGER.warning("No previously saved temperature, setting to %s",
                            self._target_temp)

    @property
    def state(self):
        """Return the current state."""
        if self._is_device_active:
            return self.current_operation
        if self._enabled:
            return STATE_IDLE
        return STATE_OFF

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def precision(self):
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._in_temp

    @property
    def current_operation(self):
        """Return current operation."""
        return self._current_operation

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temp

    @property
    def operation_list(self):
        """List of available operation modes."""
        return self._operation_list

    async def async_set_operation_mode(self, operation_mode):
        """Set operation mode."""
        if operation_mode == STATE_HEAT:
            self._current_operation = STATE_HEAT
            self._enabled = True
            await self._async_control_heating(force=True)
        elif operation_mode == STATE_COOL:
            self._current_operation = STATE_COOL
            self._enabled = True
            await self._async_control_heating(force=True)
        elif operation_mode == STATE_OFF:
            self._current_operation = STATE_OFF
            self._enabled = False
            if self._is_device_active:
                await self._async_heater_turn_off()
        else:
            _LOGGER.error("Unrecognized operation mode: %s", operation_mode)
            return
        # Ensure we update the current operation after changing the mode
        self.schedule_update_ha_state()

    async def async_turn_on(self):
        """Turn thermostat on."""
        await self.async_set_operation_mode(self.operation_list[0])

    async def async_turn_off(self):
        """Turn thermostat off."""
        await self.async_set_operation_mode(STATE_OFF)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._target_temp = temperature
        await self._async_control_heating(force=True)
        await self.async_update_ha_state()

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        if self._min_temp:
            return self._min_temp

        # get default temp from super class
        return super().min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._max_temp:
            return self._max_temp

        # Get default temp from super class
        return super().max_temp

    async def _async_in_temp_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return

        self._async_update_in_temp(new_state)
        await self._async_control_heating()
        await self.async_update_ha_state()

    async def _async_out_temp_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return

        self._async_update_out_temp(new_state)
        await self._async_control_heating()
        await self.async_update_ha_state()

    @callback
    def _async_switch_changed(self, entity_id, old_state, new_state):
        """Handle heater switch state changes."""
        if new_state is None:
            return
        self.async_schedule_update_ha_state()

    @callback
    def _async_update_in_temp(self, state):
        """Update thermostat with latest indoor temperature."""
        try:
            self._in_temp = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)
    
    @callback
    def _async_update_out_temp(self, state):
        """Update thermostat with latest outdoor temperature."""
        try:
            self._out_temp = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)

    async def _async_control_heating_old(self, time=None, force=False):
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            if not self._active and None not in (self._in_temp,
                                                 self._target_temp):
                self._active = True
                _LOGGER.info("Obtained current and target temperature. "
                             "Generic smart thermostat active. %s, %s",
                             self._in_temp, self._target_temp)

            if not self._active or not self._enabled:
                return

            if not force and time is None:
                # If the `force` argument is True, we
                # ignore `min_cycle_duration`.
                # If the `time` argument is not none, we were invoked for
                # keep-alive purposes, and `min_cycle_duration` is irrelevant.
                if self.min_cycle_power:
                    if self._is_device_active:
                        current_state = STATE_ON
                    else:
                        current_state = STATE_OFF
                    long_enough = condition.state(
                        self.hass, self.heater_entity_id, current_state,
                        self.min_cycle_power)
                    if not long_enough:
                        return

            too_cold = \
                self._target_temp - self._in_temp >= self._cold_tolerance
            too_hot = \
                self._in_temp - self._target_temp >= self._hot_tolerance
            if self._is_device_active:
                if (self.ac_mode and too_cold) or \
                   (not self.ac_mode and too_hot):
                    _LOGGER.info("Turning off heater %s",
                                 self.heater_entity_id)
                    await self._async_heater_turn_off()
                elif time is not None:
                    # The time argument is passed only in keep-alive case
                    await self._async_heater_turn_on()
            else:
                if (self.ac_mode and too_hot) or \
                   (not self.ac_mode and too_cold):
                    _LOGGER.info("Turning on heater %s", self.heater_entity_id)
                    await self._async_heater_turn_on()
                elif time is not None:
                    # The time argument is passed only in keep-alive case
                    await self._async_heater_turn_off()

    async def _async_control_heating(self, time=None, force=False):

        now = datetime.now()
        _LOGGER.debug("Control heating @{}".format(now))

        if not self._enabled:  # Thermostat is off
            if self.forced or self.heat:  # thermostat setting was just changed so we kill the heating
                self.forced = False
                self.endheat = now
                _LOGGER.debug("Switching heat Off !")
                await self._async_heater_turn_off()

        elif False:  # Thermostat is in forced mode (TODO)
            if self.forced:
                if self.endheat <= now:
                    self.forced = False
                    self.endheat = now
                    _LOGGER.debug("Forced mode Off !")
                    await self.async_turn_on() # set thermostat to normal mode (TODO)
                    await self._async_heater_turn_off()
            else:
                self.forced = True
                self.endheat = now + timedelta(minutes=self.forcedduration)
                _LOGGER.debug("Forced mode On !")
                await self._async_heater_turn_on()

        else:  # Thermostat is in mode auto

            if self.forced:  # thermostat setting was just changed from "forced" so we kill the forced mode
                self.forced = False
                self.endheat = now
                self.nextcalc = now   # this will force a recalculation on next heartbeat
                _LOGGER.debug("Forced mode Off !")
                await self._async_heater_turn_off()

            elif (self.endheat <= now or self.pause) and self.heat:  # heat cycle is over
                self.endheat = now
                self.heat = False
                if self.Internals['LastPwr'] < 100:
                    await self._async_heater_turn_off()
                # if power was 100(i.e. a full cycle), then we let the next calculation (at next heartbeat) decide
                # to switch off in order to avoid potentially damaging quick off/on cycles to the heater(s)

            elif self.pause and not self.pauserequested:  # we are in pause and the pause switch is now off
                if self.pauserequestchangedtime + timedelta(minutes=self.pauseoffdelay) <= now:
                    _LOGGER.info("Pause is now Off")
                    self.pause = False

            elif not self.pause and self.pauserequested:  # we are not in pause and the pause switch is now on
                if self.pauserequestchangedtime + timedelta(minutes=self.pauseondelay) <= now:
                    _LOGGER.info("Pause is now On")
                    self.pause = True
                    await self._async_heater_turn_off()

            elif (self.nextcalc <= now) and not self.pause:  # we start a new calculation
                self.nextcalc = now + timedelta(minutes=self._calculate_period)
                _LOGGER.debug("Next calculation time will be : " + str(self.nextcalc))

                # do the thermostat work
                await self.auto_mode()


    @property
    def _is_device_active(self):
        """If the toggleable device is currently active."""
        return self.hass.states.is_state(self.heater_entity_id, STATE_ON)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    async def _async_heater_turn_on(self):
        """Turn heater toggleable device on."""
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(HA_DOMAIN, SERVICE_TURN_ON, data)

    async def _async_heater_turn_off(self):
        """Turn heater toggleable device off."""
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(HA_DOMAIN, SERVICE_TURN_OFF, data)

    @property
    def is_away_mode_on(self):
        """Return true if away mode is on."""
        return self._is_away

    async def async_turn_away_mode_on(self):
        """Turn away mode on by setting it on away hold indefinitely."""
        if self._is_away:
            return
        self._is_away = True
        self._saved_target_temp = self._target_temp
        self._target_temp = self._away_temp
        await self._async_control_heating(force=True)
        await self.async_update_ha_state()

    async def async_turn_away_mode_off(self):
        """Turn away off."""
        if not self._is_away:
            return
        self._is_away = False
        self._target_temp = self._saved_target_temp
        await self._async_control_heating(force=True)
        await self.async_update_ha_state()


    async def auto_mode(self):

        _LOGGER.debug("Temperatures: Inside = {} / Outside = {}".format(self._in_temp, self._out_temp))

        if self._in_temp > self._target_temp + self._hot_tolerance:
            _LOGGER.debug("Temperature exceeds _target_temp")
            overshoot = True
            power = 0
        else:
            overshoot = False
            if self._learn:
                self.auto_callib()
            else:
                self._learn = True
            if self._out_temp is None:
                power = round((self._target_temp - self._in_temp) * self.Internals["ConstC"], 1)
            else:
                power = round((self._target_temp - self._in_temp) * self.Internals["ConstC"] +
                              (self._target_temp - self._out_temp) * self.Internals["ConstT"], 1)

        if power < 0:
            power = 0  # lower limit
        elif power > 100:
            power = 100  # upper limit

        # apply minimum power as required
        if power <= self.min_cycle_power and (Parameters["Mode4"] == "Forced" or not overshoot):
            _LOGGER.debug(
                "Calculated power is {}, applying minimum power of {}".format(power, self.min_cycle_power))
            power = self.min_cycle_power

        heatduration = round(power * self._calculate_period / 100)
        _LOGGER.debug("Calculation: Power = {} -> heat duration = {} minutes".format(power, heatduration))

        if power == 0:
            await self._async_heater_turn_off()
            _LOGGER.debug("No heating requested !")
        else:
            self.endheat = datetime.now() + timedelta(minutes=heatduration)
            _LOGGER.debug("End Heat time = " + str(self.endheat))
            await self._async_heater_turn_on()
            if self.Internals["ALStatus"] < 2:
                self.Internals['LastPwr'] = power
                self.Internals['LastInT'] = self._in_temp
                self.Internals['LastOutT'] = self._out_temp
                self.Internals['LastSetPoint'] = self._target_temp
                self.Internals['ALStatus'] = 1
                self.save_user_var()  # update user variables with latest learning

        self.lastcalc = datetime.now()


    def auto_callib(self):

        now = datetime.now()
        if self.Internals['ALStatus'] != 1:  # not initalized... do nothing
            _LOGGER.debug("Fist pass at AutoCallib... no callibration")
            pass
        elif self.Internals['LastPwr'] == 0:  # heater was off last time, do nothing
            _LOGGER.debug("Last power was zero... no callibration")
            pass
        elif self.Internals['LastPwr'] == 100 and self._in_temp < self.Internals['LastSetPoint']:
            # heater was on max but setpoint was not reached... no learning
            _LOGGER.debug("Last power was 100% but setpoint not reached... no callibration")
            pass
        elif self._in_temp > self.Internals['LastInT'] and self.Internals['LastSetPoint'] > self.Internals['LastInT']:
            # learning ConstC
            ConstC = (self.Internals['ConstC'] * ((self.Internals['LastSetPoint'] - self.Internals['LastInT']) /
                                                  (self._in_temp - self.Internals['LastInT']) *
                                                  (timedelta.total_seconds(now - self.lastcalc) /
                                                   (self._calculate_period * 60))))
            _LOGGER.debug("New calc for ConstC = {}".format(ConstC))
            self.Internals['ConstC'] = round((self.Internals['ConstC'] * self.Internals['nbCC'] + ConstC) /
                                             (self.Internals['nbCC'] + 1), 1)
            self.Internals['nbCC'] = min(self.Internals['nbCC'] + 1, 50)
            _LOGGER.debug("ConstC updated to {}".format(self.Internals['ConstC']))
        elif self._out_temp is not None and self.Internals['LastSetPoint'] > self.Internals['LastOutT']:
            # learning ConstT
            ConstT = (self.Internals['ConstT'] + ((self.Internals['LastSetPoint'] - self._in_temp) /
                                                  (self.Internals['LastSetPoint'] - self.Internals['LastOutT']) *
                                                  self.Internals['ConstC'] *
                                                  (timedelta.total_seconds(now - self.lastcalc) /
                                                   (self._calculate_period * 60))))
            _LOGGER.debug("New calc for ConstT = {}".format(ConstT))
            self.Internals['ConstT'] = round((self.Internals['ConstT'] * self.Internals['nbCT'] + ConstT) /
                                             (self.Internals['nbCT'] + 1), 1)
            self.Internals['nbCT'] = min(self.Internals['nbCT'] + 1, 50)
            _LOGGER.debug("ConstT updated to {}".format(self.Internals['ConstT']))

    def save_user_var(self):
        try:
            with open(self._data_file, "w") as fd:
                json.dump(self.Internals, fd)
        except:
            _LOGGER.error("Failed to save user var to {}".format(self._data_file))

    def read_user_var(self):
        from os.path import exists
        self.Internals = self.InternalsDefaults.copy()
        try:
            if exists(self._data_file):
                with open(self._data_file, "r") as fd:
                    self.Internals = json.load(fd)
        except:
            _LOGGER.error("Failed to read user var in {}".format(self._data_file))

