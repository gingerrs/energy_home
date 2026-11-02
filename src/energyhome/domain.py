"""Meter profiles, tariffs, devices and scheduled runs."""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class EnergyError(Exception):
    """An expected error that can be shown to the user."""


class DeviceError(EnergyError):
    """Invalid device parameters."""


class ValidationError(DeviceError):
    """A domain value violates its constructor contract."""


class TariffError(EnergyError):
    """Invalid tariff configuration or timestamp."""


class ScheduleConflictError(EnergyError):
    """A scheduled run conflicts with a scheduling constraint."""


class MeterDataError(ValidationError):
    """Invalid meter values or interval grid."""


@dataclass(frozen=True)
class MeterReading:
    """Raw power at the beginning of a measurement interval."""

    timestamp: datetime
    power_kw: float


class LoadProfile:
    """A regular UTC grid of nonnegative power readings; empty grids are allowed."""

    def __init__(self, timestamps: Iterable[datetime], power_kw: Iterable[float],
                 resolution_minutes: int) -> None:
        if type(resolution_minutes) is not int or resolution_minutes <= 0:
            raise MeterDataError('Resolution must be a positive integer')

        self.timestamps = list(timestamps)
        self.power_kw = list(power_kw)
        self.resolution_minutes = resolution_minutes

        if len(self.timestamps) != len(self.power_kw):
            raise MeterDataError('Timestamps and power must have the same length')

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

    def __len__(self) -> int:
        return len(self.timestamps)

    def __iter__(self) -> Iterator[tuple[datetime, float]]:
        return iter(zip(self.timestamps, self.power_kw))

    def __repr__(self) -> str:
        return f'LoadProfile(points={len(self)}, resolution_minutes={self.resolution_minutes})'

    def energy_kwh(self) -> float:
        """Integrate the observed power over the interval grid."""
        return sum(self.power_kw) * self.resolution_minutes / 60

    def peak_kw(self) -> float:
        """Return zero for an empty profile, otherwise the largest power."""
        return max(self.power_kw, default=0.0)

    def start_time(self) -> datetime | None:
        """Return the first interval start, or None for an empty profile."""
        return self.timestamps[0] if self.timestamps else None

    def end_time(self) -> datetime | None:
        """Return the exclusive end, or None for an empty profile."""
        if not self.timestamps:
            return None
        return self.timestamps[-1] + timedelta(minutes=self.resolution_minutes)


class Tariff(ABC):
    """Local-clock tariff rates in UAH per kWh."""

    def __init__(self, tariff_id: str, name: str, timezone_name: str) -> None:
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
    def rate_at(self, timestamp: datetime) -> float:
        """Return the rate for the interval beginning at timestamp."""
        raise NotImplementedError

    @abstractmethod
    def zone_at(self, timestamp: datetime) -> str:
        """Return the local tariff zone for an interval start."""
        raise NotImplementedError

    def cost(self, profile: LoadProfile | None) -> float:
        """Return core's final cost, using the same rounding as every report."""
        from .core import calculate_cost

        return calculate_cost(profile, self).total_cost


class SingleZoneTariff(Tariff):
    """One nonnegative rate throughout the local day."""

    def __init__(self, tariff_id: str, name: str, timezone_name: str, rate: float) -> None:
        super().__init__(tariff_id, name, timezone_name)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise TariffError('Rate must be a number')
        if not isfinite(rate) or rate < 0:
            raise TariffError('Rate must be finite and non-negative')
        self.rate = float(rate)

    def rate_at(self, timestamp: datetime) -> float:
        """Return the fixed rate for an aware timestamp."""
        if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
            raise TariffError('Timestamp must be a timezone-aware datetime value')
        return self.rate

    def zone_at(self, timestamp: datetime) -> str:
        """Validate the timestamp and return the single zone."""
        self.rate_at(timestamp)
        return 'single'


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
    """A power model evaluated in elapsed minutes from a period start."""

    shiftable = False

    def __init__(self, device_id: str, power_kw: float) -> None:
        self.device_id = _check_id(device_id, 'Device')
        self.power_kw = _check_positive(power_kw, 'Power', allow_zero=True)

    @abstractmethod
    def is_on(self, minute: int) -> bool:
        """Whether the device runs at the given elapsed minute."""
        raise NotImplementedError

    def profile_for(self, period: tuple[datetime, datetime],
                    step_minutes: int) -> LoadProfile:
        """Build a regular UTC profile over a complete number of intervals."""
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
    """A device that runs throughout the requested period."""

    def is_on(self, minute: int) -> bool:
        """The fixed device remains on."""
        return True


