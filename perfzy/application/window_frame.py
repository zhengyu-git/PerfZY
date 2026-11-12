"""Prefer compositor antialiased corners; retain a region for older Windows."""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QEvent,QObject,Qt,QTimer
from PySide6.QtGui import QGuiApplication,QPlatformSurfaceEvent
from PySide6.QtQuick import QQuickWindow


class WindowFrame(QObject):
    """A native window region, refreshed only on frame geometry/state changes."""
    def __init__(self,window: QQuickWindow,radius: int=14):
        super().__init__(window)
        self.window=window
        self.radius=radius
        self.last_error=0
        self.applied=False
        self.mode=""
        self._signature=None
        self._surface_available=True
        self._user=ctypes.WinDLL('user32',use_last_error=True)
        self._gdi=ctypes.WinDLL('gdi32',use_last_error=True)
        self._user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        self._user.GetWindowRect.restype=wintypes.BOOL
        self._user.SetWindowRgn.argtypes=[wintypes.HWND,wintypes.HANDLE,wintypes.BOOL]
        self._user.SetWindowRgn.restype=ctypes.c_int
        self._gdi.CreateRoundRectRgn.argtypes=[ctypes.c_int]*6
        self._gdi.CreateRoundRectRgn.restype=wintypes.HANDLE
        self._gdi.DeleteObject.argtypes=[wintypes.HANDLE]
        self._gdi.DeleteObject.restype=wintypes.BOOL
        try:
            self._dwm=ctypes.WinDLL('dwmapi',use_last_error=True)
            self._dwm.DwmSetWindowAttribute.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
            self._dwm.DwmSetWindowAttribute.restype=ctypes.c_long
        except (AttributeError,OSError):
            self._dwm=None
        self._timer=QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.update_shape)
        for signal in (window.widthChanged,window.heightChanged,window.windowStateChanged,
                       window.visibilityChanged,window.screenChanged):
            signal.connect(self.schedule)
        window.installEventFilter(self)
        self.update_shape()

    def schedule(self,*_):
        if self._surface_available and not self._timer.isActive():
            self._timer.start(0)

    def eventFilter(self,watched,event):
        if event.type()==QEvent.PlatformSurface:
            self._signature=None
            if event.surfaceEventType()==QPlatformSurfaceEvent.SurfaceAboutToBeDestroyed:
                self._surface_available=False
                self._timer.stop()
            else:
                self._surface_available=True
                self.schedule()
        elif event.type()==QEvent.DevicePixelRatioChange:
            self.schedule()
        return False

    def update_shape(self):
        if not self._surface_available:
            return
        handle=wintypes.HWND(int(self.window.winId()))
        bounds=wintypes.RECT()
        if not self._user.GetWindowRect(handle,ctypes.byref(bounds)):
            self.last_error=ctypes.get_last_error()
            return
        width,height=bounds.right-bounds.left,bounds.bottom-bounds.top
        if width<=0 or height<=0:
            return
        square=bool(self.window.windowStates() & (Qt.WindowMaximized|Qt.WindowFullScreen))
        radius=max(1,min(round(self.radius*self.window.devicePixelRatio()),min(width,height)//2))
        signature=(handle.value,width,height,radius,square)
        if signature==self._signature:
            return
        if self._dwm is not None:
            preference=ctypes.c_int(1 if square else 2)  # DONOTROUND / ROUND
            result=self._dwm.DwmSetWindowAttribute(handle,33,ctypes.byref(preference),ctypes.sizeof(preference))
            if result==0:
                # A GDI region prevents DWM rounding and has binary stair-step
                # edges. Remove it rather than overlaying two different clips.
                if self._user.SetWindowRgn(handle,None,True):
                    self.mode="dwm"
                    self.applied=True
                    self.last_error=0
                    self._signature=signature
                    return
        region=None
        if not square:
            # GDI excludes the right/bottom boundary; +1 preserves the last row
            # and column of the frameless client while cutting all four corners.
            region=self._gdi.CreateRoundRectRgn(0,0,width+1,height+1,radius*2,radius*2)
            if not region:
                self.last_error=ctypes.get_last_error()
                return
        if self._user.SetWindowRgn(handle,region,True):
            # Windows owns region after a successful SetWindowRgn call.
            self._signature=signature
            self.applied=True
            self.mode="region"
            self.last_error=0
        else:
            self.applied=False
            self.last_error=ctypes.get_last_error()
            if region:
                self._gdi.DeleteObject(region)


def apply_window_corners(window: QQuickWindow) -> WindowFrame | None:
    if sys.platform!='win32' or QGuiApplication.platformName()!='windows':
        return None
    try:
        return WindowFrame(window)
    except (AttributeError,OSError):
        return None
