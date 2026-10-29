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