class CyclicDevice(Device):
    """A shiftable cycle with one start and a fixed duration."""

    shiftable = True

    def __init__(self, device_id: str, power_kw: float, cycle_minutes: int,
                 start_minute: int = 0) -> None:
        super().__init__(device_id, power_kw)
        self.cycle_minutes = _check_minutes(cycle_minutes, 'Cycle')
        self.start_minute = _check_minutes(start_minute, 'Start', allow_zero=True)

    def is_on(self, minute: int) -> bool:
        """The cycle runs within its half-open start/duration window."""
        end = self.start_minute + self.cycle_minutes
        return self.start_minute <= minute < end


class ThermostaticDevice(Device):
    """A repeating on/off model with a fixed duty cycle."""

    def __init__(self, device_id: str, power_kw: float, duty_cycle: float,
                 cycle_minutes: int = 60) -> None:
        super().__init__(device_id, power_kw)
        if isinstance(duty_cycle, bool) or not isinstance(
                duty_cycle, (int, float)) or not 0 <= duty_cycle <= 1:
            raise ValidationError('Duty cycle must be in [0, 1]')
        self.duty_cycle = float(duty_cycle)
        self.cycle_minutes = _check_minutes(cycle_minutes, 'Cycle')

    def is_on(self, minute: int) -> bool:
        """Use the initial fraction of each thermostat cycle."""
        on_minutes = self.duty_cycle * self.cycle_minutes
        return minute % self.cycle_minutes < on_minutes


class House:
    """A meter and devices with distinct identifiers."""

    def __init__(self, house_id: str, meter: EnergyMeter,
                 devices: list[Device] | None = None) -> None:
        self.house_id = _check_id(house_id, 'House')
        self.meter = meter
        self.devices = list(devices or [])
        ids = [d.device_id for d in self.devices]
        if len(ids) != len(set(ids)):
            raise ValidationError('Device ids must be unique')


class TariffValidationError(TariffError, ValidationError):
    """Invalid zoned tariff or configuration; compatible with both error APIs."""


@dataclass(frozen=True, slots=True)
class TariffZone:
    """A local [start, end) interval in minutes; start > end crosses midnight."""

    zone_id: str
    start_minute: int
    end_minute: int
    rate: float

    def __post_init__(self) -> None:
        if not isinstance(self.zone_id, str) or not self.zone_id.strip():
            raise TariffValidationError('Zone id must not be empty')
        start, end = self.start_minute, self.end_minute
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < 1440 or not 0 <= end <= 1440 or start == end):
            raise TariffValidationError('Zone boundaries must be distinct integer minutes of a day')
        try:
            object.__setattr__(self, 'rate', _check_positive(self.rate, 'Rate', allow_zero=True))
        except (ValidationError, OverflowError) as error:
            raise TariffValidationError('Rate must be finite and non-negative') from error


class _ZonedTariff(Tariff):
    _zone_ids: frozenset[str] = frozenset()

    def __init__(self, tariff_id: str, name: str, timezone_name: str,
                 zones: Iterable[TariffZone]) -> None:
        try:
            super().__init__(tariff_id, name, timezone_name)
            entries = tuple(zones)
        except (TariffError, TypeError) as error:
            raise TariffValidationError(str(error)) from error
        if not all(isinstance(zone, TariffZone) for zone in entries):
            raise TariffValidationError('Zones must contain TariffZone values')
        if {zone.zone_id for zone in entries} != self._zone_ids:
            raise TariffValidationError('Tariff must contain exactly its supported zone ids')
        self.zones = tuple(sorted(entries, key=lambda z: (z.start_minute, z.end_minute, z.zone_id)))
        rates, segments = {}, []
        for zone in self.zones:
            if zone.zone_id in rates and rates[zone.zone_id] != zone.rate:
                raise TariffValidationError('All intervals of one zone must use the same rate')
            rates[zone.zone_id] = zone.rate
            start, end = zone.start_minute, zone.end_minute
            spans = ((start, end),) if start < end else ((start, 1440), (0, end))
            segments.extend((a, b, zone) for a, b in spans if a < b)
        self._segments = tuple(sorted(segments, key=lambda segment: segment[0]))
        cursor = 0
        for start, end, _ in self._segments:
            if start != cursor:
                raise TariffValidationError('Zones must cover the day without gaps or overlaps')
            cursor = end
        if cursor != 1440:
            raise TariffValidationError('Zones must cover the whole day')

    def _zone_at(self, timestamp: datetime) -> TariffZone:
        if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
            raise TariffValidationError('Timestamp must be a timezone-aware datetime value')
        try:
            local = timestamp.astimezone(timezone.utc).astimezone(ZoneInfo(self.timezone_name))
        except (OverflowError, ValueError) as error:
            raise TariffValidationError('Timestamp is outside the supported range') from error
        minute = local.hour * 60 + local.minute
        return next(zone for start, end, zone in self._segments if start <= minute < end)

    def zone_at(self, timestamp: datetime) -> str:
        """Return the zone id at the instant, using the tariff's local clock."""
        return self._zone_at(timestamp).zone_id

    def rate_at(self, timestamp: datetime) -> float:
        """Return the zone rate in UAH/kWh, with no I/O, rounding or state changes."""
        return self._zone_at(timestamp).rate


