"""pytest configuration — mock Home Assistant modules to allow testing without HA installed."""
import sys
import types
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


class _OptionsFlowWithReload:
    """Minimal stand-in so SipOptionsFlow is a real, testable class."""

    config_entry = None

    def async_show_form(self, **kwargs):
        return ("form", kwargs)

    def async_create_entry(self, **kwargs):
        return ("entry", kwargs)


mock_config_entries.OptionsFlowWithReload = _OptionsFlowWithReload


class _ConfigFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


mock_config_entries.ConfigFlow = _ConfigFlow
# `from homeassistant import config_entries` reads the attribute, not sys.modules.
sys.modules["homeassistant"].config_entries = mock_config_entries
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

# homeassistant.exceptions / auth constants: real exception classes so
# integration code can raise/catch them and tests can assert on them.
class HomeAssistantError(Exception):
    pass


class ServiceValidationError(HomeAssistantError):
    pass


class Unauthorized(HomeAssistantError):
    def __init__(self, context=None, user_id=None, entity_id=None,
                 config_entry_id=None, perm_category=None, permission=None):
        super().__init__(self.__class__.__name__)
        self.context = context
        self.user_id = user_id
        self.entity_id = entity_id
        self.config_entry_id = config_entry_id
        self.perm_category = perm_category
        self.permission = permission


class UnknownUser(Unauthorized):
    pass


_exceptions_mod = types.ModuleType("homeassistant.exceptions")
_exceptions_mod.HomeAssistantError = HomeAssistantError
_exceptions_mod.ServiceValidationError = ServiceValidationError
_exceptions_mod.Unauthorized = Unauthorized
_exceptions_mod.UnknownUser = UnknownUser
sys.modules["homeassistant.exceptions"] = _exceptions_mod

sys.modules["homeassistant.auth"] = MagicMock()
sys.modules["homeassistant.auth.permissions"] = MagicMock()
_auth_const_mod = types.ModuleType("homeassistant.auth.permissions.const")
_auth_const_mod.CAT_ENTITIES = "entities"
_auth_const_mod.POLICY_CONTROL = "control"
_auth_const_mod.POLICY_READ = "read"
sys.modules["homeassistant.auth.permissions.const"] = _auth_const_mod

sys.modules["homeassistant.helpers.restore_state"].RestoreEntity = MockRestoreEntity
sys.modules["homeassistant.components.switch"].SwitchEntity = MockBase


class _TextSelectorType:
    PASSWORD = "password"
    TEXT = "text"


class _TextSelectorConfig(dict):
    def __init__(self, type=None, autocomplete=None, **kwargs):
        super().__init__()
        if type is not None:
            self["type"] = type
        if autocomplete is not None:
            self["autocomplete"] = autocomplete
        self.update(kwargs)


class _TextSelector:
    def __init__(self, config=None):
        self.config = dict(config) if config is not None else {}

    def __call__(self, data):
        return data


class _SelectSelectorMode:
    DROPDOWN = "dropdown"


class _SelectSelectorConfig(dict):
    def __init__(self, **kwargs):
        super().__init__(kwargs)


class _SelectSelector:
    def __init__(self, config=None):
        self.config = dict(config) if config is not None else {}

    def __call__(self, data):
        if data not in self.config.get("options", ()):
            raise ValueError(f"invalid option {data!r}")
        return data


_selector_mod = types.ModuleType("homeassistant.helpers.selector")
_selector_mod.TextSelector = _TextSelector
_selector_mod.TextSelectorConfig = _TextSelectorConfig
_selector_mod.TextSelectorType = _TextSelectorType
_selector_mod.SelectSelector = _SelectSelector
_selector_mod.SelectSelectorConfig = _SelectSelectorConfig
_selector_mod.SelectSelectorMode = _SelectSelectorMode
sys.modules["homeassistant.helpers.selector"] = _selector_mod
