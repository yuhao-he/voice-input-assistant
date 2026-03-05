"""
Global hotkey listener using pynput.

Tracks modifier state + a main key. When the configured hotkey combo
is pressed, emits a Qt signal to start recording; on release, emits
a signal to stop recording.
"""

from __future__ import annotations

import threading
from typing import Optional, Set, Callable

from pynput import keyboard
from pynput.keyboard import Key, KeyCode

from PyQt6.QtCore import QObject, pyqtSignal

import platform
_IS_MACOS = platform.system() == "Darwin"


# Mapping of modifier Key objects to a canonical string
_MODIFIER_MAP = {
    Key.ctrl_l: "ctrl",
    Key.ctrl_r: "ctrl",
    Key.shift_l: "shift",
    Key.shift_r: "shift",
    Key.alt_l: "alt",
    Key.alt_r: "alt",
    Key.cmd_l: "cmd",
    Key.cmd_r: "cmd",
}

# Reverse: canonical string -> set of pynput Key objects
_MODIFIER_KEYS: dict[str, set] = {}
for _k, _v in _MODIFIER_MAP.items():
    _MODIFIER_KEYS.setdefault(_v, set()).add(_k)


def key_to_str(key) -> str:
    """Convert a pynput key object to a human-readable string."""
    if key in _MODIFIER_MAP:
        return _MODIFIER_MAP[key]
    if isinstance(key, KeyCode):
        if key.char:
            return key.char.lower()
        if key.vk is not None:
            return f"<{key.vk}>"
    if isinstance(key, Key):
        return key.name
    return str(key)


class HotkeyCombo:
    """Represents a hotkey combination like Ctrl+Shift+R."""

    def __init__(self, modifiers: Optional[Set[str]] = None, main_key: Optional[str] = None):
        self.modifiers: Set[str] = modifiers or set()
        self.main_key: Optional[str] = main_key

    def __str__(self) -> str:
        parts = sorted(self.modifiers) + ([self.main_key.upper()] if self.main_key else [])
        return "+".join(parts)

    def is_valid(self) -> bool:
        return self.main_key is not None


class HotkeySignals(QObject):
    """Qt signals emitted by the hotkey listener."""
    hotkey_pressed = pyqtSignal(bool)  # is_secondary
    hotkey_released = pyqtSignal(bool) # is_secondary
    toggle_settings_requested = pyqtSignal()
    cancel_requested = pyqtSignal()  # Escape pressed: cancel all in-flight work
    key_event = pyqtSignal(object, bool)  # (key, is_press) — used for hotkey capture mode


