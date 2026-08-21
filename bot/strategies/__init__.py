"""Strategy framework module."""
from .base import Strategy, Signal, Side, Intent, OpenPosition, ArmedState
from .signals import SignalAggregator, CompositeSignal
