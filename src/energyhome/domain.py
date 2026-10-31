from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class EnergyError(Exception):
    pass


class DeviceError(EnergyError):
    pass


class TariffError(EnergyError):
    pass


class ScheduleConflictError(EnergyError):
    pass


class MeterDataError(EnergyError):
    pass


@dataclass(frozen=True)
class MeterReading:
    timestamp: datetime
    power_kw: float


class LoadProfile:
    def __init__(self, timestamps, power_kw, resolution_minutes):
        if type(resolution_minutes) is not int or resolution_minutes <= 0:
            raise MeterDataError('Resolution must be a positive integer')

        self.timestamps = list(timestamps)
        self.power_kw = list(power_kw)
        self.resolution_minutes = resolution_minutes

        if not self.timestamps or len(self.timestamps) != len(self.power_kw):
            raise MeterDataError('Timestamps and power must have the same non-zero length')

        for i, timestamp in enumerate(self.timestamps):
            if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
                raise MeterDataError('Timestamps must be timezone-aware datetime values')
            self.timestamps[i] = timestamp.astimezone(timezone.utc)

        step = timedelta(minutes=resolution_minutes)
        for i in range(1, len(self.timestamps)):
            if self.timestamps[i] - self.timestamps[i - 1] != step:
                raise MeterDataError('Timestamps must follow a regular increasing grid')

        for i, power in enumerate(self.power_kw):
            if isinstance(power, bool) or not isinstance(power, (int, float)):
                raise MeterDataError('Power must be a number')
            if not isfinite(power) or power < 0:
                raise MeterDataError('Power must be finite and non-negative')
            self.power_kw[i] = float(power)

    def __len__(self):
        return len(self.timestamps)

    def __iter__(self):
        return iter(zip(self.timestamps, self.power_kw))

    def __repr__(self):
        return f'LoadProfile(points={len(self)}, resolution_minutes={self.resolution_minutes})'

    def energy_kwh(self):
        return sum(self.power_kw) * self.resolution_minutes / 60

    def peak_kw(self):
        return max(self.power_kw)

    def start_time(self):
        return self.timestamps[0]

    def end_time(self):
        return self.timestamps[-1] + timedelta(minutes=self.resolution_minutes)


class Tariff(ABC):
    def __init__(self, tariff_id, name, timezone_name):
        if not isinstance(tariff_id, str) or not tariff_id.strip():
            raise TariffError('Tariff id must not be empty')
        if not isinstance(name, str):
            raise TariffError('Tariff name must be a string')
        if not isinstance(timezone_name, str):
            raise TariffError('Timezone must be an IANA name')
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise TariffError('Unknown timezone') from error

        self.tariff_id = tariff_id
        self.name = name
        self.timezone_name = timezone_name

    @abstractmethod
    def rate_at(self, timestamp):
        pass

    @abstractmethod
    def zone_at(self, timestamp):
        pass

    def cost(self, profile):
        total = 0.0
        for timestamp, power in profile:
            total += power * profile.resolution_minutes / 60 * self.rate_at(timestamp)
        return total


class SingleZoneTariff(Tariff):
    def __init__(self, tariff_id, name, timezone_name, rate):
        super().__init__(tariff_id, name, timezone_name)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise TariffError('Rate must be a number')
        if not isfinite(rate) or rate < 0:
            raise TariffError('Rate must be finite and non-negative')
        self.rate = float(rate)

    def rate_at(self, timestamp):
        if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
            raise TariffError('Timestamp must be a timezone-aware datetime value')
        return self.rate

    def zone_at(self, timestamp):
        self.rate_at(timestamp)
        return 'single'


# W2-A
class ValidationError(DeviceError):
    pass


def _check_id(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f'{what} id must not be empty')
    return value


def _check_positive(value: float, what: str,
                    allow_zero: bool = False) -> float:
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not is_number or not isfinite(value) or value < 0 \
            or (value == 0 and not allow_zero):
        raise ValidationError(f'{what} must be a positive finite number')
    return float(value)


def _check_minutes(value: int, what: str, allow_zero: bool = False) -> int:
    if type(value) is not int or value < 0 or (value == 0 and not allow_zero):
        raise ValidationError(f'{what} must be a positive integer')
    return value


class EnergyMeter:
    """Grid connection point of a house with a power limit."""

    def __init__(self, meter_id: str, max_power_kw: float) -> None:
        self.meter_id = _check_id(meter_id, 'Meter')
        self.max_power_kw = _check_positive(max_power_kw, 'Power limit')


class Device(ABC):
    shiftable = False

    def __init__(self, device_id: str, power_kw: float) -> None:
        self.device_id = _check_id(device_id, 'Device')
        self.power_kw = _check_positive(power_kw, 'Power', allow_zero=True)

    @abstractmethod
    def is_on(self, minute: int) -> bool:
        pass

    def profile_for(self, period: tuple[datetime, datetime],
                    step_minutes: int) -> LoadProfile:
        start, end = period
        if type(step_minutes) is not int or step_minutes <= 0:
            raise ValidationError('Step must be a positive integer')
        if any(not isinstance(t, datetime) or t.utcoffset() is None for t in (start, end)):
            raise ValidationError('Period boundaries must be timezone-aware datetimes')
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        duration = end - start
        step = timedelta(minutes=step_minutes)
        if duration <= timedelta(0):
            raise ValidationError('Period must contain at least one step')
        if duration % step:
            raise ValidationError('Period must contain a whole number of steps')
        steps = duration // step
        minutes = [i * step_minutes for i in range(steps)]
        return LoadProfile(
            [start + timedelta(minutes=m) for m in minutes],
            [self.power_kw if self.is_on(m) else 0.0 for m in minutes],
            step_minutes)


class FixedDevice(Device):
    def is_on(self, minute: int) -> bool:
        return True


class CyclicDevice(Device):
    shiftable = True

    def __init__(self, device_id: str, power_kw: float, cycle_minutes: int,
                 start_minute: int = 0) -> None:
        super().__init__(device_id, power_kw)
        self.cycle_minutes = _check_minutes(cycle_minutes, 'Cycle')
        self.start_minute = _check_minutes(start_minute, 'Start', allow_zero=True)

    def is_on(self, minute: int) -> bool:
        end = self.start_minute + self.cycle_minutes
        return self.start_minute <= minute < end


class ThermostaticDevice(Device):
    def __init__(self, device_id: str, power_kw: float, duty_cycle: float,
                 cycle_minutes: int = 60) -> None:
        super().__init__(device_id, power_kw)
        if isinstance(duty_cycle, bool) or not isinstance(
                duty_cycle, (int, float)) or not 0 <= duty_cycle <= 1:
            raise ValidationError('Duty cycle must be in [0, 1]')
        self.duty_cycle = float(duty_cycle)
        self.cycle_minutes = _check_minutes(cycle_minutes, 'Cycle')

    def is_on(self, minute: int) -> bool:
        on_minutes = self.duty_cycle * self.cycle_minutes
        return minute % self.cycle_minutes < on_minutes


class House:
    def __init__(self, house_id: str, meter: EnergyMeter,
                 devices: list[Device] | None = None) -> None:
        self.house_id = _check_id(house_id, 'House')
        self.meter = meter
        self.devices = list(devices or [])
        ids = [d.device_id for d in self.devices]
        if len(ids) != len(set(ids)):
            raise ValidationError('Device ids must be unique')