class HotkeyListener:
    """
    Global hotkey listener that runs pynput in a daemon thread.

    Communicates with the Qt main thread via HotkeySignals.
    """

    def __init__(self):
        self.signals = HotkeySignals()
        self._primary_combo: Optional[HotkeyCombo] = None
        self._secondary_combo: Optional[HotkeyCombo] = None
        self._active_modifiers: Set[str] = set()
        self._main_key_down: Optional[str] = None  # None, 'primary', or 'secondary'
        self._listener: Optional[keyboard.Listener] = None
        self._capture_mode: bool = False  # When True, next key press sets the hotkey
        self._tap_run_loop = None
        self._fn_state: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_hotkeys(self, primary: HotkeyCombo, secondary: HotkeyCombo | None):
        """Set the hotkey combos."""
        self._primary_combo = primary
        self._secondary_combo = secondary
        self._main_key_down = None

    def start(self):
        """Start listening for global key events."""
        if self._listener is not None:
            return

        if _IS_MACOS:
            import threading
            threading.Thread(target=self._start_mac_fn_tap, daemon=True).start()

        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        """Stop the listener."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def set_capture_mode(self, enabled: bool):
        """Enable/disable hotkey capture mode (for setting the hotkey)."""
        self._capture_mode = enabled

    # ------------------------------------------------------------------
    # Internal callbacks (run in pynput thread)
    # ------------------------------------------------------------------

    def _start_mac_fn_tap(self):
        try:
            import Quartz
        except ImportError:
            return

        def event_tap_callback(proxy, type_, event, refcon):
            if type_ == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
                is_down = bool(flags & Quartz.kCGEventFlagMaskSecondaryFn)

                if is_down != self._fn_state:
                    self._fn_state = is_down

                    if self._capture_mode:
                        from pynput.keyboard import KeyCode
                        self.signals.key_event.emit(KeyCode(vk=63), is_down)
                        return None

                    is_primary = self._primary_combo is not None and self._primary_combo.is_valid() and self._primary_combo.main_key in ("fn", "<63>")
                    is_secondary = self._secondary_combo is not None and self._secondary_combo.is_valid() and self._secondary_combo.main_key in ("fn", "<63>")
                    
                    if is_primary or is_secondary:
                        target_combo = self._secondary_combo if is_secondary else self._primary_combo
                        is_target_secondary = is_secondary
                        
                        if is_down:
                            if self._active_modifiers == target_combo.modifiers and self._main_key_down is None:
                                self._main_key_down = 'secondary' if is_target_secondary else 'primary'
                                self.signals.hotkey_pressed.emit(is_target_secondary)
                                return None
                        else:
                            if self._main_key_down is not None:
                                is_sec = self._main_key_down == 'secondary'
                                self._main_key_down = None
                                self.signals.hotkey_released.emit(is_sec)
                                return None
            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
            event_tap_callback,
            None
        )

        if not tap:
            print("[Hotkey] Warning: Failed to create CGEventTap for Fn key. Missing Accessibility permissions?")
            return

        run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._tap_run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._tap_run_loop, run_loop_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        Quartz.CFRunLoopRun()

    def _on_press(self, key):
        # Escape always means "cancel everything now", even outside capture mode.
        if key == Key.esc:
            self.signals.cancel_requested.emit()
            return

        # In capture mode, forward every key press to the UI
        if self._capture_mode:
            self.signals.key_event.emit(key, True)
            return

        if key in _MODIFIER_MAP:
            self._active_modifiers.add(_MODIFIER_MAP[key])
            return

        key_str = key_to_str(key)

        is_settings_combo = (
            self._active_modifiers == {"ctrl", "shift", "alt"}
            and (
                key_str == "q" or (isinstance(key, KeyCode) and getattr(key, 'vk', None) == 81)
            )
        )
        if is_settings_combo:
            self.signals.toggle_settings_requested.emit()
            return

        is_primary = (
            self._primary_combo is not None 
            and self._primary_combo.is_valid() 
            and key_str == self._primary_combo.main_key
            and self._active_modifiers == self._primary_combo.modifiers
        )
        is_secondary = (
            self._secondary_combo is not None 
            and self._secondary_combo.is_valid() 
            and key_str == self._secondary_combo.main_key
            and self._active_modifiers == self._secondary_combo.modifiers
        )

        if is_secondary and self._main_key_down is None:
            self._main_key_down = 'secondary'
            self.signals.hotkey_pressed.emit(True)
        elif is_primary and self._main_key_down is None:
            self._main_key_down = 'primary'
            self.signals.hotkey_pressed.emit(False)

    def _on_release(self, key):
        if self._capture_mode:
            self.signals.key_event.emit(key, False)
            return

        if key in _MODIFIER_MAP:
            mod_name = _MODIFIER_MAP[key]
            self._active_modifiers.discard(mod_name)
            return

        key_str = key_to_str(key)
        is_primary = (
            self._primary_combo is not None 
            and self._primary_combo.is_valid() 
            and key_str == self._primary_combo.main_key
        )
        is_secondary = (
            self._secondary_combo is not None 
            and self._secondary_combo.is_valid() 
            and key_str == self._secondary_combo.main_key
        )

        if is_secondary and self._main_key_down == 'secondary':
            self._main_key_down = None
            self.signals.hotkey_released.emit(True)
        elif is_primary and self._main_key_down == 'primary':
            self._main_key_down = None
            self.signals.hotkey_released.emit(False)