class TwoZoneTariff(_ZonedTariff):
    """A fully covered local day with day/night zones and explicit rates."""

    _zone_ids = frozenset({'day', 'night'})


class ThreeZoneTariff(_ZonedTariff):
    """A fully covered local day with day/night/peak zones and explicit rates."""

    _zone_ids = frozenset({'day', 'night', 'peak'})


def tariff_from_config(tariff_id: str, registry: Mapping[str, Mapping[str, object]]) -> Tariff:
    """Build from a parsed registry; unknown ids/types raise ValidationError."""
    try:
        entry = registry[tariff_id]
        if not isinstance(entry, Mapping):
            raise TariffValidationError('A tariff configuration must be an object')
        entry = dict(entry)
        kind = entry.pop('type')
        tariff_type = {'single': SingleZoneTariff, 'two_zone': TwoZoneTariff,
                       'three_zone': ThreeZoneTariff}[kind]
        if tariff_type is not SingleZoneTariff:
            entry['zones'] = tuple(TariffZone(**zone) for zone in entry['zones'])
        return tariff_type(tariff_id=tariff_id, **entry)
    except (KeyError, TypeError, ValueError, TariffError, OverflowError) as error:
        raise TariffValidationError(f'Invalid tariff {tariff_id!r}: {error}') from error

@dataclass(frozen=True)
class ScheduledRun:
    """One device start in UTC and a positive duration in minutes."""

    device_id: str
    start: datetime
    duration: int

    def __post_init__(self) -> None:
        _check_id(self.device_id, 'Device')
        if not isinstance(self.start, datetime) or self.start.utcoffset() is None:
            raise ValidationError('Scheduled run start must be a timezone-aware datetime')
        object.__setattr__(self, 'start', self.start.astimezone(timezone.utc))
        _check_minutes(self.duration, 'Duration')


@dataclass(frozen=True, slots=True, init=False)
class Schedule:
    """Ordered runs whose device IDs belong to an explicit device registry."""

    _runs: tuple[ScheduledRun, ...]

    def __init__(self, runs: Iterable[ScheduledRun] = (),
                 *, device_ids: Iterable[str] = ()) -> None:
        known = frozenset(_check_id(value, 'Device') for value in device_ids)
        entries = tuple(runs)
        for run in entries:
            if not isinstance(run, ScheduledRun):
                raise ValidationError('Schedule must contain ScheduledRun instances')
            if run.device_id not in known:
                raise ValidationError(f'Unknown device id: {run.device_id}')
        ordered = tuple(sorted(entries, key=lambda r: (r.start, r.device_id)))
        object.__setattr__(self, '_runs', ordered)

    def __len__(self) -> int:
        return len(self._runs)

    def __iter__(self) -> Iterator[ScheduledRun]:
        return iter(self._runs)

    def __repr__(self) -> str:
        return f'Schedule(runs={len(self._runs)})'


@dataclass(frozen=True)
class SavingsResult:
    """Monetary comparison of baseline and optimized runs."""

    baseline_cost_uah: float
    optimized_cost_uah: float
    savings_uah: float
    savings_percent: float


@dataclass(frozen=True)
class OptimizationRequest:
    """Inputs for a future scheduler; period boundaries are interval starts/ends."""

    house: House
    tariff: Tariff
    period: tuple[datetime, datetime]
    step_minutes: int = 60


@dataclass(frozen=True)
class OptimizationResult:
    """A completed schedule and its monetary comparison."""

    schedule: Schedule
    savings: SavingsResult
