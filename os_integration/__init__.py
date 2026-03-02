import platform
from .base import OSIntegration

_instance = None

def get_os_integration() -> OSIntegration:
    global _instance
    if _instance is not None:
        return _instance

    sys_name = platform.system().lower()
    
    if sys_name == "darwin":
        from .macos import MacOSIntegration
        _instance = MacOSIntegration()
    elif sys_name == "windows":
        from .windows import WindowsIntegration
        _instance = WindowsIntegration()
    else:
        # Default/Linux fallback
        from .linux import LinuxIntegration
        _instance = LinuxIntegration()
        
    return _instance
