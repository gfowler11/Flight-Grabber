from .base import Provider, ProviderError, ProviderQuery, QueryLeg
from .demo import DemoProvider
from .google import GoogleFlightsProvider


def get_provider(name: str) -> Provider:
    if name == "demo":
        return DemoProvider()
    if name == "google":
        return GoogleFlightsProvider()
    raise ValueError(f"Unknown provider: {name}")


__all__ = [
    "Provider",
    "ProviderError",
    "ProviderQuery",
    "QueryLeg",
    "DemoProvider",
    "GoogleFlightsProvider",
    "get_provider",
]
