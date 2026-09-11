"""pytest configuration — mock Home Assistant modules to allow testing without HA installed."""
import sys
from unittest.mock import MagicMock


class MockBase:
    """Mock base class to support generic subclassing."""
    def __init__(self, *args, **kwargs):
        pass

    def __class_getitem__(cls, item):
        return cls

    async def async_added_to_hass(self):
        return None


class MockRestoreEntity:
    """Separate base so SwitchEntity + RestoreEntity is a valid MRO."""

    async def async_added_to_hass(self):
        return None


def mock_callback(func):
    """Mock for homeassistant.core.callback decorator."""
    return func


# Set up core mocks
sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.const"] = MagicMock()

# Mock homeassistant.core
mock_core = MagicMock()
mock_core.callback = mock_callback
sys.modules["homeassistant.core"] = mock_core

# Mock homeassistant.config_entries
mock_config_entries = MagicMock()
sys.modules["homeassistant.config_entries"] = mock_config_entries

# Mock homeassistant.helpers.entity
mock_entity = MagicMock()
mock_entity.Entity = MockBase
sys.modules["homeassistant.helpers.entity"] = mock_entity

# Mock homeassistant.helpers.storage
mock_storage = MagicMock()
mock_storage.Store = MockBase
sys.modules["homeassistant.helpers.storage"] = mock_storage

# Mock other helper modules
for mod in [
    "homeassistant.helpers",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.dispatcher",
    "homeassistant.helpers.event",
    "homeassistant.helpers.restore_state",
    "homeassistant.helpers.entity_platform",
    "homeassistant.components",
    "homeassistant.components.websocket_api",
    "homeassistant.components.sensor",
    "homeassistant.components.binary_sensor",
    "homeassistant.components.switch",
    "homeassistant.components.number",
    "homeassistant.components.select",
    "homeassistant.components.button",
    "homeassistant.util",
    "homeassistant.util.dt",
]:
    sys.modules[mod] = MagicMock()

sys.modules["homeassistant.helpers.restore_state"].RestoreEntity = MockRestoreEntity
sys.modules["homeassistant.components.switch"].SwitchEntity = MockBase
